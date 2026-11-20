import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://gamma-api.polymarket.com"
SPORTS = re.compile(
    r"sports|nba|wnba|nfl|mlb|nhl|ncaa|ncaab|ncaaf|soccer|football|tennis|golf|ufc|mma|boxing|cricket|rugby|formula.?1|f1|esports|basketball|baseball|hockey|league of legends|valorant",
    re.I,
)
THRESHOLDS = (0.90, 0.95, 0.98, 0.99)
MAX_POINTS = 120


def as_list(value):
    if isinstance(value, list):
        return value
    try:
        return json.loads(value or "[]")
    except (TypeError, ValueError):
        return []


def is_sports(event):
    tags = " ".join(
        str(t.get("slug") or t.get("label") or t.get("name") or "")
        for t in (event.get("tags") or [])
    )
    haystack = " ".join(
        str(event.get(k) or "") for k in ("title", "name", "slug")
    ) + " " + tags
    return bool(SPORTS.search(haystack))


def fetch_events():
    session = requests.Session()
    session.headers.update({"User-Agent": "SportsMarketScanner/1.0"})
    events = []
    for offset in range(0, 500, 100):
        response = session.get(
            API + "/events",
            params={
                "active": "true",
                "closed": "false",
                "limit": 100,
                "offset": offset,
                "order": "volume24hr",
                "ascending": "false",
            },
            timeout=25,
        )
        response.raise_for_status()
        batch = response.json()
        if not isinstance(batch, list) or not batch:
            break
        events.extend(batch)
        if len(batch) < 100:
            break
    return events


def main(path):
    file_path = Path(path)
    data = json.loads(file_path.read_text()) if file_path.exists() else {
        "schema_version": 1, "generated_at": None, "markets": {}
    }
    store = data.setdefault("markets", {})
    now = int(time.time())
    events = fetch_events()
    seen = 0

    for event in events:
        if not is_sports(event):
            continue
        event_title = event.get("title") or event.get("name") or "Sports event"
        league = " · ".join(
            str(t.get("label") or t.get("name"))
            for t in (event.get("tags") or [])
            if t.get("label") or t.get("name")
        ) or "Polymarket sports"

        for market in event.get("markets") or []:
            if market.get("closed") or market.get("active") is False:
                continue
            outcomes = as_list(market.get("outcomes"))
            prices = as_list(market.get("outcomePrices"))
            if not prices and isinstance(market.get("outcomePrices"), dict):
                prices = list(market["outcomePrices"].values())
            if not prices:
                continue
            base_id = str(market.get("id") or market.get("conditionId") or market.get("slug") or "")
            for index, raw_price in enumerate(prices):
                try:
                    probability = float(raw_price)
                except (TypeError, ValueError):
                    continue
                if probability > 1:
                    probability /= 100
                if not 0 <= probability <= 1:
                    continue
                outcome = str(outcomes[index]) if index < len(outcomes) else f"Outcome {index + 1}"
                market_id = f"{base_id}:{index}"
                title = market.get("question") or market.get("groupItemTitle") or event_title
                record = store.get(market_id)
                if record is None:
                    record = {
                        "id": market_id,
                        "title": title,
                        "event": event_title,
                        "league": league,
                        "outcome": outcome,
                        "first_seen": now,
                        "peak": probability,
                        "low": probability,
                        "thresholds": {},
                        "points": [],
                    }
                    store[market_id] = record

                record.update({
                    "title": title,
                    "event": event_title,
                    "league": league,
                    "outcome": outcome,
                    "probability": probability,
                    "volume": market.get("volumeNum") or market.get("volume"),
                    "last_seen": now,
                })
                record["peak"] = max(float(record.get("peak", probability)), probability)
                record["low"] = min(float(record.get("low", probability)), probability)

                for threshold in THRESHOLDS:
                    key = str(int(threshold * 100))
                    if probability >= threshold and key not in record["thresholds"]:
                        record["thresholds"][key] = now

                points = record.setdefault("points", [])
                if not points or abs(float(points[-1]["p"]) - probability) >= 0.0005 or now - int(points[-1]["t"]) >= 3600:
                    points.append({"t": now, "p": round(probability, 6)})
                record["points"] = points[-MAX_POINTS:]
                seen += 1

    data["generated_at"] = now
    data["events_scanned"] = len(events)
    data["sports_outcomes_seen"] = seen
    file_path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n")
    print(f"Scanned {len(events)} events; updated {seen} sports outcomes; stored {len(store)} histories.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "history.json")
