from django.core.cache import cache


def client_ip(request):
    return request.META.get('REMOTE_ADDR', 'unknown')


def allow(key, limit, window_seconds):
    """True while under `limit` hits per window. Uses the Django cache.

    The default cache is per-process, which is fine for development.
    Production should use a shared cache (set up in 18K).
    """
    cache.add(key, 0, window_seconds)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, window_seconds)
        count = 1
    return count <= limit