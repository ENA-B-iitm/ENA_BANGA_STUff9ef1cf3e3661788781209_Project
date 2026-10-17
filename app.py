import csv
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from functools import wraps

from celery import Celery
from flask import Flask, request, jsonify, session, g, send_file, render_template
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import redis
except Exception:
    redis = None


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "tma.sqlite3")
EXPORT_DIR = os.path.join(BASE_DIR, "exports")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config.update(
    CELERY_BROKER_URL=REDIS_URL,
    CELERY_RESULT_BACKEND=REDIS_URL,
)


def make_celery(flask_app):
    celery_app = Celery(
        flask_app.import_name,
        broker=flask_app.config["CELERY_BROKER_URL"],
        backend=flask_app.config["CELERY_RESULT_BACKEND"],
    )
    celery_app.conf.update(
        timezone="Asia/Kolkata",
        beat_schedule={
            "daily-trek-reminders": {
                "task": "app.daily_reminders",
                "schedule": 24 * 60 * 60,
            },
            "monthly-admin-report": {
                "task": "app.monthly_admin_report",
                "schedule": 30 * 24 * 60 * 60,
            },
        },
    )

    class ContextTask(celery_app.Task):
        def __call__(self, *args, **kwargs):
            with flask_app.app_context():
                return self.run(*args, **kwargs)

    celery_app.Task = ContextTask
    return celery_app


celery = make_celery(app)


class LocalCache:
    def __init__(self):
        self.store = {}

    def get(self, key):
        item = self.store.get(key)
        if not item:
            return None
        expires_at, value = item
        if expires_at < time.time():
            self.store.pop(key, None)
            return None
        return value

    def setex(self, key, seconds, value):
        self.store[key] = (time.time() + seconds, value)

    def delete(self, key):
        self.store.pop(key, None)


def get_cache():
    if redis is None:
        return LocalCache()
    try:
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        client.ping()
        return client
    except Exception:
        return LocalCache()


cache = get_cache()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def rows_to_dicts(rows):
    return [dict(row) for row in rows]


def invalidate_trek_cache():
    cache.delete("open_treks")


def init_db():
    os.makedirs(EXPORT_DIR, exist_ok=True)
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'staff', 'user')),
                contact TEXT DEFAULT '',
                gender TEXT NOT NULL DEFAULT 'unspecified' CHECK(gender IN ('female', 'male', 'other', 'unspecified')),
                status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'inactive', 'blacklisted')),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS treks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                location TEXT NOT NULL,
                difficulty TEXT NOT NULL CHECK(difficulty IN ('Easy', 'Moderate', 'Hard')),
                duration_days INTEGER NOT NULL CHECK(duration_days > 0),
                available_slots INTEGER NOT NULL CHECK(available_slots >= 0),
                assigned_staff_id INTEGER,
                status TEXT NOT NULL CHECK(status IN ('Pending', 'Approved', 'Open', 'Closed', 'Completed')),
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (assigned_staff_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS trek_staff (
                trek_id INTEGER NOT NULL,
                staff_id INTEGER NOT NULL,
                PRIMARY KEY (trek_id, staff_id),
                FOREIGN KEY (trek_id) REFERENCES treks(id) ON DELETE CASCADE,
                FOREIGN KEY (staff_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                trek_id INTEGER NOT NULL,
                booking_date TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('Booked', 'Cancelled', 'Completed')),
                payment_status TEXT DEFAULT 'Pending',
                UNIQUE(user_id, trek_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (trek_id) REFERENCES treks(id)
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                read_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            """
        )
        admin = conn.execute("SELECT id FROM users WHERE role = 'admin'").fetchone()
        if not admin:
            conn.execute(
                """
                INSERT INTO users (name, email, password_hash, role, contact, gender, created_at)
                VALUES (?, ?, ?, 'admin', ?, 'unspecified', ?)
                """,
                (
                    "System Admin",
                    "admin@treck.com",
                    generate_password_hash("admin123"),
                    "9999999999",
                    datetime.utcnow().isoformat(),
                ),
            )
        if conn.execute("SELECT COUNT(*) AS count FROM treks").fetchone()["count"] == 0:
            staff_id = conn.execute(
                """
                INSERT INTO users (name, email, password_hash, role, contact, gender, created_at)
                VALUES (?, ?, ?, 'staff', ?, 'female', ?)
                """,
                (
                    "Anita Guide",
                    "staff@tma.local",
                    generate_password_hash("staff123"),
                    "8888888888",
                    datetime.utcnow().isoformat(),
                ),
            ).lastrowid
            today = datetime.utcnow().date()
            sample_treks = [
                ("Valley Ridge Trail", "Himachal Pradesh", "Moderate", 4, 18, staff_id, "Open", today + timedelta(days=10), today + timedelta(days=14)),
                ("Forest Creek Walk", "Kerala", "Easy", 2, 24, staff_id, "Approved", today + timedelta(days=18), today + timedelta(days=20)),
                ("Summit Pass Expedition", "Uttarakhand", "Hard", 7, 10, staff_id, "Open", today + timedelta(days=30), today + timedelta(days=37)),
            ]
            for trek in sample_treks:
                conn.execute(
                    """
                    INSERT INTO treks
                    (name, location, difficulty, duration_days, available_slots, assigned_staff_id, status, start_date, end_date, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*trek[:7], trek[7].isoformat(), trek[8].isoformat(), datetime.utcnow().isoformat()),
                )


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(user) if user else None


def public_user(user):
    if not user:
        return None
    clean = dict(user)
    clean.pop("password_hash", None)
    return clean


def require_login(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify({"error": "Login required"}), 401
        if user["status"] != "active":
            session.clear()
            return jsonify({"error": "Account is not active"}), 403
        return fn(user, *args, **kwargs)

    return wrapper


def require_role(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(user, *args, **kwargs):
            if user["role"] not in roles:
                return jsonify({"error": "Permission denied"}), 403
            return fn(user, *args, **kwargs)

        return wrapper

    return decorator


@app.route("/")
def index():
    return render_template("index.html")


@app.post("/api/register")
def register():
    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    if not name or not email or len(password) < 6:
        return jsonify({"error": "Name, valid email, and 6 character password are required"}), 400
    try:
        with db() as conn:
            user_id = conn.execute(
                """
                INSERT INTO users (name, email, password_hash, role, contact, gender, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    email,
                    generate_password_hash(password),
                    data.get("role", "user") if data.get("role") in ("user", "staff") else "user",
                    data.get("contact", ""),
                    data.get("gender", "unspecified"),
                    datetime.utcnow().isoformat(),
                ),
            ).lastrowid
        session["user_id"] = user_id
        return jsonify({"message": "Registered", "user": public_user(current_user())})
    except sqlite3.IntegrityError:
        return jsonify({"error": "Email already exists"}), 409


@app.post("/api/login")
def login():
    data = request.get_json(force=True)
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (data.get("email", "").strip().lower(),)).fetchone()
    if not user or not check_password_hash(user["password_hash"], data.get("password", "")):
        return jsonify({"error": "Invalid email or password"}), 401
    if user["status"] != "active":
        return jsonify({"error": "Account is not active"}), 403
    session["user_id"] = user["id"]
    return jsonify({"message": "Logged in", "user": public_user(user)})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"message": "Logged out"})


@app.get("/api/me")
def me():
    return jsonify({"user": public_user(current_user())})


@app.put("/api/profile")
@require_login
def update_profile(user):
    data = request.get_json(force=True)
    name = data.get("name", user["name"]).strip()
    contact = data.get("contact", user["contact"]).strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400
    with db() as conn:
        conn.execute("UPDATE users SET name = ?, contact = ? WHERE id = ?", (name, contact, user["id"]))
    return jsonify({"message": "Profile updated", "user": public_user(current_user())})


def fetch_treks(user=None):
    params = []
    where = []
    q = request.args.get("q", "").strip()
    difficulty = request.args.get("difficulty", "").strip()
    location = request.args.get("location", "").strip()
    duration = request.args.get("duration", "").strip()
    if q:
        where.append("(t.name LIKE ? OR t.location LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if difficulty:
        where.append("t.difficulty = ?")
        params.append(difficulty)
    if location:
        where.append("t.location LIKE ?")
        params.append(f"%{location}%")
    if duration:
        where.append("t.duration_days <= ?")
        params.append(duration)
    if user and user["role"] == "user":
        where.append("t.status = 'Open'")
    sql = """
        SELECT t.*,
        (SELECT COUNT(*) FROM bookings b WHERE b.trek_id = t.id AND b.status = 'Booked') AS booked_count,
        (SELECT GROUP_CONCAT(u2.name, ', ') FROM trek_staff ts JOIN users u2 ON u2.id = ts.staff_id WHERE ts.trek_id = t.id) AS staff_names
        FROM treks t
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY t.start_date ASC"
    with db() as conn:
        return rows_to_dicts(conn.execute(sql, params).fetchall())


@app.get("/api/treks")
@require_login
def list_treks(user):
    if user["role"] == "user" and not request.query_string:
        cached = cache.get("open_treks")
        if cached:
            return jsonify({"treks": json.loads(cached), "cached": True})
        treks = fetch_treks(user)
        cache.setex("open_treks", 60, json.dumps(treks))
        return jsonify({"treks": treks, "cached": False})
    return jsonify({"treks": fetch_treks(user)})


@app.post("/api/treks")
@require_login
@require_role("admin")
def create_trek(user):
    data = request.get_json(force=True)
    required = ["name", "location", "difficulty", "duration_days", "available_slots", "status", "start_date", "end_date"]
    if any(data.get(field) in ("", None) for field in required):
        return jsonify({"error": "All trek fields are required"}), 400
    with db() as conn:
        trek_id = conn.execute(
            """
            INSERT INTO treks
            (name, location, difficulty, duration_days, available_slots, assigned_staff_id, status, start_date, end_date, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["name"].strip(),
                data["location"].strip(),
                data["difficulty"],
                int(data["duration_days"]),
                int(data["available_slots"]),
                data.get("assigned_staff_id") or None,
                data["status"],
                data["start_date"],
                data["end_date"],
                data.get("notes", ""),
                datetime.utcnow().isoformat(),
            ),
        ).lastrowid
    staff_ids = data.get("staff_ids", [])
    if staff_ids:
        with db() as conn:
            conn.execute("DELETE FROM trek_staff WHERE trek_id = ?", (trek_id,))
            for sid in staff_ids:
                conn.execute("INSERT OR IGNORE INTO trek_staff (trek_id, staff_id) VALUES (?, ?)", (trek_id, sid))
    invalidate_trek_cache()
    return jsonify({"message": "Trek created", "id": trek_id})


@app.put("/api/treks/<int:trek_id>")
@require_login
def update_trek(user, trek_id):
    data = request.get_json(force=True)
    with db() as conn:
        trek = conn.execute("SELECT * FROM treks WHERE id = ?", (trek_id,)).fetchone()
        if not trek:
            return jsonify({"error": "Trek not found"}), 404
        if user["role"] == "staff":
            is_assigned = trek["assigned_staff_id"] == user["id"]
            if not is_assigned:
                is_assigned = conn.execute(
                    "SELECT 1 FROM trek_staff WHERE trek_id = ? AND staff_id = ?",
                    (trek_id, user["id"]),
                ).fetchone()
            if not is_assigned:
                return jsonify({"error": "Only assigned staff can manage this trek"}), 403
        if user["role"] not in ("admin", "staff"):
            return jsonify({"error": "Permission denied"}), 403
        fields = {
            "name": data.get("name", trek["name"]),
            "location": data.get("location", trek["location"]),
            "difficulty": data.get("difficulty", trek["difficulty"]),
            "duration_days": int(data.get("duration_days", trek["duration_days"])),
            "available_slots": int(data.get("available_slots", trek["available_slots"])),
            "assigned_staff_id": data.get("assigned_staff_id", trek["assigned_staff_id"]) if user["role"] == "admin" else trek["assigned_staff_id"],
            "status": data.get("status", trek["status"]),
            "start_date": data.get("start_date", trek["start_date"]),
            "end_date": data.get("end_date", trek["end_date"]),
            "notes": data.get("notes", trek["notes"]),
        }
        conn.execute(
            """
            UPDATE treks SET name=?, location=?, difficulty=?, duration_days=?, available_slots=?,
            assigned_staff_id=?, status=?, start_date=?, end_date=?, notes=? WHERE id=?
            """,
            (*fields.values(), trek_id),
        )
    staff_ids = data.get("staff_ids", [])
    if "staff_ids" in data:
        with db() as conn:
            conn.execute("DELETE FROM trek_staff WHERE trek_id = ?", (trek_id,))
            for sid in staff_ids:
                conn.execute("INSERT OR IGNORE INTO trek_staff (trek_id, staff_id) VALUES (?, ?)", (trek_id, sid))
    invalidate_trek_cache()
    return jsonify({"message": "Trek updated"})


@app.delete("/api/treks/<int:trek_id>")
@require_login
@require_role("admin")
def delete_trek(user, trek_id):
    with db() as conn:
        conn.execute("DELETE FROM bookings WHERE trek_id = ?", (trek_id,))
        conn.execute("DELETE FROM treks WHERE id = ?", (trek_id,))
    invalidate_trek_cache()
    return jsonify({"message": "Trek removed"})


@app.post("/api/staff")
@require_login
@require_role("admin")
def create_staff(user):
    data = request.get_json(force=True)
    if not data.get("name") or not data.get("email") or len(data.get("password", "")) < 6:
        return jsonify({"error": "Name, email and 6 character password are required"}), 400
    try:
        with db() as conn:
            staff_id = conn.execute(
                """
                INSERT INTO users (name, email, password_hash, role, contact, gender, created_at)
                VALUES (?, ?, ?, 'staff', ?, ?, ?)
                """,
                (
                    data["name"].strip(),
                    data["email"].strip().lower(),
                    generate_password_hash(data["password"]),
                    data.get("contact", ""),
                    data.get("gender", "unspecified"),
                    datetime.utcnow().isoformat(),
                ),
            ).lastrowid
        return jsonify({"message": "Staff added", "id": staff_id})
    except sqlite3.IntegrityError:
        return jsonify({"error": "Email already exists"}), 409


@app.get("/api/users")
@require_login
@require_role("admin")
def list_users(user):
    q = request.args.get("q", "").strip()
    role = request.args.get("role", "").strip()
    params = []
    where = []
    if q:
        where.append("(name LIKE ? OR email LIKE ? OR contact LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if role:
        where.append("role = ?")
        params.append(role)
    sql = "SELECT id, name, email, role, contact, gender, status, created_at FROM users"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY role, name"
    with db() as conn:
        return jsonify({"users": rows_to_dicts(conn.execute(sql, params).fetchall())})


@app.put("/api/users/<int:user_id>/status")
@require_login
@require_role("admin")
def update_user_status(user, user_id):
    data = request.get_json(force=True)
    status = data.get("status")
    if status not in ("active", "inactive", "blacklisted"):
        return jsonify({"error": "Invalid status"}), 400
    if user_id == user["id"]:
        return jsonify({"error": "Admin cannot deactivate own account"}), 400
    with db() as conn:
        conn.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))
    return jsonify({"message": "Status updated"})


@app.post("/api/bookings")
@require_login
@require_role("user")
def book_trek(user):
    data = request.get_json(force=True)
    trek_id = int(data.get("trek_id", 0))
    with db() as conn:
        trek = conn.execute("SELECT * FROM treks WHERE id = ?", (trek_id,)).fetchone()
        if not trek:
            return jsonify({"error": "Trek not found"}), 404
        active_bookings = conn.execute(
            "SELECT COUNT(*) AS count FROM bookings WHERE trek_id = ? AND status = 'Booked'", (trek_id,)
        ).fetchone()["count"]
        if trek["status"] != "Open":
            return jsonify({"error": "Only open treks can be booked"}), 400
        if active_bookings >= trek["available_slots"]:
            return jsonify({"error": "No slots available"}), 400
        if user["gender"] == "female" and not data.get("confirm_male_staff"):
            female_staff = conn.execute(
                """
                SELECT u.id FROM trek_staff ts
                JOIN users u ON u.id = ts.staff_id
                WHERE ts.trek_id = ? AND u.gender = 'female'
                """,
                (trek_id,),
            ).fetchone()
            if not female_staff:
                return jsonify({"warning": "no_female_staff"}), 200
        try:
            booking_id = conn.execute(
                """
                INSERT INTO bookings (user_id, trek_id, booking_date, status, payment_status)
                VALUES (?, ?, ?, 'Booked', 'Paid')
                """,
                (user["id"], trek_id, datetime.utcnow().isoformat()),
            ).lastrowid
            conn.execute(
                "INSERT INTO notifications (user_id, message, created_at) VALUES (?, ?, ?)",
                (user["id"], f"Booking confirmed for {trek['name']}", datetime.utcnow().isoformat()),
            )
        except sqlite3.IntegrityError:
            return jsonify({"error": "You already have a booking for this trek"}), 409
    return jsonify({"message": "Trek booked", "id": booking_id})


@app.get("/api/bookings")
@require_login
def list_bookings(user):
    params = []
    where = []
    if user["role"] == "user":
        where.append("b.user_id = ?")
        params.append(user["id"])
    elif user["role"] == "staff":
        where.append("t.assigned_staff_id = ?")
        params.append(user["id"])
    sql = """
        SELECT b.*, t.name AS trek_name, t.location, t.start_date, t.end_date, u.name AS user_name, u.email AS user_email
        FROM bookings b
        JOIN treks t ON t.id = b.trek_id
        JOIN users u ON u.id = b.user_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY b.booking_date DESC"
    with db() as conn:
        return jsonify({"bookings": rows_to_dicts(conn.execute(sql, params).fetchall())})


@app.put("/api/bookings/<int:booking_id>")
@require_login
def update_booking(user, booking_id):
    data = request.get_json(force=True)
    status = data.get("status")
    if status not in ("Booked", "Cancelled", "Completed"):
        return jsonify({"error": "Invalid booking status"}), 400
    with db() as conn:
        booking = conn.execute(
            """
            SELECT b.*, t.assigned_staff_id FROM bookings b
            JOIN treks t ON t.id = b.trek_id WHERE b.id = ?
            """,
            (booking_id,),
        ).fetchone()
        if not booking:
            return jsonify({"error": "Booking not found"}), 404
        allowed = user["role"] == "admin" or booking["user_id"] == user["id"] or booking["assigned_staff_id"] == user["id"]
        if not allowed:
            return jsonify({"error": "Permission denied"}), 403
        conn.execute("UPDATE bookings SET status = ? WHERE id = ?", (status, booking_id))
    return jsonify({"message": "Booking updated"})


@app.get("/api/dashboard")
@require_login
def dashboard(user):
    with db() as conn:
        if user["role"] == "admin":
            stats = {
                "treks": conn.execute("SELECT COUNT(*) AS c FROM treks").fetchone()["c"],
                "users": conn.execute("SELECT COUNT(*) AS c FROM users WHERE role = 'user'").fetchone()["c"],
                "staff": conn.execute("SELECT COUNT(*) AS c FROM users WHERE role = 'staff'").fetchone()["c"],
                "bookings": conn.execute("SELECT COUNT(*) AS c FROM bookings").fetchone()["c"],
            }
            popular = rows_to_dicts(conn.execute(
                """
                SELECT t.name, COUNT(b.id) AS bookings FROM treks t
                LEFT JOIN bookings b ON b.trek_id = t.id
                GROUP BY t.id ORDER BY bookings DESC LIMIT 5
                """
            ).fetchall())
            month = datetime.utcnow().strftime("%B %Y")
            monthly = {
                "month": month,
                "treks_conducted": conn.execute("SELECT COUNT(*) AS c FROM treks WHERE status = 'Completed'").fetchone()["c"],
                "participants": conn.execute("SELECT COUNT(*) AS c FROM bookings WHERE status = 'Completed'").fetchone()["c"],
                "popular": rows_to_dicts(conn.execute(
                    """
                    SELECT t.name, COUNT(b.id) AS bookings FROM treks t
                    LEFT JOIN bookings b ON b.trek_id = t.id
                    GROUP BY t.id ORDER BY bookings DESC LIMIT 3
                    """
                ).fetchall()),
            }
            return jsonify({"stats": stats, "popular": popular, "monthly": monthly})
        if user["role"] == "staff":
            assigned = rows_to_dicts(conn.execute(
                """
                SELECT DISTINCT t.*, COUNT(b.id) AS registered_users FROM treks t
                LEFT JOIN bookings b ON b.trek_id = t.id AND b.status = 'Booked'
                LEFT JOIN trek_staff ts ON ts.trek_id = t.id
                WHERE t.assigned_staff_id = ? OR ts.staff_id = ?
                GROUP BY t.id ORDER BY t.start_date
                """,
                (user["id"], user["id"]),
            ).fetchall())
            return jsonify({"assigned": assigned})
        available = rows_to_dicts(conn.execute("SELECT * FROM treks WHERE status = 'Open' ORDER BY start_date").fetchall())
        booked = rows_to_dicts(conn.execute(
            """
            SELECT b.*, t.name AS trek_name, t.status AS trek_status, t.start_date, t.end_date
            FROM bookings b JOIN treks t ON t.id = b.trek_id
            WHERE b.user_id = ? ORDER BY t.start_date DESC
            """,
            (user["id"],),
        ).fetchall())
        return jsonify({"available": available, "booked": booked})


@app.get("/api/notifications")
@require_login
def get_notifications(user):
    with db() as conn:
        rows = rows_to_dicts(conn.execute(
            """
            SELECT * FROM notifications WHERE user_id = ?
            ORDER BY created_at DESC LIMIT 10
            """,
            (user["id"],),
        ).fetchall())
    return jsonify({"notifications": rows})


@app.post("/api/notifications/read")
@require_login
def mark_notifications_read(user):
    with db() as conn:
        conn.execute(
            "UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL",
            (datetime.utcnow().isoformat(), user["id"]),
        )
    return jsonify({"message": "Marked as read"})


@app.get("/api/exports/bookings/download")
@require_login
@require_role("user")
def export_bookings_download(user):
    path = build_booking_csv(user["id"])
    return send_file(path, as_attachment=True, download_name=os.path.basename(path), mimetype="text/csv")


@app.post("/api/exports/bookings")
@require_login
@require_role("user")
def export_bookings(user):
    if isinstance(cache, LocalCache):
        path = build_booking_csv(user["id"])
        return jsonify({"message": "Export ready", "path": path, "fallback": True})
    try:
        task = export_booking_history.delay(user["id"])
        return jsonify({"message": "Export started", "task_id": task.id})
    except Exception:
        path = build_booking_csv(user["id"])
        return jsonify({"message": "Export ready", "path": path, "fallback": True})


def build_booking_csv(user_id):
    file_name = f"booking_history_user_{user_id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.csv"
    path = os.path.join(EXPORT_DIR, file_name)
    with db() as conn:
        bookings = conn.execute(
            """
            SELECT b.user_id, t.name AS trek_name, t.location, b.status, t.start_date, t.end_date
            FROM bookings b JOIN treks t ON t.id = b.trek_id
            WHERE b.user_id = ? ORDER BY t.start_date DESC
            """,
            (user_id,),
        ).fetchall()
    with open(path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["User ID", "Trek Name", "Location", "Booking Status", "Start Date", "End Date"])
        for row in bookings:
            writer.writerow([row["user_id"], row["trek_name"], row["location"], row["status"], row["start_date"], row["end_date"]])
    with db() as conn:
        conn.execute(
            "INSERT INTO notifications (user_id, message, created_at) VALUES (?, ?, ?)",
            (user_id, f"Booking history export ready: {file_name}", datetime.utcnow().isoformat()),
        )
    return path


@celery.task(name="app.export_booking_history")
def export_booking_history(user_id):
    return build_booking_csv(user_id)


@celery.task(name="app.daily_reminders")
def daily_reminders():
    upcoming = datetime.utcnow().date() + timedelta(days=3)
    with db() as conn:
        rows = conn.execute(
            """
            SELECT b.user_id, u.name AS user_name, t.name AS trek_name, t.start_date, t.location
            FROM bookings b
            JOIN treks t ON t.id = b.trek_id
            JOIN users u ON u.id = b.user_id
            WHERE b.status = 'Booked' AND t.start_date <= ?
            """,
            (upcoming.isoformat(),),
        ).fetchall()
        for row in rows:
            conn.execute(
                "INSERT INTO notifications (user_id, message, created_at) VALUES (?, ?, ?)",
                (
                    row["user_id"],
                    f"Reminder: Your trek '{row['trek_name']}' at {row['location']} starts on {row['start_date']}. Please be prepared!",
                    datetime.utcnow().isoformat(),
                ),
            )
    return {"reminders": len(rows)}


@celery.task(name="app.monthly_admin_report")
def monthly_admin_report():
    with db() as conn:
        admin = conn.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        treks = conn.execute("SELECT COUNT(*) AS c FROM treks WHERE status = 'Completed'").fetchone()["c"]
        participants = conn.execute("SELECT COUNT(*) AS c FROM bookings WHERE status = 'Completed'").fetchone()["c"]
        popular = rows_to_dicts(conn.execute(
            """
            SELECT t.name, COUNT(b.id) AS bookings FROM treks t
            LEFT JOIN bookings b ON b.trek_id = t.id
            GROUP BY t.id ORDER BY bookings DESC LIMIT 3
            """
        ).fetchall())
        month = datetime.utcnow().strftime("%B %Y")
        message = (
            f"Monthly Report - {month} | "
            f"Treks conducted: {treks} | "
            f"Participants: {participants} | "
            f"Top treks: {', '.join(p['name'] + ' (' + str(p['bookings']) + ')' for p in popular)}"
        )
        if admin:
            conn.execute(
                "INSERT INTO notifications (user_id, message, created_at) VALUES (?, ?, ?)",
                (admin["id"], message, datetime.utcnow().isoformat()),
            )
    return {"treks": treks, "participants": participants}


if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=3101)