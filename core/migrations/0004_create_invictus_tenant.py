from datetime import time

from django.conf import settings
from django.db import migrations

INVICTUS_SLUG = 'the-invictus-field'


def create_invictus_tenant(apps, schema_editor):
    TurfBusiness = apps.get_model('core', 'TurfBusiness')
    Membership = apps.get_model('core', 'Membership')
    TenantSettings = apps.get_model('core', 'TenantSettings')
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))

    owner = User.objects.filter(is_superuser=True).order_by('id').first()

    tenant, _ = TurfBusiness.objects.get_or_create(
        slug=INVICTUS_SLUG,
        defaults={
            'name': 'The Invictus Field',
            'owner': owner,
            'city': 'Pune',
            'status': 'active',
        },
    )

    TenantSettings.objects.get_or_create(
        tenant=tenant,
        defaults={
            'operating_start': time(6, 0),
            'operating_end': time(23, 0),
            'lights_start': time(18, 0),
        },
    )

    # Existing staff become members. The first superuser is the owner.
    for user in User.objects.filter(is_staff=True):
        role = 'owner' if (owner and user.id == owner.id) else 'manager'
        Membership.objects.get_or_create(user=user, tenant=tenant, defaults={'role': role})


def remove_invictus_tenant(apps, schema_editor):
    TurfBusiness = apps.get_model('core', 'TurfBusiness')
    TurfBusiness.objects.filter(slug=INVICTUS_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_tenant_foundation'),
    ]

    operations = [
        migrations.RunPython(create_invictus_tenant, remove_invictus_tenant),
    ]
    