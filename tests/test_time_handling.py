import unittest
from datetime import datetime, timezone
from resource_platform.time_handling import parse_local_datetime, display_berlin, sqlite_timestamp, to_utc

class TimeHandlingTests(unittest.TestCase):
    def test_normal_berlin_to_utc(self):
        value = parse_local_datetime('2026-09-10T12:00')
        self.assertEqual(value, datetime(2026, 9, 10, 10, tzinfo=timezone.utc))

    def test_midnight_crossing(self):
        self.assertLess(parse_local_datetime('2026-09-10T23:00'), parse_local_datetime('2026-09-11T01:00'))

    def test_nonexistent_dst_rejected(self):
        with self.assertRaises(ValueError): parse_local_datetime('2027-03-28T02:30')

    def test_ambiguous_dst_rejected(self):
        with self.assertRaises(ValueError): parse_local_datetime('2026-10-25T02:30')

    def test_display_round_trip_and_sqlite_format(self):
        value = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
        self.assertEqual(display_berlin(value).hour, 12)
        self.assertEqual(sqlite_timestamp(value), '2026-09-10T10:00:00+00:00')
        self.assertEqual(to_utc(display_berlin(value)), value)

    def test_start_before_end(self):
        self.assertLess(parse_local_datetime('2026-09-10T10:00'), parse_local_datetime('2026-09-10T11:00'))

if __name__ == '__main__': unittest.main()
