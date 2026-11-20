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
