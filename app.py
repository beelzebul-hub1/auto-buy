import time
import random
import threading
import json
import os
from collections import defaultdict
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from curl_cffi import requests as cf_requests

app = Flask(__name__)

# ============================================================
# ACCOUNTS
# ============================================================

ACCOUNTS = {
    "zanx(first)":         "394368525|clKAy8oE4BegnF4sY3bXne2gBpoUgnQJF62Sr2PL",
    "zanx(second)":        "396743061|uRfycdtHuG820Eh6YndVzwNjYMz3yHefQoBe9Sr9",
    "zanx(third)":         "413653748|qxOAvUeqbuZn0EVP7iNSFU9n5q7T7jfNjonXomh3",
    "beelzebul(main)":     "411974707|EF5wZhmzNgvgN8N601gB6ptsBBEPFN3p9Hu5k7Q5",
    "beelzebul(second)":   "394369253|UWm1yFfkGTRJTAigL8GzBk2v3krGIshNPIMjSFf5",
    "beelzebul(third)":    "413648380|hK7vsoRJ00XnrXbopmS2fWq9kHfer67UoB0oiIgB",
    "solven(first)":       "396744096|Ap9ByrWmm4Wr6aybvGs14QcMSNVWJXMTeTOWACHq",
    "solven(second)":      "403537480|SEf7QhXUxFnFzTOicnHrg391p2X0UTiZ3Z3xmECK",
    "solven(third)":       "413650092|OK77BD6172XyS4LaBAOmxxPRLoEoZRE2lzx1yGzU",
    "jamesvc(first)":      "409281234|OWBv5RRFqBjDa5TsDgqdUemDH3W1x1Mrvg1E6r6m",
    "jamesvc(second)":     "409283385|3tnqdzQ3U4hNoEXrmmVFqgW9wX5QrNqxfCTbSeYU",
    "jamesvc(third)":      "413654819|qNshCQ4vwMJMfqEzzUKnGKlfG8uQXj6cJjEWfnIW",
    "davenbuster(first)":  "403538491|PSJVNZ8iCJNTQqLpGN7yOcMkDy7QaoJUCYliThUt",
    "davenbuster(second)": "403539096|uLbRWsAE3nKzlpGRWTCU71MfryoFEFjOPVRKdMtU",
    "davenbuster(third)":  "413652390|eZ1t1FJ9BzRqNTJSWI8F4R8qg5fTgv2aGU9bP13J",
}

# ============================================================
# STATE
# ============================================================

# tasks[task_id] = {
#   id, account, channel, mode, message, min_cost, max_cost,
#   reward_name (mode1), status, logs, thread, stop_event
# }
tasks = {}
tasks_lock = threading.Lock()
task_counter = 0

CHARS = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def make_transaction_id():
    t = int(time.time() * 1000)
    timestamp = ""
    for _ in range(10):
        timestamp = CHARS[t % 32] + timestamp
        t //= 32
    return timestamp + "".join(random.choices(CHARS, k=16))


def log(task_id, msg):
    ts = datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    with tasks_lock:
        if task_id in tasks:
            tasks[task_id]["logs"].append(entry)
            if len(tasks[task_id]["logs"]) > 200:
                tasks[task_id]["logs"] = tasks[task_id]["logs"][-200:]


def make_poll_session(token, channel):
    s = cf_requests.Session(impersonate="chrome120", verify=True)
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Origin": "https://kick.com",
        "Referer": f"https://kick.com/{channel}",
        "Connection": "keep-alive",
        "Keep-Alive": "timeout=30, max=1000",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    })
    return s


def make_redeem_session(token, channel):
    s = cf_requests.Session(impersonate="chrome120", verify=True)
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Origin": "https://kick.com",
        "Referer": f"https://kick.com/{channel}",
        "Connection": "keep-alive",
        "Keep-Alive": "timeout=30, max=1000",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    })
    return s


def get_rewards(session, rewards_url, task_id):
    try:
        t0 = time.perf_counter()
        r = session.get(rewards_url, timeout=0.15)
        get_ms = (time.perf_counter() - t0) * 1000
        if r.status_code != 200:
            return None, get_ms
        return r.json(), get_ms
    except Exception:
        return None, 0.0


def is_available(reward):
    return (
        reward.get("is_enabled", False)
        and not reward.get("is_paused", False)
        and reward.get("is_in_stock", True)
    )


def is_within_limit(reward, min_cost, max_cost):
    cost = reward.get("cost", 0) or reward.get("points", 0) or 0
    if min_cost and cost < min_cost:
        return False
    if max_cost and cost > max_cost:
        return False
    return True


def redeem_reward(task_id, redeem_session, redeem_url, message, get_ms, retry=False):
    attempt = 0
    while True:
        attempt += 1
        body = {"transaction_id": make_transaction_id()}
        if message:
            body["message"] = message
        try:
            t0 = time.perf_counter()
            r = redeem_session.post(redeem_url, json=body, timeout=2)
            post_ms = (time.perf_counter() - t0) * 1000

            if r.status_code in (200, 201):
                log(task_id, f"✅ REDEEMED | GET: {get_ms:.1f}ms  POST: {post_ms:.1f}ms")
                return True
            if r.status_code == 401:
                log(task_id, "❌ Session expired")
                return False
            if r.status_code == 429:
                if not retry:
                    log(task_id, f"⚠️ Rate limited")
                    return False
                retry_after = int(r.headers.get("Retry-After", 1))
                log(task_id, f"⚠️ Rate limited, waiting {retry_after}s")
                time.sleep(retry_after)
                continue
            log(task_id, f"❌ Failed: HTTP {r.status_code}")
            if not retry:
                return False
            time.sleep(1)
        except Exception as e:
            log(task_id, f"❌ Error: {e}")
            if not retry:
                return False
            time.sleep(2)


# ============================================================
# WORKER THREADS
# ============================================================

def run_mode1(task_id, token, channel, reward_name, message, min_cost, max_cost):
    rewards_url = f"https://kick.com/api/v2/channels/{channel}/rewards"
    redeem_url_template = f"https://kick.com/api/v2/channels/{channel}/rewards/{{reward_id}}/redeem"

    poll_session = make_poll_session(token, channel)
    redeem_sess = make_redeem_session(token, channel)

    log(task_id, f"👁 Watching '{reward_name}' on {channel}")

    stop_event = tasks[task_id]["stop_event"]
    reward_id = None
    last_status = None

    while not stop_event.is_set():
        try:
            rewards, get_ms = get_rewards(poll_session, rewards_url, task_id)

            if reward_id is None:
                if not rewards:
                    continue
                data = rewards.get("data", rewards)
                if isinstance(data, list):
                    for rw in data:
                        if rw.get("title", "").lower() == reward_name.lower():
                            reward_id = rw["id"]
                            log(task_id, f"Found reward ID: {reward_id}")
                            break
                if reward_id is None:
                    if last_status != "notfound":
                        log(task_id, "Reward not found — check name is correct")
                        last_status = "notfound"
                    continue
            else:
                if not rewards:
                    continue
                reward = None
                data = rewards.get("data", rewards)
                if isinstance(data, list):
                    for rw in data:
                        if rw.get("id") == reward_id:
                            reward = rw
                            break
                if reward is None:
                    continue

                available = is_available(reward)
                needs_message = reward.get("is_user_input_required", False)
                use_msg = message if needs_message else ""

                if available and last_status != "available":
                    if not is_within_limit(reward, min_cost, max_cost):
                        last_status = "skipped"
                        log(task_id, "Reward outside cost limit, skipping")
                    else:
                        log(task_id, "🎯 Reward available — redeeming!")
                        redeem_url = redeem_url_template.format(reward_id=reward_id)
                        success = redeem_reward(task_id, redeem_sess, redeem_url, use_msg, get_ms, retry=True)
                        if not success:
                            break
                        last_status = "available"
                elif not available and last_status != "unavailable":
                    log(task_id, "Reward unavailable, watching...")
                    last_status = "unavailable"

        except Exception as e:
            log(task_id, f"Error: {e}")
            time.sleep(5)

    with tasks_lock:
        if task_id in tasks:
            tasks[task_id]["status"] = "stopped"
    log(task_id, "⏹ Stopped")


def run_mode2(task_id, token, channel, message, min_cost, max_cost):
    rewards_url = f"https://kick.com/api/v2/channels/{channel}/rewards"
    redeem_url_template = f"https://kick.com/api/v2/channels/{channel}/rewards/{{reward_id}}/redeem"

    poll_session = make_poll_session(token, channel)
    stop_event = tasks[task_id]["stop_event"]

    log(task_id, f"🎯 SNIPER ACTIVE on {channel}")

    known_ids = set()
    known_available = set()

    initial, _ = get_rewards(poll_session, rewards_url, task_id)
    if initial:
        data = initial.get("data", [])
        for reward in data:
            rid = reward["id"]
            known_ids.add(rid)
            if is_available(reward):
                known_available.add(rid)
    log(task_id, f"Loaded {len(known_ids)} rewards. Watching...")

    while not stop_event.is_set():
        try:
            rewards, get_ms = get_rewards(poll_session, rewards_url, task_id)
            if not rewards:
                continue

            data = rewards.get("data", [])
            for reward in data:
                rid = reward["id"]
                available = is_available(reward)

                if rid not in known_ids:
                    known_ids.add(rid)
                    if available and is_within_limit(reward, min_cost, max_cost):
                        title = reward.get("title", "Unknown")
                        log(task_id, f"🆕 NEW: {title} — REDEEMING")
                        redeem_url = redeem_url_template.format(reward_id=rid)
                        redeem_sess = make_redeem_session(token, channel)
                        needs_message = reward.get("is_user_input_required", False)
                        use_msg = message if needs_message else ""
                        threading.Thread(
                            target=redeem_reward,
                            args=(task_id, redeem_sess, redeem_url, use_msg, get_ms),
                            daemon=True,
                        ).start()
                        known_available.add(rid)
                    continue

                if available and rid not in known_available:
                    if is_within_limit(reward, min_cost, max_cost):
                        title = reward.get("title", "Unknown")
                        log(task_id, f"✨ AVAILABLE: {title} — REDEEMING")
                        redeem_url = redeem_url_template.format(reward_id=rid)
                        redeem_sess = make_redeem_session(token, channel)
                        needs_message = reward.get("is_user_input_required", False)
                        use_msg = message if needs_message else ""
                        threading.Thread(
                            target=redeem_reward,
                            args=(task_id, redeem_sess, redeem_url, use_msg, get_ms),
                            daemon=True,
                        ).start()
                        known_available.add(rid)
                elif not available and rid in known_available:
                    known_available.discard(rid)

        except Exception as e:
            log(task_id, f"Loop error: {e}")
            continue

    with tasks_lock:
        if task_id in tasks:
            tasks[task_id]["status"] = "stopped"
    log(task_id, "⏹ Stopped")


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def index():
    return render_template("index.html", accounts=list(ACCOUNTS.keys()))


@app.route("/api/start", methods=["POST"])
def start_task():
    global task_counter
    data = request.json

    account = data.get("account")
    channel = data.get("channel", "").strip()
    mode = data.get("mode", "2")
    message = data.get("message", "").strip()
    reward_name = data.get("reward_name", "").strip()
    min_cost = int(data.get("min_cost") or 0)
    max_cost = int(data.get("max_cost") or 0)

    if not account or account not in ACCOUNTS:
        return jsonify({"error": "Invalid account"}), 400
    if not channel:
        return jsonify({"error": "Channel name required"}), 400
    if mode == "1" and not reward_name:
        return jsonify({"error": "Reward name required for mode 1"}), 400

    token = ACCOUNTS[account]

    with tasks_lock:
        task_counter += 1
        task_id = task_counter
        stop_event = threading.Event()
        tasks[task_id] = {
            "id": task_id,
            "account": account,
            "channel": channel,
            "mode": mode,
            "message": message,
            "reward_name": reward_name,
            "min_cost": min_cost,
            "max_cost": max_cost,
            "status": "running",
            "logs": [],
            "stop_event": stop_event,
        }

    if mode == "1":
        t = threading.Thread(
            target=run_mode1,
            args=(task_id, token, channel, reward_name, message, min_cost, max_cost),
            daemon=True,
        )
    else:
        t = threading.Thread(
            target=run_mode2,
            args=(task_id, token, channel, message, min_cost, max_cost),
            daemon=True,
        )

    with tasks_lock:
        tasks[task_id]["thread"] = t
    t.start()

    return jsonify({"task_id": task_id})


@app.route("/api/stop/<int:task_id>", methods=["POST"])
def stop_task(task_id):
    with tasks_lock:
        if task_id not in tasks:
            return jsonify({"error": "Task not found"}), 404
        tasks[task_id]["stop_event"].set()
        tasks[task_id]["status"] = "stopping"
    return jsonify({"ok": True})


@app.route("/api/delete/<int:task_id>", methods=["POST"])
def delete_task(task_id):
    with tasks_lock:
        if task_id not in tasks:
            return jsonify({"error": "Task not found"}), 404
        tasks[task_id]["stop_event"].set()
        del tasks[task_id]
    return jsonify({"ok": True})


@app.route("/api/tasks")
def get_tasks():
    with tasks_lock:
        result = []
        for tid, t in tasks.items():
            result.append({
                "id": t["id"],
                "account": t["account"],
                "channel": t["channel"],
                "mode": t["mode"],
                "reward_name": t.get("reward_name", ""),
                "status": t["status"],
                "log_count": len(t["logs"]),
            })
    return jsonify(result)


@app.route("/api/logs/<int:task_id>")
def get_logs(task_id):
    with tasks_lock:
        if task_id not in tasks:
            return jsonify({"error": "Task not found"}), 404
        return jsonify({"logs": tasks[task_id]["logs"]})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
