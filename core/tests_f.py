import datetime as dt

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from . import services
from .models import Booking, Membership, Tournament, TournamentGround
from .tests import FUTURE, PASSWORD, book, make_world

T = dt.time


class TournamentOnTimelineTests(TestCase):
    def setUp(self):
        self.w = make_world('alpha', 'ALP')
        t = Tournament._base_manager.create(
            tenant=self.w['tenant'], tournament_code='TRN-101', name='Summer Cup', organizer='O',
            contact='9876543210', date=FUTURE, start_time=T(10), end_time=T(12), final_amount=0)
        TournamentGround.objects.create(tournament=t, config=self.w['cfg']['8A'])   # uses S1 + S2
        self.t = t

    def segs(self, n):
        return services.get_availability_for_resource(self.w['tenant'], self.w['res'][n], FUTURE)

    def test_tournament_shows_on_the_spaces_it_uses(self):
        for n in (1, 2):
            booked = [s for s in self.segs(n) if s['status'] == 'booked']
            self.assertEqual(len(booked), 1)
            self.assertEqual(booked[0]['tournament'].id, self.t.id)

    def test_tournament_does_not_show_elsewhere(self):
        self.assertTrue(all(s['status'] == 'available' for s in self.segs(3)))

    def test_booking_and_tournament_share_a_timeline(self):
        book(self.w, '6-1', T(14), T(15))
        kinds = [(s['tournament'] is not None, s['booking'] is not None)
                 for s in self.segs(1) if s['status'] == 'booked']
        self.assertEqual(kinds, [(True, False), (False, True)])

    def test_pages_render_with_a_tournament(self):
        self.client.login(username='alpha-owner', password=PASSWORD)
        self.assertContains(self.client.get(reverse('availability'), {'date': FUTURE.isoformat()}), 'Summer Cup')
        self.assertContains(self.client.get(reverse('calendar_view'), {'date': FUTURE.isoformat()}), 'Summer Cup')


class DashboardRoleTests(TestCase):
    def setUp(self):
        self.w = make_world('alpha', 'ALP')

    def page(self, role):
        user = User.objects.create_user(f"{role}-u", password=PASSWORD)
        Membership.objects.create(user=user, tenant=self.w['tenant'], role=role)
        self.client.login(username=user.username, password=PASSWORD)
        return self.client.get(reverse('dashboard'))

    def test_owner_sees_everything(self):
        resp = self.page('owner')
        self.assertContains(resp, 'Revenue')
        self.assertContains(resp, 'Occupancy')

    def test_booking_staff_do_not_see_money(self):
        resp = self.page('booking_staff')
        self.assertContains(resp, 'Occupancy')
        self.assertNotContains(resp, 'Revenue')
        self.assertNotContains(resp, 'Recent Expenses')

    def test_finance_staff_do_not_see_operations(self):
        resp = self.page('finance_staff')
        self.assertContains(resp, 'Revenue')
        self.assertNotContains(resp, 'Occupancy')


class RequestsCardTests(TestCase):
    def test_pending_customer_request_is_counted(self):
        w = make_world('alpha', 'ALP')
        book(w, '6-1', T(10), T(11), status='pending')
        self.client.login(username='alpha-owner', password=PASSWORD)
        resp = self.client.get(reverse('dashboard'))
        self.assertContains(resp, 'Requests waiting')
        self.assertContains(resp, 'Customer requests waiting for you')