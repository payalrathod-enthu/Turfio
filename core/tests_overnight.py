import datetime as dt

from django.test import TestCase
from django.urls import reverse

from . import services
from .forms_public import SlotForm
from .models import TenantDayHours, TenantSettings
from .tests import FUTURE, PASSWORD, book, make_world
from .tests_setup import make_blank, rules_data

T = dt.time


def next_weekday(n):
    d = FUTURE
    while d.weekday() != n:
        d += dt.timedelta(days=1)
    return d


class OvernightBase(TestCase):
    def setUp(self):
        self.w = make_world('alpha', 'ALP')
        self.t = self.w['tenant']
        services.get_week(self.t)
        # Friday: open 6 PM, close 1 AM the next morning
        TenantDayHours.objects.filter(tenant=self.t, weekday=4).update(
            open_time=T(18), close_time=T(1), closes_next_day=True)
        self.fri = next_weekday(4)
        self.sat = self.fri + dt.timedelta(days=1)

    def conflict(self, key, d, start, end):
        return services.find_conflicting_booking(self.t, self.w['cfg'][key], d, start, end)


class WindowAndRulesTests(OvernightBase):
    def test_window_runs_past_midnight(self):
        self.assertEqual(services.day_window(self.t, self.fri), (1080, 1500))

    def test_duration_wraps_midnight(self):
        self.assertEqual(services.calculate_duration_minutes(T(23), T(1)), 120)
        with self.assertRaises(services.ValidationError):
            services.calculate_duration_minutes(T(10), T(10))

    def test_booking_across_midnight_is_allowed(self):
        services.validate_booking_rules(self.t, self.fri, T(23), T(1))

    def test_booking_past_closing_is_refused(self):
        with self.assertRaises(services.ValidationError):
            services.validate_booking_rules(self.t, self.fri, T(23), T(2))

    def test_after_midnight_start_belongs_to_the_previous_evening(self):
        services.validate_booking_rules(self.t, self.sat, T(0, 30), T(1))
        with self.assertRaises(services.ValidationError):
            services.validate_booking_rules(self.t, self.sat, T(1), T(2))

    def test_business_date(self):
        self.assertEqual(services.business_date(self.t, self.sat, T(0, 30), T(1)), self.fri)
        self.assertEqual(services.business_date(self.t, self.sat, T(10), T(11)), self.sat)


class ConflictTests(OvernightBase):
    def test_overnight_booking_blocks_the_early_hours_of_the_next_date(self):
        book(self.w, '6-1', T(23), T(1), d=self.fri)
        self.assertIsNotNone(self.conflict('6-1', self.sat, T(0, 30), T(1, 30)))
        self.assertIsNone(self.conflict('6-1', self.sat, T(1), T(2)))            # back to back
        self.assertIsNone(self.conflict('6-2', self.sat, T(0, 30), T(1, 30)))    # other ground

    def test_conflict_is_found_from_the_other_side(self):
        book(self.w, '6-1', T(0, 30), T(1, 30), d=self.sat)
        self.assertIsNotNone(self.conflict('6-1', self.fri, T(23), T(1)))

    def test_shared_ground_blocks_across_midnight(self):
        book(self.w, '9', T(23), T(1), d=self.fri)
        self.assertIsNotNone(self.conflict('6-3', self.sat, T(0), T(0, 30)))


class TimelineTests(OvernightBase):
    def segs(self, d):
        return services.get_availability_for_resource(self.t, self.w['res'][1], d)

    def test_timeline_runs_to_closing_and_shows_the_tail(self):
        book(self.w, '6-1', T(23), T(1), d=self.fri)
        segs = self.segs(self.fri)
        self.assertEqual(segs[0]['start_str'], '18:00')
        booked = [s for s in segs if s['status'] == 'booked']
        self.assertEqual((booked[0]['start_str'], booked[0]['end_str']), ('23:00', '01:00'))

    def test_available_slot_after_midnight_links_to_the_next_date(self):
        segs = self.segs(self.fri)
        self.assertEqual(segs[0]['date_str'], self.fri.isoformat())

    def test_after_midnight_booking_shows_on_the_evening_it_belongs_to(self):
        book(self.w, '6-1', T(0, 30), T(1, 30), d=self.sat)
        self.assertTrue(any(s['status'] == 'booked' for s in self.segs(self.fri)))
        self.assertTrue(all(s['status'] == 'available' for s in self.segs(self.sat)))


class LightsTests(OvernightBase):
    def test_lights_cover_the_night(self):
        t6 = self.w['types']['6-a-side']
        p = services.calculate_split_pricing(self.t, 'weekday', t6, T(23), T(1), self.fri)
        self.assertEqual((p['daylight_minutes'], p['lights_minutes']), (0, 120))
        p = services.calculate_split_pricing(self.t, 'weekday', t6, T(0, 30), T(1), self.sat)
        self.assertEqual((p['daylight_minutes'], p['lights_minutes']), (0, 30))

    def test_lights_time_can_be_changed(self):
        TenantSettings.objects.filter(tenant=self.t).update(lights_start=T(19))
        p = services.calculate_split_pricing(self.t, 'weekday', self.w['types']['6-a-side'], T(18), T(20), self.fri)
        self.assertEqual((p['daylight_minutes'], p['lights_minutes']), (60, 60))


class FormTests(TestCase):
    def slot(self, w):
        return {'bookable_type': str(w['types']['6-a-side'].id), 'date': FUTURE.isoformat(),
                'start_time': '23:00', 'end_time': '01:00'}

    def test_public_form_rejects_wrapping_times_when_turf_closes_by_midnight(self):
        w = make_world('alpha', 'ALP')
        self.assertFalse(SlotForm(self.slot(w), tenant=w['tenant']).is_valid())

    def test_public_form_accepts_them_when_turf_runs_past_midnight(self):
        w = make_world('alpha', 'ALP')
        services.get_week(w['tenant'])
        TenantDayHours.objects.filter(tenant=w['tenant'], weekday=4).update(
            open_time=T(18), close_time=T(1), closes_next_day=True)
        self.assertTrue(SlotForm(self.slot(w), tenant=w['tenant']).is_valid())

    def test_hours_page_saves_after_midnight_closing_but_refuses_typos(self):
        tenant, _ = make_blank('nets')
        self.client.login(username='nets-owner', password=PASSWORD)
        self.client.post(reverse('setup_hours'), rules_data(day_4_start='18:00', day_4_end='08:00'))
        self.assertEqual(TenantSettings.objects.get(tenant=tenant).booking_code_prefix, 'BK')   # refused
        self.client.post(reverse('setup_hours'), rules_data(day_4_start='06:00', day_4_end='01:00'))
        row = TenantDayHours.objects.get(tenant=tenant, weekday=4)
        self.assertTrue(row.closes_next_day)
        self.assertEqual(row.close_time, T(1))
        self.assertEqual(TenantDayHours.objects.get(tenant=tenant, weekday=0).closes_next_day, False)