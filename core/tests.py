import datetime as dt
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from . import services
from .models import (BookableConfig, BookableType, Booking, BookingPayment, ConfigResource,
                     Customer, Membership, PricingRule, Resource, TenantSettings, TurfBusiness)
from .tenancy import tenant_context

T = dt.time
FUTURE = dt.date.today() + dt.timedelta(days=14)
PASSWORD = "pw12345!"
BASE_PRICES = {'6-a-side': Decimal('900'), '8-a-side': Decimal('1600'), '9-a-side': Decimal('2500')}


def make_world(slug, prefix, lights_start=T(18, 0)):
    """A turf laid out like Invictus: 3 sections, 6 bookable options."""
    user = User.objects.create_user(f"{slug}-owner", password=PASSWORD)
    tenant = TurfBusiness.objects.create(name=slug, slug=slug, owner=user, status='active')
    TenantSettings.objects.create(tenant=tenant, lights_start=lights_start, booking_code_prefix=prefix)
    Membership.objects.create(user=user, tenant=tenant, role='owner')

    with tenant_context(tenant):
        res = {i: Resource.objects.create(tenant=tenant, name=f"S{i}", display_order=i) for i in (1, 2, 3)}
        types = {}
        for order, (name, price) in enumerate(BASE_PRICES.items(), start=1):
            types[name] = BookableType.objects.create(tenant=tenant, name=name, display_order=order)
            for day in ('weekday', 'weekend'):
                for lights in (False, True):
                    PricingRule.objects.create(
                        tenant=tenant, bookable_type=types[name], ground_type=name, day_type=day,
                        lights=lights, price_per_hour=price + (100 if lights else 0))

        def make_cfg(name, type_name, resource_numbers, order):
            cfg = BookableConfig.objects.create(
                tenant=tenant, bookable_type=types[type_name], name=name, display_order=order)
            for n in resource_numbers:
                ConfigResource.objects.create(config=cfg, resource=res[n])
            return cfg

        cfgs = {
            '6-1': make_cfg('6-a-side 1', '6-a-side', [1], 1),
            '6-2': make_cfg('6-a-side 2', '6-a-side', [2], 2),
            '6-3': make_cfg('6-a-side 3', '6-a-side', [3], 3),
            '8A': make_cfg('8-a-side A', '8-a-side', [1, 2], 4),
            '8B': make_cfg('8-a-side B', '8-a-side', [2, 3], 5),
            '9': make_cfg('9-a-side', '9-a-side', [1, 2, 3], 6),
        }
    return {'tenant': tenant, 'user': user, 'res': res, 'types': types, 'cfg': cfgs}


def book(world, key, start, end, d=FUTURE, status='confirmed', phone='9000000001', name='Test'):
    tenant = world['tenant']
    with tenant_context(tenant):
        customer, _ = Customer.objects.get_or_create(tenant=tenant, phone=phone, defaults={'name': name})
        return Booking.objects.create(
            tenant=tenant, booking_code=services.next_booking_code(tenant), customer=customer,
            config=world['cfg'][key], date=d, start_time=start, end_time=end,
            duration_minutes=services.calculate_duration_minutes(start, end),
            day_type=services.calculate_day_type(d), price_per_hour=Decimal('900'),
            base_amount=Decimal('900'), final_amount=Decimal('900'), booking_status=status)


def conflict(world, key, start, end, d=FUTURE):
    return services.find_conflicting_booking(world['tenant'], world['cfg'][key], d, start, end)


class ConflictRuleTests(TestCase):
    def setUp(self):
        self.w = make_world('alpha', 'ALP')

    def check(self, booked, expectations):
        book(self.w, booked, T(18), T(19))
        for key, should_conflict in expectations.items():
            got = conflict(self.w, key, T(18), T(19)) is not None
            self.assertEqual(got, should_conflict, f"{booked} booked, trying {key}")

    def test_six_a_side_one(self):
        self.check('6-1', {'6-1': True, '6-2': False, '6-3': False, '8A': True, '8B': False, '9': True})

    def test_six_a_side_two(self):
        self.check('6-2', {'6-1': False, '6-2': True, '6-3': False, '8A': True, '8B': True, '9': True})

    def test_eight_a_side_a(self):
        self.check('8A', {'6-1': True, '6-2': True, '6-3': False, '8A': True, '8B': True, '9': True})

    def test_eight_a_side_b(self):
        self.check('8B', {'6-1': False, '6-2': True, '6-3': True, '8A': True, '8B': True, '9': True})

    def test_nine_a_side_blocks_everything(self):
        self.check('9', {k: True for k in ('6-1', '6-2', '6-3', '8A', '8B', '9')})

    def test_back_to_back_is_allowed(self):
        book(self.w, '6-1', T(10), T(11))
        self.assertIsNone(conflict(self.w, '6-1', T(11), T(12)))
        self.assertIsNone(conflict(self.w, '6-1', T(9), T(10)))

    def test_partial_overlap_is_rejected(self):
        book(self.w, '6-1', T(10), T(11))
        self.assertIsNotNone(conflict(self.w, '6-1', T(10, 30), T(11, 30)))

    def test_one_minute_overlap_is_rejected(self):
        book(self.w, '6-1', T(10), T(12))
        self.assertIsNotNone(conflict(self.w, '6-1', T(11, 59), T(13)))

    def test_cancelled_booking_does_not_block(self):
        book(self.w, '6-1', T(10), T(11), status='cancelled')
        self.assertIsNone(conflict(self.w, '6-1', T(10), T(11)))

    def test_other_dates_do_not_conflict(self):
        book(self.w, '6-1', T(10), T(11))
        self.assertIsNone(conflict(self.w, '6-1', T(10), T(11), d=FUTURE + dt.timedelta(days=1)))

    def test_other_business_is_independent(self):
        other = make_world('bravo', 'BRA')
        book(self.w, '9', T(10), T(11))
        self.assertIsNone(conflict(other, '6-1', T(10), T(11)))

    def test_foreign_config_is_rejected(self):
        other = make_world('bravo', 'BRA')
        with self.assertRaises(services.ValidationError):
            services.find_conflicting_booking(
                self.w['tenant'], other['cfg']['6-1'], FUTURE, T(10), T(11))

    def test_inactive_config_cannot_be_selected(self):
        cfg = self.w['cfg']['6-1']
        BookableConfig._base_manager.filter(pk=cfg.pk).update(is_active=False)
        self.assertIsNone(services.get_config(self.w['tenant'], cfg.id))


class LightsPricingTests(TestCase):
    def setUp(self):
        self.w = make_world('alpha', 'ALP')
        self.no_lights = make_world('bravo', 'BRA', lights_start=None)

    def price(self, world, start, end, type_name='6-a-side'):
        return services.calculate_split_pricing(
            world['tenant'], 'weekday', world['types'][type_name], start, end)

    def test_all_daylight(self):
        p = self.price(self.w, T(15), T(17))
        self.assertEqual((p['daylight_minutes'], p['lights_minutes']), (120, 0))
        self.assertEqual(p['base_amount'], Decimal('1800'))

    def test_ending_exactly_at_threshold_needs_no_lights(self):
        self.assertEqual(self.price(self.w, T(16), T(18))['lights_minutes'], 0)

    def test_crossing_threshold_is_split(self):
        p = self.price(self.w, T(17), T(19))
        self.assertEqual((p['daylight_minutes'], p['lights_minutes']), (60, 60))
        self.assertEqual(p['base_amount'], Decimal('1900'))
        self.assertTrue(p['lights_required'])

    def test_half_hour_of_lights(self):
        p = self.price(self.w, T(16), T(18, 30))
        self.assertEqual((p['daylight_minutes'], p['lights_minutes']), (120, 30))

    def test_fully_under_lights(self):
        p = self.price(self.w, T(19), T(21))
        self.assertEqual((p['daylight_minutes'], p['lights_minutes']), (0, 120))
        self.assertEqual(p['base_amount'], Decimal('2000'))

    def test_turf_without_lights_never_charges_lights(self):
        p = self.price(self.no_lights, T(19), T(21))
        self.assertEqual(p['lights_minutes'], 0)
        self.assertEqual(p['base_amount'], Decimal('1800'))

    def test_prices_are_per_business(self):
        PricingRule._base_manager.filter(
            tenant=self.w['tenant'], bookable_type=self.w['types']['6-a-side'], lights=False
        ).update(price_per_hour=Decimal('5000'))
        self.assertEqual(self.price(self.w, T(10), T(11))['base_amount'], Decimal('5000'))
        self.assertEqual(self.price(self.no_lights, T(10), T(11))['base_amount'], Decimal('900'))

    def test_missing_rule_raises(self):
        PricingRule._base_manager.filter(
            tenant=self.w['tenant'], bookable_type=self.w['types']['9-a-side']).delete()
        with self.assertRaises(services.PricingError):
            self.price(self.w, T(10), T(11), '9-a-side')


class TenantIsolationTests(TestCase):
    def setUp(self):
        self.a = make_world('alpha', 'ALP')
        self.b = make_world('bravo', 'BRA')
        self.booking_a = book(self.a, '6-1', T(10), T(11), name='Alice-A')
        self.booking_b = book(self.b, '6-1', T(10), T(11), phone='9000000002', name='Bob-B')
        for booking, world in ((self.booking_a, self.a), (self.booking_b, self.b)):
            with tenant_context(world['tenant']):
                BookingPayment.objects.create(
                    booking=booking, amount=Decimal('500'), payment_mode='cash', payment_type='advance')
        self.client.login(username='alpha-owner', password=PASSWORD)

    def test_booking_list_shows_only_own(self):
        resp = self.client.get(reverse('bookings'))
        self.assertContains(resp, 'ALP-1001')
        self.assertNotContains(resp, 'BRA-1001')

    def test_other_booking_detail_is_404(self):
        self.assertEqual(self.client.get(reverse('booking_detail', args=[self.booking_b.id])).status_code, 404)

    def test_cannot_cancel_other_booking(self):
        resp = self.client.post(reverse('booking_cancel', args=[self.booking_b.id]),
                                {'reason': 'x', 'refund_amount': '0', 'refund_mode': 'cash'})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(Booking._base_manager.get(pk=self.booking_b.pk).booking_status, 'confirmed')

    def test_cannot_record_payment_on_other_booking(self):
        before = BookingPayment._base_manager.filter(booking=self.booking_b).count()
        resp = self.client.post(reverse('record_payment', args=[self.booking_b.id]),
                                {'cash_amount': '100', 'online_amount': '0'})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(BookingPayment._base_manager.filter(booking=self.booking_b).count(), before)

    def test_other_customer_detail_is_404(self):
        self.assertEqual(
            self.client.get(reverse('customer_detail', args=[self.booking_b.customer_id])).status_code, 404)

    def test_payments_page_hides_other_business(self):
        resp = self.client.get(reverse('payments'))
        self.assertContains(resp, 'ALP-1001')
        self.assertNotContains(resp, 'BRA-1001')

    def test_csv_export_hides_other_business(self):
        content = self.client.get(reverse('export_bookings_csv')).content.decode()
        self.assertIn('ALP-1001', content)
        self.assertNotIn('BRA-1001', content)

    def test_cannot_toggle_other_business_price_rule(self):
        rule = PricingRule._base_manager.filter(tenant=self.b['tenant']).first()
        self.assertEqual(self.client.post(reverse('settings_pricing_toggle', args=[rule.id])).status_code, 404)
        self.assertTrue(PricingRule._base_manager.get(pk=rule.pk).is_active)

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()
        resp = self.client.get(reverse('bookings'))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp['Location'].startswith('/login/'))

    def test_user_without_a_business_gets_403(self):
        User.objects.create_user('loner', password=PASSWORD)
        self.client.logout()
        self.client.login(username='loner', password=PASSWORD)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 403)

    def test_default_manager_fails_closed(self):
        self.assertEqual(Booking.objects.count(), 0)
        with tenant_context(self.a['tenant']):
            self.assertEqual(Booking.objects.count(), 1)