# Sports Probability Scanner

A mobile-first sports market intelligence dashboard. The first release is read-only: it tracks market probabilities and odds, records historical changes, and surfaces signals. It does not place bets.

## Core probability tracking

For each market outcome, store timestamped observations including:
- Source and market identifier
- Sport, league, event, market, and outcome
- Raw source price/odds and normalized implied probability
- Observation timestamp and source timestamp when available

Calculate and display:
- Current implied probability
- All-time observed high and low
- Change from prior observation and from peak (percentage points)
- Time and source of the observed peak
- Largest rises and falls over selectable windows

## Historical search

Allow users to search/filter outcomes that crossed a probability threshold (for example 90%, 95%, 98%, or 99%), including outcomes that later moved away from that level. Store the time reached, peak, current value, and subsequent movement.

## Data integrity

- Label Polymarket prices as market-implied probabilities, not guaranteed real-world chances.
- Convert sportsbook odds to implied probability and identify the source.
- Keep source-specific observations separate; do not silently combine unlike markets.
- Preserve timestamps and missing-data gaps. Do not fabricate historical observations.
- Show whether values are live, delayed, stale, or unavailable.
- Account for fees, spread, and vig when comparing prices where data permits.

## Initial development phases

1. Mobile-first dashboard and data model.
2. Polymarket public market-data integration.
3. Probability history storage and threshold search.
4. Live and upcoming sports views.
5. Cross-source odds comparison, value estimates, and alerts.
6. Additional providers where permitted and technically accessible.

## Safety and scope

This project is an information and analytics tool. It must not place wagers automatically. Users must independently verify market rules, prices, availability, and local eligibility before acting.


## Collector operations (November 2026)

- `main` contains the dashboard and `collector.py`; `data` contains persistent `history.json`.
- GitHub Actions workflow: [Collect Polymarket sports history](https://github.com/vholl1997-collab/fanduel-scanner/actions/workflows/collect-history.yml). It supports manual dispatch and a best-effort schedule at minutes 2, 7, 12, etc. **GitHub does not guarantee five-minute execution**; the dashboard labels delayed/stale snapshots.
- On each successful run, the collector validates the API response, retries transient failures, records UTC Unix-second timestamps, and atomically replaces the history file. If collection fails, the previous saved history remains intact and the job fails visibly.
- The collector scans **at most the top 500 active events by 24-hour volume**; it does not cover every Polymarket market or guarantee live/in-play pricing.
- `generated_at` and `last_success_at` are the last successful collection times in Unix seconds UTC. Browser-local timestamps are milliseconds and converted when comparing or exporting.
- `points` are sampled observations, not a complete tick-by-tick record. Existing older records retain their original observation times; gaps cannot be backfilled.
- The dashboard reads `data/history.json` from the public `data` branch. It labels the snapshot RECENT (up to 15 minutes), DELAYED (up to 60 minutes), or STALE (older).
- Run the collector tests with `python -m unittest discover -s tests -v`. They also execute in the collector workflow.
- For a manual smoke test, open the workflow, choose **Run workflow** on `main`, wait for a green check, then inspect the latest commit on the `data` branch and the dashboard's freshness label. Confirm a **schedule**-triggered run appears later before calling unattended monitoring verified.
- GitHub repository history is not a scalable long-term database. For production reliability, migrate to a hosted scheduler and database with monitoring, retention, and backups.
