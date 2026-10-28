import re
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail, signing
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .models import Membership, TenantSettings, TurfBusiness
from .tests import PASSWORD, make_world

GOOD_PW = "Str0ng-pass-123"


def signup_data(**over):
    data = {'owner_name': 'Ravi Kumar', 'business_name': 'Green Kick Arena', 'email': 'Ravi@Example.com',
            'phone': '9876543210', 'password1': GOOD_PW, 'password2': GOOD_PW}
    data.update(over)
    return data


class SignupFlowTests(TestCase):
    def setUp(self):
        cache.clear()

    def signup(self, **over):
        return self.client.post(reverse('signup'), signup_data(**over))

    def verification_path(self):
        body = mail.outbox[-1].body
        return re.search(r'http://testserver(/verify/\S+)', body).group(1)

    def test_signup_creates_inactive_owner_and_business(self):
        resp = self.signup()
        self.assertRedirects(resp, reverse('check_email'))
        user = User.objects.get(username='ravi@example.com')
        self.assertFalse(user.is_active)
        self.assertFalse(user.is_staff or user.is_superuser)
        tenant = TurfBusiness.objects.get(owner=user)
        self.assertEqual(tenant.slug, 'green-kick-arena')
        self.assertEqual(tenant.status, 'onboarding')
        self.assertTrue(TenantSettings.objects.filter(tenant=tenant).exists())
        self.assertEqual(Membership.objects.get(user=user).role, 'owner')
        self.assertEqual(len(mail.outbox), 1)

    def test_cannot_log_in_until_verified_then_can(self):
        self.signup()
        self.assertFalse(self.client.login(username='ravi@example.com', password=GOOD_PW))
        self.client.get(self.verification_path())
        self.assertTrue(User.objects.get(username='ravi@example.com').is_active)
        self.assertTrue(self.client.login(username='ravi@example.com', password=GOOD_PW))

    def test_new_owner_is_sent_to_setup(self):
        self.signup()
        self.client.get(self.verification_path())
        self.client.login(username='ravi@example.com', password=GOOD_PW)
        resp = self.client.get(reverse('dashboard'))
        self.assertRedirects(resp, reverse('setup_home'), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('setup_profile')).status_code, 200)

    def test_tampered_token_does_not_activate(self):
        self.signup()
        self.client.get('/verify/not-a-real-token/')
        self.assertFalse(User.objects.get(username='ravi@example.com').is_active)

    def test_expired_token_is_rejected(self):
        self.signup()
        path = self.verification_path()
        with mock.patch('core.auth_views.signing.loads', side_effect=signing.SignatureExpired('old')):
            self.client.get(path)
        self.assertFalse(User.objects.get(username='ravi@example.com').is_active)

    def test_duplicate_email_is_rejected(self):
        self.signup()
        resp = self.signup(business_name='Other Turf')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(User.objects.filter(username='ravi@example.com').count(), 1)

    def test_same_business_name_gets_unique_slug(self):
        self.signup()
        self.signup(email='second@example.com')
        slugs = set(TurfBusiness.objects.exclude(slug='the-invictus-field').values_list('slug', flat=True))
        self.assertEqual(slugs, {'green-kick-arena', 'green-kick-arena-2'})

    def test_reserved_name_cannot_take_a_reserved_slug(self):
        self.signup(business_name='Admin')
        self.assertNotEqual(TurfBusiness.objects.exclude(slug='the-invictus-field').get().slug, 'admin')

    def test_weak_or_mismatched_password_is_rejected(self):
        self.signup(password1='123', password2='123')
        self.signup(email='b@example.com', password2='different-Pass-9')
        self.assertEqual(User.objects.count(), 0)

    def test_invalid_phone_is_rejected(self):
        self.signup(phone='12345')
        self.assertEqual(User.objects.count(), 0)

    def test_signup_is_rate_limited(self):
        for i in range(5):
            self.signup(email=f"u{i}@example.com", business_name=f"Turf {i}")
        self.assertEqual(self.signup(email='late@example.com').status_code, 429)

    def test_password_reset_flow(self):
        self.signup()
        self.client.get(self.verification_path())
        mail.outbox.clear()
        self.client.post(reverse('password_reset'), {'email': 'ravi@example.com'})
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(r'http://testserver(/reset/\S+)', mail.outbox[0].body).group(1)
        page = self.client.get(link, follow=True)
        self.client.post(page.request['PATH_INFO'],
                         {'new_password1': 'Another-Pass-456', 'new_password2': 'Another-Pass-456'})
        self.assertTrue(self.client.login(username='ravi@example.com', password='Another-Pass-456'))

    def test_password_reset_for_unknown_email_sends_nothing(self):
        self.client.post(reverse('password_reset'), {'email': 'nobody@example.com'})
        self.assertEqual(len(mail.outbox), 0)


class RolePermissionTests(TestCase):
    ACCESS = {
        'owner': {'bookings': 1, 'booking_create': 1, 'payments': 1, 'expenses': 1, 'analytics': 1,
                  'customers': 1, 'settings_pricing': 1, 'team': 1},
        'manager': {'bookings': 1, 'booking_create': 1, 'payments': 1, 'expenses': 1, 'analytics': 1,
                    'customers': 1, 'settings_pricing': 1, 'team': 0},
        'booking_staff': {'bookings': 1, 'booking_create': 1, 'payments': 1, 'expenses': 0, 'analytics': 0,
                          'customers': 1, 'settings_pricing': 0, 'team': 0},
        'finance_staff': {'bookings': 1, 'booking_create': 0, 'payments': 1, 'expenses': 1, 'analytics': 1,
                          'customers': 0, 'settings_pricing': 0, 'team': 0},
    }

    def setUp(self):
        self.w = make_world('alpha', 'ALP')

    def member(self, role):
        user = User.objects.create_user(f"{role}-user", password=PASSWORD)
        Membership.objects.create(user=user, tenant=self.w['tenant'], role=role)
        return user

    def test_permission_matrix(self):
        for role, pages in self.ACCESS.items():
            self.member(role)
            self.client.logout()
            self.client.login(username=f"{role}-user", password=PASSWORD)
            for name, allowed in pages.items():
                with self.subTest(role=role, page=name):
                    resp = self.client.get(reverse(name))
                    if allowed:
                        self.assertEqual(resp.status_code, 200)
                    else:
                        self.assertEqual(resp.status_code, 302)
                        self.assertEqual(resp['Location'], reverse('dashboard'))

    def test_removed_member_loses_access(self):
        user = self.member('manager')
        Membership.objects.filter(user=user).update(is_active=False)
        self.client.login(username=user.username, password=PASSWORD)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 403)


class TeamManagementTests(TestCase):
    def setUp(self):
        cache.clear()
        self.a = make_world('alpha', 'ALP')
        self.b = make_world('bravo', 'BRA')
        self.client.login(username='alpha-owner', password=PASSWORD)

    def invite(self, email='staff@example.com', role='booking_staff'):
        return self.client.post(reverse('team'), {'name': 'Sam', 'email': email, 'role': role})

    def test_owner_invites_staff_who_accepts_and_logs_in(self):
        self.invite()
        user = User.objects.get(username='staff@example.com')
        self.assertFalse(user.is_active)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(Membership.objects.get(user=user).role, 'booking_staff')
        path = re.search(r'http://testserver(/invite/\S+)', mail.outbox[-1].body).group(1)
        self.client.logout()
        self.client.post(path, {'new_password1': 'Staff-pass-789', 'new_password2': 'Staff-pass-789'})
        self.assertTrue(self.client.login(username='staff@example.com', password='Staff-pass-789'))
        self.assertEqual(self.client.get(reverse('bookings')).status_code, 200)
        self.assertEqual(self.client.get(reverse('expenses')).status_code, 302)

    def test_used_invite_link_cannot_be_reused(self):
        self.invite()
        path = re.search(r'http://testserver(/invite/\S+)', mail.outbox[-1].body).group(1)
        self.client.logout()
        data = {'new_password1': 'Staff-pass-789', 'new_password2': 'Staff-pass-789'}
        self.client.post(path, data)
        self.client.post(path, {'new_password1': 'Hijack-pass-000', 'new_password2': 'Hijack-pass-000'})
        self.assertFalse(self.client.login(username='staff@example.com', password='Hijack-pass-000'))

    def test_existing_account_email_cannot_be_invited(self):
        self.invite(email='bravo-owner')            # username of another business's owner
        User.objects.filter(username='bravo-owner').update(email='bravo@example.com')
        self.invite(email='bravo@example.com')
        self.assertEqual(Membership.objects.filter(tenant=self.a['tenant']).count(), 1)

    def test_cannot_manage_another_businesss_member(self):
        other = Membership.objects.get(tenant=self.b['tenant'])
        resp = self.client.post(reverse('team_update', args=[other.id]), {'action': 'deactivate'})
        self.assertEqual(resp.status_code, 404)
        other.refresh_from_db()
        self.assertTrue(other.is_active)

    def test_owner_access_cannot_be_removed(self):
        owner = Membership.objects.get(tenant=self.a['tenant'], role='owner')
        self.client.post(reverse('team_update', args=[owner.id]), {'action': 'deactivate'})
        owner.refresh_from_db()
        self.assertTrue(owner.is_active)

    def test_role_can_be_changed_and_takes_effect(self):
        self.invite(role='booking_staff')
        member = Membership.objects.get(user__username='staff@example.com')
        self.client.post(reverse('team_update', args=[member.id]), {'action': 'role', 'role': 'finance_staff'})
        member.refresh_from_db()
        self.assertEqual(member.role, 'finance_staff')
        self.client.post(reverse('team_update', args=[member.id]), {'action': 'role', 'role': 'owner'})
        member.refresh_from_db()
        self.assertEqual(member.role, 'finance_staff')   # 'owner' is not assignable