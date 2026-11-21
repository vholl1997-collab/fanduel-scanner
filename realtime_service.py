"""Continuous read-only Polymarket price monitor for a paid Render web service.

This service observes CLOB market websocket updates, tracks 99% peaks,
and serves the latest observed outcomes. It never places orders.
"""
import json
import logging
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify
from websocket import WebSocketApp

GAMMA = "https://gamma-api.polymarket.com"
CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
DISCOVERY_SECONDS = 120
MAX_ASSETS = 1500
STALE_SECONDS = 90
SUBSCRIPTION_BATCH = 100
app = Flask(__name__)
lock = threading.RLock()
markets = {}
prices = {}
peaks = {}
last_tick = 0
last_discovery = 0
last_error = ""
events = deque(maxlen=200)
high_probability_observations = {}
feed_stats = {"messages": 0, "last_message_at": None, "subscriptions_sent": 0, "last_close": None, "event_types": {}, "matched_price_updates": 0, "unmatched_assets": 0}
session = requests.Session()

@app.after_request
def allow_public_dashboard(response):
    response.headers['Access-Control-Allow-Origin'] = 'https://vholl1997-collab.github.io'
    response.headers['Cache-Control'] = 'no-store'
    return response



def parse_list(value):
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def classify_market(market, outcome):
    """Keep spread and total lines distinct; do not infer missing lines."""
    question = str(market.get("question") or "")
    kind = str(market.get("sportsMarketType") or market.get("sports_market_type") or "").lower()
    label = question + " " + str(outcome)
    if "spread" in kind or re.search(r"\\b(spread|handicap)\\b", label, re.I):
        category = "spread"
    elif "total" in kind or re.search(r"\\b(over|under|o/u|total points|total goals)\\b", label, re.I):
        category = "total"
    else:
        category = "other"
    line = market.get("line")
    if line is None:
        line = market.get("groupItemThreshold")
    if line is None:
        match = re.search(r"(?<!\\d)[+-]?\\d+(?:\\.\\d+)?", str(outcome))
        if match:
            line = match.group()
    return category, str(line) if line is not None else None


def discover():
    """Fetch active sports markets; skip esports and virtual events."""
    from collector import is_sports
    found = {}
    for offset in range(0, 1200, 100):
        response = session.get(GAMMA + "/events", params={
            "active": "true", "closed": "false", "limit": 100,
            "offset": offset, "order": "id", "ascending": "false",
        }, timeout=25)
        response.raise_for_status()
        batch = response.json()
        if not isinstance(batch, list):
            raise ValueError("Invalid Gamma event payload")
        for event in batch:
            if not is_sports(event):
                continue
            for market in event.get("markets") or []:
                if market.get("closed") or market.get("active") is False:
                    continue
                tokens = parse_list(market.get("clobTokenIds"))
                outcomes = parse_list(market.get("outcomes"))
                for index, token in enumerate(tokens):
                    if not token:
                        continue
                    category, line = classify_market(market, outcomes[index] if index < len(outcomes) else "")
                    found[str(token)] = {
                        "token_id": str(token),
                        "event": event.get("title", ""),
                        "event_slug": event.get("slug", ""),
                        "outcome": str(outcomes[index]) if index < len(outcomes) else str(index),
                        "market": market.get("question", ""),
                        "market_id": str(market.get("id", "")),
                        "market_type": category,
                        "line": line,
                        "sport_game_id": str(event.get("gameId") or ""),
                    }
                    if len(found) >= MAX_ASSETS:
                        return found
        if len(batch) < 100:
            break
    return found


def observe(token, probability, source):
    global last_tick
    try:
        probability = float(probability)
    except (ValueError, TypeError):
        return
    if not 0 <= probability <= 1:
        return
    now = time.time()
    with lock:
        if token not in markets:
            feed_stats["unmatched_assets"] += 1
            return
        feed_stats["matched_price_updates"] += 1
        previous = prices.get(token, {}).get("probability")
        peak = max(peaks.get(token, probability), probability)
        peaks[token] = peak
        prices[token] = {"probability": probability, "updated_at": now, "source": source}
        last_tick = now
        if probability >= .97:
            record = high_probability_observations.get(token)
            if record is None:
                high_probability_observations[token] = {
                    **markets[token], "first_seen_at": now, "first_probability": probability,
                    "highest_probability": probability, "last_seen_at": now,
                    "last_probability": probability, "result": "pending",
                }
            else:
                record["highest_probability"] = max(record["highest_probability"], probability)
                record["last_probability"] = probability
                record["last_seen_at"] = now
        if peak >= .99 and previous is not None and previous > probability and peak - probability >= .02:
            if previous - probability >= .001:
                events.appendleft({
                    **markets[token], "peak": peak, "probability": probability,
                    "drop_pp": round((peak - probability) * 100, 3),
                    "updated_at": now, "source": source,
                })


def on_message(ws, raw):
    if raw in ("PING", "PONG", "ping", "pong"):
        return
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return
    with lock:
        feed_stats["messages"] += 1
        feed_stats["last_message_at"] = time.time()
    messages = payload if isinstance(payload, list) else [payload]
    for message in messages:
        if not isinstance(message, dict):
            continue
        kind = message.get("event_type") or "unknown"
        with lock:
            types = feed_stats["event_types"]
            types[kind] = types.get(kind, 0) + 1
        if kind == "price_change":
            for change in message.get("price_changes") or []:
                token = str(change.get("asset_id") or "")
                # A last trade is a transaction price, not a fair-value estimate.
                value = change.get("last_trade_price")
                if value is not None:
                    observe(token, value, "clob_last_trade")
        elif kind == "last_trade_price":
            observe(str(message.get("asset_id") or ""), message.get("price"), "clob_last_trade")
        elif kind == "best_bid_ask":
            try:
                bid = float(message["best_bid"])
                ask = float(message["best_ask"])
                if 0 <= bid <= ask <= 1:
                    observe(str(message.get("asset_id") or ""), (bid + ask) / 2, "clob_best_bid_ask")
            except (ValueError, TypeError, KeyError):
                pass
        elif kind == "book":
            token = str(message.get("asset_id") or "")
            bids = message.get("bids") or message.get("buys") or []
            asks = message.get("asks") or message.get("sells") or []
            try:
                bid = max(float(x["price"]) for x in bids)
                ask = min(float(x["price"]) for x in asks)
                if 0 <= bid <= ask <= 1:
                    observe(token, (bid + ask) / 2, "clob_book_mid")
            except (ValueError, TypeError, KeyError):
                pass


def monitor():
    global markets, last_discovery, last_error
    delay = 3
    while True:
        try:
            found = discover()
            if not found:
                raise RuntimeError("No active sports CLOB assets discovered")
            with lock:
                markets = found
                last_discovery = time.time()
                last_error = ""
            def opened(ws):
                ids = list(found)
                with lock:
                    feed_stats["subscriptions_sent"] = 0
                for start in range(0, len(ids), SUBSCRIPTION_BATCH):
                    batch = ids[start:start + SUBSCRIPTION_BATCH]
                    request = {"assets_ids": batch, "type": "market"} if start == 0 else {"assets_ids": batch, "operation": "subscribe"}
                    ws.send(json.dumps(request))
                    with lock:
                        feed_stats["subscriptions_sent"] += len(batch)
                    time.sleep(0.15)
                logging.info("Subscribed %d CLOB assets in batches", len(ids))
            def failed(ws, error):
                global last_error
                last_error = str(error)[:250]
                logging.warning("CLOB socket error: %s", error)
            def closed(ws, code, reason):
                with lock:
                    feed_stats["last_close"] = {"code": code, "reason": str(reason)[:200], "at": time.time()}
                logging.warning("CLOB socket closed: %s %s", code, reason)
            ws = WebSocketApp(CLOB_WS, on_open=opened, on_message=on_message, on_error=failed, on_close=closed)
            # Periodically reconnect to refresh token subscriptions.
            timer = threading.Timer(DISCOVERY_SECONDS, ws.close)
            timer.daemon = True
            timer.start()
            try:
                ws.run_forever(ping_interval=25, ping_timeout=10)
            finally:
                timer.cancel()
            delay = 3
        except Exception as exc:
            last_error = str(exc)[:250]
            logging.exception("Monitor failure")
        time.sleep(delay)
        delay = min(delay * 2, 30)


@app.get("/health")
def health():
    with lock:
        age = time.time() - last_tick if last_tick else None
        return jsonify({
            "service": "polymarket-pulse", "status": "receiving" if age is not None and age < STALE_SECONDS else "awaiting_live_prices",
            "tracked_assets": len(markets), "last_price_age_seconds": round(age, 1) if age is not None else None,
            "last_discovery_at": last_discovery, "error": last_error, "feed": dict(feed_stats),
        })


@app.get("/high-probability")
def high_probability():
    """Observed 97%+ selections; not verified wins or betting edges."""
    with lock:
        records = sorted(high_probability_observations.values(),
                         key=lambda x: -x["last_seen_at"])
        return jsonify({
            "generated_at": time.time(), "threshold": 0.97,
            "observations": records[:500],
            "verified_results": 0,
            "fan_duel_comparison_status": "not_connected",
            "result_tracking_status": "not_connected",
            "persistence": "memory_only_restart_resets",
            "note": "Prices are implied probabilities, not actual win probabilities. No value claim without matching sportsbook line and verified result.",
        })


@app.get("/signals")
def signals():
    now = time.time()
    with lock:
        active = [
            {**markets[token], **price, "peak": peaks.get(token, price["probability"]),
             "drop_pp": round((peaks.get(token, price["probability"]) - price["probability"]) * 100, 3)}
            for token, price in prices.items()
            if token in markets and now - price["updated_at"] < STALE_SECONDS
            and peaks.get(token, 0) >= .99
        ]
        return jsonify({"generated_at": now, "stale_after_seconds": STALE_SECONDS,
                        "tracked_99_outcomes": sorted(active, key=lambda x: -x["drop_pp"])[:100],
                        "recent_alerts": list(events)[:50], "health": health().get_json()})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    threading.Thread(target=monitor, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), threaded=True)
