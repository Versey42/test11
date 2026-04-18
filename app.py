import uuid
from flask import Flask, request, jsonify, render_template, redirect, url_for, session
import requests
from datetime import datetime

app = Flask(__name__)
app.secret_key = "super-secret-key-change-this"

PASSWORD = "FanoDaddy"

# =========================
# AUTH HELPER
# =========================
def is_logged_in():
    return session.get("auth") == True


# =========================
# LOGIN PAGE
# =========================
@app.route("/", methods=["GET", "POST"])
def login():
    try:
        # already logged in → go dashboard
        if session.get("auth"):
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            password = request.form.get("password")

            if password == PASSWORD:
                session["auth"] = True
                return redirect(url_for("dashboard"))
            else:
                return render_template("login.html", error="Wrong password")

        return render_template("login.html")

    except Exception as e:
        return f"Login error: {str(e)}"



# =========================
# DASHBOARD
# =========================
@app.route("/dashboard")
def dashboard():
    if not session.get("auth"):
        return redirect(url_for("login"))

    return render_template("index.html")


# =========================
# SEND EVENT FUNCTION
# =========================
def send_single(app_token, event_token, device_id, is_ios, use_s2s):
    url = "https://app.adjust.com/event"

    data = {
        "app_token": app_token,
        "event_token": event_token,
        "environment": "production",
        "currency": "USD",
        "revenue": "4.99"
    }

    if is_ios:
        data["idfa"] = device_id
    else:
        data["gps_adid"] = device_id
        data["android_uuid"] = str(uuid.uuid4())
        data["google_app_set_id"] = str(uuid.uuid4())

    if use_s2s:
        data["s2s"] = "1"

    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }

    response = requests.post(url, data=data, headers=headers)

    text = response.text.lower()

    # ✅ CUSTOM ERROR HANDLING (like your old script)
    if "invalid app token" in text:
        return {"error": "Event request failed (Invalid app token)"}

    if "invalid event token" in text:
        return {"error": "Event request failed (Invalid event token)"}

    if "device" in text and "not found" in text:
        return {
            "app_token": app_token,
            "adid": device_id,
            "error": "Event request failed (Device not found)"
        }

    if "already" in text or "duplicate" in text:
        return {
            "app_token": app_token,
            "adid": device_id,
            "error": "Event request failed (Ignoring event, earlier unique event tracked)"
        }

    return {
        "status": response.status_code,
        "response": response.text
    }


# =========================
# SEND NOW
# =========================
@app.route("/send-now", methods=["POST"])
def send_now():
    if not is_logged_in():
        return jsonify({"error": "unauthorized"}), 401

    data = request.json

    result = send_single(
        data.get("app_token"),
        data.get("event_token"),
        data.get("device_id"),
        data.get("is_ios"),
        data.get("use_s2s")
    )

    return jsonify(result)


# =========================
# CREDIT NOW (FIXED)
# =========================
@app.route("/credit-now", methods=["POST"])
def credit_now():
    if not is_logged_in():
        return jsonify({"error": "unauthorized"}), 401

    data = request.json

    result = send_single(
        data.get("app_token"),
        data.get("event_token"),
        data.get("device_id"),
        data.get("is_ios"),
        data.get("use_s2s")
    )

    return jsonify(result)


# =========================
# JOB STORAGE (IN MEMORY)
# =========================
jobs = {}


# =========================
# SCHEDULE JOB
# =========================
@app.route("/schedule", methods=["POST"])
def schedule():
    if not is_logged_in():
        return jsonify({"error": "unauthorized"}), 401

    data = request.json

    job_id = str(uuid.uuid4())

    run_at = datetime.utcnow().timestamp() + int(data.get("delay", 0))

    jobs[job_id] = {
        "id": job_id,
        "data": data,
        "run_at": run_at
    }

    return jsonify({"success": True, "job_id": job_id})


# =========================
# GET JOBS
# =========================
@app.route("/jobs")
def get_jobs():
    if not is_logged_in():
        return jsonify({"error": "unauthorized"}), 401

    return jsonify(list(jobs.values()))


# =========================
# CANCEL JOB
# =========================
@app.route("/cancel/<job_id>", methods=["POST"])
def cancel(job_id):
    if not is_logged_in():
        return jsonify({"error": "unauthorized"}), 401

    if job_id in jobs:
        del jobs[job_id]

    return jsonify({"success": True})


# =========================
# BACKGROUND LOOP
# =========================
import threading
import time

def job_runner():
    while True:
        try:
            now = datetime.utcnow().timestamp()

            for job_id in list(jobs.keys()):
                job = jobs[job_id]

                if now >= job["run_at"]:
                    send_single(
                        job["data"].get("app_token"),
                        job["data"].get("event_token"),
                        job["data"].get("device_id"),
                        job["data"].get("is_ios"),
                        job["data"].get("use_s2s")
                    )

                    del jobs[job_id]

            time.sleep(1)

        except Exception as e:
            print("BACKGROUND ERROR:", e)
            time.sleep(2)


# ✅ ONLY start thread in production-safe way
if not app.debug:
    threading.Thread(target=job_runner, daemon=True).start()


# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(debug=True)
