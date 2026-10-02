from rest_framework.throttling import ScopedRateThrottle


class StorefrontRateThrottle(ScopedRateThrottle):
    """
    Same scoped-rate mechanism the rest of the API already uses for auth endpoints, but keyed by the storefront's
    own ``public_id`` rather than by IP - one storefront hammering the catalog never eats into another's allowance,
    and a storefront behind a shared/rotating IP (a caching layer, a host's own proxy) is not needlessly punished.
    Falls back to IP (the base class's own behaviour) when there is no authenticated storefront yet, e.g. a request
    with a bad credential, which ``StorefrontKeyAuthentication`` rejects before this throttle would matter anyway.
    """

    scope = "storefront"

    def get_cache_key(self, request, view):
        from .models import ExternalStorefront

        if isinstance(request.auth, ExternalStorefront):
            ident = f"storefront:{request.auth.public_id}"
            return self.cache_format % {"scope": self.scope, "ident": ident}
        return super().get_cache_key(request, view)
