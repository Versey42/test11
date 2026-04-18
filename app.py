import uuid
import threading
import time
import json
import os
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash
import requests

app = Flask(__name__)
app.secret_key = "super_secret_key_change_this"

USERS_FILE = "users.json"
JOBS_FILE = "jobs.json"

users = {}
jobs = {}
job_id_counter = 0
lock = threading.Lock()


# ---------- LOAD / SAVE ----------

def load_data():
    global users, jobs, job_id_counter

    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            users.update(json.load(f))

    if os.path.exists(JOBS_FILE):
        with open(JOBS_FILE, "r") as f:
            raw = json.load(f)
            for jid, j in raw.items():
                j["target"] = datetime.fromisoformat(j["target"])
                jobs[int(jid)] = j

            if jobs:
                job_id_counter = max(jobs.keys())


def save_users():
    with open(USERS_FILE, "w") as f:
        json.dump(users, f)


def save_jobs():
    with open(JOBS_FILE, "w") as f:
        serializable = {}
        for jid, j in jobs.items():
            temp = j.copy()
            temp["target"] = temp["target"].isoformat()
            serializable[jid] = temp
        json.dump(serializable, f)


# ---------- AUTH ----------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        data = request.form
        username = data["username"]
        password = data["password"]

        if username in users and check_password_hash(users[username], password):
            session["user"] = username
            return redirect("/")

        return "Invalid login"

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        data = request.form
        username = data["username"]
        password = data["password"]

        if username in users:
            return "User exists"

        users[username] = generate_password_hash(password)
        save_users()
        return redirect("/login")

    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


def require_login():
    return "user" in session


# ---------- CORE ----------

def send_single(app_token, event_token, device_id, is_ios, use_s2s):
    try:
        url = "https://app.adjust.com/event"

        headers = {
            "accept-encoding": "gzip",
            "client-sdk": "android4.36.0",
            "content-type": "application/x-www-form-urlencoded"
        }

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

        r = requests.post(url, data=data, headers=headers, timeout=10)

        try:
            return r.json()
        except:
            return {"raw": r.text, "status": r.status_code}

    except Exception as e:
        return {"error": str(e)}


def run_job(jid):
    job = jobs[jid]

    while True:
        if job["cancelled"]:
            return

        if datetime.now() >= job["target"]:
            break

        time.sleep(1)

    if job["cancelled"]:
        return

    result = send_single(
        job["app_token"],
        job["event_token"],
        job["device_id"],
        job["is_ios"],
        job["use_s2s"]
    )

    job["done"] = True
    job["result"] = result
    save_jobs()


# ---------- ROUTES ----------

@app.route("/")
def home():
    if not require_login():
        return redirect("/login")
    return render_template("index.html", user=session["user"])


@app.route("/credit-now", methods=["POST"])
def credit_now():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True)

    result = send_single(
        data["app_token"],
        data["event_token"],
        data["device_id"],
        data["is_ios"],
        data["use_s2s"]
    )

    return jsonify(result)


@app.route("/schedule", methods=["POST"])
def schedule():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401

    global job_id_counter
    data = request.get_json(force=True)

    seconds = (
        int(data.get("hours", 0)) * 3600 +
        int(data.get("minutes", 0)) * 60 +
        int(data.get("seconds", 0))
    )

    target = datetime.now() + timedelta(seconds=seconds)

    with lock:
        job_id_counter += 1
        jid = job_id_counter

        jobs[jid] = {
            "id": jid,
            "user": session["user"],
            "target": target,
            "app_token": data["app_token"],
            "event_token": data["event_token"],
            "device_id": data["device_id"],
            "is_ios": data["is_ios"],
            "use_s2s": data["use_s2s"],
            "cancelled": False,
            "done": False,
            "result": None
        }

        save_jobs()

    threading.Thread(target=run_job, args=(jid,), daemon=True).start()

    return jsonify({"ok": True})


@app.route("/jobs")
def get_jobs():
    if not require_login():
        return jsonify([])

    output = []

    for jid, j in list(jobs.items()):
        if j["user"] != session["user"]:
            continue

        if j["cancelled"]:
            del jobs[jid]
            save_jobs()
            continue

        remaining = int((j["target"] - datetime.now()).total_seconds())
        if remaining < 0:
            remaining = 0

        output.append({
            "id": j["id"],
            "remaining": remaining,
            "done": j["done"],
            "result": j["result"]
        })

    return jsonify(output)


@app.route("/cancel/<int:jid>", methods=["POST"])
def cancel(jid):
    if jid in jobs and jobs[jid]["user"] == session.get("user"):
        jobs[jid]["cancelled"] = True
        save_jobs()
        return jsonify({"ok": True})
    return jsonify({"error": "not found"}), 404


# ---------- RESTART JOBS ----------

def restart_jobs():
    for jid, j in jobs.items():
        if not j["done"] and not j["cancelled"]:
            threading.Thread(target=run_job, args=(jid,), daemon=True).start()


load_data()
restart_jobs()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
