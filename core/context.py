from .roles import FINANCE, MANAGE, OPS, OWNER_ONLY


def tenant_nav(request):
    membership = getattr(request, 'membership', None)
    role = membership.role if membership else None
    return {
        'tenant': getattr(request, 'tenant', None),
        'role': role,
        'can_ops': role in OPS,
        'can_finance': role in FINANCE,
        'can_manage': role in MANAGE,
        'is_owner': role in OWNER_ONLY,
    }