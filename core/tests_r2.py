import datetime as dt

from django.test import TestCase
from django.urls import reverse

from .models import Booking, Sport
from .tests import FUTURE, PASSWORD, make_world


def mk_sport(world, name, active=True):
    return Sport._base_manager.create(tenant=world['tenant'], name=name, is_active=active)


def post_booking(client, world, key='6-1', sport=None, phone='9876543210'):
    data = {
        'customer_name': 'Tester', 'customer_phone': phone, 'config': str(world['cfg'][key].id),
        'date': FUTURE.isoformat(), 'start_time': '10:00', 'end_time': '11:00',
        'booking_type': 'regular', 'discount_type': 'none', 'cash_paid': '0', 'online_paid': '0',
    }
    if sport is not None:
        data['sport'] = str(sport.id)
    return client.post(reverse('booking_create'), data)


class SportSettingsTests(TestCase):
    def setUp(self):
        self.a = make_world('alpha', 'ALP')
        self.b = make_world('bravo', 'BRA')
        self.client.login(username='alpha-owner', password=PASSWORD)

    def act(self, **data):
        return self.client.post(reverse('sports_action'), data)

    def names(self, world):
        return sorted(Sport._base_manager.filter(tenant=world['tenant']).values_list('name', flat=True))

    def test_add_and_page_loads(self):
        self.act(action='add', name='Football')
        self.assertEqual(self.names(self.a), ['Football'])
        self.assertContains(self.client.get(reverse('settings_sports')), 'Football')

    def test_duplicate_is_refused_ignoring_case_and_spaces(self):
        self.act(action='add', name='Football')
        self.act(action='add', name='football')
        self.act(action='add', name='  FOOTBALL ')
        self.assertEqual(self.names(self.a), ['Football'])

    def test_two_businesses_can_use_the_same_name(self):
        mk_sport(self.b, 'Football')
        self.act(action='add', name='Football')
        self.assertEqual(len(self.names(self.a)), 1)

    def test_rename_cannot_collide(self):
        s1, s2 = mk_sport(self.a, 'Football'), mk_sport(self.a, 'Cricket')
        self.act(action='rename', sport_id=str(s2.id), name='football')
        s2.refresh_from_db()
        self.assertEqual(s2.name, 'Cricket')

    def test_cannot_touch_another_business(self):
        theirs = mk_sport(self.b, 'Tennis')
        self.assertEqual(self.act(action='toggle', sport_id=str(theirs.id)).status_code, 404)
        self.assertEqual(self.act(action='rename', sport_id=str(theirs.id), name='X').status_code, 404)
        theirs.refresh_from_db()
        self.assertTrue(theirs.is_active)
        self.assertEqual(theirs.name, 'Tennis')

    def test_matrix_saves_and_ignores_foreign_sports(self):
        football = mk_sport(self.a, 'Football')
        theirs = mk_sport(self.b, 'Tennis')
        cfg = self.a['cfg']['6-1']
        self.act(action='save_matrix', **{f'cfg_{cfg.id}': [str(football.id)]})
        self.assertEqual(list(Sport._base_manager.filter(configs=cfg)), [football])
        self.act(action='save_matrix', **{f'cfg_{cfg.id}': [str(theirs.id)]})
        self.assertEqual(list(Sport._base_manager.filter(configs=cfg)), [football])   # refused, unchanged


class BookingSportTests(TestCase):
    def setUp(self):
        self.a = make_world('alpha', 'ALP')
        self.b = make_world('bravo', 'BRA')
        self.client.login(username='alpha-owner', password=PASSWORD)

    def count(self):
        return Booking._base_manager.filter(tenant=self.a['tenant']).count()

    def test_no_sports_configured_means_no_sport_needed(self):
        post_booking(self.client, self.a)
        self.assertEqual(self.count(), 1)
        self.assertIsNone(Booking._base_manager.get(tenant=self.a['tenant']).sport)

    def test_page_links_to_sports_setup_when_none(self):
        self.assertContains(self.client.get(reverse('booking_create')), reverse('settings_sports'))

    def test_sport_is_required_once_sports_exist(self):
        mk_sport(self.a, 'Football')
        post_booking(self.client, self.a)
        self.assertEqual(self.count(), 0)

    def test_sport_is_saved(self):
        football = mk_sport(self.a, 'Football')
        post_booking(self.client, self.a, sport=football)
        self.assertEqual(Booking._base_manager.get(tenant=self.a['tenant']).sport, football)

    def test_other_business_sport_is_refused(self):
        mk_sport(self.a, 'Football')
        theirs = mk_sport(self.b, 'Tennis')
        post_booking(self.client, self.a, sport=theirs)
        self.assertEqual(self.count(), 0)

    def test_inactive_sport_is_refused(self):
        mk_sport(self.a, 'Football')
        old = mk_sport(self.a, 'Hockey', active=False)
        post_booking(self.client, self.a, sport=old)
        self.assertEqual(self.count(), 0)

    def test_sport_must_fit_the_ground(self):
        football, cricket = mk_sport(self.a, 'Football'), mk_sport(self.a, 'Cricket')
        cfg = self.a['cfg']['6-1']
        Sport.configs.through.objects.create(bookableconfig_id=cfg.id, sport_id=football.id)
        post_booking(self.client, self.a, key='6-1', sport=cricket)
        self.assertEqual(self.count(), 0)
        post_booking(self.client, self.a, key='6-1', sport=football)
        self.assertEqual(self.count(), 1)

    def test_unrestricted_ground_accepts_any_sport(self):
        mk_sport(self.a, 'Football')
        cricket = mk_sport(self.a, 'Cricket')
        post_booking(self.client, self.a, key='6-2', sport=cricket)
        self.assertEqual(self.count(), 1)

    def test_history_survives_deactivation_and_old_bookings_have_no_sport(self):
        football = mk_sport(self.a, 'Football')
        post_booking(self.client, self.a, sport=football)
        football.is_active = False
        football.save()
        booking = Booking._base_manager.get(tenant=self.a['tenant'])
        self.assertEqual(booking.sport, football)
        resp = self.client.get(reverse('booking_detail', args=[booking.id]))
        self.assertContains(resp, 'Football')
        Booking._base_manager.filter(pk=booking.pk).update(sport=None)       # like a pre-sports booking
        self.assertEqual(self.client.get(reverse('booking_detail', args=[booking.id])).status_code, 200)
        self.assertEqual(self.client.get(reverse('bookings')).status_code, 200)