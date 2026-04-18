import uuid
import sqlite3
import threading
import time
from datetime import datetime

from flask import Flask, request, jsonify, session, redirect, render_template
from werkzeug.security import generate_password_hash, check_password_hash
import requests

app = Flask(__name__)
app.secret_key = "secret-key-change-this"

DB = "jobs.db"


# ---------------- DB ----------------
def db():
    return sqlite3.connect(DB, check_same_thread=False)


def init_db():
    conn = db()
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE,
        password TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS jobs(
        id TEXT PRIMARY KEY,
        user TEXT,
        app_token TEXT,
        event_token TEXT,
        device_id TEXT,
        is_ios INTEGER,
        use_s2s INTEGER,
        run_at REAL
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user TEXT,
        message TEXT,
        time TEXT
    )""")

    conn.commit()
    conn.close()


init_db()


# ---------------- CORE EVENT ----------------
def send_event(app_token, event_token, device_id, is_ios, use_s2s):
    url = "https://app.adjust.com/event"

    data = {
        "app_token": app_token,
        "event_token": event_token,
        "environment": "production"
    }

    if is_ios:
        data["idfa"] = device_id
    else:
        data["gps_adid"] = device_id
        data["android_uuid"] = str(uuid.uuid4())

    if use_s2s:
        data["s2s"] = "1"

    try:
        r = requests.post(url, data=data)
        return r.text
    except Exception as e:
        return str(e)


def add_log(user, msg):
    conn = db()
    c = conn.cursor()
    c.execute("INSERT INTO logs(user,message,time) VALUES (?,?,?)",
              (user, msg, datetime.now().strftime("%H:%M:%S")))
    conn.commit()
    conn.close()


# ---------------- BACKGROUND WORKER ----------------
def worker():
    while True:
        conn = db()
        c = conn.cursor()

        now = time.time()

        c.execute("SELECT * FROM jobs WHERE run_at <= ?", (now,))
        jobs = c.fetchall()

        for j in jobs:
            job_id, user, app_token, event_token, device_id, is_ios, use_s2s, run_at = j

            result = send_event(app_token, event_token, device_id, is_ios, use_s2s)

            add_log(user, result)

            c.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            conn.commit()

        conn.close()
        time.sleep(1)


# 🔥 IMPORTANT FIX (prevents multiple workers on Render)
if not hasattr(app, "worker_started"):
    threading.Thread(target=worker, daemon=True).start()
    app.worker_started = True


# ---------------- AUTH ----------------
@app.route("/login", methods=["POST"])
def login():
    data = request.json
    conn = db()
    c = conn.cursor()

    c.execute("SELECT * FROM users WHERE username=?", (data["username"],))
    user = c.fetchone()

    if user and check_password_hash(user[2], data["password"]):
        session["user"] = data["username"]
        return jsonify(success=True)

    return jsonify(success=False)


@app.route("/register", methods=["POST"])
def register():
    data = request.json
    conn = db()
    c = conn.cursor()

    try:
        c.execute("INSERT INTO users(username,password) VALUES (?,?)",
                  (data["username"], generate_password_hash(data["password"])))
        conn.commit()
    except:
        return jsonify(success=False)

    return jsonify(success=True)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# ---------------- ROUTES ----------------
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/credit-now", methods=["POST"])
def credit_now():
    user = session.get("user")
    if not user:
        return jsonify(error="not logged in"), 401

    d = request.json

    result = send_event(
        d["app_token"],
        d["event_token"],
        d["device_id"],
        d["is_ios"],
        d["use_s2s"]
    )

    add_log(user, result)

    return jsonify(result=result)


@app.route("/schedule", methods=["POST"])
def schedule():
    user = session.get("user")
    if not user:
        return jsonify(error="not logged in"), 401

    d = request.json

    seconds = int(d["seconds"])
    run_at = time.time() + seconds

    job_id = str(uuid.uuid4())

    conn = db()
    c = conn.cursor()

    c.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)",
              (job_id, user, d["app_token"], d["event_token"],
               d["device_id"], d["is_ios"], d["use_s2s"], run_at))

    conn.commit()
    conn.close()

    return jsonify(id=job_id)


@app.route("/jobs")
def jobs():
    user = session.get("user")
    if not user:
        return jsonify(error="not logged in"), 401  # 🔥 FIX

    conn = db()
    c = conn.cursor()

    c.execute("SELECT id, run_at FROM jobs WHERE user=?", (user,))
    data = [{"id": r[0], "run_at": r[1]} for r in c.fetchall()]

    return jsonify(data)


@app.route("/cancel", methods=["POST"])
def cancel():
    user = session.get("user")
    if not user:
        return jsonify(error="not logged in"), 401

    job_id = request.json["id"]

    conn = db()
    c = conn.cursor()

    c.execute("DELETE FROM jobs WHERE id=? AND user=?", (job_id, user))
    conn.commit()

    return jsonify(success=True)


@app.route("/logs")
def logs():
    user = session.get("user")
    if not user:
        return jsonify(error="not logged in"), 401

    conn = db()
    c = conn.cursor()

    c.execute("SELECT message,time FROM logs WHERE user=? ORDER BY id DESC LIMIT 50", (user,))
    data = [{"msg": r[0], "time": r[1]} for r in c.fetchall()]

    return jsonify(data)


@app.route("/clear-logs", methods=["POST"])
def clear_logs():
    user = session.get("user")
    if not user:
        return jsonify(error="not logged in"), 401

    conn = db()
    c = conn.cursor()

    c.execute("DELETE FROM logs WHERE user=?", (user,))
    conn.commit()

    return jsonify(success=True)


if __name__ == "__main__":
    app.run()
