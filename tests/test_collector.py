import unittest
from collector import as_list, collect, is_sports, sport_name


def sample_event(price="0.99"):
    return {
        "title": "NBA: Test Match", "tags": [{"label": "NBA"}],
        "markets": [{
            "id": "m1", "active": True, "closed": False,
            "question": "Will the home team win?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": f'["{price}","0.01"]',
        }],
    }


class CollectorTests(unittest.TestCase):
    def test_sports_filter(self):
        self.assertTrue(is_sports(sample_event()))
        self.assertFalse(is_sports({"title": "Presidential election", "tags": []}))

    def test_sport_category_and_previous_snapshot(self):
        event = sample_event()
        self.assertEqual(sport_name(event), "Basketball")
        data = {"markets": {}}
        collect(data, [event], 1000)
        collect(data, [sample_event("0.70")], 1300)
        record = data["markets"]["m1:0"]
        self.assertEqual(record["sport"], "Basketball")
        self.assertEqual(record["previous_probability"], 0.99)
        self.assertEqual(record["previous_seen"], 1000)

    def test_list_parsing(self):
        self.assertEqual(as_list('["Yes","No"]'), ["Yes", "No"])
        self.assertEqual(as_list("invalid"), [])

    def test_thresholds_and_peak(self):
        data = {"markets": {}}
        collect(data, [sample_event()], 1000)
        record = data["markets"]["m1:0"]
        self.assertEqual(record["peak"], 0.99)
        self.assertEqual(record["thresholds"]["99"], 1000)
        self.assertEqual(record["points"], [{"t": 1000, "p": 0.99}])
        collect(data, [sample_event("0.70")], 1300)
        self.assertEqual(record["peak"], 0.99)
        self.assertEqual(record["probability"], 0.70)
        self.assertEqual(len(record["points"]), 2)
        self.assertEqual(data["timestamp_unit"], "unix_seconds_utc")

    def test_empty_scan_rejected(self):
        with self.assertRaises(ValueError):
            collect({"markets": {}}, [{"title": "Election", "markets": []}], 1000)

    def test_bad_probability_rejected(self):
        event = sample_event()
        event["markets"][0]["outcomePrices"] = '["NaN","Infinity"]'
        with self.assertRaises(ValueError):
            collect({"markets": {}}, [event], 1000)


if __name__ == "__main__":
    unittest.main()
