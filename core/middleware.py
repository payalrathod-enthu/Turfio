from django.shortcuts import redirect

from .models import Membership
from .tenancy import tenant_context

SETUP_ALLOWED = ('/setup/', '/logout/', '/static/', '/media/', '/admin/', '/t/')


class TenantMiddleware:
    """Works out which business the logged-in user belongs to."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = None
        request.membership = None
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            membership = (
                Membership.objects
                .filter(user=user, is_active=True, tenant__status__in=('active', 'onboarding'))
                .select_related('tenant').order_by('created_at').first())
            if membership:
                request.tenant = membership.tenant
                request.membership = membership

        tenant = request.tenant
        if (tenant is not None and tenant.status == 'onboarding'
                and request.membership.role == 'owner'
                and not request.path.startswith(SETUP_ALLOWED)):
            return redirect('setup_home')

        with tenant_context(request.tenant):
            return self.get_response(request)