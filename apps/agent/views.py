"""The two endpoints a node's agent calls. The agent always connects out to WHPanel; WHPanel never connects in (ADR 16)."""
import json
import time

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.core.exceptions import ServiceError

from . import enrollment, services, throttle


MAX_LONG_POLL = 25


def _node(request):
    header = request.headers.get("Authorization", "")
    node = services.authenticate(header[6:].strip() if header.startswith("Agent ") else "", request.headers.get(settings.AGENT_CERT_HEADER, ""))
    if node is not None:
        throttle.node_hit(node.pk)  # raises RateLimited when this node is over its minute; the guard turns it into 429
    return node


def _body(request):
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _fail(error):
    return JsonResponse({"error": error.message}, status=error.status_code)


@csrf_exempt
@require_POST
@throttle.guarded_enrolment
def enroll(request):
    """The only call that needs no identity yet: it carries the one-time token."""
    body = _body(request)
    if body is None:
        return JsonResponse({"error": "Send a JSON object."}, status=400)
    try:
        reply = enrollment.enroll(body.get("token"), body.get("csr"), body.get("node"), body.get("agent_version", ""), request)
    except ServiceError as exc:
        return _fail(exc)
    return JsonResponse(reply)


@csrf_exempt
@require_POST
@throttle.guarded
def renew(request):
    node = _node(request)
    body = _body(request)
    if node is None:
        return JsonResponse({"error": "Unknown agent."}, status=401)
    if body is None:
        return JsonResponse({"error": "Send a JSON object."}, status=400)
    try:
        return JsonResponse(enrollment.renew(node, body.get("csr"), request))
    except ServiceError as exc:
        return _fail(exc)


@csrf_exempt
@require_POST
@throttle.guarded
def heartbeat(request):
    node = _node(request)
    body = _body(request)
    if node is None:
        return JsonResponse({"error": "Unknown agent."}, status=401)
    if body is None:
        return JsonResponse({"error": "Send a JSON object."}, status=400)
    try:
        enrollment.heartbeat(node, body)
    except ServiceError as exc:
        return _fail(exc)
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
@throttle.guarded
def poll(request):
    node = _node(request)
    if node is None:
        return JsonResponse({"error": "Unknown agent."}, status=401)
    wait = min(max(int(request.GET.get("wait", 0)) if request.GET.get("wait", "0").isdigit() else 0, 0), MAX_LONG_POLL)
    deadline = time.monotonic() + wait
    while True:  # long poll: the agent may ask to be kept waiting a few seconds so a customer's screen is not slowed by the polling interval
        try:
            envelope = services.poll(node, request.headers.get("X-Agent-Version", ""))
        except ServiceError as exc:
            return _fail(exc)
        if envelope or time.monotonic() >= deadline:
            return JsonResponse({"job": envelope})  # {"job": null} when there is nothing to do
        time.sleep(0.5)


@csrf_exempt
@require_POST
@throttle.guarded
def result(request, job_id):
    node = _node(request)
    if node is None:
        return JsonResponse({"error": "Unknown agent."}, status=401)
    body = _body(request)
    if body is None:
        return JsonResponse({"error": "Send a JSON object."}, status=400)
    try:
        job = services.record_result(node, job_id, body)
    except ServiceError as exc:
        return _fail(exc)
    return JsonResponse({"status": job.status})
