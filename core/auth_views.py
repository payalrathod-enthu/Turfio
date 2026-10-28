import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.models import User
from django.core import signing
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify

from . import ratelimit
from .forms_auth import InviteForm, ResendVerificationForm, SignupForm
from .models import RESERVED_SLUGS, Membership, TenantSettings, TurfBusiness
from .roles import ASSIGNABLE_ROLES, OWNER_ONLY
from .tenancy import require_roles, tenant_view

VERIFY_SALT = 'turfio.email-verify'
INVITE_SALT = 'turfio.staff-invite'
VERIFY_MAX_AGE = 3 * 24 * 3600
INVITE_MAX_AGE = 7 * 24 * 3600


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _unique_slug(name):
    base = (slugify(name)[:50] or 'turf').strip('-') or 'turf'
    if base in RESERVED_SLUGS:
        base = f"{base}-turf"
    slug, n = base, 2
    while TurfBusiness.objects.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


def _code_prefix(name):
    words = re.findall(r'[A-Za-z0-9]+', name)
    initials = ''.join(w[0] for w in words).upper()
    if len(initials) < 2:
        initials = (words[0][:3].upper() if words else 'BK')
    return initials[:4]


def _send(subject, body, to_email):
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to_email], fail_silently=False)


def _send_verification(request, user):
    token = signing.dumps({'uid': str(user.pk)}, salt=VERIFY_SALT)
    link = request.build_absolute_uri(reverse('verify_email', args=[token]))
    _send("Confirm your Turfio account",
          f"Hi {user.first_name},\n\nConfirm your email to activate your Turfio account:\n{link}\n\n"
          f"This link works for 3 days. If you didn't sign up, ignore this email.\n\n— Turfio",
          user.email)


def _send_invite(request, user, tenant):
    token = signing.dumps({'uid': str(user.pk)}, salt=INVITE_SALT)
    link = request.build_absolute_uri(reverse('accept_invite', args=[token]))
    _send(f"You're invited to {tenant.name} on Turfio",
          f"Hi {user.first_name},\n\n{tenant.name} added you to its Turfio team.\n"
          f"Set your password here:\n{link}\n\nThis link works for 7 days.\n\n— Turfio",
          user.email)


# ---------------------------------------------------------------------------
# sign-up and verification
# ---------------------------------------------------------------------------

def signup(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == "POST":
        if not ratelimit.allow(f"signup:{ratelimit.client_ip(request)}", 5, 3600):
            return render(request, "auth/signup.html", {
                "form": SignupForm(),
                "throttled": True}, status=429)
        form = SignupForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        username=cd['email'], email=cd['email'], password=cd['password1'],
                        first_name=cd['owner_name'], is_active=False)
                    tenant = TurfBusiness.objects.create(
                        name=cd['business_name'], slug=_unique_slug(cd['business_name']),
                        owner=user, contact_email=cd['email'], contact_phone=cd['phone'],
                        status='onboarding')
                    TenantSettings.objects.create(
                        tenant=tenant, booking_code_prefix=_code_prefix(cd['business_name']))
                    Membership.objects.create(user=user, tenant=tenant, role='owner')
            except IntegrityError:
                form.add_error(None, "Something went wrong creating your account. Please try again.")
            else:
                try:
                    _send_verification(request, user)
                except Exception:
                    messages.warning(request, "Account created, but the email could not be sent. "
                                              "Use 'Resend verification email' on the sign-in page.")
                request.session['signup_email'] = user.email
                return redirect('check_email')
    else:
        form = SignupForm()
    return render(request, "auth/signup.html", {"form": form})


def check_email(request):
    return render(request, "auth/check_email.html", {"email": request.session.get('signup_email', '')})


def verify_email(request, token):
    try:
        data = signing.loads(token, salt=VERIFY_SALT, max_age=VERIFY_MAX_AGE)
        user = User.objects.get(pk=data['uid'])
    except (signing.BadSignature, User.DoesNotExist, KeyError, ValueError):
        messages.error(request, "That confirmation link is invalid or has expired. Request a new one below.")
        return redirect('resend_verification')

    if user.is_active:
        messages.info(request, "Your email is already confirmed. You can sign in.")
    else:
        user.is_active = True
        user.save(update_fields=['is_active'])
        messages.success(request, "Email confirmed. You can sign in now.")
    return redirect('login_view')


def resend_verification(request):
    form = ResendVerificationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data['email'].strip().lower()
        if ratelimit.allow(f"resend:{ratelimit.client_ip(request)}:{email}", 3, 3600):
            user = User.objects.filter(username__iexact=email).first()
            # Only self-signed-up, unverified accounts. Invited staff use their invite link.
            if user and not user.is_active and user.has_usable_password():
                try:
                    _send_verification(request, user)
                except Exception:
                    pass
        messages.success(request, "If that email has an unconfirmed account, we've sent a new link.")
        return redirect('login_view')
    return render(request, "auth/resend_verification.html", {"form": form})


# ---------------------------------------------------------------------------
# staff invites
# ---------------------------------------------------------------------------

def accept_invite(request, token):
    try:
        data = signing.loads(token, salt=INVITE_SALT, max_age=INVITE_MAX_AGE)
        user = User.objects.get(pk=data['uid'])
    except (signing.BadSignature, User.DoesNotExist, KeyError, ValueError):
        messages.error(request, "That invitation link is invalid or has expired. Ask the owner to resend it.")
        return redirect('login_view')

    if user.has_usable_password():
        messages.info(request, "This invitation has already been used. Sign in instead.")
        return redirect('login_view')
    if not Membership.objects.filter(user=user, is_active=True).exists():
        messages.error(request, "This invitation is no longer active.")
        return redirect('login_view')

    form = SetPasswordForm(user, request.POST or None)
    for field in form.fields.values():
        field.widget.attrs.setdefault('class', 'form-control')
    if request.method == "POST" and form.is_valid():
        form.save()
        user.is_active = True
        user.save(update_fields=['is_active'])
        messages.success(request, "Password set. You can sign in now.")
        return redirect('login_view')
    return render(request, "auth/accept_invite.html", {"form": form, "invitee": user})


# ---------------------------------------------------------------------------
# team management (owner only)
# ---------------------------------------------------------------------------

@tenant_view
@require_roles(*OWNER_ONLY)
def team(request):
    tenant = request.tenant
    if request.method == "POST":
        form = InviteForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            with transaction.atomic():
                user = User.objects.create_user(
                    username=cd['email'], email=cd['email'], first_name=cd['name'], is_active=False)
                Membership.objects.create(user=user, tenant=tenant, role=cd['role'])
            try:
                _send_invite(request, user, tenant)
                messages.success(request, f"Invitation sent to {user.email}.")
            except Exception:
                messages.warning(request, "Team member added, but the email could not be sent. Use Resend.")
            return redirect('team')
    else:
        form = InviteForm()

    members = Membership.objects.filter(tenant=tenant).select_related('user').order_by('created_at')
    return render(request, "auth/team.html", {
        "active": "team", "form": form, "members": members,
        "assignable_roles": [(r, dict(Membership.ROLE_CHOICES)[r]) for r in ASSIGNABLE_ROLES],
    })


@tenant_view
@require_roles(*OWNER_ONLY)
def team_update(request, member_id):
    member = get_object_or_404(Membership, id=member_id, tenant=request.tenant)
    if request.method != "POST":
        return redirect('team')
    if member.role == 'owner' or member.user_id == request.user.id:
        messages.error(request, "The owner's access can't be changed here.")
        return redirect('team')

    action = request.POST.get('action')
    if action == 'role' and request.POST.get('role') in ASSIGNABLE_ROLES:
        member.role = request.POST['role']
        member.save(update_fields=['role'])
        messages.success(request, "Role updated.")
    elif action == 'deactivate':
        member.is_active = False
        member.save(update_fields=['is_active'])
        messages.success(request, "Access removed.")
    elif action == 'activate':
        member.is_active = True
        member.save(update_fields=['is_active'])
        messages.success(request, "Access restored.")
    return redirect('team')


@tenant_view
@require_roles(*OWNER_ONLY)
def team_resend(request, member_id):
    member = get_object_or_404(Membership, id=member_id, tenant=request.tenant)
    user = member.user
    if request.method == "POST" and not user.is_active and not user.has_usable_password():
        if ratelimit.allow(f"invite:{user.pk}", 5, 3600):
            try:
                _send_invite(request, user, request.tenant)
                messages.success(request, f"Invitation re-sent to {user.email}.")
            except Exception:
                messages.error(request, "The email could not be sent. Try again shortly.")
        else:
            messages.error(request, "Too many attempts. Try again in an hour.")
    return redirect('team')