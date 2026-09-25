import unittest

from app.domain.constraints import StopRole
from app.services.itinerary_scheduler import TimelineScheduler


class ItinerarySchedulerTest(unittest.TestCase):
    def test_meal_anchor_wait_does_not_depend_on_exact_stop_count(self) -> None:
        schedule = TimelineScheduler().schedule(
            start_minutes=11 * 60,
            roles=(StopRole.LUNCH, StopRole.ACTIVITY, StopRole.DINNER),
            travel_minutes=(5, 5, 5),
            stop_durations=(60, 240, 60),
        )

        self.assertEqual(schedule.stops[0].start_minutes, 11 * 60 + 5)
        self.assertEqual(schedule.stops[1].end_minutes, 16 * 60 + 10)
        self.assertEqual(schedule.stops[2].start_minutes, 17 * 60)
        self.assertEqual(schedule.stops[2].wait_minutes, 45)
        self.assertEqual(schedule.total_wait_minutes, 45)
        self.assertEqual(len(schedule.stops), 3)
        self.assertEqual(
            [item.wait_minutes for item in schedule.stops],
            [0, 0, 45],
        )

    def test_return_travel_is_part_of_elapsed_timeline(self) -> None:
        schedule = TimelineScheduler().schedule(
            start_minutes=14 * 60,
            roles=(StopRole.ACTIVITY,),
            travel_minutes=(10,),
            stop_durations=(30,),
            return_travel_minutes=20,
        )

        self.assertEqual(schedule.end_minutes, 14 * 60 + 60)
        self.assertEqual(schedule.elapsed_minutes, 60)
