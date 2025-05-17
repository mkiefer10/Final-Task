rom flask import Flask, request, jsonify
import re
import random
import time
import json
import sqlite3
from datetime import datetime

app = Flask(__name__)

# Regex for Ethereum and Tron wallet validation
ETH_REGEX = re.compile(r"^0x[a-fA-F0-9]{40}$")
TRON_REGEX = re.compile(r"^T[a-zA-Z0-9]{33}$")

# FX mock rate (e.g., USDC → KES)
FX_RATES = {"USDC_KES": 142.00}

# Simulated blacklist
BLOCKED_COUNTRIES = ["IRN", "RUS"]
BLOCKED_WALLETS = ["0xblockedwallet"]

# Retry tracking (simulate failure twice before success)
RETRY_STATE = {}

# SQLite DB for logs
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
    conn.commit()
    conn.close()

# Log to SQLite

def log_event(step, status, details):
    conn = sqlite3.connect('payout_logs.db')
    c = conn.cursor()
    c.execute("INSERT INTO logs (timestamp, step, status, details) VALUES (?, ?, ?, ?)",
              (datetime.utcnow().isoformat(), step, status, json.dumps(details)))
    conn.commit()
    conn.close()

# Validate wallet address

def is_valid_wallet(wallet, chain="ethereum"):
    if chain == "ethereum":
        return ETH_REGEX.match(wallet) is not None
    elif chain == "tron":
        return TRON_REGEX.match(wallet) is not None
    return False

# FX Conversion

def convert_currency(amount, currency, target):
    rate = FX_RATES.get(f"{currency}_{target}", None)
    if rate:
        return round(amount * rate, 2), rate
    return None, None

# Compliance check

def check_compliance(wallet, country):
    if wallet in BLOCKED_WALLETS or country.upper() in BLOCKED_COUNTRIES:
        return False
    return True

# Simulated payment API call with retry logic

def simulate_payment_api(tx_id, rail):
    count = RETRY_STATE.get(tx_id, 0)
    if count < 2:
        RETRY_STATE[tx_id] = count + 1
        raise Exception(f"Simulated failure #{count + 1}")
    return {"status": "success", "rail": rail}

# Router function

def route_payout(tx_id, rail, converted_amount):
    try:
        result = simulate_payment_api(tx_id, rail)
        log_event("Payout", "success", result)
        return result
    except Exception as e:
        log_event("Payout", "retry", {"tx_id": tx_id, "error": str(e)})
        time.sleep(1)  # simulate backoff
        return route_payout(tx_id, rail, converted_amount)

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

    # Validate wallet
    if not is_valid_wallet(wallet):
        log_event("Validation", "fail", {"reason": "Invalid wallet address"})
        return jsonify({"status": "fail", "reason": "Invalid wallet address"}), 400

    # Compliance check
    if not check_compliance(wallet, country):
        log_event("Compliance", "blocked", {"reason": "Sanctions or blacklisted wallet"})
        return jsonify({"status": "blocked", "reason": "Sanctions or blacklisted wallet"}), 403

    # FX Conversion
    converted_amount, rate = convert_currency(amount, currency, "KES")
    if not converted_amount:
        log_event("FX", "fail", {"reason": "FX rate missing"})
        return jsonify({"status": "fail", "reason": "FX rate unavailable"}), 400

    log_event("FX", "success", {"converted_amount": converted_amount, "rate": rate})

    # Determine rail
    rail = "Visa Direct" if urgency == "high" else "SWIFT/SEPA"
    log_event("Routing", "selected", {"rail": rail})

    # Process payout with retry
    result = route_payout(tx_id, rail, converted_amount)

    return jsonify({"status": "success", "tx_id": tx_id, "rail": rail, "converted_amount": converted_amount})

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)
