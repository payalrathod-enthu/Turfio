import datetime as dt

from django.test import TestCase
from django.urls import reverse

from .models import Booking, BookableConfig, Resource, TurfBusiness
from .tests import PASSWORD, make_world
from .tests_setup import make_blank

FUTURE = dt.date.today() + dt.timedelta(days=14)


def slot(world, start='10:00', end='11:00'):
    return {'bookable_type': str(world['types']['6-a-side'].id), 'date': FUTURE.isoformat(),
            'start_time': start, 'end_time': end}


class PublicChoiceTests(TestCase):
    def setUp(self):
        self.w = make_world('alpha', 'ALP')
        self.url = reverse('public_book', args=['alpha'])

    def post(self, config='', phone='9876543210'):
        data = slot(self.w)
        data.update({'name': 'Cust', 'phone': phone, 'config': config})
        return self.client.post(self.url, data)

    def pending(self):
        return Booking._base_manager.filter(tenant=self.w['tenant'], booking_status='pending')

    def test_customer_can_pick_a_specific_option(self):
        self.post(config=str(self.w['cfg']['6-3'].id))
        self.assertEqual(self.pending().get().config_id, self.w['cfg']['6-3'].id)

    def test_picking_a_taken_option_creates_nothing(self):
        self.post(config=str(self.w['cfg']['6-3'].id))
        resp = self.post(config=str(self.w['cfg']['6-3'].id), phone='9876543299')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.pending().count(), 1)

    def test_any_option_still_works(self):
        self.post()
        self.assertEqual(self.pending().count(), 1)

    def test_other_business_option_cannot_be_forced(self):
        other = make_world('bravo', 'BRA')
        resp = self.post(config=str(other['cfg']['6-1'].id))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Booking._base_manager.count(), 0)

    def test_book_page_offers_the_choices(self):
        resp = self.client.get(self.url, slot(self.w))
        self.assertContains(resp, 'Which one would you like?')
        self.assertContains(resp, '6-a-side 2')


class SetupPagesTests(TestCase):
    def setUp(self):
        self.w = make_world('alpha', 'ALP')
        self.client.login(username='alpha-owner', password=PASSWORD)

    def test_prices_page_wording(self):
        self.assertContains(self.client.get(reverse('setup_pricing')), 'How much do you charge?')

    def test_review_page_has_change_links(self):
        resp = self.client.get(reverse('setup_review'))
        self.assertContains(resp, 'When you')
        self.assertContains(resp, 'Change')

    def test_public_page_shows_weekly_hours(self):
        self.client.logout()
        self.assertContains(self.client.get(reverse('public_home', args=['alpha'])), 'Opening hours')


class LayoutChangeTests(TestCase):
    def setUp(self):
        self.tenant, _ = make_blank('nets')
        self.client.login(username='nets-owner', password=PASSWORD)
        self.client.post(reverse('setup_layout'), {'layout': 'separate'})
        self.client.post(reverse('setup_grounds_action'),
                         {'action': 'create_separate', 'type_name': 'Net', 'names': ['A', 'B']})

    def layout(self):
        return TurfBusiness._base_manager.get(pk=self.tenant.pk).layout_type

    def test_change_needs_confirmation_and_deletes_nothing(self):
        resp = self.client.post(reverse('setup_layout'), {'layout': 'whole'})
        self.assertContains(resp, 'kept exactly as they are')
        self.assertEqual(self.layout(), 'separate')
        self.client.post(reverse('setup_layout'), {'layout': 'whole', 'confirm': '1'})
        self.assertEqual(self.layout(), 'whole')
        self.assertEqual(Resource._base_manager.filter(tenant=self.tenant).count(), 2)
        self.assertEqual(BookableConfig._base_manager.filter(tenant=self.tenant).count(), 2)