import uuid

from django.contrib import messages
from django.db import transaction
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from . import onboarding
from .models import BookableConfig, Sport
from .roles import MANAGE
from .tenancy import require_roles, tenant_view

ConfigSport = BookableConfig.sports.through     # the hidden table linking options and sports


def _sport(request):
    try:
        pk = uuid.UUID(request.POST.get('sport_id', ''))
    except ValueError:
        raise Http404
    return get_object_or_404(Sport._base_manager, pk=pk, tenant=request.tenant)


def _name_taken(tenant, name, exclude=None):
    qs = Sport._base_manager.filter(tenant=tenant, name__iexact=name)
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    return qs.exists()


@tenant_view
@require_roles(*MANAGE)
def sports_page(request):
    tenant = request.tenant
    sports = list(Sport._base_manager.filter(tenant=tenant)
                  .annotate(booking_count=Count('bookings', distinct=True)).order_by('name'))
    configs = list(BookableConfig._base_manager.filter(tenant=tenant)
                   .select_related('bookable_type').order_by('display_order', 'name'))
    ticked = {}
    for config_id, sport_id in ConfigSport.objects.filter(
            bookableconfig__tenant=tenant).values_list('bookableconfig_id', 'sport_id'):
        ticked.setdefault(config_id, set()).add(sport_id)

    active = [s for s in sports if s.is_active]
    stranded = [c.name for c in configs
                if c.is_active and ticked.get(c.id) and not (ticked[c.id] & {s.id for s in active})]
    return render(request, "setup/sports.html", {
        "active": "settings", "sports": sports, "active_sports": active,
        "matrix": [{"config": c, "ticked": ticked.get(c.id, set())} for c in configs],
        "stranded": stranded,
    })


@tenant_view
@require_roles(*MANAGE)
def sports_action(request):
    if request.method != "POST":
        return redirect('settings_sports')
    tenant, post, action = request.tenant, request.POST, request.POST.get('action', '')
    try:
        with transaction.atomic():
            if action == 'add':
                name = onboarding.clean_name(post.get('name'), "Sport name")
                if _name_taken(tenant, name):
                    raise onboarding.SetupError(f"You already have a sport called '{name}'.")
                Sport._base_manager.create(tenant=tenant, name=name)
                messages.success(request, f"{name} added.")
            elif action == 'rename':
                sport = _sport(request)
                name = onboarding.clean_name(post.get('name'), "Sport name")
                if _name_taken(tenant, name, exclude=sport):
                    raise onboarding.SetupError(f"You already have a sport called '{name}'.")
                sport.name = name
                sport.save(update_fields=['name'])
                messages.success(request, "Renamed. Old bookings now show the new name.")
            elif action == 'toggle':
                sport = _sport(request)
                sport.is_active = not sport.is_active
                sport.save(update_fields=['is_active'])
                messages.success(request, f"{sport.name} is now {'on' if sport.is_active else 'off'}.")
            elif action == 'save_matrix':
                valid = set(Sport._base_manager.filter(tenant=tenant).values_list('id', flat=True))
                for config in BookableConfig._base_manager.filter(tenant=tenant):
                    wanted = set()
                    for raw in post.getlist(f"cfg_{config.id}"):
                        try:
                            sid = uuid.UUID(raw)
                        except ValueError:
                            raise onboarding.SetupError("Invalid selection.")
                        if sid not in valid:
                            raise onboarding.SetupError("Invalid selection.")
                        wanted.add(sid)
                    ConfigSport.objects.filter(bookableconfig_id=config.id).delete()
                    ConfigSport.objects.bulk_create(
                        [ConfigSport(bookableconfig_id=config.id, sport_id=sid) for sid in wanted])
                messages.success(request, "Saved which sports each ground option allows.")
            else:
                raise onboarding.SetupError("Unknown action.")
    except onboarding.SetupError as e:
        messages.error(request, str(e))
    return redirect('settings_sports')