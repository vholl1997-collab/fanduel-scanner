"""Collect timestamped public Polymarket market price observations.

The feed paginates active events; snapshots are not real-time odds.
All stored timestamps are UTC Unix seconds.
"""
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API = "https://gamma-api.polymarket.com"
SPORTS = re.compile(
    r"\b(?:sports|nba|wnba|nfl|mlb|nhl|ncaa|ncaab|ncaaf|soccer|football|tennis|golf|ufc|mma|boxing|cricket|rugby|formula.?1|f1|esports|basketball|baseball|hockey|valorant)\b",
    re.I,
)
THRESHOLDS = (90, 95, 98, 99)
MAX_POINTS = 300
MAX_EVENTS = 20000
PAGE_SIZE = 100
MAX_SCAN_SECONDS = 180
MIN_OUTCOMES = 1


def as_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def is_sports(event):
    tags = " ".join(str(t.get("slug") or t.get("label") or t.get("name") or "")
                    for t in (event.get("tags") or []) if isinstance(t, dict))
    return bool(SPORTS.search(" ".join(str(event.get(k) or "") for k in
                                      ("title", "name", "slug")) + " " + tags))


def fetch_events(session):
    events = []
    started = time.monotonic()
    for offset in range(0, MAX_EVENTS, PAGE_SIZE):
        if offset and time.monotonic() - started > MAX_SCAN_SECONDS:
            raise TimeoutError("Event pagination exceeded time budget; refusing partial snapshot")
        response = session.get(API + "/events", params={
            "active": "true", "closed": "false", "limit": PAGE_SIZE, "offset": offset,
            "order": "id", "ascending": "false",
        }, timeout=(10, 30))
        # Gamma may reject deep offset pagination with HTTP 422.
        # Preserve the usable pages already fetched instead of failing every scan.
        if response.status_code == 422 and offset > 0 and events:
            print(f"Gamma rejected offset {offset} (422); saving {len(events)} fetched events with partial coverage.")
            break
        response.raise_for_status()
        batch = response.json()
        if not isinstance(batch, list):
            raise ValueError("Unexpected Polymarket events response")
        if not batch:
            break
        events.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
    else:
        raise ValueError("Reached MAX_EVENTS cap; refusing to label partial discovery complete")
    if not events:
        raise ValueError("Empty Polymarket event feed: refusing to overwrite history")
    return events


def collect(data, events, now):
    store = data.setdefault("markets", {})
    seen = 0
    sports_events = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        sports_events += int(is_sports(event))
        event_title = event.get("title") or event.get("name") or "Polymarket event"
        league = " · ".join(str(t.get("label") or t.get("name"))
                            for t in (event.get("tags") or [])
                            if isinstance(t, dict) and (t.get("label") or t.get("name"))) or "Polymarket"
        for market in event.get("markets") or []:
            if not isinstance(market, dict) or market.get("closed") or market.get("active") is False:
                continue
            outcomes = as_list(market.get("outcomes"))
            prices = as_list(market.get("outcomePrices"))
            base = market.get("id") or market.get("conditionId") or market.get("slug")
            if not base or not prices:
                continue
            for i, raw in enumerate(prices):
                try:
                    p = float(raw)
                except (ValueError, TypeError):
                    continue
                if not math.isfinite(p) or p < 0 or p > 1:
                    continue
                key = f"{base}:{i}"
                record = store.get(key)
                if record is None:
                    record = {
                        "id": key, "source": "polymarket_gamma", "first_seen": now,
                        "peak": p, "low": p, "peak_at": now, "low_at": now,
                        "thresholds": {}, "points": [],
                    }
                    store[key] = record
                record.update({
                    "title": market.get("question") or market.get("groupItemTitle") or event_title,
                    "event": event_title, "league": league,
                    "outcome": str(outcomes[i]) if i < len(outcomes) else f"Outcome {i+1}",
                    "probability": p, "volume": market.get("volumeNum") or market.get("volume"),
                    "last_seen": now, "source": "polymarket_gamma",
                    "event_start": event.get("startDate") or event.get("startTime") or event.get("gameStartTime"),
                    "event_end": event.get("endDate"),
                    "market_end": market.get("endDate"),
                    "accepting_orders": market.get("acceptingOrders"),
                    "market_closed": bool(market.get("closed")),
                })
                if p > float(record.get("peak", p)):
                    record["peak"], record["peak_at"] = p, now
                if p < float(record.get("low", p)):
                    record["low"], record["low_at"] = p, now
                thresholds = record.setdefault("thresholds", {})
                for threshold in THRESHOLDS:
                    if p >= threshold / 100:
                        thresholds.setdefault(str(threshold), now)
                points = record.setdefault("points", [])
                # Old snapshots have second-based timestamps; never fabricate intermediate points.
                if not points or abs(float(points[-1]["p"]) - p) >= 0.0005 or now - int(points[-1]["t"]) >= 3600:
                    points.append({"t": now, "p": round(p, 6)})
                record["points"] = points[-MAX_POINTS:]
                seen += 1
    if seen < MIN_OUTCOMES:
        raise ValueError("No usable outcomes: refusing to publish empty snapshot")
    data.update({
        "schema_version": 2, "generated_at": now, "last_success_at": now,
        "events_scanned": len(events), "sports_events_scanned": sports_events,
        "sports_outcomes_seen": seen,
        "all_outcomes_seen": seen,
        "coverage": "paginated_active_events_until_api_limit_not_guaranteed_complete",
        "timestamp_unit": "unix_seconds_utc",
    })
    return seen


def main(path):
    file_path = Path(path)
    data = json.loads(file_path.read_text()) if file_path.exists() else {
        "schema_version": 2, "generated_at": None, "markets": {}
    }
    session = requests.Session()
    session.headers.update({"User-Agent": "SportsMarketScanner/2.0"})
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET"])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    events = fetch_events(session)
    now = int(time.time())
    seen = collect(data, events, now)
    tmp = file_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n")
    os.replace(tmp, file_path)
    print(f"UTC collection {now}: {len(events)} events scanned, {seen} outcomes updated, "
          f"{len(data['markets'])} total historical outcomes.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "history.json")
