"""When the WHM server cannot be reached, the message says why (timeout, refused, name, certificate) - and still never leaks a query string."""
from unittest import mock

import pytest
import requests

from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.whm_api import WhmApiAdapter, _describe, _redact_url


def adapter():
    return WhmApiAdapter(host="whm.example.com", port=2087, credentials={"api_username": "root", "api_token": "t"})


def test_the_cause_after_the_address_is_kept_but_the_query_string_is_removed():
    exc = requests.ConnectionError("HTTPSConnectionPool(host='h', port=2087): Max retries exceeded with url: /json-api/createacct"
                                   "?api.version=1&password=s3cret-P4ss (Caused by NewConnectionError('boom'))")
    text = _redact_url(exc)
    assert "s3cret-P4ss" not in text and "api.version" not in text and "Caused by NewConnectionError" in text and "/json-api/createacct" in text


@pytest.mark.parametrize("exc,expected", [
    (requests.ConnectTimeout("Connection to whm.example.com timed out. (connect timeout=30)"), "did not answer in time"),
    (requests.ReadTimeout("Read timed out"), "did not answer in time"),
    (requests.ConnectionError("Failed to establish a new connection: [Errno 111] Connection refused"), "refused the connection"),
    (requests.ConnectionError("[WinError 10061] No connection could be made because the target machine actively refused it"), "refused the connection"),
    (requests.ConnectionError("Failed to resolve 'whm.example.com' ([Errno -2] Name or service not known)"), "name could not be found"),
    (requests.ConnectionError("[Errno 101] Network is unreachable"), "no network route"),
    (requests.exceptions.SSLError("certificate verify failed: self-signed certificate"), "certificate could not be verified"),
    (requests.ConnectionError("something else entirely"), "something else entirely"),
])
def test_each_kind_of_network_failure_is_explained(exc, expected):
    assert expected in _describe(exc)


def test_a_timeout_while_creating_an_account_says_so_and_still_hides_the_password():
    timeout = requests.ConnectTimeout("HTTPSConnectionPool(host='whm.example.com', port=2087): Max retries exceeded with url: "
                                      "/json-api/createacct?password=s3cret-P4ss (Caused by ConnectTimeoutError('timed out'))")
    with mock.patch("requests.post", side_effect=timeout):
        with pytest.raises(HostingError) as caught:
            adapter().create_account(username="example", domain="example.com", package="starter", contact_email="a@b.com", password="s3cret-P4ss")
    message = str(caught.value)
    assert message.startswith("Could not reach the server:") and "did not answer in time" in message and "public IP address" in message
    assert "s3cret-P4ss" not in message
