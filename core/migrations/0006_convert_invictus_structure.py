from django.db import migrations

INVICTUS_SLUG = 'the-invictus-field'
TENANT_MODELS = ['Customer', 'Booking', 'BookingPayment', 'Expense',
                 'Tournament', 'AuditLog', 'SyncQueue', 'PricingRule']


def convert(apps, schema_editor):
    TurfBusiness = apps.get_model('core', 'TurfBusiness')
    TenantSettings = apps.get_model('core', 'TenantSettings')
    Ground = apps.get_model('core', 'Ground')
    Resource = apps.get_model('core', 'Resource')
    BookableType = apps.get_model('core', 'BookableType')
    BookableConfig = apps.get_model('core', 'BookableConfig')
    ConfigResource = apps.get_model('core', 'ConfigResource')
    Booking = apps.get_model('core', 'Booking')
    TournamentGround = apps.get_model('core', 'TournamentGround')
    PricingRule = apps.get_model('core', 'PricingRule')

    try:
        tenant = TurfBusiness.objects.get(slug=INVICTUS_SLUG)
    except TurfBusiness.DoesNotExist:
        return

    # 1. Every existing row belongs to The Invictus Field.
    for name in TENANT_MODELS:
        apps.get_model('core', name).objects.filter(tenant__isnull=True).update(tenant=tenant)

    TenantSettings.objects.filter(tenant=tenant).update(booking_code_prefix='TIF')

    # 2. Types, resources and configs from the existing grounds.
    types = {}

    def get_type(type_name):
        if type_name not in types:
            types[type_name], _ = BookableType.objects.get_or_create(
                tenant=tenant, name=type_name, defaults={'display_order': len(types) + 1})
        return types[type_name]

    def make_config(name, bookable_type, resources, order, active):
        cfg, _ = BookableConfig.objects.get_or_create(
            tenant=tenant, name=name,
            defaults={'bookable_type': bookable_type, 'display_order': order, 'is_active': active})
        for r in resources:
            ConfigResource.objects.get_or_create(config=cfg, resource=r)
        return cfg

    grounds = list(Ground.objects.order_by('display_order', 'name'))
    six = [g for g in grounds if g.type == '6-a-side']
    sections = []
    for i, _g in enumerate(six, start=1):
        r, _ = Resource.objects.get_or_create(
            tenant=tenant, name=f"Section {i}", defaults={'display_order': i})
        sections.append(r)

    ground_config = {}   # ground id -> config
    eight_config = {}    # (ground id, '1_2' | '2_3') -> config
    order = 0
    for g in grounds:
        order += 1
        bt = get_type(g.type)
        if g.type == '6-a-side':
            ground_config[g.id] = make_config(g.name, bt, [sections[six.index(g)]], order, g.is_active)
        elif g.type == '8-a-side':
            for key, label, res in (('1_2', '1+2', sections[0:2]), ('2_3', '2+3', sections[1:3])):
                if len(res) == 2:
                    eight_config[(g.id, key)] = make_config(
                        f"{g.name} (Sections {label})", bt, res, order, g.is_active)
        elif g.type == '9-a-side' and sections:
            ground_config[g.id] = make_config(g.name, bt, sections, order, g.is_active)
        else:
            r, _ = Resource.objects.get_or_create(
                tenant=tenant, name=g.name, defaults={'display_order': 100 + order})
            ground_config[g.id] = make_config(g.name, bt, [r], order, g.is_active)

    def pick(ground_id, section_config):
        g = next((x for x in grounds if x.id == ground_id), None)
        if g is None:
            return None, False
        if g.type == '8-a-side':
            defaulted = not section_config
            return eight_config.get((g.id, section_config or '1_2')), defaulted
        return ground_config.get(g.id), False

    # 3. Backfill bookings and tournament grounds.
    defaulted_bookings = 0
    for b in Booking.objects.filter(config__isnull=True):
        cfg, defaulted = pick(b.ground_id, b.section_config)
        if cfg:
            Booking.objects.filter(pk=b.pk).update(config=cfg)
            defaulted_bookings += defaulted
    defaulted_tournaments = 0
    for tg in TournamentGround.objects.filter(config__isnull=True):
        cfg, defaulted = pick(tg.ground_id, None)
        if cfg:
            TournamentGround.objects.filter(pk=tg.pk).update(config=cfg)
            defaulted_tournaments += defaulted

    # 4. Price rules point at a type instead of a fixed ground-type word.
    for rule in PricingRule.objects.filter(bookable_type__isnull=True):
        PricingRule.objects.filter(pk=rule.pk).update(bookable_type=get_type(rule.ground_type))

    if defaulted_bookings or defaulted_tournaments:
        print(f"\n  NOTE: {defaulted_bookings} booking(s) and {defaulted_tournaments} tournament ground(s) "
              f"had no 8-a-side section choice and were assigned 'Sections 1+2'.")


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_resources_and_tenant_fields'),
    ]

    operations = [
        migrations.RunPython(convert, migrations.RunPython.noop),
    ]