import uuid
import threading
import time
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template
import requests

app = Flask(__name__)

jobs = {}
job_id_counter = 0
lock = threading.Lock()


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

        # 🔥 TRY PARSE JSON RESPONSE
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


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/credit-now", methods=["POST"])
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
            "result": None
        }

    threading.Thread(target=run_job, args=(jid,), daemon=True).start()

    return jsonify({"ok": True})


@app.route("/jobs")
def get_jobs():
    output = []

    for jid, j in list(jobs.items()):
        if j["cancelled"]:
            del jobs[jid]
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
    if jid in jobs:
        jobs[jid]["cancelled"] = True
        return jsonify({"ok": True})
    return jsonify({"error": "not found"}), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
