"""
College Resource & Venue Management System
Student requests -> Admin approval | Teacher direct booking
"""

import os
import re
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "database.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "jpg", "jpeg", "png"}

app = Flask(__name__)
app.secret_key = "crvms-pbl-secret-key-2024"
app.config["DATABASE"] = DATABASE
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

COLLEGE_NAME = "Graphic Era Hill University"
STUDENT_RESOURCES = ["Seminar Hall", "Auditorium", "Room 101"]
TEACHER_PRIORITY_MSG = (
    "Your request was rejected because the resource was assigned for faculty usage."
)
OPEN_HOUR = 8
CLOSE_HOUR = 17

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def migrate_db(cursor):
    """Upgrade existing database schema."""
    tables = {r[0] for r in cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    if "faculty" in tables and "teachers" not in tables:
        cursor.execute(
            """
            CREATE TABLE teachers (
                id TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                name TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            "INSERT INTO teachers SELECT id, password, name FROM faculty"
        )

    req_cols = {r[1] for r in cursor.execute("PRAGMA table_info(requests)").fetchall()}
    if "requested_on" not in req_cols:
        cursor.execute("ALTER TABLE requests ADD COLUMN requested_on TEXT")
    if "rejection_reason" not in req_cols:
        cursor.execute("ALTER TABLE requests ADD COLUMN rejection_reason TEXT")
    if "recommendation_file" not in req_cols:
        cursor.execute("ALTER TABLE requests ADD COLUMN recommendation_file TEXT")

    book_cols = {r[1] for r in cursor.execute("PRAGMA table_info(bookings)").fetchall()}
    if "approved_by" not in book_cols:
        cursor.execute("ALTER TABLE bookings ADD COLUMN approved_by TEXT")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS notices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            posted_by TEXT NOT NULL,
            posted_on TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            user_id TEXT,
            subject TEXT NOT NULL,
            message TEXT NOT NULL,
            submitted_on TEXT NOT NULL
        )
        """
    )


def init_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    cursor = db.cursor()

    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS students (
            id TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS teachers (
            id TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS admin (
            id TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL,
            capacity INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            event_title TEXT NOT NULL,
            resource TEXT NOT NULL,
            date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            attendees INTEGER NOT NULL,
            reason TEXT NOT NULL,
            status TEXT DEFAULT 'Pending',
            requested_on TEXT,
            rejection_reason TEXT,
            recommendation_file TEXT
        );
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resource TEXT NOT NULL,
            date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            booked_by TEXT NOT NULL,
            role TEXT NOT NULL,
            approved_by TEXT
        );
        """
    )
    migrate_db(cursor)

    cursor.execute(
        "INSERT OR REPLACE INTO students (id, password, name) VALUES (?, ?, ?)",
        ("220145", "stu123", "Rahul Sharma"),
    )
    cursor.execute(
        "INSERT OR REPLACE INTO teachers (id, password, name) VALUES (?, ?, ?)",
        ("CSE101", "fac123", "Dr. Mehta"),
    )
    cursor.execute(
        "INSERT OR REPLACE INTO admin (id, password, name) VALUES (?, ?, ?)",
        ("ADMIN01", "admin123", "System Admin"),
    )
    cursor.execute("DELETE FROM students WHERE id != '220145'")
    cursor.execute("DELETE FROM teachers WHERE id != 'CSE101'")
    cursor.execute("DELETE FROM admin WHERE id != 'ADMIN01'")

    for name, rtype, cap in [
        ("Seminar Hall", "Hall", 200),
        ("Auditorium", "Auditorium", 500),
        ("Room 101", "Classroom", 60),
        ("Lab 1", "Lab", 40),
    ]:
        cursor.execute(
            "INSERT OR IGNORE INTO resources (name, type, capacity) VALUES (?, ?, ?)",
            (name, rtype, cap),
        )

    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_recommendation_file(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_file(file_storage.filename):
        return None
    filename = secure_filename(file_storage.filename)
    unique = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
    path = os.path.join(app.config["UPLOAD_FOLDER"], unique)
    file_storage.save(path)
    return f"uploads/{unique}"


def time_to_minutes(t):
    if not t:
        return 0
    try:
        parts = str(t).strip().split(":")
        if len(parts) < 2:
            return 0
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, TypeError):
        return 0


def times_overlap(start1, end1, start2, end2):
    s1, e1 = time_to_minutes(start1), time_to_minutes(end1)
    s2, e2 = time_to_minutes(start2), time_to_minutes(end2)
    return s1 < e2 and s2 < e1


def is_lab_resource(name):
    return str(name).strip().lower().startswith("lab")


def get_resource_names():
    db = get_db()
    return [
        r["name"]
        for r in db.execute("SELECT name FROM resources ORDER BY name").fetchall()
    ]


def get_student_resources():
    return [r for r in get_resource_names() if r in STUDENT_RESOURCES]


def validate_future_date(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date() >= datetime.now().date()
    except ValueError:
        return False


def validate_time_slot(start_time, end_time):
    start_m, end_m = time_to_minutes(start_time), time_to_minutes(end_time)
    if start_m >= end_m:
        return False
    if start_m < OPEN_HOUR * 60 or end_m > CLOSE_HOUR * 60:
        return False
    return True


def validate_booking_form(date, start_time, end_time):
    if not validate_future_date(date):
        return "Please select a valid future date."
    if not validate_time_slot(start_time, end_time):
        return "Invalid time slot selected."
    return None


def has_booking_conflict(resource, date, start_time, end_time):
    db = get_db()
    for row in db.execute(
        "SELECT start_time, end_time FROM bookings WHERE resource = ? AND date = ?",
        (resource, date),
    ).fetchall():
        if times_overlap(start_time, end_time, row["start_time"], row["end_time"]):
            return True
    return False


def reject_conflicting_pending(resource, date, start_time, end_time):
    db = get_db()
    for req in db.execute(
        "SELECT * FROM requests WHERE status = 'Pending' AND resource = ? AND date = ?",
        (resource, date),
    ).fetchall():
        if times_overlap(start_time, end_time, req["start_time"], req["end_time"]):
            db.execute(
                "UPDATE requests SET status = 'Rejected', rejection_reason = ? WHERE id = ?",
                (TEACHER_PRIORITY_MSG, req["id"]),
            )


def format_time_slot(start_time, end_time):
    """Display time range for tables."""
    return f"{start_time} - {end_time}"


def get_dynamic_availability(resource_filter=None, date_filter=None):
    """
    Build availability list from actual bookings in the database only.
    Each confirmed booking appears as 'Booked'. No hardcoded slots.
    """
    db = get_db()
    query = "SELECT resource, date, start_time, end_time FROM bookings WHERE 1=1"
    params = []
    if resource_filter:
        query += " AND resource = ?"
        params.append(resource_filter)
    if date_filter:
        query += " AND date = ?"
        params.append(date_filter)
    query += " ORDER BY date ASC, resource ASC, start_time ASC"
    bookings = db.execute(query, params).fetchall()
    return [
        {
            "resource": b["resource"],
            "date": b["date"],
            "time_slot": format_time_slot(b["start_time"], b["end_time"]),
            "status": "Booked",
        }
        for b in bookings
    ]


def get_notices():
    db = get_db()
    return db.execute(
        "SELECT * FROM notices ORDER BY id DESC"
    ).fetchall()


def get_feedback_list():
    db = get_db()
    return db.execute(
        "SELECT * FROM feedback ORDER BY id DESC"
    ).fetchall()


def get_all_bookings(search=""):
    db = get_db()
    rows = db.execute(
        """
        SELECT b.*,
               COALESCE(b.approved_by, t.name, a.name, s.name, b.booked_by) AS display_approved_by
        FROM bookings b
        LEFT JOIN teachers t ON b.booked_by = t.id AND b.role = 'teacher'
        LEFT JOIN students s ON b.booked_by = s.id AND b.role = 'student'
        LEFT JOIN admin a ON b.booked_by = a.id AND b.role = 'admin'
        ORDER BY b.id DESC
        """
    ).fetchall()
    if search:
        rows = [
            b for b in rows
            if search in b["resource"].lower() or search in b["date"].lower()
        ]
    return rows


def get_today_schedule(teacher_id=None):
    db = get_db()
    today = datetime.now().strftime("%Y-%m-%d")
    if teacher_id:
        return db.execute(
            "SELECT * FROM bookings WHERE date = ? AND booked_by = ? ORDER BY start_time",
            (today, teacher_id),
        ).fetchall()
    return db.execute(
        "SELECT * FROM bookings WHERE date = ? ORDER BY start_time",
        (today,),
    ).fetchall()


def get_admin_stats():
    db = get_db()
    return {
        "total_resources": db.execute("SELECT COUNT(*) AS c FROM resources").fetchone()["c"],
        "pending": db.execute(
            "SELECT COUNT(*) AS c FROM requests WHERE status = 'Pending'"
        ).fetchone()["c"],
        "approved": db.execute(
            "SELECT COUNT(*) AS c FROM requests WHERE status = 'Approved'"
        ).fetchone()["c"],
        "rejected": db.execute(
            "SELECT COUNT(*) AS c FROM requests WHERE status = 'Rejected'"
        ).fetchone()["c"],
        "total_bookings": db.execute("SELECT COUNT(*) AS c FROM bookings").fetchone()["c"],
    }


def get_analytics():
    db = get_db()
    stats = get_admin_stats()
    by_resource = db.execute(
        "SELECT resource, COUNT(*) AS c FROM bookings GROUP BY resource"
    ).fetchall()
    return {
        "stats": stats,
        "resource_labels": [r["resource"] for r in by_resource],
        "resource_values": [r["c"] for r in by_resource],
    }


def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in first.", "warning")
                return redirect(url_for("login"))
            if role and session.get("role") != role:
                flash("Access denied.", "danger")
                return redirect(dashboard_for_role(session.get("role")))
            return f(*args, **kwargs)
        return wrapped
    return decorator


def dashboard_for_role(role):
    if role == "student":
        return url_for("student_dashboard")
    if role == "teacher":
        return url_for("teacher_dashboard")
    if role == "admin":
        return url_for("admin_dashboard")
    return url_for("login")


def validate_user_id(user_id, role):
    if role == "student":
        return bool(re.fullmatch(r"\d+", user_id))
    if role in ("teacher", "admin"):
        return bool(re.fullmatch(r"[A-Za-z0-9]+", user_id)) and re.search(r"[A-Za-z]", user_id)
    return False


@app.context_processor
def inject_globals():
    role = session.get("role", "")
    return {
        "college_name": COLLEGE_NAME,
        "current_user_name": session.get("user_name", ""),
        "current_user_id": session.get("user_id", ""),
        "user_role": role,
    }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(dashboard_for_role(session.get("role")))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user_id = request.form.get("user_id", "").strip()
        password = request.form.get("password", "").strip()
        role = request.form.get("role", "").strip()

        if not user_id or not password or role not in ("student", "teacher", "admin"):
            flash("All fields are required.", "danger")
            return render_template("login.html")

        if not validate_user_id(user_id, role):
            flash("Invalid ID format for selected role.", "danger")
            return render_template("login.html")

        db = get_db()
        table = {"student": "students", "teacher": "teachers", "admin": "admin"}[role]
        user = db.execute(
            f"SELECT * FROM {table} WHERE id = ? AND password = ?",
            (user_id, password),
        ).fetchone()

        if user:
            session.clear()
            session["user_id"] = user_id
            session["user_name"] = user["name"]
            session["role"] = role
            flash(f"Welcome, {user['name']}.", "success")
            return redirect(dashboard_for_role(role))

        flash("Invalid login. Check ID, password and role.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Student
# ---------------------------------------------------------------------------
@app.route("/student/dashboard")
@login_required("student")
def student_dashboard():
    db = get_db()
    uid = session["user_id"]
    available_resources = db.execute("SELECT COUNT(*) AS c FROM resources").fetchone()["c"]
    pending = db.execute(
        "SELECT COUNT(*) AS c FROM requests WHERE student_id = ? AND status = 'Pending'",
        (uid,),
    ).fetchone()["c"]
    approved = db.execute(
        "SELECT COUNT(*) AS c FROM requests WHERE student_id = ? AND status = 'Approved'",
        (uid,),
    ).fetchone()["c"]
    recent = db.execute(
        "SELECT * FROM requests WHERE student_id = ? ORDER BY id DESC LIMIT 8",
        (uid,),
    ).fetchall()
    return render_template(
        "student_dashboard.html",
        available_resources=available_resources,
        pending=pending,
        approved=approved,
        recent_requests=recent,
        active_page="dashboard",
    )


@app.route("/student/request", methods=["GET", "POST"])
@login_required("student")
def request_resource():
    resources = get_student_resources()
    if request.method == "POST":
        event_title = request.form.get("event_title", "").strip()
        resource = request.form.get("resource", "").strip()
        date = request.form.get("date", "").strip()
        start_time = request.form.get("start_time", "").strip()
        end_time = request.form.get("end_time", "").strip()
        attendees = request.form.get("attendees", "").strip()
        reason = request.form.get("reason", "").strip()
        rec_file = request.files.get("recommendation_file")

        ctx = {"resources": resources, "active_page": "request"}

        if not reason:
            flash("Purpose/Reason is required.", "danger")
            return render_template("request_resource.html", **ctx)

        if not all([event_title, resource, date, start_time, end_time, attendees]):
            flash("Please fill in all fields.", "danger")
            return render_template("request_resource.html", **ctx)

        if is_lab_resource(resource) or resource not in STUDENT_RESOURCES:
            flash("Students are not allowed to request lab resources.", "danger")
            return render_template("request_resource.html", **ctx)

        rec_path = save_recommendation_file(rec_file)
        if not rec_path:
            flash("Upload faculty recommendation or signed application (PDF/DOC/image).", "danger")
            return render_template("request_resource.html", **ctx)

        err = validate_booking_form(date, start_time, end_time)
        if err:
            flash(err, "danger")
            return render_template("request_resource.html", **ctx)

        try:
            attendees_count = int(attendees)
            if attendees_count < 1:
                raise ValueError
        except ValueError:
            flash("Enter valid expected participants.", "danger")
            return render_template("request_resource.html", **ctx)

        if has_booking_conflict(resource, date, start_time, end_time):
            flash("Resource already booked for selected slot.", "danger")
            return render_template("request_resource.html", **ctx)

        db = get_db()
        db.execute(
            """
            INSERT INTO requests
            (student_id, event_title, resource, date, start_time, end_time,
             attendees, reason, status, requested_on, recommendation_file)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?)
            """,
            (
                session["user_id"], event_title, resource, date,
                start_time, end_time, attendees_count, reason,
                datetime.now().strftime("%d-%m-%Y %H:%M"), rec_path,
            ),
        )
        db.commit()
        flash("Request submitted. Wait for admin approval.", "success")
        return redirect(url_for("my_requests"))

    return render_template("request_resource.html", resources=resources, active_page="request")


@app.route("/student/my-requests")
@login_required("student")
def my_requests():
    db = get_db()
    status_filter = request.args.get("status", "").strip()
    search = request.args.get("search", "").strip().lower()
    query = "SELECT * FROM requests WHERE student_id = ?"
    params = [session["user_id"]]
    if status_filter in ("Pending", "Approved", "Rejected"):
        query += " AND status = ?"
        params.append(status_filter)
    query += " ORDER BY id DESC"
    rows = db.execute(query, params).fetchall()
    if search:
        rows = [
            r for r in rows
            if search in r["event_title"].lower()
            or search in r["resource"].lower()
            or search in (r["date"] or "").lower()
        ]
    return render_template(
        "my_requests.html",
        requests=rows,
        status_filter=status_filter,
        search=search,
        active_page="my_requests",
    )


@app.route("/student/availability")
@login_required("student")
def student_availability():
    resource_filter = request.args.get("resource", "").strip() or None
    date_filter = request.args.get("date", "").strip() or None
    rows = get_dynamic_availability(resource_filter, date_filter)
    return render_template(
        "student_availability.html",
        availability_rows=rows,
        resources=get_resource_names(),
        resource_filter=resource_filter or "",
        date_filter=date_filter or "",
        active_page="availability",
    )


@app.route("/student/notices")
@login_required("student")
def student_notices():
    return render_template(
        "student_notices.html",
        notices=get_notices(),
        active_page="notices",
    )


@app.route("/student/feedback", methods=["GET", "POST"])
@login_required("student")
def student_feedback():
    if request.method == "POST":
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()
        if not subject or not message:
            flash("Subject and message are required.", "danger")
            return render_template("student_feedback.html", active_page="feedback")
        db = get_db()
        db.execute(
            """
            INSERT INTO feedback (name, role, user_id, subject, message, submitted_on)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session.get("user_name", ""),
                "Student",
                session["user_id"],
                subject,
                message,
                datetime.now().strftime("%d-%m-%Y %H:%M"),
            ),
        )
        db.commit()
        flash("Feedback submitted. Thank you.", "success")
        return redirect(url_for("student_feedback"))
    return render_template("student_feedback.html", active_page="feedback")


# ---------------------------------------------------------------------------
# Teacher
# ---------------------------------------------------------------------------
@app.route("/teacher/dashboard")
@login_required("teacher")
def teacher_dashboard():
    uid = session["user_id"]
    today_bookings = get_today_schedule(uid)
    return render_template(
        "teacher_dashboard.html",
        today_count=len(today_bookings),
        today_bookings=today_bookings,
        active_page="dashboard",
    )


@app.route("/teacher/direct-booking", methods=["GET", "POST"])
@login_required("teacher")
def teacher_direct_booking():
    resources = get_resource_names()
    if request.method == "POST":
        resource = request.form.get("resource", "").strip()
        date = request.form.get("date", "").strip()
        start_time = request.form.get("start_time", "").strip()
        end_time = request.form.get("end_time", "").strip()
        ctx = {
            "resources": resources,
            "bookings": get_all_bookings(),
            "active_page": "booking",
        }
        if not all([resource, date, start_time, end_time]):
            flash("Please fill in all fields.", "danger")
            return render_template("teacher_booking.html", **ctx)
        err = validate_booking_form(date, start_time, end_time)
        if err:
            flash(err, "danger")
            return render_template("teacher_booking.html", **ctx)
        if has_booking_conflict(resource, date, start_time, end_time):
            flash("Resource already booked for selected slot.", "danger")
            return render_template("teacher_booking.html", **ctx)

        db = get_db()
        reject_conflicting_pending(resource, date, start_time, end_time)
        teacher_name = session.get("user_name", "Teacher")
        db.execute(
            """
            INSERT INTO bookings
            (resource, date, start_time, end_time, booked_by, role, approved_by)
            VALUES (?, ?, ?, ?, ?, 'teacher', ?)
            """,
            (resource, date, start_time, end_time, session["user_id"], teacher_name),
        )
        db.commit()
        flash("Booking confirmed.", "success")
        return redirect(url_for("teacher_direct_booking"))

    return render_template(
        "teacher_booking.html",
        resources=resources,
        bookings=get_all_bookings()[:20],
        active_page="booking",
    )


@app.route("/teacher/schedule")
@login_required("teacher")
def teacher_schedule():
    return render_template(
        "teacher_schedule.html",
        schedule=get_today_schedule(session["user_id"]),
        today=datetime.now().strftime("%d-%m-%Y"),
        active_page="schedule",
    )


@app.route("/teacher/availability")
@login_required("teacher")
def teacher_availability():
    resource_filter = request.args.get("resource", "").strip() or None
    date_filter = request.args.get("date", "").strip() or None
    rows = get_dynamic_availability(resource_filter, date_filter)
    return render_template(
        "teacher_availability.html",
        availability_rows=rows,
        resources=get_resource_names(),
        resource_filter=resource_filter or "",
        date_filter=date_filter or "",
        active_page="availability",
    )


@app.route("/teacher/notices")
@login_required("teacher")
def teacher_notices():
    return render_template(
        "teacher_notices.html",
        notices=get_notices(),
        active_page="notices",
    )


@app.route("/teacher/feedback", methods=["GET", "POST"])
@login_required("teacher")
def teacher_feedback():
    if request.method == "POST":
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()
        if not subject or not message:
            flash("Subject and message are required.", "danger")
            return render_template("teacher_feedback.html", active_page="feedback")
        db = get_db()
        db.execute(
            """
            INSERT INTO feedback (name, role, user_id, subject, message, submitted_on)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session.get("user_name", ""),
                "Teacher",
                session["user_id"],
                subject,
                message,
                datetime.now().strftime("%d-%m-%Y %H:%M"),
            ),
        )
        db.commit()
        flash("Feedback submitted. Thank you.", "success")
        return redirect(url_for("teacher_feedback"))
    return render_template("teacher_feedback.html", active_page="feedback")


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
@app.route("/admin/dashboard")
@login_required("admin")
def admin_dashboard():
    stats = get_admin_stats()
    pending_list = get_db().execute(
        """
        SELECT r.*, s.name AS student_name FROM requests r
        JOIN students s ON r.student_id = s.id
        WHERE r.status = 'Pending' ORDER BY r.id DESC LIMIT 5
        """
    ).fetchall()
    return render_template(
        "admin_dashboard.html",
        stats=stats,
        pending_list=pending_list,
        active_page="dashboard",
    )


@app.route("/admin/requests")
@login_required("admin")
def admin_requests():
    db = get_db()
    status_filter = request.args.get("status", "").strip()
    search = request.args.get("search", "").strip().lower()
    query = (
        "SELECT r.*, s.name AS student_name FROM requests r "
        "JOIN students s ON r.student_id = s.id WHERE 1=1"
    )
    params = []
    if status_filter in ("Pending", "Approved", "Rejected"):
        query += " AND r.status = ?"
        params.append(status_filter)
    query += " ORDER BY r.id DESC"
    rows = db.execute(query, params).fetchall()
    if search:
        rows = [
            r for r in rows
            if search in r["event_title"].lower()
            or search in r["resource"].lower()
            or search in (r["student_name"] or "").lower()
        ]
    return render_template(
        "admin_requests.html",
        requests=rows,
        status_filter=status_filter,
        search=search,
        active_page="requests",
    )


@app.route("/admin/approve/<int:request_id>", methods=["POST"])
@login_required("admin")
def admin_approve(request_id):
    db = get_db()
    req = db.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
    if not req:
        flash("Request not found.", "danger")
        return redirect(url_for("admin_requests"))
    if req["status"] != "Pending":
        flash("Request already processed.", "warning")
        return redirect(url_for("admin_requests"))
    if not req["recommendation_file"]:
        flash("Cannot approve without faculty recommendation file.", "danger")
        return redirect(url_for("admin_requests"))
    if has_booking_conflict(req["resource"], req["date"], req["start_time"], req["end_time"]):
        flash("Resource already booked for selected slot.", "danger")
        return redirect(url_for("admin_requests"))

    admin_name = session.get("user_name", "Admin")
    db.execute("UPDATE requests SET status = 'Approved' WHERE id = ?", (request_id,))
    db.execute(
        """
        INSERT INTO bookings
        (resource, date, start_time, end_time, booked_by, role, approved_by)
        VALUES (?, ?, ?, ?, ?, 'student', ?)
        """,
        (
            req["resource"], req["date"], req["start_time"], req["end_time"],
            req["student_id"], admin_name,
        ),
    )
    db.commit()
    flash("Request approved.", "success")
    return redirect(url_for("admin_requests"))


@app.route("/admin/reject/<int:request_id>", methods=["POST"])
@login_required("admin")
def admin_reject(request_id):
    db = get_db()
    req = db.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
    if not req or req["status"] != "Pending":
        flash("Request not found or already processed.", "warning")
        return redirect(url_for("admin_requests"))
    db.execute(
        "UPDATE requests SET status = 'Rejected', rejection_reason = ? WHERE id = ?",
        ("Rejected by admin.", request_id),
    )
    db.commit()
    flash("Request rejected.", "info")
    return redirect(url_for("admin_requests"))


@app.route("/admin/bookings")
@login_required("admin")
def admin_bookings():
    search = request.args.get("search", "").strip().lower()
    return render_template(
        "admin_bookings.html",
        bookings=get_all_bookings(search),
        search=search,
        active_page="bookings",
    )


@app.route("/admin/resources", methods=["GET", "POST"])
@login_required("admin")
def admin_resources():
    db = get_db()
    if request.method == "POST":
        res_id = request.form.get("resource_id", "").strip()
        capacity = request.form.get("capacity", "").strip()
        try:
            cap = int(capacity)
            if cap < 1:
                raise ValueError
            db.execute("UPDATE resources SET capacity = ? WHERE id = ?", (cap, res_id))
            db.commit()
            flash("Resource updated.", "success")
        except (ValueError, TypeError):
            flash("Enter valid capacity.", "danger")
    resources = db.execute("SELECT * FROM resources ORDER BY name").fetchall()
    return render_template(
        "admin_resources.html",
        resources=resources,
        active_page="resources",
    )


@app.route("/admin/reports")
@login_required("admin")
def admin_reports():
    analytics = get_analytics()
    return render_template(
        "admin_reports.html",
        analytics=analytics,
        active_page="reports",
    )


@app.route("/admin/notices", methods=["GET", "POST"])
@login_required("admin")
def admin_notices():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "create":
            title = request.form.get("title", "").strip()
            message = request.form.get("message", "").strip()
            if title and message:
                db.execute(
                    "INSERT INTO notices (title, message, posted_by, posted_on) VALUES (?, ?, ?, ?)",
                    (
                        title,
                        message,
                        session.get("user_name", "Admin"),
                        datetime.now().strftime("%d-%m-%Y %H:%M"),
                    ),
                )
                db.commit()
                flash("Notice posted.", "success")
            else:
                flash("Title and message required.", "danger")
        elif action == "edit":
            nid = request.form.get("notice_id")
            title = request.form.get("title", "").strip()
            message = request.form.get("message", "").strip()
            if nid and title and message:
                db.execute(
                    "UPDATE notices SET title = ?, message = ? WHERE id = ?",
                    (title, message, nid),
                )
                db.commit()
                flash("Notice updated.", "success")
        elif action == "delete":
            nid = request.form.get("notice_id")
            if nid:
                db.execute("DELETE FROM notices WHERE id = ?", (nid,))
                db.commit()
                flash("Notice deleted.", "info")
        return redirect(url_for("admin_notices"))
    return render_template(
        "admin_notices.html",
        notices=get_notices(),
        active_page="notices",
    )


@app.route("/admin/feedback", methods=["GET", "POST"])
@login_required("admin")
def admin_feedback():
    if request.method == "POST":
        fid = request.form.get("feedback_id")
        if fid:
            get_db().execute("DELETE FROM feedback WHERE id = ?", (fid,))
            get_db().commit()
            flash("Feedback deleted.", "info")
        return redirect(url_for("admin_feedback"))
    return render_template(
        "admin_feedback.html",
        feedback_list=get_feedback_list(),
        active_page="feedback",
    )


@app.route("/admin/availability")
@login_required("admin")
def admin_availability():
    resource_filter = request.args.get("resource", "").strip() or None
    date_filter = request.args.get("date", "").strip() or None
    rows = get_dynamic_availability(resource_filter, date_filter)
    return render_template(
        "admin_availability.html",
        availability_rows=rows,
        resources=get_resource_names(),
        resource_filter=resource_filter or "",
        date_filter=date_filter or "",
        active_page="availability",
    )


# Legacy redirects
@app.route("/faculty/<path:path>")
def faculty_redirect(path):
    return redirect(url_for("teacher_dashboard"))


with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
