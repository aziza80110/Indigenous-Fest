import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from automation.send_travel_emails import can_claim, claim_due, due_events, event_key, event_stage


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.event = {
            "id": "sample-festival-2027",
            "name": "示例祭典",
            "startDate": "2027-06-20",
            "location": "臺東縣某鄉",
            "town": "臺東縣某鄉",
            "sourceUrl": "https://example.org/festival",
            "dateStatus": "confirmed",
            "datePrecision": "exact",
            "active": True,
        }

    def test_planning_window_is_15_to_30_days(self):
        self.assertEqual(event_stage(self.event, date(2027, 5, 21)), "planning")
        self.assertEqual(event_stage(self.event, date(2027, 6, 5)), "planning")
        self.assertIsNone(event_stage(self.event, date(2027, 5, 20)))
        self.assertIsNone(event_stage(self.event, date(2027, 6, 6)))

    def test_weather_update_window_is_1_to_7_days(self):
        self.assertEqual(event_stage(self.event, date(2027, 6, 13)), "weather_update")
        self.assertEqual(event_stage(self.event, date(2027, 6, 19)), "weather_update")
        self.assertIsNone(event_stage(self.event, date(2027, 6, 12)))
        self.assertIsNone(event_stage(self.event, date(2027, 6, 20)))

    def test_requires_active_event_and_official_source(self):
        self.assertIsNone(event_stage({**self.event, "active": False}, date(2027, 6, 5)))
        self.assertIsNone(event_stage({**self.event, "sourceUrl": ""}, date(2027, 6, 5)))
        self.assertIsNone(event_stage({**self.event, "startDate": "TBD"}, date(2027, 6, 5)))

    def test_claim_deduplicates_and_retry_lease(self):
        today = date(2027, 6, 5)
        state = {}
        keys = claim_due([self.event], state, today, "recipient@example.com",
                         datetime(2027, 6, 5, tzinfo=timezone.utc))
        self.assertEqual(len(keys), 1)
        self.assertEqual(claim_due([self.event], state, today, "recipient@example.com",
                                   datetime(2027, 6, 5, 0, 2, tzinfo=timezone.utc)), [])
        self.assertFalse(can_claim(state[keys[0]], datetime(2027, 6, 5, 0, 2, tzinfo=timezone.utc)))
        self.assertTrue(can_claim(state[keys[0]], datetime(2027, 6, 5, 2, tzinfo=timezone.utc)))
        self.assertEqual(event_key(self.event, "planning", "recipient@example.com"), keys[0])

    def test_duplicate_event_ids_only_claim_once(self):
        self.assertEqual(len(due_events([self.event, self.event], date(2027, 6, 5))), 1)


if __name__ == "__main__":
    unittest.main()
