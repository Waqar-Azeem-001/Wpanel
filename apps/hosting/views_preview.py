"""
The one internal answer the panel's web server asks for before it forwards a preview request: which website does this preview host belong to, and where is its
hosting server. Only the web server on this machine may ask (it sends the shared key); anyone else gets a plain 403, so the mapping is never public.
"""
import hmac

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.http import require_GET

from . import preview


@require_GET
def resolve(request):
    key = getattr(settings, "PREVIEW_PROXY_KEY", "")
    sent = request.headers.get("X-Preview-Key", "")
    if not key or not hmac.compare_digest(key.encode(), sent.encode()):
        return HttpResponse(status=403)
    preview_host = request.headers.get("X-Preview-Host", "").lower().split(":")[0]
    account = preview.account_for_host(preview_host)
    server_ip = str(account.server.ip_address) if account is not None and account.server is not None and account.server.ip_address else ""
    if account is None or not server_ip:
        return HttpResponse(status=403)  # nginx's auth_request reads 401/403 as "not allowed" and shows its unknown-address answer
    reply = HttpResponse(status=204)
    reply["X-Real-Host"] = account.domain.lower()
    reply["X-Backend"] = server_ip
    reply["X-Preview-Token"] = preview.token(preview_host)
    return reply
