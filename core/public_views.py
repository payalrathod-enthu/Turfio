import uuid
from datetime import timedelta
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone

from . import onboarding, ratelimit, services
from .forms_public import PublicBookingForm, SlotForm
from .forms_setup import DAY_NAMES
from .models import Booking, Customer, PricingRule, TurfBusiness
from .tenancy import tenant_context


def public_tenant(view):
    """Finds the live business for /t/<slug>/ and runs the view inside its tenant scope."""
    @wraps(view)
    def wrapped(request, slug, *args, **kwargs):
        tenant = TurfBusiness._base_manager.filter(slug=slug.lower(), status='active').first()
        if tenant is None:
            raise Http404
        with tenant_context(tenant):
            return view(request, tenant, *args, **kwargs)
    return wrapped


def _interval(tenant):
    return services.get_settings(tenant).slot_interval_minutes


def _evaluate(tenant, cd, config_id=None):
    """One check used by the availability page and again when a request is saved."""
    try:
        services.validate_public_request(tenant, cd['date'], cd['start_time'], cd['end_time'])
        options = services.find_available_configs(
            tenant, cd['bookable_type'], cd['date'], cd['start_time'], cd['end_time'])
    except services.ValidationError as e:
        return {'available': False, 'error': str(e)}
    if not options:
        return {'available': False, 'error': None}

    config = options[0]
    if config_id:
        chosen = next((o for o in options if str(o.id) == str(config_id)), None)
        if chosen is None:
            return {'available': False, 'error': "That choice was just taken. Please pick another."}
        config = chosen

    day_type = services.calculate_day_type(
        services.business_date(tenant, cd['date'], cd['start_time'], cd['end_time']))
    try:
        pricing = services.calculate_split_pricing(
            tenant, day_type, config.bookable_type, cd['start_time'], cd['end_time'], cd['date'])
    except services.PricingError:
        return {'available': False, 'error': "Online booking isn't available for this option right now."}

    duration = services.calculate_duration_minutes(cd['start_time'], cd['end_time'])
    return {'available': True, 'error': None, 'config': config, 'options': options,
            'pricing': pricing, 'day_type': day_type, 'duration_minutes': duration,
            'duration_label': services.duration_label(duration),
            'date': cd['date'], 'start': cd['start_time'], 'end': cd['end_time']}


def _slot_outcome(tenant, data):
    slot = SlotForm(data, tenant=tenant)
    return _evaluate(tenant, slot.cleaned_data) if slot.is_valid() else None


def _price_table(tenant):
    rules = {(r.bookable_type_id, r.day_type, r.lights): r.price_per_hour
             for r in PricingRule._base_manager.filter(tenant=tenant, is_active=True)}
    has_lights = bool(services.get_settings(tenant).lights_start)
    rows = []
    for t in onboarding.types_in_use(tenant):
        rows.append({
            'type': t,
            'weekday_base': rules.get((t.id, 'weekday', False)),
            'weekday_lights': rules.get((t.id, 'weekday', True)) if has_lights else None,
            'weekend_base': rules.get((t.id, 'weekend', False)),
            'weekend_lights': rules.get((t.id, 'weekend', True)) if has_lights else None,
        })
    return rows


def _week_rows(tenant):
    return [{'day': DAY_NAMES[r.weekday], 'open': r.is_open, 'start': r.open_time,
             'end': r.close_time, 'next_day': r.closes_next_day}
            for r in services.get_week(tenant)]


def _recent_duplicate(tenant, cd):
    return (Booking._base_manager
            .filter(tenant=tenant, customer__phone=cd['phone'], date=cd['date'],
                    start_time=cd['start_time'], end_time=cd['end_time'],
                    booking_status='pending', created_by__isnull=True,
                    created_at__gte=timezone.now() - timedelta(minutes=10),
                    config__bookable_type=cd['bookable_type'])
            .first())


def _create_request(tenant, cd, outcome):
    customer = Customer.objects.filter(tenant=tenant, phone=cd['phone']).first()
    if customer is None:                       # never overwrite an existing customer's name from a public form
        customer = Customer.objects.create(tenant=tenant, name=cd['name'], phone=cd['phone'])

    pricing = outcome['pricing']
    hours = Decimal(outcome['duration_minutes']) / Decimal(60)
    notes = ("Online request. " + (cd.get('notes') or '')).strip()
    return Booking.objects.create(
        tenant=tenant, booking_code=services.next_booking_code(tenant),
        customer=customer, config=outcome['config'],
        date=cd['date'], start_time=cd['start_time'], end_time=cd['end_time'],
        duration_minutes=outcome['duration_minutes'], day_type=outcome['day_type'],
        lights=pricing['lights_required'],
        price_per_hour=(pricing['base_amount'] / hours).quantize(Decimal('0.01')),
        base_amount=pricing['base_amount'], discount_amount=0, additional_charges_amount=0,
        final_amount=pricing['base_amount'], booking_type='regular',
        booking_status='pending', payment_status='unpaid', notes=notes)


# ---------------------------------------------------------------------------
# views
# ---------------------------------------------------------------------------

@public_tenant
def public_home(request, tenant):
    form = SlotForm(request.GET or None, tenant=tenant, interval=_interval(tenant))
    outcome = _evaluate(tenant, form.cleaned_data) if request.GET and form.is_valid() else None
    return render(request, "public/home.html", {
        "biz": tenant, "form": form, "outcome": outcome, "searched": bool(request.GET),
        "prices": _price_table(tenant), "week_rows": _week_rows(tenant)})


@public_tenant
def public_book(request, tenant):
    interval = _interval(tenant)
    outcome, chosen = None, ''

    if request.method == "POST":
        form = PublicBookingForm(request.POST, tenant=tenant, interval=interval)
        chosen = request.POST.get('config', '')
        if form.is_valid():
            cd = form.cleaned_data
            if cd.get('website'):                                   # honeypot
                return redirect('public_home', slug=tenant.slug)
            if not ratelimit.allow(f"pubbook:{tenant.pk}:{ratelimit.client_ip(request)}", 10, 3600):
                messages.error(request, "Too many requests. Please try again later.")
                return render(request, "public/book.html",
                              {"biz": tenant, "form": form, "outcome": None, "chosen": chosen}, status=429)

            booking, error = None, None
            with transaction.atomic():
                services.lock_tenant(tenant)
                booking = _recent_duplicate(tenant, cd)
                if booking is None:
                    result = _evaluate(tenant, cd, cd.get('config'))
                    if result['available']:
                        booking = _create_request(tenant, cd, result)
                    else:
                        error = result.get('error')
            if booking:
                request.session['public_booking'] = str(booking.id)
                return redirect('public_done', slug=tenant.slug)
            messages.error(request, error or "Sorry, that time was just taken. Please choose another.")
        outcome = _slot_outcome(tenant, request.POST)
        if outcome is not None and not outcome.get('available'):
            outcome = None
    else:
        slot = SlotForm(request.GET, tenant=tenant, interval=interval)
        outcome = _evaluate(tenant, slot.cleaned_data) if slot.is_valid() else None
        if not outcome or not outcome['available']:
            messages.error(request, (outcome or {}).get('error')
                           or "That time isn't available. Please choose another.")
            return redirect('public_home', slug=tenant.slug)
        form = PublicBookingForm(initial={
            'bookable_type': slot.cleaned_data['bookable_type'].pk, 'date': slot.cleaned_data['date'],
            'start_time': slot.cleaned_data['start_time'], 'end_time': slot.cleaned_data['end_time']},
            tenant=tenant, interval=interval)

    return render(request, "public/book.html", {"biz": tenant, "form": form, "outcome": outcome, "chosen": chosen})


@public_tenant
def public_done(request, tenant):
    booking = None
    try:
        booking_id = uuid.UUID(request.session.get('public_booking', ''))
    except ValueError:
        booking_id = None
    if booking_id:
        booking = (Booking._base_manager.select_related('config__bookable_type')
                   .filter(tenant=tenant, id=booking_id).first())
    if booking is None:
        return redirect('public_home', slug=tenant.slug)
    return render(request, "public/done.html", {"biz": tenant, "booking": booking})