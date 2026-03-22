import json
import smtplib
import sqlite3
from copy import deepcopy
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import streamlit as st

st.set_page_config(page_title="SQL Quiz App", page_icon="🗄️", layout="wide")

# -----------------------------
# EMAIL SETTINGS
# -----------------------------
SENDER_EMAIL = "vetrikvk@gmail.com"
SENDER_APP_PASSWORD = "yyxe hzeo mnox hnlx"
RECEIVER_EMAILS = [
    "knkarthi2002@gmail.com",
    "vetrivelkvk@gmail.com",
    "knirmalak99@gmail.com",
]

# -----------------------------
# QUIZ CONFIG
# -----------------------------
QUIZ_FILES = {
    "SQL Quiz 1 - Basics-1": "sql_quiz_1.json",
    "SQL Quiz 2 - Basics-2": "sql_quiz_2.json",
    "SQL Quiz 3 - Basics-3": "sql_quiz_3.json",
}


# -----------------------------
# HELPER FUNCTIONS
# -----------------------------
def load_questions(file_name):
    with open(file_name, "r", encoding="utf-8") as file:
        return json.load(file)


def check_answer(user_answer, correct_answer):
    return sorted(user_answer) == sorted(correct_answer)


def get_review_text(percentage):
    if percentage == 100:
        return "Excellent performance. You answered all questions correctly."
    if percentage >= 75:
        return "Very good performance. You have a strong understanding of SQL basics."
    if percentage >= 50:
        return "Good effort. You understand several concepts, but there is still room for improvement."
    return "You need more practice. Review the concepts and try again."


def reset_quiz_state():
    st.session_state.submitted = False
    st.session_state.answers = {}
    st.session_state.sql_answers = {}
    st.session_state.sql_run_results = {}
    st.session_state.email_sent = False
    st.session_state.result_data = None


def render_question(question_id, question_text):
    parts = question_text.split("\n\n", 1)

    if len(parts) == 2:
        question_title, code_snippet = parts
        st.markdown(f"### Q{question_id}. {question_title}")
        st.code(code_snippet, language="sql")
    else:
        st.markdown(f"### Q{question_id}. {question_text}")


# -----------------------------
# SQL HELPERS
# -----------------------------
def create_in_memory_db(schema_sql, seed_data):
    conn = sqlite3.connect(":memory:")
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
            values = [row[col] for col in columns]
            cursor.execute(insert_sql, values)

    conn.commit()
    return conn


def normalize_sql_result(rows):
    normalized = []
    for row in rows:
        normalized.append(tuple(row))
    return normalized


def run_sql_query(schema_sql, seed_data, query):
    try:
        conn = create_in_memory_db(schema_sql, seed_data)
        cursor = conn.cursor()
        cursor.execute(query)

        if query.strip().lower().startswith("select"):
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            conn.close()
            return {
                "success": True,
                "columns": columns,
                "rows": rows,
                "error": None,
            }

        conn.commit()
        conn.close()
        return {
            "success": True,
            "columns": [],
            "rows": [],
            "error": None,
        }
    except Exception as error:
        return {
            "success": False,
            "columns": [],
            "rows": [],
            "error": str(error),
        }


def compare_sql_results(actual_rows, expected_rows):
    actual_normalized = normalize_sql_result(actual_rows)
    expected_normalized = normalize_sql_result(expected_rows)
    return actual_normalized == expected_normalized


def format_sql_result_for_email(columns, rows):
    if not rows and not columns:
        return "No result returned"

    lines = []
    if columns:
        lines.append(" | ".join(columns))
        lines.append("-" * 60)

    for row in rows:
        lines.append(" | ".join(str(value) for value in row))

    return "\n".join(lines) if lines else "No rows returned"


# -----------------------------
# EMAIL
# -----------------------------
def build_email_body(quiz_name, score, total_questions, percentage, review, question_results):
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


def send_result_email(quiz_name, score, total_questions, percentage, review, question_results):
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

        query_text = st.text_area(
            "Write your SQL query here:",
            value=st.session_state.sql_answers.get(sql_key, ""),
            height=180,
            key=sql_key,
        )
        st.session_state.sql_answers[sql_key] = query_text

        if st.button("Run Query", key=run_key):
            run_result = run_sql_query(
                q.get("schema_sql", ""),
                deepcopy(q.get("seed_data", [])),
                query_text,
            )
            st.session_state.sql_run_results[run_key] = run_result

        if run_key in st.session_state.sql_run_results:
            run_result = st.session_state.sql_run_results[run_key]

            if run_result["success"]:
                st.success("Query executed successfully.")
                if run_result["rows"]:
                    st.dataframe(run_result["rows"], use_container_width=True)
                else:
                    st.info("Query ran successfully. No rows returned.")
            else:
                st.error(f"Query execution failed: {run_result['error']}")

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
                failure_reason = "SQL query execution failed."
            else:
                is_correct = compare_sql_results(run_result["rows"], expected_rows)
                failure_reason = (
                    "Answered correctly."
                    if is_correct
                    else "Query result did not match the expected result."
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
    percentage = (score / total_questions) * 100
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
            st.code(item["user_query"] if item["user_query"].strip() else "No query entered", language="sql")

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
            st.error(f"Failed to send email: {error_message}")

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