import uuid
import time
import threading
import sqlite3
import requests
from datetime import datetime
from flask import Flask, request, jsonify, render_template, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "secret-key-change-this"

DB = "data.db"

# ---------------- DB ----------------
def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id TEXT,
        user_id INTEGER,
        app_token TEXT,
        event_token TEXT,
        device_id TEXT,
        run_at REAL,
        status TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS logs (
        user_id INTEGER,
        message TEXT,
        time TEXT
    )""")

    conn.commit()
    conn.close()

init_db()

# ---------------- LOGGING ----------------
def add_log(user_id, msg):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("INSERT INTO logs VALUES (?, ?, ?)",
              (user_id, msg, datetime.now().strftime("%H:%M:%S")))
    conn.commit()
    conn.close()

# ---------------- SEND EVENT ----------------
def send_event(app_token, event_token, device_id):
    url = "https://app.adjust.com/event"

    data = {
        "app_token": app_token,
        "event_token": event_token,
        "environment": "production"
    }

    data["gps_adid"] = device_id

    try:
        r = requests.post(url, data=data)

        txt = r.text

        if "Invalid app token" in txt:
            return {"error": "Event request failed (Invalid app token)"}

        if "Invalid event token" in txt:
            return {"error": "Event request failed (Invalid event token)"}

        if "Device not found" in txt:
            return {"error": "Event request failed (Device not found)"}

        if "tracked" in txt:
            return {"error": "Event request failed (Ignoring event, earlier unique event tracked)"}

        return {"success": txt}

    except Exception as e:
        return {"error": str(e)}

# ---------------- BACKGROUND WORKER ----------------
def worker():
    while True:
        conn = sqlite3.connect(DB)
        c = conn.cursor()

        now = time.time()

        c.execute("SELECT * FROM jobs WHERE status='pending' AND run_at <= ?", (now,))
        jobs = c.fetchall()

        for job in jobs:
            job_id, user_id, app_token, event_token, device_id, run_at, status = job

            res = send_event(app_token, event_token, device_id)

            add_log(user_id, str(res))

            c.execute("UPDATE jobs SET status='done' WHERE id=?", (job_id,))
            conn.commit()

        conn.close()
        time.sleep(2)

threading.Thread(target=worker, daemon=True).start()

# ---------------- AUTH ----------------
@app.route("/register", methods=["POST"])
def register():
    data = request.json
    username = data["username"]
    password = generate_password_hash(data["password"])

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    try:
        c.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
        conn.commit()
        return jsonify({"success": True})
    except:
        return jsonify({"error": "User exists"})

@app.route("/login", methods=["POST"])
def login():
    data = request.json
    username = data["username"]
    password = data["password"]

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("SELECT id, password FROM users WHERE username=?", (username,))
    user = c.fetchone()

    if user and check_password_hash(user[1], password):
        session["user_id"] = user[0]
        return jsonify({"success": True})

    return jsonify({"error": "Invalid login"})

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ---------------- JOBS ----------------
@app.route("/schedule", methods=["POST"])
def schedule():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"})

    data = request.json

    delay = int(data["seconds"])
    run_at = time.time() + delay

    job_id = str(uuid.uuid4())

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
              (job_id, session["user_id"], data["app_token"],
               data["event_token"], data["device_id"], run_at, "pending"))

    conn.commit()
    conn.close()

    return jsonify({"success": True})

@app.route("/credit-now", methods=["POST"])
def credit_now():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"})

    data = request.json

    res = send_event(data["app_token"], data["event_token"], data["device_id"])
    add_log(session["user_id"], str(res))

    return jsonify(res)

@app.route("/jobs")
def jobs():
    if "user_id" not in session:
        return jsonify([])

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("SELECT id, run_at, status FROM jobs WHERE user_id=?", (session["user_id"],))
    rows = c.fetchall()

    conn.close()

    return jsonify(rows)

@app.route("/cancel", methods=["POST"])
def cancel():
    data = request.json

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("DELETE FROM jobs WHERE id=?", (data["id"],))
    conn.commit()
    conn.close()

    return jsonify({"success": True})

@app.route("/logs")
def logs():
    if "user_id" not in session:
        return jsonify([])

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("SELECT message, time FROM logs WHERE user_id=? ORDER BY ROWID DESC LIMIT 50",
              (session["user_id"],))
    rows = c.fetchall()

    conn.close()

    return jsonify(rows)

# ---------------- UI ----------------
@app.route("/")
def home():
    return render_template("index.html")

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run()
