"""
Looking at the machine, not at what runs on it: a thin client for the company that rents us a server (today only Contabo).

This is separate from the hosting adapters on purpose. An adapter creates and manages hosting accounts on a cPanel/WHM
server; this module only asks the provider "which machines does this login own, and what state are they in?" so staff can check
that a server's IP address and status are what they think. Everything here is read-only and nothing is ever created.

Contabo's API (api.contabo.com): you sign in at its auth server with the OAuth2 "password" grant (client ID, client
secret, API user, API password) to get a short-lived token, then call ``/v1/compute/instances``. The credentials travel in a
POST body only; error messages never contain them.
"""
import uuid

import requests
from django.conf import settings

TIMEOUT = 20  # the two Contabo addresses live in settings (CONTABO_AUTH_URL, CONTABO_API_URL), not in the code


class InfraError(Exception):
    """The provider refused us, was unreachable, or answered in a way we do not understand."""


def _plain(exc):
    return str(exc).split("?", 1)[0]  # an exception message may quote an address; never anything after a "?"


def contabo_instances(client_id, client_secret, api_user, api_password):
    """The machines this Contabo login can see: ``[{"id", "name", "status", "region", "ip"}]``. Read-only."""
    if not (client_id and client_secret and api_user and api_password):
        raise InfraError("Enter the client ID, client secret, API user and API password first.")
    try:
        auth = requests.post(settings.CONTABO_AUTH_URL, data={"client_id": client_id, "client_secret": client_secret, "username": api_user,
                                             "password": api_password, "grant_type": "password"}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise InfraError(f"Could not reach Contabo's sign-in service: {_plain(exc)}") from exc
    if auth.status_code in (400, 401, 403):
        raise InfraError("Contabo refused the login. Check the client ID, client secret, API user (an e-mail address) and "
                         "API password; the API password is the one you set under API in the Customer Control Panel.")
    try:
        auth.raise_for_status()
        token = auth.json()["access_token"]
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise InfraError("Contabo's sign-in service answered in an unexpected way.") from exc
    try:
        response = requests.get(f"{settings.CONTABO_API_URL.rstrip('/')}/compute/instances", params={"size": 100}, timeout=TIMEOUT, headers={
            "Authorization": f"Bearer {token}", "x-request-id": str(uuid.uuid4())})
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise InfraError(f"Contabo did not return the machine list: {_plain(exc)}") from exc
    except ValueError as exc:
        raise InfraError("Contabo returned a machine list we could not read.") from exc
    found = []
    for item in payload.get("data") or []:
        v4 = ((item.get("ipConfig") or {}).get("v4") or {})
        found.append({"id": str(item.get("instanceId", "")), "name": item.get("displayName") or item.get("name") or "",
                      "status": item.get("status") or "unknown", "region": item.get("region") or "", "ip": v4.get("ip") or ""})
    return found


def instances_for(server):
    """Dispatch on the server's infrastructure provider. Only Contabo is supported."""
    if server.infra_provider == "contabo":
        return contabo_instances(server.infra_client_id, server.get_infra_client_secret(), server.infra_api_user,
                                 server.get_infra_api_password())
    raise InfraError("Choose an infrastructure provider for this server first.")
