"""Read-only Kalshi sports discovery and 99% reversal monitoring.
Uses public REST quotes; no trading credentials or order placement.
"""
import logging
import threading
import time
import requests

BASE = "https://external-api.kalshi.com/trade-api/v2"
INTERVAL = 300
MAX_PAGES = 3
PAGE_SIZE = 200
STALE = 900

class KalshiFeed:
    def __init__(self):
        self.lock = threading.RLock()
        self.session = requests.Session()
        self.markets = {}
        self.peaks = {}
        self.alerts = {}
        self.last_success = 0
        self.error = ""
        self.scanned = 0
        self.series = set()
        self.series_refreshed = 0
        self.backoff_until = 0
        self.backoff_seconds = 300

    def get(self, path, params=None):
        response = self.session.get(BASE + path, params=params, timeout=22)
        if response.status_code == 429:
            retry = response.headers.get("Retry-After", "")
            try:
                delay = max(300, min(3600, int(retry)))
            except (ValueError, TypeError):
                delay = self.backoff_seconds
            self.backoff_until = time.time() + delay
            self.backoff_seconds = min(self.backoff_seconds * 2, 3600)
            raise RuntimeError("Kalshi rate limited (429); retrying after cooldown")
        response.raise_for_status()
        return response.json()

    def refresh_series(self):
        if self.series and time.time() - self.series_refreshed < 3600:
            return
        data = self.get("/series", {"category": "Sports"})
        series = {str(s.get("ticker") or "").upper() for s in data.get("series", []) if s.get("ticker")}
        if not series:
            raise ValueError("Kalshi Sports series lookup returned no series")
        self.series = series
        self.series_refreshed = time.time()

    @staticmethod
    def price(m):
        # Prefer executable YES ask, not last trade or midpoint. Empty asks are not a quote.
        for field in ("yes_ask_dollars", "yes_ask"):
            v = m.get(field)
            if v is None or v == "":
                continue
            try:
                n = float(v)
                if field == "yes_ask":
                    n /= 100
                if 0 < n < 1:
                    return n
            except (ValueError, TypeError):
                pass
        return None

    def scan(self):
        self.refresh_series()
        cursor = ""
        seen = {}
        scanned = 0
        for page in range(MAX_PAGES):
            params = {"status": "open", "limit": PAGE_SIZE, "with_nested_markets": "true"}
            if cursor:
                params["cursor"] = cursor
            data = self.get("/events", params)
            batch = data.get("events") or []
            scanned += len(batch)
            for event in batch:
                series = str(event.get("series_ticker") or "").upper()
                if series not in self.series:
                    continue
                title = str(event.get("title") or "")
                for m in event.get("markets") or []:
                    if m.get("status") != "open":
                        continue
                    p = self.price(m)
                    if p is None:
                        continue
                    ticker = str(m.get("ticker") or "")
                    if not ticker:
                        continue
                    seen[ticker] = {
                        "ticker": ticker, "event": title,
                        "market": str(m.get("title") or title),
                        "outcome": str(m.get("yes_sub_title") or "Yes"),
                        "series": series, "probability": p,
                        "volume": m.get("volume_fp") or m.get("volume") or 0,
                        "source": "Kalshi YES ask", "updated_at": time.time(),
                        "url": "https://kalshi.com/markets/" + series.lower(),
                    }
            cursor = str(data.get("cursor") or "")
            if not cursor or not batch:
                break
        now = time.time()
        with self.lock:
            for ticker, item in seen.items():
                prev = self.markets.get(ticker)
                peak = max(self.peaks.get(ticker, item["probability"]), item["probability"])
                self.peaks[ticker] = peak
                item["peak"] = peak
                item["previous_probability"] = prev["probability"] if prev else None
                if peak >= .99 and peak - item["probability"] >= .02 and prev and prev["probability"] > item["probability"]:
                    self.alerts[ticker] = {**item, "drop_pp": round((peak-item["probability"])*100, 2), "alert_at": now}
            self.markets = seen
            self.last_success = now
            self.scanned = scanned
            self.error = ""
            self.backoff_seconds = 300
            self.alerts = {k:v for k,v in self.alerts.items() if now-v["alert_at"] < 3600}

    def run(self):
        while True:
            if time.time() < self.backoff_until:
                time.sleep(min(60, max(1, self.backoff_until-time.time())))
                continue
            try:
                self.scan()
            except Exception as exc:
                with self.lock:
                    self.error = str(exc)[:220]
                logging.exception("Kalshi sports scan failed")
            time.sleep(INTERVAL)

    def snapshot(self):
        with self.lock:
            now = time.time()
            fresh = now-self.last_success < STALE
            rows = list(self.markets.values()) if fresh else []
            rows.sort(key=lambda x: (x["peak"] >= .99, x["probability"]), reverse=True)
            alerts = sorted(self.alerts.values(), key=lambda x: -x["alert_at"]) if fresh else []
            return {
                "status": "receiving" if fresh else "delayed",
                "last_success": self.last_success,
                "error": self.error,
                "retry_after_seconds": max(0, round(self.backoff_until-now)),
                "scanned_events": self.scanned,
                "coverage": "Partial: first 3 pages of open events; not all Kalshi sports markets",
                "tracked_markets": len(rows),
                "current_99": sum(x["probability"] >= .99 for x in rows),
                "alerts": alerts[:60],
                "markets": rows[:500],
            }

feed = KalshiFeed()
