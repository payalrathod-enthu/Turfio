from datetime import time
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from core.models import (BookableConfig, BookableType, ConfigResource, Membership,
                         PricingRule, Resource, TenantSettings, TurfBusiness)

# (weekday no-lights, weekday lights, weekend no-lights, weekend lights)
PRICES = {
    '6-a-side': (900, 1000, 900, 1000),
    '8-a-side': (1600, 1800, 1700, 1900),
    '9-a-side': (2500, 2700, 2700, 2900),
}


class Command(BaseCommand):
    help = "Creates The Invictus Field with its sections, ground options and prices."

    def handle(self, *args, **options):
        owner = User.objects.filter(is_superuser=True).order_by('id').first()
        if owner is None:
            self.stderr.write("Create a superuser first: python manage.py createsuperuser")
            return

        tenant, _ = TurfBusiness._base_manager.get_or_create(
            slug='the-invictus-field',
            defaults={'name': 'The Invictus Field', 'owner': owner, 'city': 'Pune', 'status': 'active'})
        TenantSettings._base_manager.get_or_create(
            tenant=tenant,
            defaults={'operating_start': time(6, 0), 'operating_end': time(23, 0),
                      'lights_start': time(18, 0), 'booking_code_prefix': 'TIF'})
        Membership._base_manager.get_or_create(user=owner, tenant=tenant, defaults={'role': 'owner'})

        sections = {}
        for i in (1, 2, 3):
            sections[i], _ = Resource._base_manager.get_or_create(
                tenant=tenant, name=f"Section {i}", defaults={'display_order': i})

        types = {}
        for order, name in enumerate(PRICES, start=1):
            types[name], _ = BookableType._base_manager.get_or_create(
                tenant=tenant, name=name, defaults={'display_order': order})

        options = [
            ("6-a-side 1", '6-a-side', [1]),
            ("6-a-side 2", '6-a-side', [2]),
            ("6-a-side 3", '6-a-side', [3]),
            ("8-a-side (Sections 1+2)", '8-a-side', [1, 2]),
            ("8-a-side (Sections 2+3)", '8-a-side', [2, 3]),
            ("9-a-side", '9-a-side', [1, 2, 3]),
        ]
        for order, (name, type_name, section_numbers) in enumerate(options, start=1):
            cfg, _ = BookableConfig._base_manager.get_or_create(
                tenant=tenant, name=name,
                defaults={'bookable_type': types[type_name], 'display_order': order})
            for n in section_numbers:
                ConfigResource.objects.get_or_create(config=cfg, resource=sections[n])

        for type_name, (wd, wdl, we, wel) in PRICES.items():
            for day, lights, price in (('weekday', False, wd), ('weekday', True, wdl),
                                       ('weekend', False, we), ('weekend', True, wel)):
                PricingRule._base_manager.get_or_create(
                    tenant=tenant, bookable_type=types[type_name], day_type=day, lights=lights,
                    defaults={'ground_type': type_name, 'price_per_hour': Decimal(price), 'is_active': True})

        self.stdout.write(self.style.SUCCESS(
            f"Ready: {tenant.name} — {len(sections)} sections, {len(options)} ground options, "
            f"{PricingRule._base_manager.filter(tenant=tenant).count()} price rules."))
        