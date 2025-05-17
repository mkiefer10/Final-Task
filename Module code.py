from flask import Flask, request, jsonify
import re
import random
import time
import json
import sqlite3
from datetime import datetime
import os

app = Flask(__name__)

# --- Constants and Globals ---
ETH_REGEX = re.compile(r"^0x[a-fA-F0-9]{40}$")
TRON_REGEX = re.compile(r"^T[a-zA-Z0-9]{33}$")
FX_RATES = {"USDC_KES": 142.00}
BLOCKED_COUNTRIES = ["IRN", "RUS"]
BLOCKED_WALLETS = ["0xblockedwallet"]
RETRY_STATE = {}
LOG_FILE = 'log.jsonl'

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect('payout_logs.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        step TEXT,
        status TEXT,
        details TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS retry_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tx_id TEXT,
        reason TEXT,
        retries INTEGER DEFAULT 0
    )''')
    conn.commit()
    conn.close()

# --- Logging ---
def log_event(step, status, details):
    event = {
        "timestamp": datetime.utcnow().isoformat(),
        "step": step,
        "status": status,
        "details": details
    }
    with open(LOG_FILE, 'a') as f:
        f.write(json.dumps(event) + '\n')
    conn = sqlite3.connect('payout_logs.db')
    c = conn.cursor()
    c.execute("INSERT INTO logs (timestamp, step, status, details) VALUES (?, ?, ?, ?)",
              (event['timestamp'], step, status, json.dumps(details)))
    conn.commit()
    conn.close()

# --- Wallet Validation ---
def is_valid_wallet(wallet, chain="ethereum"):
    if chain == "ethereum":
        return ETH_REGEX.match(wallet) is not None
    elif chain == "tron":
        return TRON_REGEX.match(wallet) is not None
    return False

# --- FX Conversion ---
def convert_currency(amount, currency, target):
    rate = FX_RATES.get(f"{currency}_{target}")
    if rate:
        return round(amount * rate, 2), rate
    return None, None

# --- Compliance ---
def check_compliance(wallet, country):
    return wallet not in BLOCKED_WALLETS and country.upper() not in BLOCKED_COUNTRIES

# --- Retry and Route ---
def simulate_payment_api(tx_id, rail):
    count = RETRY_STATE.get(tx_id, 0)
    if count < 2:
        RETRY_STATE[tx_id] = count + 1
        raise Exception(f"Simulated failure #{count + 1}")
    return {"status": "success", "rail": rail}

def route_payout(tx_id, rail, converted_amount):
    try:
        result = simulate_payment_api(tx_id, rail)
        log_event("Payout", "success", result)
        return result
    except Exception as e:
        log_event("Payout", "retry", {"tx_id": tx_id, "error": str(e)})
        conn = sqlite3.connect('payout_logs.db')
        c = conn.cursor()
        c.execute("INSERT INTO retry_queue (tx_id, reason, retries) VALUES (?, ?, ?)",
                  (tx_id, str(e), RETRY_STATE.get(tx_id, 0)))
        conn.commit()
        conn.close()
        time.sleep(1)
        return route_payout(tx_id, rail, converted_amount)

# --- API Routes ---
@app.route('/payout', methods=['POST'])
def payout():
    data = request.get_json()
    wallet = data.get("wallet_address")
    amount = data.get("amount")
    currency = data.get("currency")
    country = data.get("destination_country")
    urgency = data.get("urgency")

    tx_id = f"tx_{int(time.time())}_{random.randint(1000,9999)}"
    log_event("Received", "start", data)

    if not is_valid_wallet(wallet):
        log_event("Validation", "fail", {"reason": "Invalid wallet address"})
        return jsonify({"status": "fail", "reason": "Invalid wallet address"}), 400

    if not check_compliance(wallet, country):
        log_event("Compliance", "blocked", {"reason": "Sanctions or blacklisted wallet"})
        return jsonify({"status": "blocked", "reason": "Sanctions or blacklisted wallet"}), 403

    converted_amount, rate = convert_currency(amount, currency, "KES")
    if not converted_amount:
        log_event("FX", "fail", {"reason": "FX rate missing"})
        return jsonify({"status": "fail", "reason": "FX rate unavailable"}), 400

    log_event("FX", "success", {"converted_amount": converted_amount, "rate": rate})

    rail = "Visa Direct" if urgency == "high" else "SWIFT/SEPA"
    log_event("Routing", "selected", {"rail": rail})

    result = route_payout(tx_id, rail, converted_amount)

    return jsonify({"status": "success", "tx_id": tx_id, "rail": rail, "converted_amount": converted_amount})

@app.route('/logs', methods=['GET'])
def view_logs():
    with open(LOG_FILE, 'r') as f:
        lines = f.readlines()
    logs = [json.loads(line) for line in lines[-100:]]  # return last 100 entries
    return jsonify(logs)

@app.route('/admin/retries', methods=['GET'])
def view_failed_retries():
    conn = sqlite3.connect('payout_logs.db')
    c = conn.cursor()
    c.execute("SELECT tx_id, reason, retries FROM retry_queue ORDER BY id DESC LIMIT 100")
    rows = c.fetchall()
    conn.close()
    return jsonify([{"tx_id": r[0], "reason": r[1], "retries": r[2]} for r in rows])

if __name__ == '__main__':
    if not os.path.exists(LOG_FILE):
        open(LOG_FILE, 'w').close()
    init_db()
    app.run(debug=True, port=5000)
