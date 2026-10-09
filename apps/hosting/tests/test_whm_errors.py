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


def test_a_slow_read_is_asked_for_again_once_but_a_change_never_is():
    timeout = requests.ConnectTimeout("timed out")
    ok = mock.Mock(status_code=200)
    ok.json.return_value = {"metadata": {"result": 1}, "data": {"version": "11"}}
    with mock.patch("requests.get", side_effect=[timeout, ok]) as get:
        assert adapter()._call("version")["data"]["version"] == "11" and get.call_count == 2
    with mock.patch("requests.get", side_effect=[timeout, timeout]) as get:
        with pytest.raises(HostingError, match="did not answer in time"):
            adapter()._call("version")
    assert get.call_count == 2
    with mock.patch("requests.post", side_effect=[timeout, ok]) as post:
        with pytest.raises(HostingError, match="did not answer in time"):
            adapter()._call("createacct", {"username": "x"}, sensitive=True)
    assert post.call_count == 1  # an account must never be created twice because an answer was slow


def test_a_call_that_changes_something_is_never_repeated_even_when_it_is_sent_as_a_plain_read_request():
    from apps.hosting.adapters.whm_api import is_read

    for function, params in (("version", {}), ("dumpzone", {}), ("uapi_cpanel", {"cpanel.function": "list_pops_with_disk"}), ("uapi_cpanel", {"cpanel.function": "domains_data"}),
                             ("cpanel", {"cpanel_jsonapi_func": "listcron"}), ("uapi_cpanel", {"cpanel.function": "get_site_errors"})):
        assert is_read(function, params), (function, params)
    for function, params in (("removezonerecord", {}), ("createacct", {}), ("passwd", {}), ("uapi_cpanel", {"cpanel.function": "delete_forwarder"}),
                             ("uapi_cpanel", {"cpanel.function": "add_pop"}), ("cpanel", {"cpanel_jsonapi_func": "fileop"}), ("uapi_cpanel", {}), ("mystery", {})):
        assert not is_read(function, params), (function, params)
    timeout = requests.ConnectTimeout("timed out")
    with mock.patch("requests.get", side_effect=[timeout, mock.Mock()]) as get:
        with pytest.raises(HostingError, match="may still have been made"):
            adapter()._call("uapi_cpanel", {"cpanel.user": "a", "cpanel.module": "Email", "cpanel.function": "delete_forwarder"})
    assert get.call_count == 1
