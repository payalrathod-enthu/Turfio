import contextvars
from contextlib import contextmanager
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models
from django.shortcuts import redirect, render

_current_tenant = contextvars.ContextVar("current_tenant", default=None)


def get_current_tenant():
    return _current_tenant.get()


@contextmanager
def tenant_context(tenant):
    token = _current_tenant.set(tenant)
    try:
        yield
    finally:
        _current_tenant.reset(token)


class TenantQuerySet(models.QuerySet):
    def create(self, **kwargs):
        if 'tenant' not in kwargs and 'tenant_id' not in kwargs:
            tenant = get_current_tenant()
            if tenant is not None:
                kwargs['tenant'] = tenant
        return super().create(**kwargs)


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    """Fails closed: with no active tenant it returns nothing."""

    def get_queryset(self):
        qs = super().get_queryset()
        tenant = get_current_tenant()
        if tenant is None:
            return qs.none()
        return qs.filter(tenant=tenant)


def tenant_view(view_func):
    """Login required, and the user must belong to a business."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if getattr(request, 'tenant', None) is None:
            return render(request, 'core/no_tenant.html', status=403)
        return view_func(request, *args, **kwargs)
    return _wrapped


def require_roles(*roles):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            membership = getattr(request, 'membership', None)
            if membership is None or membership.role not in roles:
                messages.error(request, "You don't have permission to open that page.")
                return redirect('dashboard')
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator