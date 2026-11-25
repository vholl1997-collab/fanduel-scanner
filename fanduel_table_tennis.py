"""Budgeted, read-only FanDuel table-tennis polling through Crawlora."""
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import requests

API = "https://api.crawlora.net/api/v1/fanduel/sportsbook/"
SPORT_ID = "2593174"
ARIZONA = timezone(timedelta(hours=-7))
SCORE_SECONDS = 30
DISCOVERY_SECONDS = 300
WINDOW_SECONDS = 25 * 60
STALE_SECONDS = 75
MAX_IDS = 100


def window_offset(now, start):
    local = datetime.fromtimestamp(now, ARIZONA)
    seconds = local.hour * 3600 + local.minute * 60 + local.second
    return (seconds - start) % 86400


def set_winner(home, away):
    if any(type(points) is not int or points < 0 for points in (home, away)):
        return None
    high, low = max(home, away), min(home, away)
    if high != max(11, low + 2) or high - low < 2:
        return None
    return "home" if home > away else "away"


def comeback_side(event):
    if event.get("sport") != "tableTennis" or event.get("ended") is True:
        return None
    details = event.get("details")
    if not isinstance(details, dict):
        return None
    home, away = details.get("home"), details.get("away")
    if not isinstance(home, dict) or not isinstance(away, dict):
        return None
    home, away = home.get("points_per_set"), away.get("points_per_set")
    if not isinstance(home, list) or not isinstance(away, list):
        return None
    if len(home) != len(away) or len(home) not in (4, 5):
        return None
    if len(home) == 5:
        fifth_set = (home[4], away[4])
        if any(type(points) is not int or points < 0 for points in fifth_set):
            return None
        if set_winner(*fifth_set):
            return None
    winners = [set_winner(h, a) for h, a in zip(home[:4], away[:4])]
    if winners == ["away", "away", "home", "home"]:
        return "home"
    if winners == ["home", "home", "away", "away"]:
        return "away"
    return None


class CrawloraError(Exception):
    pass


class Scanner:
    def __init__(self, api_key, start="19:00", session=None):
        self.api_key = api_key
        self.start_label = start
        self.start = None
        if re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", start):
            hour, minute = map(int, start.split(":"))
            if minute % 5 == 0:
                self.start = hour * 3600 + minute * 60
        self.session = session or requests.Session()
        self.lock = threading.Lock()
        self.matches = {}
        self.alerts = deque(maxlen=100)
        self.announced = {}
        self.score_rows = {}
        self.last_discovery = None
        self.last_score = None
        self.errors = {}
        self.blocked_until = 0

    def fetch(self, route, params):
        response = self.session.get(
            API + route,
            params={"state": "az", **params},
            headers={"x-api-key": self.api_key},
            timeout=(5, 8),
            allow_redirects=False,
        )
        if response.status_code != 200:
            if response.status_code in (401, 403):
                self.blocked_until = float("inf")
            if response.status_code in (402, 429):
                self.blocked_until = time.time() + 3600
            raise CrawloraError(f"HTTP {response.status_code}")
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if (not isinstance(data, dict) or payload.get("code") != 200
                or data.get("state") != "az" or not isinstance(data.get("events"), list)):
            raise CrawloraError("Unexpected Arizona events response")
        if any(not isinstance(row, dict) for row in data["events"]):
            raise CrawloraError("Unexpected event record")
        return data["events"]

    def discover(self, now):
        rows = self.fetch("live", {"event_type_id": SPORT_ID})
        matches = {}
        for row in rows:
            event_id = str(row.get("event_id", ""))
            in_play = row.get("in_play") is True or any(
                market.get("in_play") is True for market in (row.get("markets") or [])
                if isinstance(market, dict)
            )
            if (in_play and str(row.get("event_type_id")) == SPORT_ID
                    and re.fullmatch(r"[1-9][0-9]*", event_id)):
                matches[event_id] = row.get("name") or event_id
        with self.lock:
            self.matches = matches
            self.announced = {key: seen for key, seen in self.announced.items()
                              if now - seen < 86400}
            self.last_discovery = now
            self.score_rows = {key: row for key, row in self.score_rows.items() if key in matches}

    def scores(self, now):
        if not self.matches or not self.last_discovery or now - self.last_discovery >= 600:
            return
        # One billable batch per tick. Rotate only if over the provider's 100-ID limit.
        ids = list(self.matches)
        batches = (len(ids) + MAX_IDS - 1) // MAX_IDS
        offset = (now // SCORE_SECONDS % batches) * MAX_IDS
        batch = ids[offset:offset + MAX_IDS]
        rows = self.fetch("live-scores", {"event_id": ",".join(batch)})
        with self.lock:
            for key in batch:
                self.score_rows.pop(key, None)
            for row in rows:
                key = str(row.get("event_id"))
                if key not in batch or row.get("sport") != "tableTennis":
                    continue
                side = comeback_side(row)
                score = {
                    "event_id": key, "name": self.matches[key], "observed_at": now,
                    "period": row.get("period"), "details": row.get("details"),
                    "comeback_side": side,
                }
                self.score_rows[key] = score
                if side and key not in self.announced:
                    self.announced[key] = now
                    self.alerts.appendleft(score)
            self.last_score = now

    def tick(self, now):
        if not self.api_key or self.start is None or now < self.blocked_until:
            return
        offset = window_offset(now, self.start)
        if offset >= WINDOW_SECONDS:
            return
        jobs = [("scores", self.scores)]
        if offset % DISCOVERY_SECONDS == 0:
            jobs.insert(0, ("discovery", self.discover))
        for name, job in jobs:
            if time.time() < self.blocked_until:
                break
            try:
                job(now)
            except (requests.RequestException, CrawloraError, ValueError) as exc:
                # Never include upstream bodies, URLs or credentials in public errors.
                with self.lock:
                    self.errors[name] = str(exc) if isinstance(exc, CrawloraError) else type(exc).__name__
            else:
                with self.lock:
                    self.errors.pop(name, None)

    def run(self):
        if not self.api_key or self.start is None:
            return
        last_slot = int(time.time()) // SCORE_SECONDS
        while True:
            slot = int(time.time()) // SCORE_SECONDS
            if slot > last_slot:
                last_slot = slot
                self.tick(slot * SCORE_SECONDS)
            time.sleep(max(0.1, (last_slot + 1) * SCORE_SECONDS - time.time()))

    def snapshot(self, now):
        with self.lock:
            in_window = self.start is not None and window_offset(now, self.start) < WINDOW_SECONDS
            if not self.api_key:
                status = "missing_api_key"
            elif self.start is None:
                status = "invalid_scan_start"
            elif now < self.blocked_until:
                status = "provider_backoff"
            elif not in_window:
                status = "outside_scan_window"
            elif self.errors:
                status = "upstream_error"
            elif self.last_discovery is None or now - self.last_discovery >= 600:
                status = "awaiting_discovery"
            elif not self.matches:
                status = "no_live_matches"
            elif self.last_score is None or now - self.last_score >= STALE_SECONDS:
                status = "awaiting_scores"
            else:
                status = "receiving"
            return {
                "source": "Crawlora / FanDuel Arizona",
                "status": status,
                "scan_start_arizona": self.start_label,
                "scan_window_minutes": WINDOW_SECONDS // 60,
                "discovery_interval_seconds": DISCOVERY_SECONDS,
                "score_interval_seconds": SCORE_SECONDS,
                "scheduled_daily_credit_ceiling": 60,
                "last_discovery_at": self.last_discovery,
                "last_score_at": self.last_score,
                "discovered_count": len(self.matches) if in_window else 0,
                "scores": [row for row in self.score_rows.values()
                           if in_window and now - row["observed_at"] < STALE_SECONDS],
                "recent_comebacks": list(self.alerts),
                "errors": dict(self.errors),
            }


scanner = Scanner(os.getenv("CRAWLORA_API_KEY", "").strip(),
                  os.getenv("FANDUEL_SCAN_START_AZ", "19:00"))
