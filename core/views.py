import calendar as pycal
import csv
import json
from datetime import date, date as date_cls, datetime as dt, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from .models import Sport

from . import services
from .forms import BookingForm, ExpenseForm, PricingRuleForm, TournamentForm
from .models import (BookableConfig, Booking, BookingAdditionalCharge, BookingDiscount,
                     BookingPayment, Customer, Expense, PricingRule, Resource,
                     Tournament, TournamentGround)
from .roles import ALL_ROLES, FINANCE, MANAGE, OPS
from .tenancy import require_roles, tenant_view

EXPENSE_CATEGORIES = [
    "Electricity & Utilities", "Staff & Salaries", "Office & Technology",
    "Bathroom Accessories", "Refuse", "Maintenance", "Cleaning",
    "Marketing", "Equipment", "Other",
]


def placeholder(request, active, page_title):
    return render(request, "core/placeholder.html", {"active": active, "page_title": page_title})


def _dec(value, default="0"):
    try:
        return Decimal(value) if value not in (None, '') else Decimal(default)
    except InvalidOperation:
        return Decimal(default)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@tenant_view
@require_roles(*ALL_ROLES)
def dashboard(request):
    tenant = request.tenant
    role = request.membership.role
    today = date.today()
    today_str = today.isoformat()
    bookings_url = reverse('bookings')
    payments_url = reverse('payments')

    todays_bookings_qs = Booking.objects.filter(date=today).exclude(booking_status='cancelled')
    todays_payments = BookingPayment.objects.filter(created_at__date=today)
    paid_in = todays_payments.exclude(payment_type='refund').aggregate(s=Sum('amount'))['s'] or 0
    refunded = todays_payments.filter(payment_type='refund').aggregate(s=Sum('amount'))['s'] or 0
    todays_cash = todays_payments.filter(payment_mode='cash').exclude(
        payment_type='refund').aggregate(s=Sum('amount'))['s'] or 0
    todays_online = todays_payments.filter(payment_mode='online').exclude(
        payment_type='refund').aggregate(s=Sum('amount'))['s'] or 0

    active_bookings = Booking.objects.exclude(booking_status='cancelled').select_related('customer', 'config')
    pending_qs = [b for b in active_bookings if b.balance > 0]

    active_resources = Resource.objects.filter(tenant=tenant, is_active=True).count()
    total_available_minutes = active_resources * services.operating_minutes_per_day(tenant)
    booked_minutes_today = sum(
        b.duration_minutes * b.config.resources.count()
        for b in todays_bookings_qs.select_related('config'))
    occupancy_pct = round((booked_minutes_today / total_available_minutes) * 100, 1) \
        if total_available_minutes else 0

    month_start = today.replace(day=1)
    upcoming_tournaments = (
        Tournament.objects.filter(date__gte=today).exclude(status='cancelled').count()
        + Booking.objects.filter(booking_type='tournament', date__gte=today)
        .exclude(booking_status='cancelled').count())
    requests_qs = Booking.objects.filter(booking_status='pending', created_by__isnull=True)

    # (label, value, is_currency, url, roles that may see it)
    cards = [
        ("Today's Revenue", paid_in - refunded, True, f"{payments_url}?date={today_str}", FINANCE),
        ("Today's Cash", todays_cash, True, f"{payments_url}?date={today_str}&mode=cash", FINANCE),
        ("Today's Online", todays_online, True, f"{payments_url}?date={today_str}&mode=online", FINANCE),
        ("Expenses (This Month)", Expense.objects.filter(date__gte=month_start).aggregate(s=Sum('amount'))['s'] or 0,
         True, reverse('expenses'), FINANCE),
        ("Cancelled Today", Booking.objects.filter(cancelled_at__date=today).count(), False,
         reverse('cancellations_report'), FINANCE),
        ("Pending Payments", sum((b.balance for b in pending_qs), start=Decimal(0)), True,
         f"{bookings_url}?pending=1", ALL_ROLES),
        ("Today's Bookings", todays_bookings_qs.count(), False, f"{bookings_url}?date={today_str}", OPS),
        ("Upcoming Bookings", Booking.objects.filter(date__gt=today, booking_status='confirmed').count(), False,
         f"{bookings_url}?upcoming=1", OPS),
        ("Requests waiting", requests_qs.count(), False, f"{bookings_url}?status=pending", OPS),
        ("Occupancy", f"{occupancy_pct}%", False, f"{reverse('calendar_view')}?date={today_str}", OPS),
        ("Upcoming Tournaments", upcoming_tournaments, False, reverse('tournaments'), OPS),
    ]
    kpi_cards = [{"label": l, "value": v, "is_currency": c, "url": u}
                 for l, v, c, u, roles in cards if role in roles]

    return render(request, "core/dashboard.html", {
        "active": "dashboard",
        "today": today,
        "kpi_cards": kpi_cards,
        "todays_schedule": todays_bookings_qs.select_related('customer', 'config__bookable_type').order_by('start_time'),
        "upcoming_bookings": Booking.objects.filter(date__gt=today, booking_status='confirmed')
            .select_related('customer', 'config__bookable_type').order_by('date', 'start_time')[:5],
        "pending_requests": requests_qs.select_related('customer', 'config').order_by('date', 'start_time')[:5],
        "pending_payments_list": sorted(pending_qs, key=lambda b: (b.date, b.start_time))[:5],
        "recent_expenses": Expense.objects.order_by('-created_at')[:5] if role in FINANCE else [],
    })


# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------

@tenant_view
@require_roles(*ALL_ROLES)
def bookings(request):
    status_filter = request.GET.get('status', '')
    date_filter = request.GET.get('date', '')
    pending = request.GET.get('pending') == '1'
    upcoming = request.GET.get('upcoming') == '1'

    qs = Booking.objects.select_related('customer', 'config__bookable_type').order_by('-date', '-start_time')
    if status_filter:
        qs = qs.filter(booking_status=status_filter)
    if date_filter:
        try:
            qs = qs.filter(date=dt.strptime(date_filter, "%Y-%m-%d").date())
        except ValueError:
            date_filter = ''
    if upcoming:
        qs = qs.filter(date__gt=date.today(), booking_status='confirmed').order_by('date', 'start_time')
    if pending:
        qs = qs.exclude(booking_status='cancelled')
        qs = qs.filter(id__in=[b.id for b in qs if b.balance > 0])

    return render(request, "core/bookings.html", {
        "active": "bookings", "bookings": qs[:100],
        "status_filter": status_filter, "date_filter": date_filter,
        "pending": pending, "upcoming": upcoming,
        "status_choices": Booking.BOOKING_STATUS_CHOICES,
    })


def _compute_preview(tenant, get_params):
    """One calculation shared by the live preview and the final save."""
    errors = []
    result = {
        "duration_minutes": None, "duration_label": "", "day_type": None,
        "price_per_hour": None, "base_amount": None, "discount_amount": Decimal("0.00"),
        "additional_amount": Decimal("0.00"), "final_amount": None,
        "cash_paid": Decimal("0.00"), "online_paid": Decimal("0.00"),
        "total_received": Decimal("0.00"), "balance": None, "conflict": None,
        "daylight_minutes": None, "lights_minutes": None,
        "daylight_rate": None, "lights_rate": None,
        "daylight_amount": Decimal("0.00"), "lights_amount": Decimal("0.00"),
        "lights_required": False,
    }

    config = services.get_config(tenant, get_params.get('config'))
    date_str = get_params.get('date')
    start_str = get_params.get('start_time')
    end_str = get_params.get('end_time')
    if not (config and date_str and start_str and end_str):
        return result, errors

    try:
        booking_date = dt.strptime(date_str, "%Y-%m-%d").date()
        start_time = dt.strptime(start_str, "%H:%M").time()
        end_time = dt.strptime(end_str, "%H:%M").time()
    except ValueError:
        errors.append("Invalid date or time format.")
        return result, errors

    try:
        duration = services.calculate_duration_minutes(start_time, end_time)
    except services.ValidationError as e:
        errors.append(str(e))
        return result, errors
    result["duration_minutes"] = duration
    result["duration_label"] = services.duration_label(duration)

    try:
        services.validate_booking_rules(tenant, booking_date, start_time, end_time)
    except services.ValidationError as e:
        errors.append(str(e))
        return result, errors

    day_type = services.calculate_day_type(
        services.business_date(tenant, booking_date, start_time, end_time))
    result["day_type"] = day_type

    try:
        pricing = services.calculate_split_pricing(
            tenant, day_type, config.bookable_type, start_time, end_time, booking_date)
    except services.PricingError as e:
        errors.append(str(e))
        return result, errors

    for key in ("daylight_minutes", "lights_minutes", "daylight_rate", "lights_rate",
                "daylight_amount", "lights_amount", "lights_required"):
        result[key] = pricing[key]
    base_amount = pricing["base_amount"]
    result["base_amount"] = base_amount
    result["price_per_hour"] = (
        (base_amount / (Decimal(duration) / Decimal(60))).quantize(Decimal("0.01"))
        if duration else Decimal("0.00"))

    try:
        discount_amount = services.calculate_discount(
            base_amount, get_params.get('discount_type', 'none'), get_params.get('discount_value'))
    except services.ValidationError as e:
        errors.append(str(e))
        discount_amount = Decimal("0.00")
    result["discount_amount"] = discount_amount

    additional_amount = _dec(get_params.get('additional_charge_amount'))
    result["additional_amount"] = additional_amount
    final_amount = base_amount - discount_amount + additional_amount
    result["final_amount"] = final_amount

    cash_paid = _dec(get_params.get('cash_paid'))
    online_paid = _dec(get_params.get('online_paid'))
    result["cash_paid"], result["online_paid"] = cash_paid, online_paid
    try:
        total_received = services.validate_payment_split(final_amount, cash_paid, online_paid)
    except services.ValidationError as e:
        errors.append(str(e))
        total_received = cash_paid + online_paid
    result["total_received"] = total_received
    result["balance"] = final_amount - total_received

    try:
        conflict = services.find_conflicting_booking(tenant, config, booking_date, start_time, end_time)
    except services.ValidationError as e:
        errors.append(str(e))
        conflict = None
    if conflict:
        result["conflict"] = conflict
        errors.append(services.conflict_message(config, conflict))

    return result, errors


@tenant_view
@require_roles(*OPS)
def booking_price_preview(request):
    result, errors = _compute_preview(request.tenant, request.GET)
    return render(request, "core/partials/price_summary.html", {"r": result, "errors": errors})


def _create_booking_records(request, tenant, cd, result):
    user = request.user
    customer = Customer.objects.filter(tenant=tenant, phone=cd['customer_phone']).first()
    if customer is None:
        customer = Customer.objects.create(tenant=tenant, name=cd['customer_name'], phone=cd['customer_phone'])
    elif customer.name != cd['customer_name']:
        customer.name = cd['customer_name']
        customer.save(update_fields=['name'])

    received, final = result["total_received"], result["final_amount"]
    is_first_partial = 0 < received < final
    payment_status = services.derive_payment_status(final, received, is_first_partial)

    booking = Booking.objects.create(
        tenant=tenant, booking_code=services.next_booking_code(tenant),
        customer=customer, config=cd['config'], sport=cd.get('sport'),
        date=cd['date'], start_time=cd['start_time'], end_time=cd['end_time'],
        duration_minutes=result["duration_minutes"], day_type=result["day_type"],
        lights=result["lights_required"],
        price_per_hour=result["price_per_hour"], base_amount=result["base_amount"],
        discount_amount=result["discount_amount"],
        additional_charges_amount=result["additional_amount"], final_amount=final,
        booking_type=cd['booking_type'], booking_status='confirmed',
        payment_status=payment_status, notes=cd.get('notes', ''), created_by=user)

    if cd.get('discount_type') and cd['discount_type'] != 'none' and result["discount_amount"] > 0:
        BookingDiscount.objects.create(
            booking=booking, discount_type=cd['discount_type'],
            discount_value=cd.get('discount_value') or 0,
            discount_amount=result["discount_amount"], applied_by=user)
    if result["additional_amount"] > 0:
        BookingAdditionalCharge.objects.create(
            booking=booking, charge_type='other',
            description=cd.get('additional_charge_desc', ''), amount=result["additional_amount"])

    payment_type = 'advance' if is_first_partial else 'final'
    for mode, amount in (('cash', result["cash_paid"]), ('online', result["online_paid"])):
        if amount > 0:
            BookingPayment.objects.create(
                tenant=tenant, booking=booking, amount=amount, payment_mode=mode,
                payment_type=payment_type, recorded_by=user)
    return booking


@tenant_view
@require_roles(*OPS)
def booking_create(request):
    tenant = request.tenant
    if request.method == "POST":
        form = BookingForm(request.POST, tenant=tenant)
        if form.is_valid():
            cd = form.cleaned_data
            result, errors = _compute_preview(tenant, {
                'config': str(cd['config'].id),
                'date': cd['date'].strftime('%Y-%m-%d'),
                'start_time': cd['start_time'].strftime('%H:%M'),
                'end_time': cd['end_time'].strftime('%H:%M'),
                'discount_type': cd.get('discount_type') or 'none',
                'discount_value': cd.get('discount_value'),
                'additional_charge_amount': cd.get('additional_charge_amount'),
                'cash_paid': cd.get('cash_paid'),
                'online_paid': cd.get('online_paid'),
            })
            if errors:
                for e in errors:
                    messages.error(request, e)
            else:
                booking = None
                try:
                    with transaction.atomic():
                        services.lock_tenant(tenant)
                        # Re-check under the lock so two people can't take the same slot.
                        conflict = services.find_conflicting_booking(
                            tenant, cd['config'], cd['date'], cd['start_time'], cd['end_time'])
                        if conflict:
                            messages.error(request, services.conflict_message(cd['config'], conflict))
                        else:
                            booking = _create_booking_records(request, tenant, cd, result)
                except services.ValidationError as e:
                    messages.error(request, str(e))
                if booking:
                    messages.success(request, f"Booking {booking.booking_code} confirmed.")
                    return redirect('booking_detail', booking_id=booking.id)
    else:
        initial = {'booking_type': 'regular', 'discount_type': 'none'}
        config = None
        if request.GET.get('config'):
            config = services.get_config(tenant, request.GET.get('config'))
        elif request.GET.get('resource'):
            config = services.config_for_single_resource(tenant, request.GET.get('resource'))
        if config:
            initial['config'] = config.id
        for key in ('date', 'start_time', 'end_time'):
            if request.GET.get(key):
                initial[key] = request.GET.get(key)
        form = BookingForm(initial=initial, tenant=tenant)

        tenant_settings = services.get_settings(tenant)
    configs = list(BookableConfig._base_manager.filter(tenant=tenant, is_active=True))
    allowed = {}
    for config_id, sport_id in BookableConfig.sports.through.objects.filter(
            bookableconfig__tenant=tenant).values_list('bookableconfig_id', 'sport_id'):
        allowed.setdefault(str(config_id), []).append(str(sport_id))
    return render(request, "core/booking_form.html", {
        "active": "bookings", "form": form,
        "lights_start": tenant_settings.lights_start.strftime('%H:%M') if tenant_settings.lights_start else "",
        "overnight": services.allows_overnight(tenant),
        "has_sports": Sport._base_manager.filter(
            tenant=tenant, is_active=True
        ).exists(),
        "config_sports": {
            str(c.id): allowed.get(str(c.id), []) for c in configs
        },
    })

@tenant_view
@require_roles(*ALL_ROLES)
def booking_detail(request, booking_id):
    booking = get_object_or_404(
        Booking.objects.select_related('customer', 'config__bookable_type'), id=booking_id)
    return render(request, "core/booking_detail.html", {
        "active": "bookings", "booking": booking,
        "payments": booking.payments.order_by('created_at'),
        "discounts": booking.discounts.all(),
        "additional_charges": booking.additional_charges.all(),
    })


@tenant_view
@require_roles(*OPS)
def booking_cancel(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)
    if request.method != "POST":
        return redirect('booking_detail', booking_id=booking.id)
    if booking.booking_status == 'cancelled':
        messages.error(request, "This booking is already cancelled.")
        return redirect('booking_detail', booking_id=booking.id)

    reason = request.POST.get('reason', '')
    refund_amount = _dec(request.POST.get('refund_amount'))
    refund_mode = request.POST.get('refund_mode', 'cash')
    if refund_amount < 0 or refund_amount > booking.total_received:
        messages.error(request, "Refund amount cannot exceed the amount received.")
        return redirect('booking_detail', booking_id=booking.id)

    with transaction.atomic():
        booking.booking_status = 'cancelled'
        booking.cancelled_at = timezone.now()
        booking.cancelled_by = request.user
        booking.cancellation_reason = reason
        booking.refund_amount = refund_amount
        booking.refund_mode = refund_mode
        if refund_amount > 0:
            BookingPayment.objects.create(
                tenant=request.tenant, booking=booking, amount=refund_amount,
                payment_mode=refund_mode, payment_type='refund',
                notes="Refund on cancellation", recorded_by=request.user)
            booking.payment_status = 'refunded' if booking.total_received <= 0 else 'partially_refunded'
        booking.save()

    messages.success(request, f"Booking {booking.booking_code} cancelled.")
    return redirect('booking_detail', booking_id=booking.id)


@tenant_view
@require_roles(*ALL_ROLES)
def record_payment(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)
    if request.method != "POST":
        return redirect('booking_detail', booking_id=booking.id)
    if booking.booking_status == 'cancelled':
        messages.error(request, "Cannot record a payment on a cancelled booking.")
        return redirect('booking_detail', booking_id=booking.id)

    cash_amount = _dec(request.POST.get('cash_amount'))
    online_amount = _dec(request.POST.get('online_amount'))
    notes = request.POST.get('notes', '')
    total_new = cash_amount + online_amount
    if cash_amount < 0 or online_amount < 0 or total_new <= 0:
        messages.error(request, "Enter a cash or online amount greater than zero.")
        return redirect('booking_detail', booking_id=booking.id)

    with transaction.atomic():
        Booking.objects.select_for_update().get(pk=booking.pk)   # serialise payments on this booking
        received_before = booking.total_received
        prospective_total = received_before + total_new
        if prospective_total > booking.final_amount:
            messages.error(request, f"Payment exceeds outstanding balance by ₹{prospective_total - booking.final_amount}.")
            return redirect('booking_detail', booking_id=booking.id)

        payment_type = ('final' if prospective_total >= booking.final_amount
                        else 'advance' if received_before <= 0 else 'partial')
        for mode, amount in (('cash', cash_amount), ('online', online_amount)):
            if amount > 0:
                BookingPayment.objects.create(
                    tenant=request.tenant, booking=booking, amount=amount, payment_mode=mode,
                    payment_type=payment_type, notes=notes, recorded_by=request.user)
        booking.payment_status = services.derive_payment_status(
            booking.final_amount, booking.total_received,
            is_first_payment_partial=(payment_type == 'advance'))
        booking.save(update_fields=['payment_status'])

    messages.success(request, f"Payment of ₹{total_new} recorded.")
    return redirect('booking_detail', booking_id=booking.id)


@tenant_view
@require_roles(*OPS)
def booking_confirm(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)
    if request.method != "POST" or booking.booking_status != 'pending':
        return redirect('booking_detail', booking_id=booking.id)
    try:
        with transaction.atomic():
            services.lock_tenant(request.tenant)
            conflict = services.find_conflicting_booking(
                request.tenant, booking.config, booking.date, booking.start_time,
                booking.end_time, exclude_booking_id=booking.id)
            if conflict:
                messages.error(request, "Cannot confirm: this time now clashes with another booking.")
                return redirect('booking_detail', booking_id=booking.id)
            booking.booking_status = 'confirmed'
            booking.save(update_fields=['booking_status'])
    except services.ValidationError as e:
        messages.error(request, str(e))
        return redirect('booking_detail', booking_id=booking.id)
    messages.success(request, f"Booking {booking.booking_code} confirmed.")
    return redirect('booking_detail', booking_id=booking.id)


# ---------------------------------------------------------------------------
# Availability and calendar
# ---------------------------------------------------------------------------

@tenant_view
@require_roles(*OPS)
def availability(request):
    tenant = request.tenant
    date_str = request.GET.get('date')
    try:
        selected_date = dt.strptime(date_str, "%Y-%m-%d").date() if date_str else date_cls.today()
    except ValueError:
        selected_date = date_cls.today()

    resources = Resource.objects.filter(tenant=tenant, is_active=True).order_by('display_order', 'name')
    context = {
        "active": "availability",
        "selected_date": selected_date,
        "selected_date_str": selected_date.strftime("%Y-%m-%d"),
        "hours_label": services.hours_label(tenant, selected_date),
        "ground_data": [
            {"ground": r, "segments": services.get_availability_for_resource(tenant, r, selected_date)}
            for r in resources],
    }
    if request.htmx:
        return render(request, "core/partials/availability_grounds.html", context)
    return render(request, "core/availability.html", context)


@tenant_view
@require_roles(*OPS)
def calendar_view(request):
    tenant = request.tenant
    today = date.today()
    sel = request.GET.get('date')
    try:
        selected_date = dt.strptime(sel, "%Y-%m-%d").date() if sel else today
    except ValueError:
        selected_date = today

    year, month = selected_date.year, selected_date.month
    first_of_month = date_cls(year, month, 1)
    prev_month = (first_of_month - timedelta(days=1)).replace(day=1)
    next_month = (first_of_month + timedelta(days=32)).replace(day=1)

    booking_counts = {
        r['date']: r['n']
        for r in Booking.objects.filter(date__year=year, date__month=month)
        .exclude(booking_status='cancelled').values('date').annotate(n=Count('id'))}
    tournament_dates = set(
        Tournament.objects.filter(date__year=year, date__month=month)
        .exclude(status='cancelled').values_list('date', flat=True)
    ) | set(
        Booking.objects.filter(date__year=year, date__month=month, booking_type='tournament')
        .exclude(booking_status='cancelled').values_list('date', flat=True))

    weeks = [[{
        "date": d, "in_month": d.month == month, "count": booking_counts.get(d, 0),
        "has_tournament": d in tournament_dates, "is_today": d == today,
        "is_selected": d == selected_date,
    } for d in week] for week in pycal.Calendar(firstweekday=0).monthdatescalendar(year, month)]

    section_rows = [
        {"number": i, "ground": r,
         "segments": services.get_availability_for_resource(tenant, r, selected_date)}
        for i, r in enumerate(
            Resource.objects.filter(tenant=tenant, is_active=True).order_by('display_order', 'name'), start=1)]

    return render(request, "core/calendar.html", {
        "active": "calendar", "selected_date": selected_date,
        "first_of_month": first_of_month, "prev_month": prev_month, "next_month": next_month,
        "weeks": weeks, "section_rows": section_rows,
        "day_bookings": Booking.objects.filter(date=selected_date)
            .select_related('customer', 'config__bookable_type').order_by('start_time'),
        "day_tournaments": Tournament.objects.filter(date=selected_date).exclude(status='cancelled'),
    })


# ---------------------------------------------------------------------------
# Payments, expenses
# ---------------------------------------------------------------------------

@tenant_view
@require_roles(*ALL_ROLES)
def payments(request):
    mode_filter = request.GET.get('mode', '')
    type_filter = request.GET.get('type', '')
    date_filter = request.GET.get('date', '')

    qs = BookingPayment.objects.select_related('booking', 'booking__customer').order_by('-created_at')
    if mode_filter:
        qs = qs.filter(payment_mode=mode_filter)
    if type_filter:
        qs = qs.filter(payment_type=type_filter)
    if date_filter:
        try:
            qs = qs.filter(created_at__date=dt.strptime(date_filter, "%Y-%m-%d").date())
        except ValueError:
            date_filter = ''

    return render(request, "core/payments.html", {
        "active": "payments", "payments": qs[:200],
        "mode_filter": mode_filter, "type_filter": type_filter, "date_filter": date_filter,
    })


def _expense_fields(cd):
    return dict(
        date=cd['date'], category=cd['category'], sub_category=cd.get('sub_category', ''),
        description=cd.get('description', ''), vendor=cd.get('vendor', ''),
        payment_mode=cd['payment_mode'], amount=cd['amount'],
        total_paid=cd.get('total_paid') or 0, paid_by=cd.get('paid_by', ''),
        status=cd['status'], notes=cd.get('notes', ''))


@tenant_view
@require_roles(*FINANCE)
def expenses(request):
    category_filter = request.GET.get('category', '')
    qs = Expense.objects.order_by('-date')
    if category_filter:
        qs = qs.filter(category=category_filter)

    if request.method == "POST":
        form = ExpenseForm(request.POST, request.FILES)
        if form.is_valid():
            cd = form.cleaned_data
            Expense.objects.create(
                tenant=request.tenant, receipt=cd.get('receipt'),
                created_by=request.user, **_expense_fields(cd))
            messages.success(request, "Expense recorded.")
            return redirect('expenses')
        messages.error(request, "Please correct the highlighted fields.")
    else:
        form = ExpenseForm(initial={'date': date.today(), 'status': 'paid'})

    return render(request, "core/expenses.html", {
        "active": "expenses", "expenses": qs[:100], "form": form,
        "categories": EXPENSE_CATEGORIES, "category_filter": category_filter,
    })


@tenant_view
@require_roles(*FINANCE)
def expense_edit(request, expense_id):
    expense = get_object_or_404(Expense, id=expense_id)
    if request.method == "POST":
        form = ExpenseForm(request.POST, request.FILES)
        if form.is_valid():
            cd = form.cleaned_data
            for field, value in _expense_fields(cd).items():
                setattr(expense, field, value)
            if cd.get('receipt'):
                expense.receipt = cd['receipt']
            expense.save()
            messages.success(request, "Expense updated.")
            return redirect('expenses')
    else:
        form = ExpenseForm(initial={
            'date': expense.date, 'category': expense.category, 'sub_category': expense.sub_category,
            'description': expense.description, 'vendor': expense.vendor,
            'payment_mode': expense.payment_mode, 'amount': expense.amount,
            'total_paid': expense.total_paid, 'paid_by': expense.paid_by,
            'status': expense.status, 'notes': expense.notes})
    return render(request, "core/expense_edit.html", {"active": "expenses", "form": form, "expense": expense})


@tenant_view
@require_roles(*FINANCE)
def expense_delete(request, expense_id):
    expense = get_object_or_404(Expense, id=expense_id)
    if request.method == "POST":
        expense.delete()
        messages.success(request, "Expense deleted.")
    return redirect('expenses')


# ---------------------------------------------------------------------------
# Tournaments
# ---------------------------------------------------------------------------

def next_tournament_code(tenant):
    highest = 100
    codes = Tournament._base_manager.filter(
        tenant=tenant, tournament_code__startswith='TRN-').values_list('tournament_code', flat=True)
    for code in codes:
        try:
            highest = max(highest, int(code.split('-')[1]))
        except (IndexError, ValueError):
            continue
    return f"TRN-{highest + 1}"


@tenant_view
@require_roles(*OPS)
def tournaments(request):
    rows = []
    for t in Tournament.objects.prefetch_related('grounds__config'):
        rows.append({
            'name': t.name, 'code': t.tournament_code, 'kind': 'Tournament',
            'date': t.date, 'start': t.start_time, 'end': t.end_time,
            'grounds': ", ".join(g.config.name for g in t.grounds.all()),
            'amount': t.final_amount, 'status': t.get_status_display(),
            'cancelled': t.status == 'cancelled',
            'url': reverse('tournament_detail', args=[t.id])})
    for b in Booking.objects.filter(booking_type='tournament').select_related('customer', 'config'):
        rows.append({
            'name': b.customer.name, 'code': b.booking_code, 'kind': 'Booking',
            'date': b.date, 'start': b.start_time, 'end': b.end_time,
            'grounds': b.config.name,
            'amount': b.final_amount, 'status': b.get_booking_status_display(),
            'cancelled': b.booking_status == 'cancelled',
            'url': reverse('booking_detail', args=[b.id])})
    rows.sort(key=lambda r: (r['date'], r['start']), reverse=True)
    return render(request, "core/tournaments.html", {"active": "tournaments", "rows": rows})


@tenant_view
@require_roles(*MANAGE)
def tournament_create(request):
    tenant = request.tenant
    if request.method == "POST":
        form = TournamentForm(request.POST, tenant=tenant)
        if form.is_valid():
            cd = form.cleaned_data
            configs = list(cd['grounds'])

            seen, overlap = set(), False
            for cfg in configs:
                ids = {r.id for r in cfg.resources.all()}
                if ids & seen:
                    overlap = True
                seen |= ids

            tournament = None
            if overlap:
                messages.error(request, "The selected ground options use the same physical space. "
                                        "Pick options that don't overlap.")
            else:
                with transaction.atomic():
                    services.lock_tenant(tenant)
                    conflicts = []
                    for cfg in configs:
                        try:
                            if services.find_conflicting_booking(
                                    tenant, cfg, cd['date'], cd['start_time'], cd['end_time']):
                                conflicts.append(cfg.name)
                        except services.ValidationError as e:
                            conflicts.append(f"{cfg.name} ({e})")
                    if conflicts:
                        messages.error(request, f"Conflict on: {', '.join(conflicts)} during this time window.")
                    else:
                        base = cd['base_ground_charges']
                        premises = cd.get('premises_charges') or Decimal(0)
                        additional = cd.get('additional_charges') or Decimal(0)
                        discount = cd.get('discount') or Decimal(0)
                        final_amount = base + premises + additional - discount
                        received = cd.get('amount_received') or Decimal(0)
                        payment_status = 'unpaid'
                        if received > 0:
                            payment_status = 'paid' if received >= final_amount else 'partially_paid'
                        tournament = Tournament.objects.create(
                            tenant=tenant, tournament_code=next_tournament_code(tenant),
                            name=cd['name'], organizer=cd['organizer'], contact=cd['contact'],
                            date=cd['date'], start_time=cd['start_time'], end_time=cd['end_time'],
                            base_ground_charges=base, premises_charges=premises,
                            additional_charges=additional, discount=discount,
                            final_amount=final_amount, amount_received=received,
                            payment_status=payment_status, status='confirmed',
                            notes=cd.get('notes', ''))
                        for cfg in configs:
                            TournamentGround.objects.create(tournament=tournament, config=cfg)
            if tournament:
                messages.success(request, f"Tournament {tournament.tournament_code} created.")
                return redirect('tournament_detail', tournament_id=tournament.id)
    else:
        form = TournamentForm(tenant=tenant)
    return render(request, "core/tournament_form.html", {"active": "tournaments", "form": form})


@tenant_view
@require_roles(*OPS)
def tournament_detail(request, tournament_id):
    t = get_object_or_404(Tournament, id=tournament_id)
    grounds = TournamentGround.objects.filter(tournament=t).select_related('config')
    return render(request, "core/tournament_detail.html", {
        "active": "tournaments", "tournament": t, "grounds": grounds})


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

@tenant_view
@require_roles(*OPS)
def customers(request):
    search = request.GET.get('q', '')
    qs = Customer.objects.all()
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(phone__icontains=search) | Q(team_name__icontains=search))

    customer_list = []
    for c in qs.order_by('name')[:200]:
        active_bookings = c.bookings.exclude(booking_status='cancelled')
        customer_list.append({
            "customer": c,
            "total_bookings": active_bookings.count(),
            "total_spent": sum((b.total_received for b in active_bookings), start=Decimal(0)),
            "last_booking": active_bookings.order_by('-date').first(),
            "cancelled_count": c.bookings.filter(booking_status='cancelled').count(),
        })
    return render(request, "core/customers.html", {
        "active": "customers", "customer_list": customer_list, "search": search})


@tenant_view
@require_roles(*OPS)
def customer_detail(request, customer_id):
    customer = get_object_or_404(Customer, id=customer_id)
    return render(request, "core/customer_detail.html", {
        "active": "customers", "customer": customer,
        "bookings": customer.bookings.select_related('config').order_by('-date')})


# ---------------------------------------------------------------------------
# Analytics, reports, exports
# ---------------------------------------------------------------------------

@tenant_view
@require_roles(*FINANCE)
def analytics(request):
    try:
        days = int(request.GET.get('days', 30))
    except ValueError:
        days = 30
    if days not in (7, 30, 90):
        days = 30
    start_date = date.today() - timedelta(days=days)

    bookings_qs = Booking.objects.filter(date__gte=start_date).exclude(booking_status='cancelled')

    revenue_by_day = (
        BookingPayment.objects.filter(created_at__date__gte=start_date)
        .exclude(payment_type='refund').annotate(day=TruncDate('created_at'))
        .values('day').annotate(total=Sum('amount')).order_by('day'))
    revenue_by_ground = bookings_qs.values('config__name').annotate(total=Sum('final_amount')).order_by('-total')
    payment_by_mode = (
        BookingPayment.objects.filter(created_at__date__gte=start_date)
        .exclude(payment_type='refund').values('payment_mode').annotate(total=Sum('amount')))

    total_bookings_count = bookings_qs.count()
    total_final = bookings_qs.aggregate(s=Sum('final_amount'))['s'] or 0
    return render(request, "core/analytics.html", {
        "active": "analytics", "days": days,
        "revenue_labels": json.dumps([r['day'].strftime('%d %b') for r in revenue_by_day]),
        "revenue_values": json.dumps([float(r['total']) for r in revenue_by_day]),
        "ground_labels": json.dumps([r['config__name'] for r in revenue_by_ground]),
        "ground_values": json.dumps([float(r['total'] or 0) for r in revenue_by_ground]),
        "mode_labels": json.dumps([r['payment_mode'] for r in payment_by_mode]),
        "mode_values": json.dumps([float(r['total']) for r in payment_by_mode]),
        "booking_type_breakdown": bookings_qs.values('booking_type').annotate(count=Count('id')).order_by('-count'),
        "total_discounts": bookings_qs.aggregate(s=Sum('discount_amount'))['s'] or 0,
        "total_bookings_count": total_bookings_count,
        "avg_booking_value": (total_final / total_bookings_count) if total_bookings_count else 0,
    })


@tenant_view
@require_roles(*FINANCE)
def reports(request):
    return render(request, "core/reports.html", {"active": "reports"})


@tenant_view
@require_roles(*FINANCE)
def cancellations_report(request):
    qs = Booking.objects.filter(booking_status='cancelled').select_related(
        'customer', 'config', 'cancelled_by').order_by('-cancelled_at')
    return render(request, "core/cancellations.html", {
        "active": "reports", "cancellations": qs,
        "total_refunded": qs.aggregate(s=Sum('refund_amount'))['s'] or 0})


@tenant_view
@require_roles(*FINANCE)
def export_bookings_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="bookings.csv"'
    writer = csv.writer(response)
    writer.writerow(['Booking Code', 'Customer', 'Phone', 'Ground', 'Date', 'Start', 'End',
                     'Duration (min)', 'Final Amount', 'Received', 'Balance',
                     'Payment Status', 'Booking Status'])
    for b in Booking.objects.select_related('customer', 'config').order_by('-date'):
        writer.writerow([b.booking_code, b.customer.name, b.customer.phone, b.config.name,
                         b.date, b.start_time, b.end_time, b.duration_minutes,
                         b.final_amount, b.total_received, b.balance,
                         b.get_payment_status_display(), b.get_booking_status_display()])
    return response


@tenant_view
@require_roles(*FINANCE)
def export_expenses_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="expenses.csv"'
    writer = csv.writer(response)
    writer.writerow(['Date', 'Category', 'Sub-category', 'Vendor', 'Payment Mode', 'Amount', 'Status'])
    for e in Expense.objects.order_by('-date'):
        writer.writerow([e.date, e.category, e.sub_category, e.vendor,
                         e.get_payment_mode_display(), e.amount, e.get_status_display()])
    return response


@tenant_view
@require_roles(*FINANCE)
def export_payments_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="payments.csv"'
    writer = csv.writer(response)
    writer.writerow(['Date', 'Booking Code', 'Customer', 'Mode', 'Type', 'Amount'])
    for p in BookingPayment.objects.select_related('booking', 'booking__customer').order_by('-created_at'):
        writer.writerow([p.created_at, p.booking.booking_code, p.booking.customer.name,
                         p.get_payment_mode_display(), p.get_payment_type_display(), p.amount])
    return response


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@tenant_view
@require_roles(*MANAGE)
def settings_page(request):
    return render(request, "core/settings_hub.html", {"active": "settings"})


@tenant_view
@require_roles(*MANAGE)
def settings_grounds(request):
    return redirect('setup_grounds')


@tenant_view
@require_roles(*MANAGE)
def settings_pricing(request):
    tenant = request.tenant
    rules = (PricingRule.objects.filter(tenant=tenant).select_related('bookable_type')
             .order_by('bookable_type__name', 'day_type', 'lights'))
    if request.method == "POST":
        form = PricingRuleForm(request.POST, tenant=tenant)
        if form.is_valid():
            cd = form.cleaned_data
            existing = PricingRule.objects.filter(
                tenant=tenant, bookable_type=cd['bookable_type'],
                day_type=cd['day_type'], lights=cd['lights']).first()
            if existing:
                existing.price_per_hour = cd['price_per_hour']
                existing.is_active = True
                existing.save()
                messages.success(request, "Pricing rule updated.")
            else:
                PricingRule.objects.create(
                    tenant=tenant, bookable_type=cd['bookable_type'],
                    ground_type=cd['bookable_type'].name, day_type=cd['day_type'],
                    lights=cd['lights'], price_per_hour=cd['price_per_hour'], is_active=True)
                messages.success(request, "Pricing rule created.")
            return redirect('settings_pricing')
    else:
        form = PricingRuleForm(tenant=tenant)
    return render(request, "core/settings_pricing.html", {"active": "settings", "rules": rules, "form": form})


@tenant_view
@require_roles(*MANAGE)
def settings_pricing_toggle(request, rule_id):
    rule = get_object_or_404(PricingRule, id=rule_id, tenant=request.tenant)
    if request.method == "POST":
        rule.is_active = not rule.is_active
        rule.save()
        messages.success(request, f"Rule {'activated' if rule.is_active else 'deactivated'}.")
    return redirect('settings_pricing')


# ---------------------------------------------------------------------------
# Login / logout (signup and verification live in auth_views.py)
# ---------------------------------------------------------------------------

def _warn_if_unverified(request, data):
    user = User.objects.filter(username__iexact=data.get('username', '')).first()
    if (user and not user.is_active and user.has_usable_password()
            and user.check_password(data.get('password', ''))):
        messages.warning(request, "Please confirm your email address first. "
                                  "Use 'Resend verification email' below if you need a new link.")


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    next_url = request.POST.get('next') or request.GET.get('next') or ''

    if request.method == "POST":
        data = request.POST.copy()
        username = data.get('username', '').strip()
        if '@' in username:
            data['username'] = username.lower()
        form = AuthenticationForm(request, data=data)
        if form.is_valid():
            login(request, form.get_user())
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
            return redirect('dashboard')
        _warn_if_unverified(request, data)
    else:
        form = AuthenticationForm()

    form.fields['username'].widget.attrs.update(
        {'class': 'form-control', 'placeholder': 'Email or username', 'autofocus': True})
    form.fields['password'].widget.attrs.update({'class': 'form-control', 'placeholder': 'Password'})
    return render(request, "core/login.html", {"form": form, "next": next_url})


def logout_view(request):
    logout(request)
    return redirect('login_view')
