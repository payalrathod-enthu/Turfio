import datetime as dt
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from . import onboarding, services
from .models import (BookableConfig, BookableType, Resource, TenantDayHours, TenantSettings,
                     TurfBusiness)
from .tests import PASSWORD, make_world
from .tests_setup import book_cfg, make_blank, rules_data

T = dt.time
FUTURE = dt.date.today() + dt.timedelta(days=14)


class LayoutBase(TestCase):
    def setUp(self):
        self.tenant, self.user = make_blank('nets')
        self.client.login(username='nets-owner', password=PASSWORD)

    def act(self, **data):
        return self.client.post(reverse('setup_grounds_action'), data)

    def spaces(self):
        return Resource._base_manager.filter(tenant=self.tenant).count()

    def configs(self):
        return BookableConfig._base_manager.filter(tenant=self.tenant).count()

    def types(self):
        return BookableType._base_manager.filter(tenant=self.tenant).count()


class LayoutChoiceTests(LayoutBase):
    def test_choice_is_saved_and_bad_choice_ignored(self):
        self.client.post(reverse('setup_layout'), {'layout': 'separate'})
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.layout_type, 'separate')
        self.client.post(reverse('setup_layout'), {'layout': 'nonsense'})
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.layout_type, 'separate')

    def test_existing_turf_layout_is_inferred(self):
        self.assertEqual(onboarding.current_layout(make_world('alpha', 'ALP')['tenant']), 'sections')

    def test_pages_load(self):
        for name in ('setup_layout', 'setup_names', 'setup_grounds', 'setup_hours'):
            self.client.post(reverse('setup_layout'), {'layout': 'whole'})
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)


class CreationTests(LayoutBase):
    def test_whole_ground(self):
        self.act(action='create_whole', ground_name='Main Turf', type_name='Football')
        self.assertEqual((self.spaces(), self.configs()), (1, 1))

    def test_separate_grounds_share_a_type(self):
        self.act(action='create_separate', type_name='Net', names=['Net A', 'Net B', 'Net C'])
        self.assertEqual((self.spaces(), self.configs(), self.types()), (3, 3, 1))

    def test_separate_grounds_with_own_prices(self):
        self.act(action='create_separate', per_ground_type='on', names=['Court 1', 'Court 2'])
        self.assertEqual((self.spaces(), self.configs(), self.types()), (2, 2, 2))

    def test_separate_grounds_do_not_block_each_other(self):
        self.act(action='create_separate', type_name='Net', names=['Net A', 'Net B'])
        a, b = list(BookableConfig._base_manager.filter(tenant=self.tenant).order_by('display_order'))
        book_cfg(self.tenant, a, T(10), T(11))
        self.assertIsNotNone(services.find_conflicting_booking(self.tenant, a, FUTURE, T(10), T(11)))
        self.assertIsNone(services.find_conflicting_booking(self.tenant, b, FUTURE, T(10), T(11)))

    def test_duplicate_or_missing_names_are_rejected(self):
        self.act(action='create_separate', type_name='Net', names=['A', 'a'])
        self.act(action='create_separate', type_name='Net', names=[])
        self.act(action='create_separate', type_name='', names=['A', 'B'])
        self.assertEqual(self.spaces(), 0)

    def test_cannot_create_twice(self):
        self.act(action='create_whole', ground_name='Main', type_name='Football')
        self.act(action='create_separate', type_name='Net', names=['A', 'B'])
        self.assertEqual(self.spaces(), 1)

    def test_sections_with_whole_ground(self):
        self.act(action='create_sections', whole_name='Full', names=['A', 'B', 'C'], add_whole='on')
        self.assertEqual((self.spaces(), self.configs()), (3, 1))
        whole = BookableConfig._base_manager.get(tenant=self.tenant)
        self.assertEqual(len(onboarding.config_resource_map(self.tenant)[whole.id]), 3)

    def test_sections_need_at_least_two(self):
        self.act(action='create_sections', whole_name='Full', names=['A'])
        self.assertEqual(self.spaces(), 0)

    def test_sections_singles_and_neighbours_block_correctly(self):
        self.act(action='create_sections', names=['A', 'B', 'C'])
        self.act(action='add_singles', type_name='Small')
        self.act(action='add_neighbours', type_name='Medium', size='2')
        self.assertEqual(self.configs(), 5)
        get = lambda n: BookableConfig._base_manager.get(tenant=self.tenant, name=n)
        small_a, small_c = get('Small — A'), get('Small — C')
        ab, bc = get('Medium — A + B'), get('Medium — B + C')
        book_cfg(self.tenant, ab, T(10), T(11))
        blocked = lambda cfg: services.find_conflicting_booking(self.tenant, cfg, FUTURE, T(10), T(11)) is not None
        self.assertTrue(blocked(small_a))
        self.assertTrue(blocked(bc))        # both use B
        self.assertFalse(blocked(small_c))

    def test_quick_add_twice_is_refused(self):
        self.act(action='create_sections', names=['A', 'B', 'C'])
        self.act(action='add_singles', type_name='Small')
        self.act(action='add_singles', type_name='Small')
        self.assertEqual(self.configs(), 3)


class OnlineAndIsolationTests(LayoutBase):
    def test_staff_only_option_is_not_offered_online(self):
        w = make_world('alpha', 'ALP')
        t6 = w['types']['6-a-side']
        self.assertIsNotNone(services.find_available_config(w['tenant'], t6, FUTURE, T(10), T(11)))
        BookableConfig._base_manager.filter(bookable_type=t6).update(online_bookable=False)
        self.assertIsNone(services.find_available_config(w['tenant'], t6, FUTURE, T(10), T(11)))
        # staff can still book it
        self.assertIsNone(services.find_conflicting_booking(w['tenant'], w['cfg']['6-1'], FUTURE, T(10), T(11)))

    def test_cannot_change_another_business(self):
        other = make_world('alpha', 'ALP')
        cfg = other['cfg']['6-1']
        resp = self.act(action='toggle_online', config_id=str(cfg.id))
        self.assertEqual(resp.status_code, 404)
        cfg.refresh_from_db()
        self.assertTrue(cfg.online_bookable)

    def test_toggle_own_option(self):
        self.act(action='create_whole', ground_name='Main', type_name='Football')
        cfg = BookableConfig._base_manager.get(tenant=self.tenant)
        self.act(action='toggle_online', config_id=str(cfg.id))
        cfg.refresh_from_db()
        self.assertFalse(cfg.online_bookable)


class HoursAndRulesTests(LayoutBase):
    def sunday(self):
        d = FUTURE
        while d.weekday() != 6:
            d += dt.timedelta(days=1)
        return d

    def test_each_day_has_its_own_hours(self):
        self.client.post(reverse('setup_hours'), rules_data(day_5_start='08:00', day_5_end='20:00'))
        sat = TenantDayHours.objects.get(tenant=self.tenant, weekday=5)
        self.assertEqual((sat.open_time, sat.close_time), (T(8), T(20)))
        self.assertEqual(TenantDayHours.objects.get(tenant=self.tenant, weekday=0).close_time, T(23))

    def test_closed_day_refuses_bookings(self):
        self.client.post(reverse('setup_hours'), rules_data(closed=(6,)))
        with self.assertRaises(services.ValidationError) as cm:
            services.validate_booking_rules(self.tenant, self.sunday(), T(10), T(11))
        self.assertIn('closed', str(cm.exception))

    def test_booking_outside_that_days_hours_is_refused(self):
        self.client.post(reverse('setup_hours'), rules_data(day_0_start='09:00', day_0_end='17:00'))
        monday = FUTURE
        while monday.weekday() != 0:
            monday += dt.timedelta(days=1)
        with self.assertRaises(services.ValidationError):
            services.validate_booking_rules(self.tenant, monday, T(17), T(18))
        services.validate_booking_rules(self.tenant, monday, T(10), T(11))

    def test_availability_is_empty_on_a_closed_day(self):
        w = make_world('alpha', 'ALP')
        TenantDayHours.objects.create(tenant=w['tenant'], weekday=6, is_open=False)
        self.assertEqual(services.get_availability_for_resource(w['tenant'], w['res'][1], self.sunday()), [])

    def test_lights_modes(self):
        self.client.post(reverse('setup_hours'), rules_data(lights_mode='from', lights_start='18:00', lights_end='21:00'))
        s = TenantSettings.objects.get(tenant=self.tenant)
        self.assertEqual((s.lights_start, s.lights_end), (T(18), T(21)))
        self.client.post(reverse('setup_hours'), rules_data(lights_mode='always'))
        s.refresh_from_db()
        self.assertEqual((s.lights_start, s.lights_end), (T(0), None))
        self.client.post(reverse('setup_hours'), rules_data(lights_mode='none'))
        s.refresh_from_db()
        self.assertIsNone(s.lights_start)

    def test_lights_window_is_respected_in_pricing(self):
        w = make_world('alpha', 'ALP')
        TenantSettings.objects.filter(tenant=w['tenant']).update(lights_start=T(18), lights_end=T(20))
        p = services.calculate_split_pricing(w['tenant'], 'weekday', w['types']['6-a-side'], T(19), T(21))
        self.assertEqual((p['daylight_minutes'], p['lights_minutes']), (60, 60))


class NoticeRuleTests(TestCase):
    def setUp(self):
        self.w = make_world('alpha', 'ALP')
        self.t = self.w['tenant']
        TenantSettings.objects.filter(tenant=self.t).update(slot_interval_minutes=30, min_advance_notice_minutes=60)
        self.day = dt.date(2026, 10, 26)
        self.now = dt.datetime(2026, 10, 26, 17, 0)

    def check(self, start, end):
        with mock.patch('core.services.local_now', return_value=self.now):
            services.validate_public_request(self.t, self.day, start, end)

    def test_too_soon_is_refused_with_the_earliest_time(self):
        with self.assertRaises(services.ValidationError) as cm:
            self.check(T(17, 30), T(18, 30))
        self.assertIn('06:00 PM', str(cm.exception))

    def test_exactly_at_the_notice_limit_is_allowed(self):
        self.check(T(18, 0), T(19, 0))

    def test_past_time_is_refused(self):
        with self.assertRaises(services.ValidationError) as cm:
            self.check(T(16, 0), T(17, 0))
        self.assertIn('passed', str(cm.exception))

    def test_start_must_follow_the_interval(self):
        with self.assertRaises(services.ValidationError):
            self.check(T(18, 15), T(19, 15))

    def test_no_notice_means_next_slot(self):
        TenantSettings.objects.filter(tenant=self.t).update(min_advance_notice_minutes=0)
        self.check(T(17, 30), T(18, 30))

    def test_booking_window_limit(self):
        TenantSettings.objects.filter(tenant=self.t).update(max_advance_days=7)
        with mock.patch('core.services.local_now', return_value=self.now):
            with self.assertRaises(services.ValidationError):
                services.validate_public_request(self.t, dt.date(2026, 12, 1), T(10), T(11))

    def test_earliest_start_rounds_up_to_the_interval(self):
        now = dt.datetime(2026, 10, 26, 17, 7)
        self.assertEqual(services.earliest_online_start(self.t, now), dt.datetime(2026, 10, 26, 18, 30))

    def test_local_time_survives_a_bad_time_zone(self):
        self.t.timezone = 'Nope/Zone'
        self.assertIsNone(services.local_now(self.t).tzinfo)