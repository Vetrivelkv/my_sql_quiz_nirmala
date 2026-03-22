import json
import re
import smtplib
import sqlite3
from copy import deepcopy
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import os
import streamlit as st

SENDER_EMAIL = st.secrets["SENDER_EMAIL"]
SENDER_APP_PASSWORD = st.secrets["SENDER_APP_PASSWORD"]
RECEIVER_EMAILS = st.secrets["RECEIVER_EMAILS"]

# optional: root-level secrets also exist as env vars
sender_email_from_env = os.environ.get("SENDER_EMAIL")# -----------------------------
# QUIZ CONFIG
# -----------------------------
QUIZ_FILES = {
    "SQL Quiz 1 - Basics-1": "sql_quiz_1.json",
    "SQL Quiz 2 - Basics-2": "sql_quiz_2.json",
    "SQL Quiz 3 - Basics-3": "sql_quiz_3.json",
}

READ_ONLY_SQL_KEYWORDS = {
    "select",
    "with",
}

BLOCKED_SQL_PATTERNS = [
    r"\binsert\b",
    r"\bupdate\b",
    r"\bdelete\b",
    r"\bdrop\b",
    r"\balter\b",
    r"\btruncate\b",
    r"\battach\b",
    r"\bdetach\b",
    r"\bpragma\b",
    r"\bcreate\b",
    r"\breplace\b",
    r"\bvacuum\b",
    r"\btransaction\b",
    r"\bbegin\b",
    r"\bcommit\b",
    r"\brollback\b",
]

# -----------------------------
# HELPER FUNCTIONS
# -----------------------------
def load_questions(file_name: str) -> list[dict[str, Any]]:
    with open(file_name, "r", encoding="utf-8") as file:
        return json.load(file)


def check_answer(user_answer: list[str], correct_answer: list[str]) -> bool:
    return sorted(user_answer) == sorted(correct_answer)


def get_review_text(percentage: float) -> str:
    if percentage == 100:
        return "Excellent performance. You answered all questions correctly."
    if percentage >= 75:
        return "Very good performance. You have a strong understanding of SQL basics."
    if percentage >= 50:
        return (
            "Good effort. You understand several concepts, but there is still room "
            "for improvement."
        )
    return "You need more practice. Review the concepts and try again."


def reset_quiz_state() -> None:
    st.session_state.submitted = False
    st.session_state.answers = {}
    st.session_state.sql_answers = {}
    st.session_state.sql_run_results = {}
    st.session_state.email_sent = False
    st.session_state.result_data = None


def render_question(question_id: int, question_text: str) -> None:
    parts = question_text.split("\n\n", 1)

    if len(parts) == 2:
        question_title, code_snippet = parts
        st.markdown(f"### Q{question_id}. {question_title}")
        st.code(code_snippet, language="sql")
    else:
        st.markdown(f"### Q{question_id}. {question_text}")


def rows_to_display_data(columns: list[str], rows: list[tuple]) -> list[dict[str, Any]]:
    if not rows:
        return []

    if not columns:
        return [{f"column_{i + 1}": value for i, value in enumerate(row)} for row in rows]

    return [dict(zip(columns, row)) for row in rows]


# -----------------------------
# SQL HELPERS
# -----------------------------
def create_in_memory_db(schema_sql: str, seed_data: list[dict[str, Any]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if schema_sql:
        cursor.executescript(schema_sql)

    for item in seed_data or []:
        table = item["table"]
        rows = item["rows"]

        if not rows:
            continue

        columns = list(rows[0].keys())
        placeholders = ", ".join(["?"] * len(columns))
        column_names = ", ".join(columns)
        insert_sql = f"INSERT INTO {table} ({column_names}) VALUES ({placeholders})"

        for row in rows:
            values = [row.get(col) for col in columns]
            cursor.execute(insert_sql, values)

    conn.commit()
    return conn


def strip_sql_comments(query: str) -> str:
    query = re.sub(r"--.*?$", "", query, flags=re.MULTILINE)
    query = re.sub(r"/\*.*?\*/", "", query, flags=re.DOTALL)
    return query.strip()


def validate_sql_query(query: str) -> tuple[bool, str | None]:
    cleaned_query = strip_sql_comments(query)

    if not cleaned_query:
        return False, "Please enter an SQL query."

    if cleaned_query.count(";") > 1 or (
        ";" in cleaned_query and not cleaned_query.rstrip().endswith(";")
    ):
        return False, "Only a single SQL statement is allowed."

    statement = cleaned_query.rstrip(";").strip()
    first_word_match = re.match(r"^([a-zA-Z_]+)", statement)

    if not first_word_match:
        return False, "Unable to detect a valid SQL statement."

    first_word = first_word_match.group(1).lower()
    if first_word not in READ_ONLY_SQL_KEYWORDS:
        return False, "Only read-only SELECT queries are allowed."

    lowered = f" {statement.lower()} "
    for pattern in BLOCKED_SQL_PATTERNS:
        if re.search(pattern, lowered):
            return False, "Only safe read-only SQL queries are allowed."

    return True, None


def normalize_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    return value


def normalize_sql_result(rows: list[Any]) -> list[tuple[Any, ...]]:
    normalized: list[tuple[Any, ...]] = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            normalized.append(tuple(normalize_value(value) for value in tuple(row)))
        elif isinstance(row, (list, tuple)):
            normalized.append(tuple(normalize_value(value) for value in row))
        else:
            normalized.append((normalize_value(row),))
    return normalized


def run_sql_query(
    schema_sql: str, seed_data: list[dict[str, Any]], query: str
) -> dict[str, Any]:
    is_valid, validation_error = validate_sql_query(query)
    if not is_valid:
        return {
            "success": False,
            "columns": [],
            "rows": [],
            "error": validation_error,
        }

    conn: sqlite3.Connection | None = None
    try:
        conn = create_in_memory_db(schema_sql, seed_data)
        cursor = conn.cursor()
        cursor.execute(query)

        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description] if cursor.description else []

        return {
            "success": True,
            "columns": columns,
            "rows": [tuple(row) for row in rows],
            "error": None,
        }
    except Exception as error:
        return {
            "success": False,
            "columns": [],
            "rows": [],
            "error": str(error),
        }
    finally:
        if conn is not None:
            conn.close()


def compare_sql_results(
    actual_columns: list[str],
    actual_rows: list[Any],
    expected_columns: list[str],
    expected_rows: list[Any],
) -> tuple[bool, str]:
    actual_columns_normalized = [col.lower() for col in actual_columns]
    expected_columns_normalized = [col.lower() for col in expected_columns]

    if expected_columns and actual_columns_normalized != expected_columns_normalized:
        return (
            False,
            "The query ran, but the returned columns did not match the expected columns.",
        )

    actual_normalized = sorted(normalize_sql_result(actual_rows))
    expected_normalized = sorted(normalize_sql_result(expected_rows))

    if actual_normalized != expected_normalized:
        return (
            False,
            "The query ran, but the returned rows did not match the expected result.",
        )

    return True, "Answered correctly."


def format_sql_result_for_email(columns: list[str], rows: list[Any]) -> str:
    if not rows and not columns:
        return "No result returned"

    lines = []
    if columns:
        lines.append(" | ".join(columns))
        lines.append("-" * 60)

    for row in rows:
        row_values = tuple(row) if isinstance(row, sqlite3.Row) else row
        lines.append(" | ".join(str(value) for value in row_values))

    return "\n".join(lines) if lines else "No rows returned"


# -----------------------------
# EMAIL
# -----------------------------
def build_email_body(
    quiz_name: str,
    score: int,
    total_questions: int,
    percentage: float,
    review: str,
    question_results: list[dict[str, Any]],
) -> str:
    lines = [
        "=" * 80,
        "SQL QUIZ RESULT",
        "=" * 80,
        "",
        f"Quiz Attended           : {quiz_name}",
        f"Total Questions         : {total_questions}",
        f"Correctly Answered      : {score}",
        f"Incorrectly Answered    : {total_questions - score}",
        f"Percentage              : {percentage:.2f}%",
        f"Overall Review          : {review}",
        "",
        "=" * 80,
        "DETAILED QUESTION-WISE REPORT",
        "=" * 80,
        "",
    ]

    for item in question_results:
        lines.extend(
            [
                f"Question ID             : {item['id']}",
                f"Question Type           : {item['type']}",
                "Question Asked          :",
                item["question"],
                "",
            ]
        )

        if item["type"] in ["single", "multiple"]:
            lines.extend(
                [
                    f"User Answer             : {item['user_answer']}",
                    f"Correct Answer          : {item['correct_answer']}",
                    f"Status                  : {'Correct' if item['is_correct'] else 'Incorrect'}",
                    "",
                ]
            )
        elif item["type"] == "sql":
            lines.extend(
                [
                    "User SQL Query          :",
                    item["user_query"] if item["user_query"].strip() else "No query entered",
                    "",
                    "Expected Result         :",
                    item["expected_result_text"],
                    "",
                    "Actual Result           :",
                    item["actual_result_text"],
                    "",
                    f"Status                  : {'Correct' if item['is_correct'] else 'Incorrect'}",
                    f"Failure Reason          : {item['failure_reason']}",
                    "",
                ]
            )

            if item["error"]:
                lines.extend(
                    [
                        "Execution Error         :",
                        item["error"],
                        "",
                    ]
                )

        lines.extend(["-" * 80, ""])

    return "\n".join(lines)


def send_result_email(
    quiz_name: str,
    score: int,
    total_questions: int,
    percentage: float,
    review: str,
    question_results: list[dict[str, Any]],
) -> tuple[bool, str | None]:
    if not SENDER_EMAIL or not SENDER_APP_PASSWORD or not RECEIVER_EMAILS:
        return False, "Email settings are missing. Add them in Streamlit secrets or environment variables."

    subject = f"SQL Quiz Result - {quiz_name}"
    body = build_email_body(
        quiz_name, score, total_questions, percentage, review, question_results
    )

    message = MIMEMultipart()
    message["From"] = SENDER_EMAIL
    message["To"] = ", ".join(RECEIVER_EMAILS)
    message["Subject"] = subject
    message.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_APP_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECEIVER_EMAILS, message.as_string())
        return True, None
    except Exception as error:
        return False, str(error)


# -----------------------------
# SESSION STATE
# -----------------------------
if "selected_quiz" not in st.session_state:
    st.session_state.selected_quiz = list(QUIZ_FILES.keys())[0]

if "submitted" not in st.session_state:
    st.session_state.submitted = False

if "answers" not in st.session_state:
    st.session_state.answers = {}

if "sql_answers" not in st.session_state:
    st.session_state.sql_answers = {}

if "sql_run_results" not in st.session_state:
    st.session_state.sql_run_results = {}

if "email_sent" not in st.session_state:
    st.session_state.email_sent = False

if "result_data" not in st.session_state:
    st.session_state.result_data = None


# -----------------------------
# UI
# -----------------------------
st.title("🗄️ SQL Quiz App")

selected_quiz = st.selectbox(
    "Select Quiz",
    options=list(QUIZ_FILES.keys()),
    index=list(QUIZ_FILES.keys()).index(st.session_state.selected_quiz),
)

if selected_quiz != st.session_state.selected_quiz:
    st.session_state.selected_quiz = selected_quiz
    reset_quiz_state()
    st.rerun()

quiz_file = QUIZ_FILES[st.session_state.selected_quiz]
questions = load_questions(quiz_file)

st.write(f"Currently selected: **{st.session_state.selected_quiz}**")
st.write(f"This quiz contains **{len(questions)}** questions.")

for q in questions:
    render_question(q["id"], q["question"])

    if q["type"] == "single":
        selected = st.radio(
            "Choose one answer:",
            q["options"],
            key=f"{st.session_state.selected_quiz}_question_{q['id']}",
            index=None,
        )
        st.session_state.answers[q["id"]] = [selected] if selected else []

    elif q["type"] == "multiple":
        selected = st.multiselect(
            "Choose all correct answers:",
            q["options"],
            key=f"{st.session_state.selected_quiz}_question_{q['id']}",
        )
        st.session_state.answers[q["id"]] = selected

    elif q["type"] == "sql":
        if q.get("schema_sql"):
            st.write("**Schema:**")
            st.code(q["schema_sql"], language="sql")

        if q.get("seed_data"):
            st.write("**Sample Data:**")
            for table_data in q["seed_data"]:
                st.write(f"**Table: {table_data['table']}**")
                st.dataframe(table_data["rows"], use_container_width=True)

        sql_key = f"{st.session_state.selected_quiz}_sql_{q['id']}"
        run_key = f"{st.session_state.selected_quiz}_sql_run_{q['id']}"

        st.caption("SQL output is hidden until you submit the quiz.")

        query_text = st.text_area(
            "Write your SQL query here:",
            value=st.session_state.sql_answers.get(sql_key, ""),
            height=180,
            key=sql_key,
            placeholder="Example: SELECT name FROM employees WHERE salary > 50000;",
        )
        st.session_state.sql_answers[sql_key] = query_text

        is_valid_query, validation_message = validate_sql_query(query_text)
        if query_text.strip() and not is_valid_query:
            st.warning(validation_message)

        if st.button("Run Query", key=run_key):
            run_result = run_sql_query(
                q.get("schema_sql", ""),
                deepcopy(q.get("seed_data", [])),
                query_text,
            )
            st.session_state.sql_run_results[run_key] = run_result

            if run_result["success"]:
                st.info("Query has been saved. The output will be shown only after you submit the quiz.")
            else:
                st.info("The query check has been saved. Any execution error will be shown only after you submit the quiz.")

    st.write("---")

submit_button = st.button("Submit Quiz", type="primary")

if submit_button:
    st.session_state.submitted = True
    st.session_state.email_sent = False

    score = 0
    question_results = []

    for q in questions:
        if q["type"] in ["single", "multiple"]:
            user_answer = st.session_state.answers.get(q["id"], [])
            correct_answer = q["answer"]
            is_correct = check_answer(user_answer, correct_answer)

            if is_correct:
                score += 1

            question_results.append(
                {
                    "id": q["id"],
                    "type": q["type"],
                    "question": q["question"],
                    "user_answer": ", ".join(user_answer) if user_answer else "No answer selected",
                    "correct_answer": ", ".join(correct_answer),
                    "is_correct": is_correct,
                }
            )

        elif q["type"] == "sql":
            sql_key = f"{st.session_state.selected_quiz}_sql_{q['id']}"
            user_query = st.session_state.sql_answers.get(sql_key, "")

            run_result = run_sql_query(
                q.get("schema_sql", ""),
                deepcopy(q.get("seed_data", [])),
                user_query,
            )

            expected_rows = q.get("expected_rows", [])
            expected_columns = q.get("expected_columns", [])

            if not user_query.strip():
                is_correct = False
                failure_reason = "No SQL query entered."
            elif not run_result["success"]:
                is_correct = False
                failure_reason = run_result["error"] or "SQL query execution failed."
            else:
                is_correct, failure_reason = compare_sql_results(
                    run_result["columns"],
                    run_result["rows"],
                    expected_columns,
                    expected_rows,
                )

            if is_correct:
                score += 1

            expected_result_text = format_sql_result_for_email(expected_columns, expected_rows)
            actual_result_text = format_sql_result_for_email(
                run_result["columns"], run_result["rows"]
            )

            question_results.append(
                {
                    "id": q["id"],
                    "type": "sql",
                    "question": q["question"],
                    "user_query": user_query,
                    "expected_result_text": expected_result_text,
                    "actual_result_text": actual_result_text,
                    "error": run_result["error"],
                    "is_correct": is_correct,
                    "failure_reason": failure_reason,
                }
            )

    total_questions = len(questions)
    percentage = (score / total_questions) * 100 if total_questions else 0
    review = get_review_text(percentage)

    st.session_state.result_data = {
        "quiz_name": st.session_state.selected_quiz,
        "score": score,
        "total_questions": total_questions,
        "percentage": percentage,
        "review": review,
        "question_results": question_results,
    }

    st.rerun()


# -----------------------------
# RESULT SECTION
# -----------------------------
if st.session_state.submitted and st.session_state.result_data:
    result = st.session_state.result_data

    st.header("Quiz Result")
    st.write(f"**Quiz Attended:** {result['quiz_name']}")
    st.subheader(f"Final Score: {result['score']} / {result['total_questions']}")
    st.subheader(f"Percentage: {result['percentage']:.2f}%")
    st.write(f"**Review:** {result['review']}")

    for item in result["question_results"]:
        if item["is_correct"]:
            st.success(f"Q{item['id']}: Correct")
        else:
            st.error(f"Q{item['id']}: Incorrect")

        st.write(f"**Question:** {item['question']}")

        if item["type"] in ["single", "multiple"]:
            st.write(f"**Your Answer:** {item['user_answer']}")
            st.write(f"**Correct Answer:** {item['correct_answer']}")
        else:
            st.write("**Your SQL Query:**")
            st.code(
                item["user_query"] if item["user_query"].strip() else "No query entered",
                language="sql",
            )

            st.write("**Expected Result:**")
            st.code(item["expected_result_text"], language="text")

            st.write("**Actual Result:**")
            st.code(item["actual_result_text"], language="text")

            st.write(f"**Failure Reason:** {item['failure_reason']}")

            if item["error"]:
                st.write("**Execution Error:**")
                st.code(item["error"], language="text")

        st.write("---")

    if not st.session_state.email_sent:
        success, error_message = send_result_email(
            quiz_name=result["quiz_name"],
            score=result["score"],
            total_questions=result["total_questions"],
            percentage=result["percentage"],
            review=result["review"],
            question_results=result["question_results"],
        )

        if success:
            st.session_state.email_sent = True
            st.success("Quiz result email sent automatically.")
        else:
            st.warning(f"Email was not sent: {error_message}")

    if result["percentage"] == 100:
        st.balloons()
        st.success("Excellent! You answered all questions correctly.")
    elif result["percentage"] >= 75:
        st.info("Very good! You have a strong understanding of this quiz.")
    elif result["percentage"] >= 50:
        st.warning("Good effort. You understand some concepts, but there is room for improvement.")
    else:
        st.warning("Keep practicing. Review the concepts and try again.")

    if st.button("Restart Quiz"):
        reset_quiz_state()
        st.rerun()
