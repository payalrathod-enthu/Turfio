import uuid
from decimal import Decimal, InvalidOperation

from django.db import transaction

from . import services
from .models import (BookableConfig, BookableType, Booking, ConfigResource, PricingRule,
                     Resource, TournamentGround)

MAX_PRICE = Decimal('1000000')
DAYS = (('weekday', 'Mon–Fri'), ('weekend', 'Sat–Sun'))

class SetupError(Exception):
    pass


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def clean_name(value, label, max_len=100):
    name = ' '.join((value or '').split())
    if not name:
        raise SetupError(f"{label} is required.")
    if len(name) > max_len:
        raise SetupError(f"{label} is too long (maximum {max_len} characters).")
    return name


def parse_ids(values):
    ids = []
    for v in values:
        try:
            ids.append(uuid.UUID(str(v)))
        except ValueError:
            raise SetupError("Invalid selection.")
    return ids


def _next_order(model, tenant):
    last = (model._base_manager.filter(tenant=tenant).order_by('-display_order')
            .values_list('display_order', flat=True).first())
    return (last or 0) + 1


def config_resource_map(tenant):
    """config id -> ordered list of Resource objects."""
    links = (ConfigResource.objects.filter(config__tenant=tenant).select_related('resource')
             .order_by('resource__display_order', 'resource__name'))
    result = {}
    for link in links:
        result.setdefault(link.config_id, []).append(link.resource)
    return result


# ---------------------------------------------------------------------------
# physical spaces
# ---------------------------------------------------------------------------

def add_resource(tenant, name):
    name = clean_name(name, "Name")
    if Resource._base_manager.filter(tenant=tenant, name__iexact=name).exists():
        raise SetupError(f"A space called '{name}' already exists.")
    return Resource._base_manager.create(
        tenant=tenant, name=name, display_order=_next_order(Resource, tenant))


def rename_resource(resource, name):
    name = clean_name(name, "Name")
    if (Resource._base_manager.filter(tenant=resource.tenant_id, name__iexact=name)
            .exclude(pk=resource.pk).exists()):
        raise SetupError(f"A space called '{name}' already exists.")
    resource.name = name
    resource.save(update_fields=['name'])


def set_resource_active(resource, active):
    if not active and ConfigResource.objects.filter(resource=resource, config__is_active=True).exists():
        raise SetupError("Switch off the ground options that use this space first.")
    resource.is_active = active
    resource.save(update_fields=['is_active'])


def delete_resource(resource):
    if ConfigResource.objects.filter(resource=resource).exists():
        raise SetupError("This space is used by a ground option. Delete that option first, or just switch the space off.")
    resource.delete()


# ---------------------------------------------------------------------------
# types and options
# ---------------------------------------------------------------------------

def get_or_create_type(tenant, name):
    name = clean_name(name, "Type name")
    existing = BookableType._base_manager.filter(tenant=tenant, name__iexact=name).first()
    if existing:
        if not existing.is_active:
            existing.is_active = True
            existing.save(update_fields=['is_active'])
        return existing
    return BookableType._base_manager.create(
        tenant=tenant, name=name, display_order=_next_order(BookableType, tenant))


@transaction.atomic
def add_config(tenant, type_name, option_name, resource_ids):
    ids = set(parse_ids(resource_ids))
    if not ids:
        raise SetupError("Tick at least one physical space.")
    resources = list(Resource._base_manager.filter(tenant=tenant, is_active=True, id__in=ids))
    if len(resources) != len(ids):
        raise SetupError("One of the selected spaces isn't available.")

    bookable_type = get_or_create_type(tenant, type_name)
    cmap = config_resource_map(tenant)
    key = {r.id for r in resources}
    siblings = list(BookableConfig._base_manager.filter(tenant=tenant, bookable_type=bookable_type))
    for cfg in siblings:
        if {r.id for r in cmap.get(cfg.id, [])} == key:
            raise SetupError(f"'{cfg.name}' already covers exactly these spaces.")

    names = ' + '.join(r.name for r in sorted(resources, key=lambda r: (r.display_order, r.name)))
    option_name = ' '.join((option_name or '').split())
    if not option_name:
        option_name = bookable_type.name if not siblings else f"{bookable_type.name} ({names})"
    option_name = clean_name(option_name, "Option name", 150)
    if BookableConfig._base_manager.filter(tenant=tenant, name__iexact=option_name).exists():
        raise SetupError(f"An option called '{option_name}' already exists. Give this one a different name.")

    cfg = BookableConfig._base_manager.create(
        tenant=tenant, bookable_type=bookable_type, name=option_name,
        display_order=_next_order(BookableConfig, tenant))
    for r in resources:
        ConfigResource.objects.create(config=cfg, resource=r)
    return cfg


def rename_config(config, name):
    name = clean_name(name, "Name", 150)
    if (BookableConfig._base_manager.filter(tenant=config.tenant_id, name__iexact=name)
            .exclude(pk=config.pk).exists()):
        raise SetupError(f"An option called '{name}' already exists.")
    config.name = name
    config.save(update_fields=['name'])


def set_config_active(config, active):
    if active:
        spaces = ConfigResource.objects.filter(config=config).select_related('resource')
        if not spaces:
            raise SetupError("This option has no physical space.")
        if any(not link.resource.is_active for link in spaces):
            raise SetupError("Switch its physical spaces on first.")
        if not config.bookable_type.is_active:
            config.bookable_type.is_active = True
            config.bookable_type.save(update_fields=['is_active'])
    config.is_active = active
    config.save(update_fields=['is_active'])


def delete_config(config):
    if (Booking._base_manager.filter(config=config).exists()
            or TournamentGround._base_manager.filter(config=config).exists()):
        raise SetupError("This option has bookings, so it can't be deleted. Switch it off instead.")
    config.delete()


# ---------------------------------------------------------------------------
# quick starts (only while nothing is set up)
# ---------------------------------------------------------------------------

def _require_empty(tenant):
    if Resource._base_manager.filter(tenant=tenant).exists():
        raise SetupError("Your ground layout is already set up. Edit it below instead.")


def _count(value, low, high):
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise SetupError("Enter a number.")
    if not low <= n <= high:
        raise SetupError(f"Enter a number between {low} and {high}.")
    return n


@transaction.atomic
def preset_whole(tenant, type_name, space_name):
    _require_empty(tenant)
    space = add_resource(tenant, space_name)
    add_config(tenant, type_name, clean_name(type_name, "Type name"), [str(space.id)])


@transaction.atomic
def preset_independent(tenant, type_name, label, count):
    _require_empty(tenant)
    n = _count(count, 1, 20)
    label = clean_name(label, "Label", 80)
    for i in range(1, n + 1):
        space = add_resource(tenant, f"{label} {i}")
        add_config(tenant, type_name, f"{label} {i}", [str(space.id)])


@transaction.atomic
def preset_divisible(tenant, label, count):
    _require_empty(tenant)
    n = _count(count, 2, 8)
    label = clean_name(label, "Label", 80)
    for i in range(1, n + 1):
        add_resource(tenant, f"{label} {i}")


# ---------------------------------------------------------------------------
# preview of what blocks what
# ---------------------------------------------------------------------------

def conflict_table(tenant):
    configs = list(BookableConfig._base_manager.filter(tenant=tenant, is_active=True)
                   .select_related('bookable_type').order_by('display_order', 'name'))
    cmap = config_resource_map(tenant)
    rows = []
    for c in configs:
        mine = {r.id for r in cmap.get(c.id, [])}
        blocks = [o.name for o in configs
                  if o.id != c.id and mine & {r.id for r in cmap.get(o.id, [])}]
        rows.append({'config': c, 'resources': cmap.get(c.id, []), 'blocks': blocks})
    return rows


# ---------------------------------------------------------------------------
# pricing
# ---------------------------------------------------------------------------

def required_rate_keys(tenant):
    keys = [('weekday', False), ('weekend', False)]
    if services.get_settings(tenant).lights_start:
        keys += [('weekday', True), ('weekend', True)]
    return keys


def types_in_use(tenant):
    return list(BookableType._base_manager.filter(tenant=tenant, configs__is_active=True)
                .distinct().order_by('display_order', 'name'))


def price_field(bookable_type, day, lights):
    return f"p_{bookable_type.id}_{day}_{int(lights)}"


def pricing_grid(tenant, posted=None):
    rules = {(r.bookable_type_id, r.day_type, r.lights): r.price_per_hour
             for r in PricingRule._base_manager.filter(tenant=tenant, is_active=True)}
    labels = dict(DAYS)
    rows = []
    for t in types_in_use(tenant):
        cells = []
        for day, lights in required_rate_keys(tenant):
            name = price_field(t, day, lights)
            value = posted.get(name, '') if posted is not None else rules.get((t.id, day, lights), '')
            cells.append({'name': name, 'value': value,
                          'label': f"{labels[day]}{' with lights' if lights else ''}"})
        rows.append({'type': t, 'cells': cells})
    return rows


def save_prices(tenant, post):
    """Returns a list of error strings. Nothing is saved if there are any."""
    parsed, errors = [], []
    for t in types_in_use(tenant):
        for day, lights in required_rate_keys(tenant):
            raw = (post.get(price_field(t, day, lights)) or '').strip()
            if raw == '':
                continue
            try:
                value = Decimal(raw)
            except InvalidOperation:
                errors.append(f"{t.name}: '{raw}' isn't a number.")
                continue
            if not value.is_finite() or value < 0 or value > MAX_PRICE:
                errors.append(f"{t.name}: price must be between 0 and {MAX_PRICE:,.0f}.")
                continue
            parsed.append((t, day, lights, value.quantize(Decimal('0.01'))))
    if errors:
        return errors
    with transaction.atomic():
        for t, day, lights, value in parsed:
            rule = (PricingRule._base_manager
                    .filter(tenant=tenant, bookable_type=t, day_type=day, lights=lights)
                    .order_by('-is_active').first())
            if rule:
                rule.price_per_hour = value
                rule.is_active = True
                rule.ground_type = t.name
                rule.save()
            else:
                PricingRule._base_manager.create(
                    tenant=tenant, bookable_type=t, ground_type=t.name, day_type=day,
                    lights=lights, price_per_hour=value, is_active=True)
    return []


# ---------------------------------------------------------------------------
# readiness
# ---------------------------------------------------------------------------

def setup_status(tenant):
    problems = {k: [] for k in ('profile', 'hours', 'grounds', 'pricing')}

    for label, value in (("business name", tenant.name), ("phone number", tenant.contact_phone),
                         ("address", tenant.address), ("city", tenant.city)):
        if not value:
            problems['profile'].append(f"Add your {label}.")

    configs = list(BookableConfig._base_manager.filter(tenant=tenant, is_active=True))
    cmap = config_resource_map(tenant)
    if not configs:
        problems['grounds'].append("Add at least one bookable ground option.")
    for cfg in configs:
        spaces = cmap.get(cfg.id, [])
        if not spaces:
            problems['grounds'].append(f"'{cfg.name}' has no physical space.")
        elif any(not r.is_active for r in spaces):
            problems['grounds'].append(f"'{cfg.name}' uses a space that is switched off.")

    types = types_in_use(tenant)
    if not types:
        problems['pricing'].append("Add ground options first, then set their prices.")
    rules = {(r.bookable_type_id, r.day_type, r.lights)
             for r in PricingRule._base_manager.filter(tenant=tenant, is_active=True)}
    keys = required_rate_keys(tenant)
    for t in types:
        missing = [k for k in keys if (t.id, k[0], k[1]) not in rules]
        if missing:
            problems['pricing'].append(f"{t.name}: {len(missing)} price(s) still missing.")

    done = {k: not v for k, v in problems.items()}
    return {'problems': problems, 'done': done, 'ok': all(done.values())}

# ---------------------------------------------------------------------------
# U2: layout wizard helpers
# ---------------------------------------------------------------------------

LAYOUTS = ('whole', 'separate', 'sections')


def current_layout(tenant):
    """The layout the owner chose, or a best guess for turfs set up before this existed."""
    from .models import TurfBusiness
    stored = TurfBusiness._base_manager.filter(pk=tenant.pk).values_list('layout_type', flat=True).first()
    if stored:
        return stored
    resources = Resource._base_manager.filter(tenant=tenant).count()
    if not resources:
        return ''
    configs = list(BookableConfig._base_manager.filter(tenant=tenant))
    cmap = config_resource_map(tenant)
    if resources == 1 and len(configs) == 1:
        return 'whole'
    if configs and len(configs) == resources and all(len(cmap.get(c.id, [])) == 1 for c in configs):
        return 'separate'
    return 'sections'


def _clean_names(values, what, low, high):
    names = [' '.join((v or '').split()) for v in values]
    names = [n for n in names if n]
    if not low <= len(names) <= high:
        raise SetupError(f"Enter between {low} and {high} {what}.")
    if len({n.lower() for n in names}) != len(names):
        raise SetupError("Give each one a different name.")
    return [clean_name(n, "Name") for n in names]


@transaction.atomic
def create_whole(tenant, type_name, ground_name):
    _require_empty(tenant)
    ground_name = clean_name(ground_name, "Ground name")
    type_name = clean_name(type_name or ground_name, "Sport or format")
    space = add_resource(tenant, ground_name)
    add_config(tenant, type_name, ground_name, [str(space.id)])


@transaction.atomic
def create_separate(tenant, type_name, names, per_ground_type):
    _require_empty(tenant)
    names = _clean_names(names, "grounds or courts", 1, 20)
    if not per_ground_type:
        type_name = clean_name(type_name, "Sport or format")
    for name in names:
        space = add_resource(tenant, name)
        add_config(tenant, name if per_ground_type else type_name, name, [str(space.id)])


@transaction.atomic
def create_sections(tenant, whole_name, section_names, add_whole):
    _require_empty(tenant)
    names = _clean_names(section_names, "sections", 2, 8)
    spaces = [add_resource(tenant, n) for n in names]
    if add_whole:
        whole = clean_name(whole_name or "Whole ground", "Name of the whole ground", 100)
        add_config(tenant, whole, whole, [str(s.id) for s in spaces])


@transaction.atomic
def add_singles(tenant, type_name):
    """One option per part, each using just that part."""
    type_name = clean_name(type_name, "Playing format")
    cmap = config_resource_map(tenant)
    bt = BookableType._base_manager.filter(tenant=tenant, name__iexact=type_name).first()
    taken = set()
    if bt:
        for c in BookableConfig._base_manager.filter(tenant=tenant, bookable_type=bt):
            taken.add(frozenset(r.id for r in cmap.get(c.id, [])))
    added = 0
    for r in Resource._base_manager.filter(tenant=tenant, is_active=True).order_by('display_order', 'name'):
        if frozenset([r.id]) in taken:
            continue
        add_config(tenant, type_name, f"{type_name} — {r.name}", [str(r.id)])
        added += 1
    if not added:
        raise SetupError("Those options already exist.")
    return added


@transaction.atomic
def add_neighbour_groups(tenant, type_name, size):
    """Options that use `size` parts that sit next to each other (1+2, 2+3, ...)."""
    size = _count(size, 2, 8)
    type_name = clean_name(type_name, "Playing format")
    spaces = list(Resource._base_manager.filter(tenant=tenant, is_active=True).order_by('display_order', 'name'))
    if size >= len(spaces):
        raise SetupError(f"You have {len(spaces)} parts. Choose a smaller number, or add the whole ground yourself.")
    cmap = config_resource_map(tenant)
    bt = BookableType._base_manager.filter(tenant=tenant, name__iexact=type_name).first()
    taken = set()
    if bt:
        for c in BookableConfig._base_manager.filter(tenant=tenant, bookable_type=bt):
            taken.add(frozenset(r.id for r in cmap.get(c.id, [])))
    added = 0
    for i in range(len(spaces) - size + 1):
        group = spaces[i:i + size]
        if frozenset(r.id for r in group) in taken:
            continue
        add_config(tenant, type_name, f"{type_name} — {' + '.join(r.name for r in group)}",
                   [str(r.id) for r in group])
        added += 1
    if not added:
        raise SetupError("Those combinations already exist.")
    return added


def set_config_online(config, online):
    config.online_bookable = online
    config.save(update_fields=['online_bookable'])