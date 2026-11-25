import unittest
from datetime import datetime
from unittest.mock import Mock

from fanduel_table_tennis import ARIZONA, Scanner, comeback_side


def score(home, away, event_id=1):
    return {
        "event_id": event_id,
        "sport": "tableTennis",
        "details": {
            "home": {"points_per_set": home},
            "away": {"points_per_set": away},
        },
    }


def payload(events, status=200):
    response = Mock(status_code=status)
    response.json.return_value = {"code": 200, "data": {"state": "az", "events": events}}
    return response


def at(hour, minute):
    return int(datetime(2026, 11, 24, hour, minute, tzinfo=ARIZONA).timestamp())


class TableTennisTests(unittest.TestCase):
    def test_finds_either_comeback_side_from_completed_sets(self):
        down_two = [7, 9, 11, 15]
        up_two = [11, 11, 4, 13]
        self.assertEqual(comeback_side(score(down_two, up_two)), "home")
        self.assertEqual(comeback_side(score(up_two + [3], down_two + [0])), "away")

    def test_skips_alternation_partial_finished_and_malformed_sets(self):
        examples = [
            score([11, 8, 11, 4], [3, 11, 7, 11]),
            score([7, 9, 11, 10], [11, 11, 4, 9]),
            score([7, 9, 11, 11, 8], [11, 11, 4, 9, 11]),
            score([7, 9, 11, 11, None], [11, 11, 4, 9, None]),
            score([7, 9, 11, 11], [11, 11, 4]),
        ]
        for event in examples:
            with self.subTest(event=event):
                self.assertIsNone(comeback_side(event))

    def test_discovery_batching_cadence_and_budget(self):
        session = Mock()
        live = {"event_id": 1, "event_type_id": 2593174, "in_play": True, "name": "A @ B"}
        scores = [score([7, 9, 11, 11], [11, 11, 4, 9])]
        session.get.side_effect = lambda url, **kwargs: payload(scores if url.endswith("scores") else [live])
        scanner = Scanner("test-key", session=session)
        start = at(19, 0)
        for now in range(start - 60, start + 26 * 60, 30):
            scanner.tick(now)
        calls = session.get.call_args_list
        discovery = [call for call in calls if call.args[0].endswith("/live")]
        refreshes = [call for call in calls if call.args[0].endswith("/live-scores")]
        self.assertEqual(len(discovery), 5)
        self.assertEqual(len(refreshes), 50)
        self.assertLessEqual((len(discovery) * 2 + len(refreshes)) * 31, 2000)
        self.assertEqual(refreshes[0].kwargs["params"], {"event_id": "1", "state": "az"})
        self.assertEqual(len(scanner.snapshot(start + 60)["recent_comebacks"]), 1)
        self.assertEqual(scanner.snapshot(start + 1500)["scores"], [])

    def test_skip_scores_without_live_matches(self):
        session = Mock()
        session.get.return_value = payload([])
        scanner = Scanner("test-key", session=session)
        start = at(19, 0)
        scanner.tick(start)
        scanner.tick(start + 30)
        session.get.assert_called_once()
        self.assertEqual(scanner.snapshot(start + 30)["status"], "no_live_matches")

    def test_rejected_key_stops_calls(self):
        session = Mock()
        session.get.return_value = payload([], status=401)
        scanner = Scanner("test-key", session=session)
        start = at(19, 0)
        scanner.tick(start)
        scanner.tick(start + 300)
        session.get.assert_called_once()
        self.assertEqual(scanner.snapshot(start + 300)["errors"], {"discovery": "HTTP 401"})
