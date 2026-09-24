"""
Unit tests for the real WHM API adapter, using mocked HTTP responses (there is
no live WHM server available in this environment - see the module docstring
in apps.hosting.adapters.whm_api). These verify our request-building and
response-parsing logic is internally consistent; they do not verify the
adapter against a real server's actual behaviour.
"""
from unittest import mock

import pytest

from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.whm_api import WhmApiAdapter, _parse_size_mb


def _adapter():
    return WhmApiAdapter(host="whm.example.com", port=2087,
                         credentials={"api_username": "root", "api_token": "secret-token"},
                         use_ssl=True, verify_ssl=True, timeout=10)


def _response(json_body, status_code=200):
    response = mock.Mock()
    response.status_code = status_code
    response.json.return_value = json_body
    response.raise_for_status = mock.Mock()
    if status_code >= 400:
        import requests

        response.raise_for_status.side_effect = requests.HTTPError("error")
    return response


@pytest.mark.parametrize("size,expected", [
    ("150M", 150), ("2.5G", 2560), ("500K", 1), ("unlimited", None), ("", None), (None, None), ("0", 0),
    ("garbage", None),
])
def test_parse_size_mb(size, expected):
    assert _parse_size_mb(size) == expected


def test_authorization_header_and_url_format():
    adapter = _adapter()
    with mock.patch("requests.get", return_value=_response({"metadata": {"result": 1}})) as mocked:
        adapter.suspend_account("example", "test")
    _, kwargs = mocked.call_args
    assert mocked.call_args[0][0] == "https://whm.example.com:2087/json-api/suspendacct"
    assert kwargs["headers"]["Authorization"] == "whm root:secret-token"
    assert kwargs["params"] == {"api.version": 1, "user": "example", "reason": "test"}
    assert kwargs["verify"] is True


def test_create_account_success_returns_username_as_provider_ref():
    adapter = _adapter()
    with mock.patch("requests.get", return_value=_response({"metadata": {"result": 1}})):
        result = adapter.create_account(username="example", domain="example.com", package="starter",
                                        contact_email="a@b.com", password="x")
    assert result == {"provider_ref": "example"}


def test_result_zero_raises_hosting_error_with_reason():
    adapter = _adapter()
    with mock.patch("requests.get", return_value=_response({"metadata": {"result": 0, "reason": "Account exists"}})):
        with pytest.raises(HostingError, match="Account exists"):
            adapter.create_account(username="example", domain="example.com", package="starter",
                                   contact_email="a@b.com", password="x")


def test_network_failure_raises_hosting_error():
    import requests

    adapter = _adapter()
    with mock.patch("requests.get", side_effect=requests.ConnectionError("refused")):
        with pytest.raises(HostingError, match="Could not reach the server"):
            adapter.suspend_account("example")


def test_non_json_response_raises_hosting_error():
    adapter = _adapter()
    bad_response = mock.Mock()
    bad_response.raise_for_status = mock.Mock()
    bad_response.json.side_effect = ValueError("not json")
    with mock.patch("requests.get", return_value=bad_response):
        with pytest.raises(HostingError, match="unexpected"):
            adapter.suspend_account("example")


def test_get_status_parses_accountsummary():
    payload = {"metadata": {"result": 1}, "data": {"acct": [{"suspended": "1", "domain": "example.com"}]}}
    adapter = _adapter()
    with mock.patch("requests.get", return_value=_response(payload)):
        result = adapter.get_status("example")
    assert result == {"suspended": True, "domain": "example.com"}


def test_get_status_raises_when_account_unknown():
    payload = {"metadata": {"result": 1}, "data": {"acct": []}}
    adapter = _adapter()
    with mock.patch("requests.get", return_value=_response(payload)):
        with pytest.raises(HostingError, match="no record"):
            adapter.get_status("ghost")


def test_get_usage_parses_disk_and_bandwidth():
    def fake_get(url, headers, params, timeout, verify):
        if "accountsummary" in url:
            return _response({"metadata": {"result": 1},
                              "data": {"acct": [{"diskused": "150M", "disklimit": "1000M"}]}})
        return _response({"metadata": {"result": 1}, "data": {"bandwidth": [{"totalbytes": 1024 * 1024 * 250}]}})

    adapter = _adapter()
    with mock.patch("requests.get", side_effect=fake_get):
        usage = adapter.get_usage("example")
    assert usage == {"disk_used_mb": 150, "disk_limit_mb": 1000, "bandwidth_used_mb": 250,
                     "bandwidth_limit_mb": None}


def test_get_usage_falls_back_to_none_on_any_failure():
    import requests

    adapter = _adapter()
    with mock.patch("requests.get", side_effect=requests.ConnectionError("refused")):
        usage = adapter.get_usage("example")
    assert usage == {"disk_used_mb": None, "disk_limit_mb": None, "bandwidth_used_mb": None,
                     "bandwidth_limit_mb": None}


def test_use_ssl_false_and_verify_ssl_false():
    adapter = WhmApiAdapter(host="10.0.0.5", port=2086, credentials={"api_username": "root", "api_token": "t"},
                            use_ssl=False, verify_ssl=False, timeout=5)
    with mock.patch("requests.get", return_value=_response({"metadata": {"result": 1}})) as mocked:
        adapter.unsuspend_account("example")
    assert mocked.call_args[0][0] == "http://10.0.0.5:2086/json-api/unsuspendacct"
    assert mocked.call_args[1]["verify"] is False
