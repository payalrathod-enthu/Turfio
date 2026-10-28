import datetime as dt

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from . import services
from .models import (BookableConfig, BookableType, Booking, Customer, Membership, PricingRule,
                     Resource, TenantSettings, TurfBusiness, TenantDayHours)
from .tests import PASSWORD, make_world

T = dt.time
FUTURE = dt.date.today() + dt.timedelta(days=14)

PROFILE = {'name': 'Nets', 'slug': 'nets', 'contact_email': 'n@example.com', 'contact_phone': '9876543210',
           'address': '1 Main Road', 'city': 'Pune', 'state': 'MH', 'postal_code': '411001'}
def rules_data(closed=(), **over):
    data = {'slot_interval_minutes': '30', 'min_duration_minutes': '60', 'max_duration_minutes': '240',
            'min_advance_notice_minutes': '60', 'max_advance_days': '90', 'booking_code_prefix': 'NET',
            'lights_mode': 'none'}
    for i in range(7):
        if i not in closed:
            data[f'day_{i}_open'] = 'on'
        data[f'day_{i}_start'] = '06:00'
        data[f'day_{i}_end'] = '23:00'
    data.update(over)
    return data



def make_blank(slug, **fields):
    user = User.objects.create_user(f"{slug}-owner", password=PASSWORD)
    data = dict(name=slug.title(), contact_phone='9876543210', address='1 Main Road', city='Pune')
    data.update(fields)
    tenant = TurfBusiness.objects.create(slug=slug, owner=user, status='onboarding', **data)
    TenantSettings.objects.create(tenant=tenant)
    Membership.objects.create(user=user, tenant=tenant, role='owner')
    return tenant, user


def book_cfg(tenant, cfg, start, end):
    customer = Customer._base_manager.create(tenant=tenant, name='X', phone=f"90000{Booking._base_manager.count():05d}")
    return Booking._base_manager.create(
        tenant=tenant, booking_code=services.next_booking_code(tenant), customer=customer, config=cfg,
        date=FUTURE, start_time=start, end_time=end, duration_minutes=60, day_type='weekday',
        price_per_hour=100, base_amount=100, final_amount=100, booking_status='confirmed')


class WizardCase(TestCase):
    def setUp(self):
        self.tenant, self.user = make_blank('nets')
        self.client.login(username='nets-owner', password=PASSWORD)

    def action(self, **data):
        return self.client.post(reverse('setup_grounds_action'), data)

    def configs(self):
        return list(BookableConfig._base_manager.filter(tenant=self.tenant).order_by('display_order'))

    def spaces(self):
        return list(Resource._base_manager.filter(tenant=self.tenant).order_by('display_order'))


class GroundBuilderTests(WizardCase):
    def test_new_owner_is_sent_to_setup(self):
        self.assertRedirects(self.client.get(reverse('dashboard')), reverse('setup_home'),
                             fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('setup_grounds')).status_code, 200)

    def test_independent_courts_do_not_block_each_other(self):
        self.action(action='preset_independent', type_name='Cricket Net', label='Net', count='4')
        self.assertEqual(len(self.spaces()), 4)
        nets = self.configs()
        self.assertEqual(len(nets), 4)
        book_cfg(self.tenant, nets[0], T(10), T(11))
        self.assertIsNotNone(services.find_conflicting_booking(self.tenant, nets[0], FUTURE, T(10), T(11)))
        self.assertIsNone(services.find_conflicting_booking(self.tenant, nets[1], FUTURE, T(10), T(11)))

    def test_whole_ground_preset(self):
        self.action(action='preset_whole', type_name='Football', space_name='Main ground')
        self.assertEqual((len(self.spaces()), len(self.configs())), (1, 1))

    def test_divisible_ground_with_custom_options(self):
        self.action(action='preset_divisible', label='Court', count='3')
        spaces = self.spaces()
        self.assertEqual((len(spaces), len(self.configs())), (3, 0))
        ids = [str(s.id) for s in spaces]
        self.action(action='add_config', type_name='Single', option_name='', resources=[ids[1]])
        self.action(action='add_config', type_name='Double', option_name='', resources=[ids[0], ids[1]])
        self.action(action='add_config', type_name='Full', option_name='', resources=ids)
        single, double, full = self.configs()
        book_cfg(self.tenant, single, T(10), T(11))
        for blocked in (single, double, full):
            self.assertIsNotNone(services.find_conflicting_booking(self.tenant, blocked, FUTURE, T(10), T(11)))
        free = self.action(action='add_config', type_name='Single', option_name='Edge', resources=[ids[2]])
        edge = self.configs()[-1]
        self.assertIsNone(services.find_conflicting_booking(self.tenant, edge, FUTURE, T(10), T(11)))

    def test_preset_refused_when_layout_exists(self):
        self.action(action='preset_whole', type_name='Football', space_name='Main')
        self.action(action='preset_independent', type_name='Net', label='Net', count='3')
        self.assertEqual(len(self.spaces()), 1)

    def test_duplicate_option_is_rejected(self):
        self.action(action='preset_divisible', label='Court', count='2')
        ids = [str(s.id) for s in self.spaces()]
        self.action(action='add_config', type_name='Pair', option_name='', resources=ids)
        self.action(action='add_config', type_name='Pair', option_name='Again', resources=ids)
        self.assertEqual(len(self.configs()), 1)

    def test_option_needs_a_space(self):
        self.action(action='preset_divisible', label='Court', count='2')
        self.action(action='add_config', type_name='Pair', option_name='', resources=[])
        self.assertEqual(len(self.configs()), 0)

    def test_space_in_use_cannot_be_switched_off(self):
        self.action(action='preset_whole', type_name='Football', space_name='Main')
        space = self.spaces()[0]
        self.action(action='toggle_resource', resource_id=str(space.id))
        space.refresh_from_db()
        self.assertTrue(space.is_active)

    def test_option_with_bookings_cannot_be_deleted(self):
        self.action(action='preset_independent', type_name='Net', label='Net', count='2')
        used, unused = self.configs()
        book_cfg(self.tenant, used, T(10), T(11))
        self.action(action='delete_config', config_id=str(used.id))
        self.action(action='delete_config', config_id=str(unused.id))
        self.assertEqual([c.id for c in self.configs()], [used.id])

    def test_cannot_touch_another_business(self):
        other = make_world('alpha', 'ALP')
        victim = other['res'][1]
        resp = self.action(action='rename_resource', resource_id=str(victim.id), name='Hacked')
        self.assertEqual(resp.status_code, 404)
        victim.refresh_from_db()
        self.assertEqual(victim.name, 'S1')


class WizardFlowTests(WizardCase):
    def prices(self, **overrides):
        t = BookableType._base_manager.get(tenant=self.tenant)
        data = {f"p_{t.id}_weekday_0": '500', f"p_{t.id}_weekend_0": '600'}
        data.update(overrides)
        return data
    
    def test_cannot_go_live_until_ready_then_can(self):
        self.client.post(reverse('setup_review'))
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.status, 'onboarding')

        self.action(action='preset_whole', type_name='Football', space_name='Main')
        self.client.post(reverse('setup_review'))
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.status, 'onboarding')      # prices still missing

        self.client.post(reverse('setup_pricing'), self.prices())
        self.client.post(reverse('setup_review'))
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.status, 'active')
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_bad_prices_save_nothing(self):
        self.action(action='preset_whole', type_name='Football', space_name='Main')
        t = BookableType._base_manager.get(tenant=self.tenant)
        self.client.post(reverse('setup_pricing'), self.prices(**{f"p_{t.id}_weekend_0": '-5'}))
        self.client.post(reverse('setup_pricing'), self.prices(**{f"p_{t.id}_weekend_0": 'abc'}))
        self.client.post(reverse('setup_pricing'), self.prices(**{f"p_{t.id}_weekend_0": 'NaN'}))
        self.assertEqual(PricingRule._base_manager.filter(tenant=self.tenant).count(), 0)

    def test_pricing_works_end_to_end_with_services(self):
        self.action(action='preset_whole', type_name='Football', space_name='Main')
        self.client.post(reverse('setup_pricing'), self.prices())
        t = BookableType._base_manager.get(tenant=self.tenant)
        p = services.calculate_split_pricing(self.tenant, 'weekday', t, T(10), T(12))
        self.assertEqual(p['base_amount'], 1000)

    def test_turning_lights_on_requires_light_prices(self):
        self.action(action='preset_whole', type_name='Football', space_name='Main')
        self.client.post(reverse('setup_pricing'), self.prices())
        data = dict(HOURS, lights_enabled='on', lights_start='18:00', lights_end='')
        self.client.post(reverse('setup_hours'), data)
        self.client.post(reverse('setup_profile'), PROFILE)
        self.client.post(reverse('setup_review'))
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.status, 'onboarding')
        t = BookableType._base_manager.get(tenant=self.tenant)
        self.client.post(reverse('setup_pricing'), self.prices(
            **{f"p_{t.id}_weekday_1": '700', f"p_{t.id}_weekend_1": '800'}))
        self.client.post(reverse('setup_review'))
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.status, 'active')

    def test_hours_validation(self):
        bad = [dict(HOURS, operating_start='22:00', operating_end='08:00'),
               dict(HOURS, lights_enabled='on', lights_start='03:00'),
               dict(HOURS, booking_code_prefix='no way!'),
               dict(HOURS, min_duration_minutes='90', max_duration_minutes='60')]
        for data in bad:
            self.client.post(reverse('setup_hours'), data)
        self.assertEqual(TenantSettings.objects.get(tenant=self.tenant).booking_code_prefix, 'BK')

    def test_hours_saved(self):
        self.client.post(reverse('setup_hours'), dict(HOURS, operating_start='07:00'))
        s = TenantSettings.objects.get(tenant=self.tenant)
        self.assertEqual((s.operating_start, s.booking_code_prefix), (T(7), 'NET'))

    def test_profile_slug_rules(self):
        make_world('taken', 'TKN')
        for slug in ('login', 'taken', 'ab'):
            self.client.post(reverse('setup_profile'), dict(PROFILE, slug=slug))
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.slug, 'nets')
        self.client.post(reverse('setup_profile'), dict(PROFILE, slug='Green-Nets'))
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.slug, 'green-nets')

    def test_slug_is_locked_after_going_live(self):
        TurfBusiness.objects.filter(pk=self.tenant.pk).update(status='active')
        self.client.post(reverse('setup_profile'), dict(PROFILE, slug='changed'))
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.slug, 'nets')

    def test_turning_lights_on_requires_light_prices(self):
        self.action(action='preset_whole', type_name='Football', space_name='Main')
        self.client.post(reverse('setup_pricing'), self.prices())
        self.client.post(reverse('setup_hours'), rules_data(lights_mode='from', lights_start='18:00'))
        self.client.post(reverse('setup_profile'), PROFILE)
        self.client.post(reverse('setup_review'))
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.status, 'onboarding')
        t = BookableType._base_manager.get(tenant=self.tenant)
        self.client.post(reverse('setup_pricing'), self.prices(
            **{f"p_{t.id}_weekday_1": '700', f"p_{t.id}_weekend_1": '800'}))
        self.client.post(reverse('setup_review'))
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.status, 'active')

    def test_hours_validation(self):
        bad = [rules_data(day_0_start='22:00', day_0_end='08:00'),
               rules_data(lights_mode='from', lights_start=''),
               rules_data(booking_code_prefix='no way!'),
               rules_data(min_duration_minutes='240', max_duration_minutes='60'),
               rules_data(closed=range(7))]
        for data in bad:
            self.client.post(reverse('setup_hours'), data)
        self.assertEqual(TenantSettings.objects.get(tenant=self.tenant).booking_code_prefix, 'BK')

    def test_hours_saved(self):
        self.client.post(reverse('setup_hours'), rules_data(day_0_start='07:00'))
        s = TenantSettings.objects.get(tenant=self.tenant)
        self.assertEqual(s.booking_code_prefix, 'NET')
        self.assertEqual(TenantDayHours.objects.get(tenant=self.tenant, weekday=0).open_time, T(7))