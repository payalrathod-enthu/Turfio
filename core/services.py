import uuid
from datetime import datetime, date as date_cls, time as time_cls, timedelta
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError as DjangoValidationError

from .models import (BookableConfig, Booking, PricingRule, TenantDayHours, TenantSettings,
                     TournamentGround, TurfBusiness)
from .tenancy import tenant_context
from django.db.models import Q

DAY = 24 * 60
NO_LIMIT = 10 ** 6


class PricingError(Exception):
    pass


class OverlapError(Exception):
    def __init__(self, message, conflicting_booking):
        super().__init__(message)
        self.conflicting_booking = conflicting_booking


class ValidationError(Exception):
    pass


class _TournamentConflict:
    """Looks enough like a booking for the conflict message."""
    is_tournament = True

    def __init__(self, tournament):
        self.start_time = tournament.start_time
        self.end_time = tournament.end_time
        self.name = tournament.name


def _scoped(fn):
    """Runs a service function with its tenant active, so related lookups work outside a request."""
    @wraps(fn)
    def wrapper(tenant, *args, **kwargs):
        with tenant_context(tenant):
            return fn(tenant, *args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Tenant helpers
# ---------------------------------------------------------------------------

def get_settings(tenant):
    settings_obj, _ = TenantSettings.objects.get_or_create(tenant=tenant)
    return settings_obj


def lock_tenant(tenant):
    """Serialises booking writes for one turf. Call inside transaction.atomic()."""
    TurfBusiness.objects.select_for_update().get(pk=tenant.pk)


def get_config(tenant, config_id):
    if not config_id:
        return None
    try:
        return (BookableConfig._base_manager
                .filter(tenant=tenant, id=config_id, is_active=True)
                .select_related('bookable_type').first())
    except (ValueError, DjangoValidationError):
        return None


# ---------------------------------------------------------------------------
# Time helpers. Times of day are minutes after midnight. A booking is measured
# on one continuous line: date.toordinal() * 1440 + minutes, so midnight is no barrier.
# ---------------------------------------------------------------------------

def to_minutes(t):
    return t.hour * 60 + t.minute


def minutes_between(a, b):
    return to_minutes(b) - to_minutes(a)


def time_from_minutes(m):
    m %= DAY
    return time_cls(m // 60, m % 60)


def hhmm(m):
    m %= DAY
    return f"{m // 60:02d}:{m % 60:02d}"


def format_minutes(m):
    text = time_from_minutes(m).strftime('%I:%M %p')
    return text + (' (next day)' if m >= DAY else '')


def duration_label(minutes):
    hrs, mins = divmod(minutes, 60)
    parts = []
    if hrs:
        parts.append(f"{hrs} hr")
    if mins:
        parts.append(f"{mins} min")
    return " ".join(parts) or "0 min"


def notice_label(minutes):
    return "no advance notice" if minutes <= 0 else f"{duration_label(minutes)} notice"


def calculate_day_type(booking_date):
    return 'weekend' if booking_date.weekday() >= 5 else 'weekday'


def calculate_duration_minutes(start_time, end_time):
    """An end time earlier than the start means the booking ends the next day."""
    s, e = to_minutes(start_time), to_minutes(end_time)
    if e == s:
        raise ValidationError("End time must be later than start time.")
    return e - s if e > s else e + DAY - s


# ---------------------------------------------------------------------------
# Opening hours (one row per weekday; a day may close after midnight)
# ---------------------------------------------------------------------------

def get_week(tenant):
    """The seven opening-hours rows, created from the business's current hours if missing."""
    s = get_settings(tenant)
    rows = {r.weekday: r for r in TenantDayHours.objects.filter(tenant=tenant)}
    for day in range(7):
        if day not in rows:
            rows[day], _ = TenantDayHours.objects.get_or_create(
                tenant=tenant, weekday=day,
                defaults={'is_open': True, 'open_time': s.operating_start, 'close_time': s.operating_end})
    return [rows[day] for day in range(7)]


def day_window(tenant, booking_date):
    """(opens, closes) in minutes after midnight of that date; closes can exceed 1440. None if closed."""
    row = get_week(tenant)[booking_date.weekday()]
    if not row.is_open:
        return None
    return to_minutes(row.open_time), to_minutes(row.close_time) + (DAY if row.closes_next_day else 0)


def allows_overnight(tenant):
    return any(r.is_open and r.closes_next_day for r in get_week(tenant))


def hours_label(tenant, booking_date):
    w = day_window(tenant, booking_date)
    return "Closed" if w is None else f"{format_minutes(w[0])} – {format_minutes(w[1])}"


def operating_minutes_per_day(tenant, booking_date=None):
    w = day_window(tenant, booking_date or date_cls.today())
    return (w[1] - w[0]) if w else 0


def _locate(tenant, booking_date, start_min, duration):
    """(offset, close): offset is 1440 when this booking is the after-midnight tail of the
    previous day's opening; close is that business day's closing time in minutes."""
    own = day_window(tenant, booking_date)
    if own and start_min >= own[0] and start_min + duration <= own[1]:
        return 0, own[1]
    prev = day_window(tenant, booking_date - timedelta(days=1))
    if (prev and prev[1] > DAY and start_min + DAY >= prev[0]
            and start_min + DAY + duration <= prev[1]):
        return DAY, prev[1]
    return 0, (own[1] if own else NO_LIMIT)


def business_date(tenant, booking_date, start_time, end_time):
    """The date of the opening day this booking belongs to (used for weekday/weekend prices)."""
    offset, _ = _locate(tenant, booking_date, to_minutes(start_time),
                        calculate_duration_minutes(start_time, end_time))
    return booking_date - timedelta(days=1) if offset else booking_date


def local_now(tenant):
    """Current time in the turf's own time zone, without tzinfo."""
    from django.utils import timezone
    for name in (tenant.timezone, 'Asia/Kolkata'):
        try:
            return datetime.now(ZoneInfo(name)).replace(tzinfo=None)
        except Exception:
            continue
    return timezone.localtime().replace(tzinfo=None)


def validate_booking_rules(tenant, booking_date, start_time, end_time):
    s = get_settings(tenant)
    duration = calculate_duration_minutes(start_time, end_time)
    start_min = to_minutes(start_time)
    own = day_window(tenant, booking_date)
    prev = day_window(tenant, booking_date - timedelta(days=1))
    fits_own = bool(own) and start_min >= own[0] and start_min + duration <= own[1]
    fits_tail = (bool(prev) and prev[1] > DAY and start_min + DAY >= prev[0]
                 and start_min + DAY + duration <= prev[1])
    if not (fits_own or fits_tail):
        if own is None:
            raise ValidationError("We're closed at that time." if (prev and prev[1] > DAY)
                                  else "We're closed on that day.")
        raise ValidationError(
            f"On that day we're open {format_minutes(own[0])} to {format_minutes(own[1])}.")
    if duration < s.min_duration_minutes:
        raise ValidationError(f"Minimum booking is {duration_label(s.min_duration_minutes)}.")
    if duration > s.max_duration_minutes:
        raise ValidationError(f"Maximum booking is {duration_label(s.max_duration_minutes)}.")


def earliest_online_start(tenant, now=None):
    """First start an online customer may pick: now + notice, rounded up to the time-slot interval."""
    s = get_settings(tenant)
    now = now or local_now(tenant)
    target = now + timedelta(minutes=s.min_advance_notice_minutes)
    total = target.hour * 60 + target.minute + (1 if (target.second or target.microsecond) else 0)
    interval = s.slot_interval_minutes or 15
    rounded = -(-total // interval) * interval
    return datetime.combine(target.date(), time_cls(0, 0)) + timedelta(minutes=rounded)


def validate_public_request(tenant, booking_date, start_time, end_time):
    """Extra rules for customer bookings, checked on the server: steps, notice and booking window."""
    s = get_settings(tenant)
    validate_booking_rules(tenant, booking_date, start_time, end_time)
    interval = s.slot_interval_minutes or 15
    if to_minutes(start_time) % interval or to_minutes(end_time) % interval:
        raise ValidationError(f"Please choose times in {interval}-minute steps.")

    now = local_now(tenant)
    start_dt = datetime.combine(booking_date, start_time)
    earliest = earliest_online_start(tenant, now)
    if start_dt < earliest:
        if start_dt <= now:
            raise ValidationError("That time has already passed. Please choose a later time.")
        raise ValidationError(
            f"Online bookings need {notice_label(s.min_advance_notice_minutes)}. "
            f"The earliest start right now is {earliest.strftime('%d %b, %I:%M %p')}.")
    if booking_date > now.date() + timedelta(days=s.max_advance_days):
        raise ValidationError(f"Bookings open up to {s.max_advance_days} days ahead.")


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

def get_price_per_hour(tenant, day_type, bookable_type, lights):
    rule = PricingRule._base_manager.filter(
        tenant=tenant, bookable_type=bookable_type, day_type=day_type,
        lights=lights, is_active=True).first()
    if not rule:
        label = "with lights" if lights else "without lights"
        raise PricingError(f"Pricing unavailable for {day_type} / {bookable_type.name} / {label}.")
    return rule.price_per_hour


def calculate_base_amount(price_per_hour, duration_minutes):
    hours = Decimal(duration_minutes) / Decimal(60)
    return (price_per_hour * hours).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_lights_split(tenant, start_time, end_time, booking_date=None):
    """(daylight_minutes, lights_minutes) using this turf's lights window.
    With a date, an after-midnight start is measured on the evening it belongs to."""
    total = calculate_duration_minutes(start_time, end_time)
    cfg = get_settings(tenant)
    if not cfg.lights_start:
        return total, 0
    s, close = to_minutes(start_time), NO_LIMIT
    if booking_date is not None:
        offset, close = _locate(tenant, booking_date, s, total)
        s += offset
    e = s + total
    lights_from = to_minutes(cfg.lights_start)
    if cfg.lights_end:
        lights_to = to_minutes(cfg.lights_end)
        if lights_to <= lights_from:
            lights_to += DAY
    else:
        lights_to = max(close, DAY)          # until closing time
    lights = max(0, min(e, lights_to) - max(s, lights_from))
    return total - lights, lights


def calculate_split_pricing(tenant, day_type, bookable_type, start_time, end_time, booking_date=None):
    daylight_minutes, lights_minutes = calculate_lights_split(tenant, start_time, end_time, booking_date)
    daylight_rate = lights_rate = None
    daylight_amount = lights_amount = Decimal("0.00")

    if daylight_minutes > 0:
        daylight_rate = get_price_per_hour(tenant, day_type, bookable_type, lights=False)
        daylight_amount = calculate_base_amount(daylight_rate, daylight_minutes)
    if lights_minutes > 0:
        lights_rate = get_price_per_hour(tenant, day_type, bookable_type, lights=True)
        lights_amount = calculate_base_amount(lights_rate, lights_minutes)

    return {
        "daylight_minutes": daylight_minutes,
        "lights_minutes": lights_minutes,
        "daylight_rate": daylight_rate,
        "lights_rate": lights_rate,
        "daylight_amount": daylight_amount,
        "lights_amount": lights_amount,
        "base_amount": daylight_amount + lights_amount,
        "lights_required": lights_minutes > 0,
    }


def calculate_discount(base_amount, discount_type, discount_value):
    if not discount_type or discount_type == 'none':
        return Decimal("0.00")
    if discount_value in (None, ''):
        return Decimal("0.00")

    discount_value = Decimal(discount_value)
    if discount_value < 0:
        raise ValidationError("Discount cannot be negative.")

    q = Decimal("0.01")
    if discount_type == 'percentage':
        if discount_value > 100:
            raise ValidationError("Percentage discount cannot exceed 100%.")
        return (base_amount * discount_value / Decimal(100)).quantize(q, rounding=ROUND_HALF_UP)
    if discount_type in ('fixed', 'promotion'):
        if discount_value > base_amount:
            raise ValidationError("Discount cannot exceed booking amount.")
        return discount_value.quantize(q, rounding=ROUND_HALF_UP)
    if discount_type == 'custom_price':
        if discount_value > base_amount:
            raise ValidationError("Custom price cannot exceed base amount.")
        return (base_amount - discount_value).quantize(q, rounding=ROUND_HALF_UP)
    return Decimal("0.00")


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

def validate_payment_split(final_amount, cash_paid, online_paid):
    total = (cash_paid or Decimal(0)) + (online_paid or Decimal(0))
    if total > final_amount:
        raise ValidationError(f"Payment exceeds booking amount by ₹{total - final_amount}.")
    return total


def derive_payment_status(final_amount, total_received, is_first_payment_partial=False):
    if total_received <= 0:
        return 'unpaid'
    if total_received < final_amount:
        return 'advance_paid' if is_first_payment_partial else 'partially_paid'
    return 'paid'


# ---------------------------------------------------------------------------
# Codes
# ---------------------------------------------------------------------------

def next_booking_code(tenant):
    """Call while holding lock_tenant() so two bookings can't get the same code."""
    prefix = get_settings(tenant).booking_code_prefix or 'BK'
    start = f"{prefix}-"
    highest = 1000
    codes = Booking._base_manager.filter(
        tenant=tenant, booking_code__startswith=start).values_list('booking_code', flat=True)
    for code in codes:
        try:
            highest = max(highest, int(code[len(start):]))
        except ValueError:
            continue
    return f"{prefix}-{highest + 1}"


# ---------------------------------------------------------------------------
# Conflicts: the times overlap AND the options share at least one physical space.
# ---------------------------------------------------------------------------

def _resource_ids(config):
    return {r.id for r in config.resources.all()}


def _booking_span(b):
    s = b.date.toordinal() * DAY + to_minutes(b.start_time)
    return s, s + b.duration_minutes


def _tournament_span(t):
    s = t.date.toordinal() * DAY + to_minutes(t.start_time)
    return s, s + max(to_minutes(t.end_time) - to_minutes(t.start_time), 0)


@_scoped
def find_conflicting_booking(tenant, config, booking_date, start_time, end_time,
                             exclude_booking_id=None):
    if config.tenant_id != tenant.id:
        raise ValidationError("That ground option doesn't belong to this business.")
    new_resources = _resource_ids(config)
    if not new_resources:
        raise ValidationError("This ground option has no physical space assigned.")

    new_start = booking_date.toordinal() * DAY + to_minutes(start_time)
    new_end = new_start + calculate_duration_minutes(start_time, end_time)
    near = (booking_date - timedelta(days=1), booking_date + timedelta(days=1))

    qs = (Booking._base_manager
          .filter(config__tenant=tenant, date__range=near)
          .exclude(booking_status='cancelled')
          .select_related('config').prefetch_related('config__resources'))
    if exclude_booking_id:
        qs = qs.exclude(id=exclude_booking_id)
    for existing in qs:
        s, e = _booking_span(existing)
        if s < new_end and e > new_start and new_resources & _resource_ids(existing.config):
            return existing

    links = (TournamentGround._base_manager
             .filter(config__tenant=tenant, tournament__date__range=near)
             .exclude(tournament__status='cancelled')
             .select_related('tournament', 'config').prefetch_related('config__resources'))
    for link in links:
        s, e = _tournament_span(link.tournament)
        if s < new_end and e > new_start and new_resources & _resource_ids(link.config):
            return _TournamentConflict(link.tournament)
    return None


def conflict_message(config, conflict):
    kind = "tournament" if getattr(conflict, 'is_tournament', False) else "booking"
    return (f"Booking conflict: {config.name} overlaps an existing {kind} from "
            f"{conflict.start_time.strftime('%I:%M %p')} to {conflict.end_time.strftime('%I:%M %p')}.")


@_scoped
def sport_offered(tenant, bookable_type, sport):
    """True if some online option of this type allows the sport (an option with no sports ticked allows all)."""
    return (BookableConfig._base_manager
            .filter(tenant=tenant, bookable_type=bookable_type, is_active=True, online_bookable=True)
            .filter(Q(sports__isnull=True) | Q(sports=sport)).exists())


@_scoped
def find_available_configs(tenant, bookable_type, booking_date, start_time, end_time, sport=None):
    """Every free, online-bookable option of a type that allows the sport, in display order."""
    qs = BookableConfig._base_manager.filter(
        tenant=tenant, bookable_type=bookable_type, is_active=True, online_bookable=True)
    if sport is not None:
        qs = qs.filter(Q(sports__isnull=True) | Q(sports=sport)).distinct()
    free = []
    for cfg in qs.prefetch_related('resources').order_by('display_order', 'name'):
        try:
            if not find_conflicting_booking(tenant, cfg, booking_date, start_time, end_time):
                free.append(cfg)
        except ValidationError:
            continue
    return free


def find_available_config(tenant, bookable_type, booking_date, start_time, end_time, sport=None):
    """First free option of a type, or None."""
    free = find_available_configs(tenant, bookable_type, booking_date, start_time, end_time, sport)
    return free[0] if free else None

@_scoped
def config_for_single_resource(tenant, resource_id):
    """The active option that occupies exactly this one resource (for pre-filling)."""
    try:
        rid = uuid.UUID(str(resource_id))
    except ValueError:
        return None
    configs = (BookableConfig._base_manager.filter(tenant=tenant, is_active=True)
               .prefetch_related('resources').order_by('display_order', 'name'))
    for cfg in configs:
        if _resource_ids(cfg) == {rid}:
            return cfg
    return None


# ---------------------------------------------------------------------------
# Availability timeline for one space on one opening day
# ---------------------------------------------------------------------------

@_scoped
def get_availability_for_resource(tenant, resource, booking_date):
    window = day_window(tenant, booking_date)
    if window is None:
        return []                       # closed that day
    base = booking_date.toordinal() * DAY
    w_start, w_end = base + window[0], base + window[1]
    total = max(w_end - w_start, 1)
    near = (booking_date - timedelta(days=1), booking_date + timedelta(days=1))

    items = []
    for b in (Booking._base_manager
              .filter(config__tenant=tenant, config__resources=resource, date__range=near)
              .exclude(booking_status='cancelled')
              .select_related('customer', 'config__bookable_type')):
        s, e = _booking_span(b)
        if e > w_start and s < w_end:
            items.append((s, e, b, None))

    seen = set()
    for link in (TournamentGround._base_manager
                 .filter(config__tenant=tenant, config__resources=resource, tournament__date__range=near)
                 .exclude(tournament__status='cancelled').select_related('tournament')):
        t = link.tournament
        if t.id in seen:
            continue
        seen.add(t.id)
        s, e = _tournament_span(t)
        if e > w_start and s < w_end:
            items.append((s, e, None, t))
    items.sort(key=lambda x: (x[0], x[1]))

    def make_segment(status, s, e, booking=None, tournament=None):
        return {
            "status": status,
            "start": time_from_minutes(s), "end": time_from_minutes(e),
            "start_str": hhmm(s), "end_str": hhmm(e),
            "date_str": date_cls.fromordinal(s // DAY).isoformat(),
            "duration_minutes": e - s,
            "width_pct": round((e - s) / total * 100, 2),
            "booking": booking, "tournament": tournament,
        }

    segments, cursor = [], w_start
    for s, e, booking, tournament in items:
        seg_start, seg_end = max(s, cursor), min(e, w_end)
        if seg_start >= seg_end:
            continue
        if seg_start > cursor:
            segments.append(make_segment("available", cursor, seg_start))
        segments.append(make_segment("booked", seg_start, seg_end, booking=booking, tournament=tournament))
        cursor = seg_end
    if cursor < w_end:
        segments.append(make_segment("available", cursor, w_end))
    return segments