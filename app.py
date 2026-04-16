import os
import time
import threading
import requests
from datetime import datetime, timezone
from flask import Flask, request, jsonify

app = Flask(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
TRADELOCKER_EMAIL    = os.getenv("TL_EMAIL",    "dortchenterprisesllc@gmail.com")
TRADELOCKER_PASSWORD = os.getenv("TL_PASSWORD", "Cashflow007$")
TRADELOCKER_SERVER   = os.getenv("TL_SERVER",   "gatesfx")
TL_AUTH_URL          = "https://demo.tradelocker.com/backend-api/auth/jwt/token"
TL_BASE_URL          = "https://demo.tradelocker.com/backend-api"

SIGNAL_WINDOW   = 300   # 5-minute window in seconds
THRESHOLD_DEFAULT = 3   # 3 out of 5 signals for most pairs
THRESHOLD_XAUUSD  = 4   # 4 out of 5 signals for XAUUSD

VALID_PAIRS   = {"XAUUSD", "NAS100", "EURUSD", "GBPUSD", "BTCUSD"}
VALID_SIGNALS = {"BUY", "SELL"}

# ─── State ────────────────────────────────────────────────────────────────────
lock         = threading.Lock()
signal_log   = []   # list of {pair, signal, source, ts}
trades_placed = []  # confirmed trades
tl_token     = {"access": None, "refresh": None, "expires": 0}

# ─── TradeLocker Auth ─────────────────────────────────────────────────────────
def get_tl_token():
    """Fetch (or reuse) a TradeLocker JWT token."""
    now = time.time()
    if tl_token["access"] and now < tl_token["expires"] - 60:
        return tl_token["access"]

    resp = requests.post(TL_AUTH_URL, json={
        "email":    TRADELOCKER_EMAIL,
        "password": TRADELOCKER_PASSWORD,
        "server":   TRADELOCKER_SERVER,
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    tl_token["access"]  = data["token"]
    tl_token["refresh"] = data.get("refreshToken")
    # tokens typically live 1 hour; store for 55 min
    tl_token["expires"] = now + 3300
    return tl_token["access"]

# ─── Trade Execution ──────────────────────────────────────────────────────────
def place_trade(pair: str, direction: str):
    """Place a market order via TradeLocker."""
    try:
        token = get_tl_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        # Get accounts
        acct_resp = requests.get(f"{TL_BASE_URL}/trade/accounts", headers=headers, timeout=15)
        acct_resp.raise_for_status()
        accounts = acct_resp.json().get("d", {}).get("accounts", [])
        if not accounts:
            return {"ok": False, "error": "No accounts found"}

        account_id = accounts[0]["id"]
        acct_num   = accounts[0]["accNum"]

        # Resolve instrument ID
        inst_resp = requests.get(
            f"{TL_BASE_URL}/trade/instruments",
            headers=headers,
            params={"locale": "en", "accountId": account_id, "routeId": "1"},
            timeout=15,
        )
        inst_resp.raise_for_status()
        instruments = inst_resp.json().get("d", {}).get("v", [])
        inst_id = None
        for inst in instruments:
            if inst.get("name") == pair:
                inst_id = inst["tradableInstrumentId"]
                break

        if not inst_id:
            return {"ok": False, "error": f"Instrument {pair} not found"}

        # Place order
        order_payload = {
            "qty":        0.01,
            "side":       direction.lower(),
            "type":       "market",
            "validity":   "GTC",
            "routeId":    1,
            "tradableInstrumentId": inst_id,
            "accountId":  account_id,
        }
        ord_resp = requests.post(
            f"{TL_BASE_URL}/trade/orders",
            headers=headers,
            json=order_payload,
            timeout=15,
        )
        ord_resp.raise_for_status()
        return {"ok": True, "response": ord_resp.json()}

    except Exception as exc:
        return {"ok": False, "error": str(exc)}

# ─── Signal Aggregation ───────────────────────────────────────────────────────
def prune_old_signals(now: float):
    cutoff = now - SIGNAL_WINDOW
    return [s for s in signal_log if s["ts"] >= cutoff]

def check_and_fire(pair: str, direction: str) -> dict:
    """Count matching signals; fire trade if threshold met."""
    now = time.time()
    recent = [s for s in signal_log if s["pair"] == pair and s["signal"] == direction]
    threshold = THRESHOLD_XAUUSD if pair == "XAUUSD" else THRESHOLD_DEFAULT
    count = len(recent)
    confirmed = count >= threshold

    result = {
        "pair":      pair,
        "direction": direction,
        "count":     count,
        "threshold": threshold,
        "confirmed": confirmed,
    }

    if confirmed:
        trade_result = place_trade(pair, direction)
        result["trade"] = trade_result
        trades_placed.append({
            "pair":      pair,
            "direction": direction,
            "ts":        datetime.now(timezone.utc).isoformat(),
            "trade":     trade_result,
        })
        # Clear signals for this pair after firing
        for s in signal_log:
            if s["pair"] == pair:
                s["ts"] = 0   # expire immediately

    return result

# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

@app.route("/status", methods=["GET"])
def status():
    now = time.time()
    with lock:
        fresh = prune_old_signals(now)
        summary = {}
        for s in fresh:
            key = f"{s['pair']}:{s['signal']}"
            summary[key] = summary.get(key, 0) + 1

    return jsonify({
        "active_signals": summary,
        "recent_trades":  trades_placed[-10:],
        "window_seconds": SIGNAL_WINDOW,
        "thresholds":     {"XAUUSD": THRESHOLD_XAUUSD, "default": THRESHOLD_DEFAULT},
        "server_time":    datetime.now(timezone.utc).isoformat(),
    })

@app.route("/signal", methods=["POST"])
def receive_signal():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    pair   = str(data.get("pair",   "")).upper().strip()
    signal = str(data.get("signal", "")).upper().strip()
    source = str(data.get("source", "unknown")).strip()

    if pair not in VALID_PAIRS:
        return jsonify({"error": f"Unknown pair '{pair}'. Valid: {list(VALID_PAIRS)}"}), 400
    if signal not in VALID_SIGNALS:
        return jsonify({"error": f"Unknown signal '{signal}'. Valid: {list(VALID_SIGNALS)}"}), 400

    now = time.time()
    with lock:
        signal_log.append({"pair": pair, "signal": signal, "source": source, "ts": now})
        # Prune stale entries globally
        signal_log[:] = prune_old_signals(now)
        result = check_and_fire(pair, signal)

    result["received"] = datetime.now(timezone.utc).isoformat()
    result["source"]   = source
    return jsonify(result), 200

# ─── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
