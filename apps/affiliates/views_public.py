"""The referral link: remember who sent the visitor, then send them on."""
from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme

from . import services
from .models import AffiliateSettings


def referral(request, code):
    """
    ``/r/<code>/``: set the referral cookie and go to the home page (or to a local ``?to=`` path). A code that does not
    work looks exactly like one that does, so nobody can probe for valid codes. The last link followed wins; a visitor
    who already carries that code is not counted again.
    """
    target = request.GET.get("to", "/")
    local = target.startswith("/") and not target.startswith("//")
    if not local or not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        target = "/"
    response = HttpResponseRedirect(target)
    affiliate = services.live_affiliate(code)
    if affiliate is not None:
        if request.COOKIES.get(services.COOKIE_NAME) != affiliate.code:
            services.count_visit(affiliate)
        response.set_cookie(services.COOKIE_NAME, affiliate.code, max_age=AffiliateSettings.load().cookie_days * 86400,
                            httponly=True, samesite="Lax", secure=not settings.DEBUG)
    return response
