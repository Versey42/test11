import uuid
import threading
import time
import json
import os
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, redirect, session
import requests

app = Flask(__name__)
app.secret_key = "supersecretkey123"

PASSWORD = "Fano"

DATA_FILE = "jobs.json"

jobs = {}
job_id_counter = 0
lock = threading.Lock()


# ---------------- SAVE / LOAD ----------------

def save_jobs():
    data = {}
    for jid, j in jobs.items():
        data[jid] = {
            **j,
            "target": j["target"].isoformat()
        }

    with open(DATA_FILE, "w") as f:
        json.dump(data, f)


def load_jobs():
    global jobs, job_id_counter

    if not os.path.exists(DATA_FILE):
        return

    with open(DATA_FILE, "r") as f:
        data = json.load(f)

    for jid, j in data.items():
        jid = int(jid)

        j["target"] = datetime.fromisoformat(j["target"])
        jobs[jid] = j

        if jid > job_id_counter:
            job_id_counter = jid

        # restart unfinished jobs
        if not j["done"] and not j["cancelled"]:
            threading.Thread(target=run_job, args=(jid,), daemon=True).start()


# ---------------- AUTH ----------------

def login_required(func):
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect("/login")
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == PASSWORD:
            session["logged_in"] = True
            return redirect("/")
        return render_template("login.html", error="Wrong password")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ---------------- CORE ----------------

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
            save_jobs()
            return

        if datetime.now() >= job["target"]:
            break

        time.sleep(1)

    if job["cancelled"] or job["executed"]:
        return

    with lock:
        if job["executed"]:
            return
        job["executed"] = True

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


# ---------------- ROUTES ----------------

@app.route("/")
@login_required
def home():
    return render_template("index.html")


@app.route("/credit-now", methods=["POST"])
@login_required
def credit_now():
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
@login_required
def schedule():
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
            "target": target,
            "app_token": data["app_token"],
            "event_token": data["event_token"],
            "device_id": data["device_id"],
            "is_ios": data["is_ios"],
            "use_s2s": data["use_s2s"],
            "cancelled": False,
            "done": False,
            "executed": False,
            "result": None
        }

    save_jobs()

    threading.Thread(target=run_job, args=(jid,), daemon=True).start()

    return jsonify({"ok": True})


@app.route("/jobs")
@login_required
def get_jobs():
    output = []

    for jid, j in list(jobs.items()):
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
@login_required
def cancel(jid):
    if jid in jobs:
        jobs[jid]["cancelled"] = True
        save_jobs()
        return jsonify({"ok": True})
    return jsonify({"error": "not found"}), 404


# 🔥 LOAD SAVED JOBS ON START
load_jobs()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
