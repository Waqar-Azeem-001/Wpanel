"""
The cPanel adapter against answers copied from a REAL cPanel/WHM server (11.138, read-only checks on 9 Oct 2026): SSL certificates with flat dotted keys,
FTP logins of every type, folders to hide, plus the FTP and log tools built on those shapes. Writes (add, change password, delete) are still unverified live.
"""
import time

import pytest

from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.capabilities import CapabilityNotSupported
from apps.hosting.tests.test_cpanel_adapter import USER, adapter

HOME = {("uapi_cpanel", "Variables::get_user_information"): {"home": "/home/acme"}}

REAL_SSL = [{"ip": "173.212.243.56", "certificate": {
    "not_before": int(time.time()) - 86400, "not_after": int(time.time()) + 300 * 86400, "issuer.commonName": "testing2.com", "issuer.organizationName": None,
    "issuer_text": "commonName\ntesting2.com", "subject.commonName": "testing2.com", "is_self_signed": 1, "is_autossl": 0, "modulus_length": 2048,
    "domains": ["acme.com", "www.acme.com", "mail.acme.com"]}}]

REAL_FTP = [{"user": "acme", "homedir": "/home/acme", "type": "main"}, {"user": "acme_logs", "homedir": "/etc/apache2/logs/domlogs/acme", "type": "logaccess"},
            {"homedir": "/home/acme/public_ftp", "type": "anonymous", "user": "ftp"}, {"user": "anonymous", "type": "anonymous", "homedir": "/home/acme/public_ftp"},
            {"user": "dev@acme.com", "homedir": "/home/acme/public_html/site", "type": "sub"}]

DOMAINS = {("uapi_cpanel", "DomainInfo::domains_data"): {"main_domain": {"domain": "acme.com", "documentroot": "/home/acme/public_html"},
                                                           "addon_domains": [], "sub_domains": [], "parked_domains": []}}


def test_a_real_self_signed_certificate_is_named_as_such_and_read_from_the_flat_keys():
    real, _ = adapter({("uapi_cpanel", "SSL::installed_hosts"): REAL_SSL})
    status = real.get_ssl_status(USER, "acme.com") if False else real.list_ssl_status(USER)[0]
    assert status.domain == "acme.com" and status.status == "active" and status.active and status.issuer == "Self-signed (browsers will warn)"
    assert status.days_left in (299, 300)


def test_a_real_issuers_name_comes_from_the_flat_organization_key():
    cert = dict(REAL_SSL[0]["certificate"], is_self_signed=0, **{"issuer.organizationName": "Let's Encrypt", "issuer.commonName": "R11"})
    real, _ = adapter({("uapi_cpanel", "SSL::installed_hosts"): [{"certificate": cert}]})
    assert real.list_ssl_status(USER)[0].issuer == "Let's Encrypt"
    cert["issuer.organizationName"] = None
    real, _ = adapter({("uapi_cpanel", "SSL::installed_hosts"): [{"certificate": cert}]})
    assert real.list_ssl_status(USER)[0].issuer == "R11"


def test_only_the_logins_the_customer_made_are_listed_and_the_folder_is_relative():
    real, fake = adapter({**DOMAINS, **HOME, ("uapi_cpanel", "Ftp::list_ftp"): REAL_FTP, "accountsummary": {"acct": [{"maxftp": "unlimited"}]}})
    assert [(a.login, a.directory) for a in real.list_ftp_accounts(USER)] == [("dev@acme.com", "public_html/site")]
    options = real.ftp_options(USER)
    assert options.host == "whm.test" and options.max_accounts is None and options.port == 21
    from django.core.cache import cache

    cache.clear()  # what the first adapter learned about this account is remembered for a while, as in real use
    real2, _ = adapter({"accountsummary": {"acct": [{"maxftp": "5"}]}})
    assert real2.ftp_options(USER).max_accounts == 5


def test_creating_an_ftp_login_checks_everything_first_and_sends_the_documented_fields():
    list_files = [{"file": "public_html", "type": "dir"}, {"file": "notes.txt", "type": "file"}]
    real, fake = adapter({**DOMAINS, **HOME, ("uapi_cpanel", "Ftp::list_ftp"): REAL_FTP, ("uapi_cpanel", "Fileman::list_files"): list_files})
    assert real.create_ftp_account(USER, "Support1", "Strong-Pass-2026", "public_html") == "support1@acme.com"
    params, secret = fake.uapi_calls("Ftp", "add_ftp")[0]
    assert (params["user"], params["homedir"], params["domain"], params["quota"], secret) == ("support1", "public_html", "acme.com", 0, True)
    for name, password, folder in (("x", "Strong-Pass-2026", ""), ("ok1", "weak", ""), ("ok1", "Strong-Pass-2026", "missing"), ("ok1", "Strong-Pass-2026", "../etc"),
                                   ("ok1", "Strong-Pass-2026", "notes.txt")):
        with pytest.raises(HostingError):
            real.create_ftp_account(USER, name, password, folder)
    assert len(fake.uapi_calls("Ftp", "add_ftp")) == 1


def test_only_the_customers_own_logins_can_be_changed_or_deleted():
    real, fake = adapter({**DOMAINS, **HOME, ("uapi_cpanel", "Ftp::list_ftp"): REAL_FTP})
    real.set_ftp_password(USER, "dev@acme.com", "Strong-Pass-2026")
    params, secret = fake.uapi_calls("Ftp", "passwd")[0]
    assert (params["user"], params["domain"], params["pass"], secret) == ("dev", "acme.com", "Strong-Pass-2026", True)
    real.delete_ftp_account(USER, "DEV@acme.com")
    assert fake.uapi_calls("Ftp", "delete_ftp")[0][0]["user"] == "dev"
    for login in ("acme", "acme_logs", "ftp", "anonymous", "acme@acme.com", "other@acme.com"):
        with pytest.raises(HostingError, match="no longer exists"):
            real.delete_ftp_account(USER, login)
        with pytest.raises(HostingError):
            real.set_ftp_password(USER, login, "Strong-Pass-2026")
    assert len(fake.uapi_calls("Ftp", "delete_ftp")) == 1 and len(fake.uapi_calls("Ftp", "passwd")) == 1


def test_the_error_log_is_read_tailed_and_filtered_and_the_access_log_is_honestly_missing():
    errors = [{"message": f"[error] line {n} missing-page"} if n % 2 else f"[warn] line {n}" for n in range(1, 61)]
    real, fake = adapter({**DOMAINS, ("uapi_cpanel", "Stats::get_site_errors"): errors})
    view = real.read_log(USER, "error", 50, "")
    assert view.total == 60 and len(view.lines) == 50 and view.lines[-1] == "[warn] line 60" and fake.uapi_calls("Stats", "get_site_errors")[0][0]["domain"] == "acme.com"
    assert real.read_log(USER, "error", 200, "MISSING-page").total == 30
    with pytest.raises(CapabilityNotSupported):
        real.read_log(USER, "access", 50, "")
    with pytest.raises(HostingError):
        real.read_log(USER, "system", 50, "")
    with pytest.raises(HostingError):
        real.read_log(USER, "error", 12345, "")
    with pytest.raises(CapabilityNotSupported):
        real.clear_log(USER, "error")


def test_an_empty_error_log_is_just_empty():
    real, _ = adapter({**DOMAINS, ("uapi_cpanel", "Stats::get_site_errors"): []})
    assert real.read_log(USER, "error", 50, "").lines == []


def test_the_servers_internal_folders_are_hidden_from_the_file_list():
    listing = [{"file": name, "type": "dir"} for name in (".cagefs", ".caldav", ".cl.selector", ".htpasswds", "etc", "logs", "mail", "public_ftp", "public_html", "tmp")]
    real, _ = adapter({**HOME, ("uapi_cpanel", "Fileman::list_files"): listing})
    assert [e.name for e in real.list_files(USER, "")] == ["etc", "logs", "mail", "public_ftp", "public_html", "tmp"]


def test_a_plain_zero_for_a_count_means_none_allowed_as_the_live_server_does():
    """Live (9 Oct 2026): the package WordPress-Starter Startup has MAXSUB, MAXADDON and MAXPARK of 0 and the server refused a subdomain."""
    answers = {"accountsummary": {"acct": [{"maxsub": "0", "maxaddons": "0", "maxparked": "0", "maxsql": "2", "maxpop": "15", "maxftp": "5"}]}}
    real, _ = adapter({**HOME, **answers, ("uapi_cpanel", "Mysql::get_restrictions"): {"prefix": "acme_"}})
    limits = real.domain_limits(USER)
    assert limits.max_subdomains == 0 and limits.max_addon_domains == 0
    assert real.database_limits(USER).max_databases == 2 and real.email_limits(USER).max_mailboxes == 15 and real.ftp_options(USER).max_accounts == 5


def test_a_missing_autossl_feature_is_explained_in_plain_words():
    real, _ = adapter({**DOMAINS, ("uapi_cpanel", "SSL::start_autossl_check"): {"__uapi_error__": "You do not have the feature “autossl”."}})
    with pytest.raises(HostingError, match="Automatic SSL is not switched on for this plan"):
        real.request_ssl(USER, "acme.com")
