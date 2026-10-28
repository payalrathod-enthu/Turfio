import uuid

from django.contrib import messages
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from . import onboarding, services
from .forms_setup import DAY_NAMES, ProfileForm, RulesForm
from .models import (BookableConfig, BookableType, Booking, ConfigResource, Resource,
                     TournamentGround, TurfBusiness)
from .roles import MANAGE
from .tenancy import require_roles, tenant_view

NEXT_PAGES = {'setup_names', 'setup_grounds'}
LAYOUT_LABELS = {'whole': 'One full ground', 'separate': 'Several separate grounds or courts',
                 'sections': 'One large ground divided into sections'}


def _lights_text(s):
    if not s.lights_start:
        return "No lights. Nobody is charged a lights price."
    if s.lights_start.hour == 0 and s.lights_start.minute == 0 and not s.lights_end:
        return "Lights are available all the time."
    text = f"Lights are available from {s.lights_start.strftime('%I:%M %p').lstrip('0')}"
    if s.lights_end:
        text += f" to {s.lights_end.strftime('%I:%M %p').lstrip('0')}"
    return text + "."


def _ctx(request, step, **extra):
    tenant = request.tenant
    status = onboarding.setup_status(tenant)
    layout = onboarding.current_layout(tenant)
    has_spaces = Resource._base_manager.filter(tenant=tenant).exists()
    steps = [
        {'key': 'profile', 'label': 'Business', 'url': reverse('setup_profile'), 'done': status['done']['profile']},
        {'key': 'layout', 'label': 'Your layout', 'url': reverse('setup_layout'), 'done': bool(layout)},
        {'key': 'names', 'label': 'Name your grounds', 'url': reverse('setup_names'), 'done': has_spaces},
        {'key': 'grounds', 'label': 'What customers book', 'url': reverse('setup_grounds'), 'done': status['done']['grounds']},
        {'key': 'hours', 'label': 'Hours & rules', 'url': reverse('setup_hours'), 'done': True},
        {'key': 'pricing', 'label': 'Prices', 'url': reverse('setup_pricing'), 'done': status['done']['pricing']},
        {'key': 'review', 'label': 'Review & launch', 'url': reverse('setup_review'), 'done': tenant.status == 'active'},
    ]
    ctx = {'active': 'settings', 'step': step, 'steps': steps, 'status': status,
           'wizard': tenant.status == 'onboarding'}
    ctx.update(extra)
    return ctx


@tenant_view
@require_roles(*MANAGE)
def setup_home(request):
    tenant = request.tenant
    if tenant.status != 'onboarding':
        return redirect('settings_page')
    status = onboarding.setup_status(tenant)
    if not status['done']['profile']:
        return redirect('setup_profile')
    if not Resource._base_manager.filter(tenant=tenant).exists():
        return redirect('setup_names' if onboarding.current_layout(tenant) else 'setup_layout')
    if not status['done']['grounds']:
        return redirect('setup_grounds')
    if not status['done']['pricing']:
        return redirect('setup_pricing')
    return redirect('setup_review')


# 1. business -----------------------------------------------------------------

@tenant_view
@require_roles(*MANAGE)
def setup_profile(request):
    instance = TurfBusiness._base_manager.get(pk=request.tenant.pk)
    if request.method == "POST":
        form = ProfileForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, "Business details saved.")
            return redirect('setup_layout' if request.tenant.status == 'onboarding' else 'setup_profile')
    else:
        form = ProfileForm(instance=instance)
    return render(request, "setup/profile.html", _ctx(request, 'profile', form=form))


# 2. layout -------------------------------------------------------------------

@tenant_view
@require_roles(*MANAGE)
def setup_layout(request):
    tenant = request.tenant
    current = onboarding.current_layout(tenant)
    has_existing = Resource._base_manager.filter(tenant=tenant).exists()

    if request.method == "POST":
        choice = request.POST.get('layout')
        if choice not in onboarding.LAYOUTS:
            messages.error(request, "Please choose one of the three setups.")
            return redirect('setup_layout')
        if has_existing and current and choice != current and request.POST.get('confirm') != '1':
            return render(request, "setup/layout_confirm.html", _ctx(
                request, 'layout', choice=choice, current=current, labels=LAYOUT_LABELS,
                booking_count=Booking._base_manager.filter(config__tenant=tenant).count()))
        TurfBusiness._base_manager.filter(pk=tenant.pk).update(layout_type=choice)
        if has_existing:
            messages.info(request, "Layout saved. Your existing grounds and bookings were not changed.")
            return redirect('setup_grounds')
        return redirect('setup_names')

    return render(request, "setup/layout.html", _ctx(
        request, 'layout', current=current, has_existing=has_existing))


# 3. names --------------------------------------------------------------------

@tenant_view
@require_roles(*MANAGE)
def setup_names(request):
    tenant = request.tenant
    layout = onboarding.current_layout(tenant)
    if not layout:
        return redirect('setup_layout')
    resources = list(Resource._base_manager.filter(tenant=tenant).order_by('display_order', 'name'))
    return render(request, "setup/names.html", _ctx(request, 'names', layout=layout, resources=resources))


# 4. what customers can book ---------------------------------------------------

@tenant_view
@require_roles(*MANAGE)
def setup_grounds(request):
    tenant = request.tenant
    resources = list(Resource._base_manager.filter(tenant=tenant).order_by('display_order', 'name'))
    cmap = onboarding.config_resource_map(tenant)
    configs = list(BookableConfig._base_manager.filter(tenant=tenant)
                   .select_related('bookable_type')
                   .order_by('bookable_type__display_order', 'display_order', 'name'))

    used_configs = (set(Booking._base_manager.filter(config__tenant=tenant).values_list('config_id', flat=True))
                    | set(TournamentGround._base_manager.filter(config__tenant=tenant).values_list('config_id', flat=True)))
    used_resources = set(ConfigResource.objects.filter(config__tenant=tenant).values_list('resource_id', flat=True))
    active_resources = [r for r in resources if r.is_active]

    return render(request, "setup/grounds.html", _ctx(
        request, 'grounds',
        layout=onboarding.current_layout(tenant),
        resource_rows=[{'resource': r, 'can_delete': r.id not in used_resources} for r in resources],
        active_resources=active_resources,
        sizes=list(range(2, len(active_resources))),
        config_rows=[{'config': c, 'resources': cmap.get(c.id, []), 'can_delete': c.id not in used_configs}
                     for c in configs],
        type_names=list(BookableType._base_manager.filter(tenant=tenant).values_list('name', flat=True)),
        conflicts=onboarding.conflict_table(tenant)))


def _obj(model, request, key):
    try:
        pk = uuid.UUID(request.POST.get(key, ''))
    except ValueError:
        raise Http404
    return get_object_or_404(model._base_manager, pk=pk, tenant=request.tenant)


def _run_action(request, action):
    tenant, post = request.tenant, request.POST

    if action == 'create_whole':
        onboarding.create_whole(tenant, post.get('type_name'), post.get('ground_name'))
        return "Your ground is ready. Now check what customers can book."
    if action == 'create_separate':
        onboarding.create_separate(tenant, post.get('type_name'), post.getlist('names'),
                                   post.get('per_ground_type') == 'on')
        return "Your grounds are ready. Now check what customers can book."
    if action == 'create_sections':
        onboarding.create_sections(tenant, post.get('whole_name'), post.getlist('names'),
                                   post.get('add_whole') == 'on')
        return "Your sections are ready. Now choose what customers can book."
    if action == 'add_singles':
        return f"{onboarding.add_singles(tenant, post.get('type_name'))} option(s) added."
    if action == 'add_neighbours':
        return f"{onboarding.add_neighbour_groups(tenant, post.get('type_name'), post.get('size'))} combination(s) added."
    if action == 'toggle_online':
        c = _obj(BookableConfig, request, 'config_id')
        onboarding.set_config_online(c, not c.online_bookable)
        return "Online booking updated."

    # older actions, kept so nothing that already works breaks
    if action == 'preset_whole':
        onboarding.preset_whole(tenant, post.get('type_name'), post.get('space_name'))
        return "Ground added."
    if action == 'preset_independent':
        onboarding.preset_independent(tenant, post.get('type_name'), post.get('label'), post.get('count'))
        return "Grounds added."
    if action == 'preset_divisible':
        onboarding.preset_divisible(tenant, post.get('label'), post.get('count'))
        return "Sections added."
    if action == 'add_resource':
        onboarding.add_resource(tenant, post.get('name'))
        return "Added."
    if action == 'rename_resource':
        onboarding.rename_resource(_obj(Resource, request, 'resource_id'), post.get('name'))
        return "Renamed."
    if action == 'toggle_resource':
        r = _obj(Resource, request, 'resource_id')
        onboarding.set_resource_active(r, not r.is_active)
        return "Updated."
    if action == 'delete_resource':
        onboarding.delete_resource(_obj(Resource, request, 'resource_id'))
        return "Deleted."
    if action == 'add_config':
        onboarding.add_config(tenant, post.get('type_name'), post.get('option_name'), post.getlist('resources'))
        return "Option added."
    if action == 'rename_config':
        onboarding.rename_config(_obj(BookableConfig, request, 'config_id'), post.get('name'))
        return "Renamed."
    if action == 'toggle_config':
        c = _obj(BookableConfig, request, 'config_id')
        onboarding.set_config_active(c, not c.is_active)
        return "Updated."
    if action == 'delete_config':
        onboarding.delete_config(_obj(BookableConfig, request, 'config_id'))
        return "Deleted."
    raise onboarding.SetupError("Unknown action.")


@tenant_view
@require_roles(*MANAGE)
def grounds_action(request):
    if request.method != "POST":
        return redirect('setup_grounds')
    action = request.POST.get('action', '')
    target = request.POST.get('next')
    if target not in NEXT_PAGES:
        target = 'setup_grounds'
    try:
        with transaction.atomic():
            message = _run_action(request, action)
        messages.success(request, message)
        if action.startswith('create_'):
            target = 'setup_grounds'
    except onboarding.SetupError as e:
        messages.error(request, str(e))
    return redirect(target)


# 5. hours and rules -----------------------------------------------------------

@tenant_view
@require_roles(*MANAGE)
def setup_hours(request):
    tenant = request.tenant
    instance = services.get_settings(tenant)
    if request.method == "POST":
        form = RulesForm(request.POST, tenant=tenant, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, "Hours and booking rules saved.")
            return redirect('setup_pricing' if tenant.status == 'onboarding' else 'setup_hours')
    else:
        form = RulesForm(tenant=tenant, instance=instance)
    return render(request, "setup/hours.html", _ctx(request, 'hours', form=form))


# 6. prices --------------------------------------------------------------------

@tenant_view
@require_roles(*MANAGE)
def setup_pricing(request):
    tenant = request.tenant
    posted = None
    if request.method == "POST":
        errors = onboarding.save_prices(tenant, request.POST)
        if errors:
            for e in errors:
                messages.error(request, e)
            posted = request.POST
        else:
            messages.success(request, "Prices saved.")
            return redirect('setup_review' if tenant.status == 'onboarding' else 'setup_pricing')
    rows = onboarding.pricing_grid(tenant, posted)
    return render(request, "setup/pricing.html", _ctx(
        request, 'pricing', rows=rows,
        headers=[c['label'] for c in rows[0]['cells']] if rows else [],
        lights_on=bool(services.get_settings(tenant).lights_start)))


# 7. review and launch ---------------------------------------------------------

@tenant_view
@require_roles(*MANAGE)
def setup_review(request):
    tenant = request.tenant
    status = onboarding.setup_status(tenant)

    if request.method == "POST":
        if tenant.status != 'onboarding':
            return redirect('dashboard')
        if not status['ok']:
            messages.error(request, "Finish the items below before going live.")
            return redirect('setup_review')
        TurfBusiness._base_manager.filter(pk=tenant.pk).update(status='active')
        messages.success(request, "Your turf is live. Customers can now book it.")
        return redirect('dashboard')

    s = services.get_settings(tenant)
    rules = [
        f"A new booking can start every {s.slot_interval_minutes} minutes.",
        f"Bookings last from {services.duration_label(s.min_duration_minutes)} to {services.duration_label(s.max_duration_minutes)}.",
        f"Online bookings need {services.notice_label(s.min_advance_notice_minutes)}.",
        f"Customers can book up to {s.max_advance_days} days ahead.",
        f"Booking numbers look like {s.booking_code_prefix}-1001.",
    ]
    return render(request, "setup/review.html", _ctx(
        request, 'review',
        biz=TurfBusiness._base_manager.get(pk=tenant.pk),
        week_rows=[{'day': DAY_NAMES[r.weekday], 'row': r} for r in services.get_week(tenant)],
        lights_text=_lights_text(s), rules=rules,
        layout_label=LAYOUT_LABELS.get(onboarding.current_layout(tenant), ''),
        conflicts=onboarding.conflict_table(tenant),
        price_rows=onboarding.pricing_grid(tenant),
        public_path=f"/t/{tenant.slug}/"))