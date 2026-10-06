"""Domains and DNS (H5): the demo provider's rules, and the customer screens end to end."""
import pytest
from django.core.management import call_command

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.core.exceptions import ServiceError
from apps.hosting import control
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.capabilities import DnsRecord
from apps.hosting.adapters.demo_domains import MAX_ADDON_DOMAINS, MAX_RECORDS, MAX_SUBDOMAINS, SERVER_IP, DemoDomains
from apps.hosting.models import HostingAccount
from apps.products import services as product_services
from apps.products.models import ProductType

pytestmark = pytest.mark.django_db


# --- The provider's rules (no web, no database) -----------------------------------------------------------------

@pytest.fixture
def folders():
    return []


@pytest.fixture
def dom(tmp_path, folders):
    return DemoDomains(tmp_path / "state", "shop.example.com", folders.append)


def rec(kind, name, value, ttl=3600, priority=None, record_id=""):
    return DnsRecord(record_id=record_id, type=kind, name=name, value=value, ttl=ttl, priority=priority)


def test_the_main_domain_is_listed_first_and_cannot_be_removed(dom):
    assert [(d.name, d.kind) for d in dom.domains()] == [("shop.example.com", "primary")]
    with pytest.raises(HostingError, match="main domain"):
        dom.remove_domain("shop.example.com")


def test_adding_an_extra_domain_makes_a_folder_a_zone_and_cleans_the_name(dom, folders):
    dom.add_domain("  MyShop.COM. ", "addon", "")
    assert [d.name for d in dom.domains()] == ["shop.example.com", "myshop.com"] and folders == ["public_html/myshop.com"]
    assert dom.domains()[1].document_root == "public_html/myshop.com" and dom.records("myshop.com")  # a default zone exists
    dom.add_domain("other.org", "addon", "public_html/custom")
    assert dom.domains()[2].document_root == "public_html/custom"


@pytest.mark.parametrize("bad", ["", "localhost", "no spaces.com", "-bad.com", "bad-.com", "a..com", "x" * 70 + ".com",
                                 "http://site.com", "site.c", "site.com/path"])
def test_names_must_be_real_domain_names(dom, bad):
    with pytest.raises(HostingError):
        dom.add_domain(bad, "addon", "")


def test_a_domain_cannot_be_added_twice_or_overlap_one_you_have(dom):
    dom.add_domain("myshop.com", "addon", "")
    with pytest.raises(HostingError, match="already"):
        dom.add_domain("myshop.com", "addon", "")
    with pytest.raises(HostingError, match="already"):
        dom.add_domain("shop.example.com", "alias", "")
    with pytest.raises(HostingError, match="overlaps"):
        dom.add_domain("blog.myshop.com", "addon", "")  # that is a subdomain of a domain you have
    with pytest.raises(HostingError, match="overlaps"):
        dom.add_domain("example.com", "addon", "")  # and this would contain your main domain


def test_subdomains_sit_under_your_own_domains_and_their_record_goes_in_the_parent_zone(dom):
    dom.add_domain("blog.shop.example.com", "subdomain", "")
    assert any(r.name == "blog" and r.type == "A" and r.value == SERVER_IP for r in dom.records("shop.example.com"))
    with pytest.raises(HostingError, match="under one of your own"):
        dom.add_domain("blog.elsewhere.com", "subdomain", "")
    with pytest.raises(HostingError, match="parent domain"):
        dom.records("blog.shop.example.com")
    dom.remove_domain("blog.shop.example.com")
    assert not any(r.name == "blog" for r in dom.records("shop.example.com"))


def test_an_alias_shows_the_main_site_and_needs_no_folder(dom, folders):
    dom.add_domain("myalias.com", "alias", "public_html/ignored")
    assert dom.domains()[1].document_root == "public_html" and folders == []
    with pytest.raises(HostingError):
        dom.add_domain("x.com", "weird", "")


def test_the_plan_limits_are_enforced_separately_for_domains_and_subdomains(dom):
    for i in range(MAX_ADDON_DOMAINS):
        dom.add_domain(f"extra{i}.com", "addon", "")
    with pytest.raises(HostingError, match="extra domains"):
        dom.add_domain("onetoomany.com", "alias", "")
    for i in range(MAX_SUBDOMAINS):
        dom.add_domain(f"s{i}.shop.example.com", "subdomain", "")
    with pytest.raises(HostingError, match="subdomains"):
        dom.add_domain("last.shop.example.com", "subdomain", "")


@pytest.mark.parametrize("root", ["../escape", "/etc", "a\\b", ".trash/x", "C:/x"])
def test_the_website_folder_must_stay_inside_the_account(dom, root):
    with pytest.raises(HostingError):
        dom.add_domain("myshop.com", "addon", root)


def test_redirects_are_validated_and_can_be_removed(dom):
    dom.add_domain("myshop.com", "addon", "")
    dom.set_redirect("myshop.com", "https://www.example.org/landing", False)
    row = dom.domains()[1]
    assert row.redirect_to == "https://www.example.org/landing" and row.redirect_permanent is False
    dom.set_redirect("shop.example.com", "https://elsewhere.example/", True)  # the main domain too
    assert dom.domains()[0].redirect_to == "https://elsewhere.example/"
    for bad in ("ftp://x.com", "javascript:alert(1)", "//x.com", "not a url", "https://myshop.com/loop", "https://x.com/" + "a" * 600):
        with pytest.raises(HostingError):
            dom.set_redirect("myshop.com", bad, True)
    dom.set_redirect("myshop.com", "", True)
    assert dom.domains()[1].redirect_to == ""
    with pytest.raises(HostingError, match="not on this account"):
        dom.set_redirect("nope.com", "https://x.com", True)


def test_removing_an_extra_domain_deletes_its_zone_and_keeps_the_files(dom, folders):
    dom.add_domain("myshop.com", "addon", "")
    dom.remove_domain("myshop.com")
    assert [d.name for d in dom.domains()] == ["shop.example.com"]
    with pytest.raises(HostingError):
        dom.records("myshop.com")
    with pytest.raises(HostingError):
        dom.remove_domain("myshop.com")


# --- DNS records ------------------------------------------------------------------------------------------------------

def test_a_new_zone_starts_with_the_records_a_site_and_its_mail_need(dom):
    kinds = {(r.type, r.name) for r in dom.records("shop.example.com")}
    assert {("A", "@"), ("CNAME", "www"), ("A", "mail"), ("MX", "@"), ("TXT", "@")} <= kinds


@pytest.mark.parametrize("record", [
    rec("A", "api", "203.0.113.5"), rec("AAAA", "api", "2001:db8::5"), rec("CNAME", "shop", "store.example.net"),
    rec("MX", "@", "mail2.example.com", priority=20), rec("TXT", "_verify", "token=abc123"),
    rec("SRV", "_sip._tcp", "5 5060 sip.example.com", priority=10), rec("CAA", "@", '0 issue "letsencrypt.org"'),
    rec("A", "*", "203.0.113.6"), rec("A", "x.api.shop.example.com", "203.0.113.7"),
])
def test_valid_records_of_every_type_are_accepted(dom, record):
    new_id = dom.add_record("shop.example.com", record)
    assert new_id in [r.record_id for r in dom.records("shop.example.com")]


@pytest.mark.parametrize("record, message", [
    (rec("A", "api", "999.1.1.1"), "IPv4"), (rec("A", "api", "2001:db8::1"), "IPv4"), (rec("AAAA", "api", "1.2.3.4"), "IPv6"),
    (rec("CNAME", "@", "x.example.net"), "CNAME cannot be set on the domain itself"), (rec("CNAME", "shop", "nodots"), "full host name"),
    (rec("MX", "@", "mail.example.org"), "priority"), (rec("MX", "@", "mail.example.org", priority=70000), "between 0 and 65535"),
    (rec("TXT", "t", ""), "TXT"), (rec("TXT", "t", "line" + chr(10) + "break"), "TXT"),
    (rec("SRV", "_sip._tcp", "5 5060", priority=1), "three parts"), (rec("SRV", "sip", "5 5060 h.example.com", priority=1), "_sip._tcp"),
    (rec("CAA", "@", "issue letsencrypt.org"), "CAA"), (rec("CAA", "@", '0 unknown "x"'), "CAA"),
    (rec("NS", "@", "ns.example.com"), "Choose a record type"), (rec("A", "bad name", "1.2.3.4"), "not valid"),
    (rec("A", "-x", "1.2.3.4"), "not valid"), (rec("A", "a*b", "1.2.3.4"), "not valid"), (rec("A", "x" * 70, "1.2.3.4"), "not valid"),
    (rec("A", "api", "1.2.3.4", ttl=10), "TTL"), (rec("A", "api", "1.2.3.4", ttl=999999), "TTL"), (rec("A", "api", "1.2.3.4", ttl="x"), "TTL"),
])
def test_invalid_records_are_refused_with_a_plain_reason(dom, record, message):
    before = len(dom.records("shop.example.com"))
    with pytest.raises(HostingError, match=message):
        dom.add_record("shop.example.com", record)
    assert len(dom.records("shop.example.com")) == before


def test_names_are_tidied_into_the_zone(dom):
    dom.add_record("shop.example.com", rec("A", "API.shop.example.com.", "10.0.0.1"))
    dom.add_record("shop.example.com", rec("A", "shop.example.com", "10.0.0.2"))
    names = {(r.name, r.value) for r in dom.records("shop.example.com")}
    assert ("api", "10.0.0.1") in names and ("@", "10.0.0.2") in names


def test_a_cname_cannot_share_a_name_and_duplicates_are_refused(dom):
    dom.add_record("shop.example.com", rec("A", "app", "10.0.0.1"))
    with pytest.raises(HostingError, match="cannot share"):
        dom.add_record("shop.example.com", rec("CNAME", "app", "x.example.net"))
    with pytest.raises(HostingError, match="cannot share"):
        dom.add_record("shop.example.com", rec("CNAME", "www", "x.example.net"))  # www is already a CNAME
    with pytest.raises(HostingError, match="CNAME"):
        dom.add_record("shop.example.com", rec("A", "www", "10.0.0.9"))
    with pytest.raises(HostingError, match="already exists"):
        dom.add_record("shop.example.com", rec("A", "app", "10.0.0.1"))
    dom.add_record("shop.example.com", rec("A", "app", "10.0.0.2"))  # a second address for the same name is fine


def test_records_can_be_edited_but_not_change_type_and_deleted(dom):
    new_id = dom.add_record("shop.example.com", rec("A", "app", "10.0.0.1"))
    dom.update_record("shop.example.com", rec("A", "app2", "10.0.0.5", ttl=300, record_id=new_id))
    changed = next(r for r in dom.records("shop.example.com") if r.record_id == new_id)
    assert (changed.name, changed.value, changed.ttl) == ("app2", "10.0.0.5", 300)
    with pytest.raises(HostingError, match="type of a record"):
        dom.update_record("shop.example.com", rec("TXT", "app2", "hello", record_id=new_id))
    with pytest.raises(HostingError, match="IPv4"):
        dom.update_record("shop.example.com", rec("A", "app2", "nope", record_id=new_id))
    with pytest.raises(HostingError, match="no longer exists"):
        dom.update_record("shop.example.com", rec("A", "x", "10.0.0.1", record_id="ghost"))
    dom.delete_record("shop.example.com", new_id)
    with pytest.raises(HostingError, match="no longer exists"):
        dom.delete_record("shop.example.com", new_id)


def test_a_zone_has_a_size_limit(dom):
    for i in range(MAX_RECORDS):
        try:
            dom.add_record("shop.example.com", rec("TXT", f"t{i}", f"value {i}"))
        except HostingError:
            break
    with pytest.raises(HostingError, match="can hold"):
        dom.add_record("shop.example.com", rec("TXT", "overflow", "x"))


def test_unknown_zones_are_refused(dom):
    for call in (lambda: dom.records("nope.com"), lambda: dom.add_record("nope.com", rec("A", "x", "1.1.1.1")),
                 lambda: dom.delete_record("nope.com", "x")):
        with pytest.raises(HostingError):
            call()


# --- The screens ------------------------------------------------------------------------------------------------------

@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def client_obj(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})


@pytest.fixture
def owner(client_obj):
    return client_obj.contacts.get().user


@pytest.fixture
def account(owner, manager):
    product_services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING})
    call_command("demo_hosting_data")
    return HostingAccount.objects.get(domain="demo-shop.example.com")


def durl(account, tail=""):
    return f"/account/hosting/{account.pk}/manage/domains/{tail}"


def nurl(account, tail=""):
    return f"/account/hosting/{account.pk}/manage/dns/{tail}"


def test_the_domains_page_starts_with_the_main_domain_counts_and_nameservers(client, owner, account):
    client.force_login(owner)
    page = client.get(durl(account)).content.decode()
    assert "demo-shop.example.com" in page and "Main" in page and "0 / 5 extra domains" in page.replace("\n", " ")
    assert "0 / 20 subdomains" in page.replace("\n", " ") and "ns1.demo-hosting.test" in page and "Add a domain" in page


def test_add_a_domain_a_subdomain_and_an_alias_then_remove_them(client, owner, account):
    client.force_login(owner)
    act = durl(account, "do/")
    client.post(act, {"action": "add", "kind": "addon", "name": "MyShop.com", "document_root": ""})
    client.post(act, {"action": "add", "kind": "subdomain", "name": "blog", "parent": "demo-shop.example.com", "document_root": ""})
    client.post(act, {"action": "add", "kind": "alias", "name": "shopalias.com", "document_root": ""})
    page = client.get(durl(account)).content.decode()
    for name in ("myshop.com", "blog.demo-shop.example.com", "shopalias.com", "Subdomain", "Alias"):
        assert name in page, name
    assert "public_html/myshop.com" in page and "2 / 5 extra domains" in page.replace("\n", " ")  # the domain and the alias
    files = client.get(f"/account/hosting/{account.pk}/manage/files/", {"path": "public_html/myshop.com"}).content.decode()
    assert "index.html" in files  # the new domain's website folder exists in the file manager
    client.post(act, {"action": "remove", "name": "myshop.com"})
    client.post(act, {"action": "remove", "name": "blog.demo-shop.example.com"})
    page = client.get(durl(account)).content.decode()
    assert "</i> myshop.com" not in page and "</i> blog.demo-shop.example.com" not in page and "</i> shopalias.com" in page


def test_redirects_are_set_shown_and_removed(client, owner, account):
    client.force_login(owner)
    act = durl(account, "do/")
    client.post(act, {"action": "redirect", "name": "demo-shop.example.com", "redirect_to": "https://www.elsewhere.org/", "kind": "302"})
    page = client.get(durl(account)).content.decode()
    assert "https://www.elsewhere.org/" in page and "302" in page
    client.post(act, {"action": "remove_redirect", "name": "demo-shop.example.com"})
    assert "https://www.elsewhere.org/" not in client.get(durl(account)).content.decode()
    bad = client.post(act, {"action": "redirect", "name": "demo-shop.example.com", "redirect_to": "javascript:alert(1)", "kind": "301"},
                      follow=True).content.decode()
    assert "full web address" in bad


def test_domain_errors_are_shown_not_raised_and_limits_disable_the_form(client, owner, account):
    client.force_login(owner)
    act = durl(account, "do/")
    bad = client.post(act, {"action": "add", "kind": "addon", "name": "not a domain"}, follow=True).content.decode()
    assert "full domain name" in bad
    twice = client.post(act, {"action": "add", "kind": "alias", "name": "demo-shop.example.com"}, follow=True).content.decode()
    assert "already on this account" in twice
    removed = client.post(act, {"action": "remove", "name": "demo-shop.example.com"}, follow=True).content.decode()
    assert "main domain of an account cannot be removed" in removed
    for i in range(5):
        client.post(act, {"action": "add", "kind": "addon", "name": f"extra{i}.com"})
    assert "reached your plan's limit of extra domains" in client.get(durl(account)).content.decode()
    assert client.post(act, {"action": "nonsense"}, follow=True).status_code == 200


def test_the_dns_page_lists_records_with_types_and_one_tab_per_domain(client, owner, account):
    client.force_login(owner)
    client.post(durl(account, "do/"), {"action": "add", "kind": "addon", "name": "myshop.com"})
    page = client.get(nurl(account)).content.decode()
    assert "Records for demo-shop.example.com" in page and "dns-type-mx" in page and "v=spf1" in page and "Add a record" in page
    assert "myshop.com" in page and "Changing DNS can take your website or your email offline" in page
    other = client.get(nurl(account), {"domain": "myshop.com"}).content.decode()
    assert "Records for myshop.com" in other and "mail.myshop.com" in other
    assert client.get(nurl(account), {"domain": "blog.demo-shop.example.com"}, follow=True).status_code == 200  # a subdomain: no zone of its own


def test_add_edit_and_delete_records_from_the_screen(client, owner, account):
    client.force_login(owner)
    act, base = nurl(account, "do/"), {"domain": "demo-shop.example.com"}
    client.post(act, {**base, "action": "add", "type": "A", "name": "api", "value": "203.0.113.55", "ttl": "300"})
    client.post(act, {**base, "action": "add", "type": "MX", "name": "@", "value": "mail2.example.org", "ttl": "3600", "priority": "20"})
    page = client.get(nurl(account), {"domain": "demo-shop.example.com"}).content.decode()
    assert "203.0.113.55" in page and "mail2.example.org" in page and ">20<" in page
    import re

    record_id = re.search(r'data-id="([^"]+)" data-type="A" data-name="api" data-value="203.0.113.55"', page).group(1)
    client.post(act, {**base, "action": "update", "record_id": record_id, "type": "A", "name": "api", "value": "203.0.113.66", "ttl": "300"})
    page = client.get(nurl(account), {"domain": "demo-shop.example.com"}).content.decode()
    assert "203.0.113.66" in page and "203.0.113.55" not in page
    client.post(act, {**base, "action": "delete", "record_id": record_id})
    assert "203.0.113.66" not in client.get(nurl(account), {"domain": "demo-shop.example.com"}).content.decode()


def test_record_errors_are_messages_and_deletes_of_key_records_warn_strongly(client, owner, account):
    client.force_login(owner)
    act, base = nurl(account, "do/"), {"domain": "demo-shop.example.com"}
    bad = client.post(act, {**base, "action": "add", "type": "A", "name": "api", "value": "not-an-ip", "ttl": "300"}, follow=True)
    assert "IPv4 address" in bad.content.decode()
    mx = client.post(act, {**base, "action": "add", "type": "MX", "name": "x", "value": "m.example.org", "ttl": "300"}, follow=True)
    assert "priority" in mx.content.decode()
    page = client.get(nurl(account), {"domain": "demo-shop.example.com"}).content.decode()
    assert "without it your website will not open" in page and "your email stops arriving" in page
    assert "data-confirm" in page


def test_the_mail_helpers_add_spf_and_dmarc_once(client, owner, account):
    client.force_login(owner)
    act, base = nurl(account, "do/"), {"domain": "demo-shop.example.com"}
    page = client.get(nurl(account), {"domain": "demo-shop.example.com"}).content.decode()
    assert "In place" in page  # the default zone already has SPF
    assert 'value="mail_dmarc"' in page
    client.post(act, {**base, "action": "mail_dmarc"})
    page = client.get(nurl(account), {"domain": "demo-shop.example.com"}).content.decode()
    assert "v=DMARC1" in page and 'value="mail_dmarc"' not in page and 'value="mail_spf"' not in page
    again = client.post(act, {**base, "action": "mail_dmarc"}, follow=True).content.decode()
    assert "already exists" in again


def test_every_change_is_audited_without_record_values(client, owner, account):
    client.force_login(owner)
    client.post(nurl(account, "do/"), {"domain": "demo-shop.example.com", "action": "add", "type": "TXT", "name": "_secret",
                                      "value": "token-do-not-log-12345", "ttl": "300"})
    client.post(durl(account, "do/"), {"action": "add", "kind": "addon", "name": "myshop.com"})
    events = list(AuditEvent.objects.filter(action="hosting.control").values_list("metadata", flat=True))
    assert {"capability": "dns", "operation": "add_dns_record", "domain": "demo-shop.example.com", "type": "TXT", "name": "_secret"} in events
    assert any(e.get("operation") == "add_domain" and e.get("domain") == "myshop.com" for e in events)
    assert "token-do-not-log-12345" not in str(events)


def test_strangers_get_nothing_and_a_provider_without_domains_shows_the_not_available_page(client, customer, owner, account):
    client.force_login(customer)
    for url in (durl(account), nurl(account)):
        assert client.get(url).status_code == 404
    assert client.post(durl(account, "do/"), {"action": "add", "kind": "addon", "name": "evil.com"}).status_code == 404
    assert client.post(nurl(account, "do/"), {"action": "add", "domain": "demo-shop.example.com", "type": "A", "name": "x",
                                              "value": "1.1.1.1"}).status_code == 404
    from apps.products.models import Server

    Server.objects.filter(kind="demo").update(kind="manual")
    client.force_login(owner)
    assert "not available for your hosting server yet" in client.get(durl(account)).content.decode()
    assert "not available for your hosting server yet" in client.get(nurl(account)).content.decode()
    with pytest.raises(ServiceError):
        control.perform(owner, account, "domains", "list_domains")
