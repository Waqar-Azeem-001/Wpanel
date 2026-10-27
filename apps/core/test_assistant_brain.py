"""The AI Agent's own brain: how it reads a customer's words, picks the knowledge entry, and turns a live check into a plain answer."""
import pytest

from apps.assistant import brain
from apps.assistant.models import KnowledgeEntry


@pytest.fixture(autouse=True)
def _only_the_entries_this_test_makes(db):
    KnowledgeEntry.objects.all().delete()  # the project ships with a starter set; these tests must see only their own


# --- reading the words ---------------------------------------------------------------------------------------------------------------------

def test_text_is_lowercased_stripped_of_punctuation_and_kept_comparable():
    assert brain.normalise("  My SITE,   is DOWN!!  ") == " my site is down "
    assert brain.normalise(None) == "  " or brain.normalise(None).strip() == ""
    assert brain.words("Check shop-one.example.com now!") == ["check", "shop-one.example.com", "now"]


@pytest.mark.parametrize("text,found", [
    ("my site example.com is down", "example.com"), ("mera shop.pk nahi khul raha", "shop.pk"), ("see logo.png on example.org", "example.org"),
    ("no domain here", ""), ("", ""), (None, ""), ("contact index.html please", ""),
])
def test_a_domain_in_the_message_is_found_but_a_file_name_is_not(text, found):
    assert brain.domain_in(text) == found


# --- choosing the knowledge entry --------------------------------------------------------------------------------------------------------

def entry(title, keywords, order=100, active=True, action=""):
    return KnowledgeEntry.objects.create(title=title, keywords=keywords, answer=f"answer for {title}", sort_order=order, is_active=active, action=action)


@pytest.mark.django_db
def test_an_exact_wording_beats_loose_matching_and_the_nearest_entry_wins():
    entry("SSL", "how do i install ssl\nssl certificate not working")
    entry("Email", "set up email on my phone\nemail not receiving")
    assert brain.best_entry("How do I install SSL on my site?").title == "SSL"
    assert brain.best_entry("my email is not receiving anything").title == "Email"  # all the core words are present, in another order


@pytest.mark.django_db
def test_a_single_word_keyword_matches_only_the_whole_word_not_a_piece_of_a_longer_one():
    entry("Backups", "backup")
    entry("DNS", "dns")
    assert brain.best_entry("I need a backup please").title == "Backups"
    assert brain.best_entry("what is dns").title == "DNS"
    assert brain.best_entry("my dnsmasq is slow") is None  # "dns" inside another word is not "dns"
    assert brain.best_entry("I need backups") is None  # nor is a different form of the word


@pytest.mark.django_db
def test_a_hidden_entry_is_never_chosen_and_nothing_matching_gives_nothing():
    entry("Hidden", "special offer coupon", active=False)
    assert brain.best_entry("any special offer coupon today") is None
    assert brain.best_entry("completely unrelated question about weather") is None
    assert brain.best_entry("") is None


@pytest.mark.django_db
def test_when_two_entries_match_equally_the_lower_sort_order_comes_first():
    entry("Second", "reset password", order=200)
    entry("First", "reset password", order=10)
    assert brain.best_entry("how to reset password").title == "First"


def test_a_wording_scores_by_how_much_of_it_the_customer_said():
    e = KnowledgeEntry(keywords="change nameservers\nupdate name servers for domain")
    text = brain.normalise("please change nameservers for my domain")
    tokens = set(brain.words("please change nameservers for my domain"))
    assert brain.score(e, text, tokens) >= brain.MIN_SCORE
    assert brain.score(KnowledgeEntry(keywords="\n\n"), text, tokens) == 0
    assert brain.score(KnowledgeEntry(keywords="how to install wordpress"), text, tokens) == 0


# --- turning a live check into words -------------------------------------------------------------------------------------------------

def test_an_error_from_a_check_is_said_plainly_for_every_kind_of_check():
    for action in ("website_status", "ssl", "disk", "dns", "invoices", "unknown"):
        assert brain.format_result(action, {"error": "the server did not answer"}) == "I could not check that: the server did not answer"


def test_the_website_check_says_whether_it_opens_how_fast_and_what_to_do():
    good = brain.format_result("website_status", {"domain": "shop.example", "reachable": True, "http_status": 200, "first_byte_ms": 180})
    assert good.startswith("shop.example opens (HTTP 200).") and "180" in good
    bad = brain.format_result("website_status", {"domain": "shop.example", "reachable": False, "problem": "connection refused",
                                                 "advice": ["Check the domain points here", "Check the plan is active", "a", "b", "c-hidden"]})
    assert "does not open" in bad and "Problem: connection refused" in bad and "Check the plan is active" in bad and "c-hidden" not in bad  # four tips at most


def test_ssl_says_when_there_is_none_and_when_it_ends():
    assert "no active SSL certificate" in brain.format_result("ssl", {"domain": "a.example", "active": False})
    on = brain.format_result("ssl", {"domain": "a.example", "active": True, "issuer": "Let's Encrypt", "days_left": 60, "auto_renew": True, "https_redirect": False})
    assert "Let's Encrypt" in on and "60 days left" in on and "auto-renew on" in on and "HTTPS redirect off" in on
    unknown = brain.format_result("ssl", {"domain": "a.example", "active": True})
    assert "issuer unknown" in unknown and "days left" not in unknown


def test_disk_and_bandwidth_show_a_dash_for_what_is_not_known():
    full = brain.format_result("disk", {"disk_used_mb": 200, "disk_limit_mb": 1000, "disk_percent": 20, "bandwidth_used_mb": 5, "bandwidth_limit_mb": 100})
    assert full.startswith("Disk: 200 MB of 1000 MB (20%)") and "Bandwidth this month: 5 MB of 100 MB" in full
    assert brain.format_result("disk", {}) == "Disk: - MB of - MB. Bandwidth this month: - MB of - MB."


def test_the_error_log_shows_the_newest_six_lines_or_says_it_is_empty():
    assert "empty" in brain.format_result("error_log", {"last_lines": []})
    shown = brain.format_result("error_log", {"last_lines": [f"line {n}" for n in range(1, 10)]})
    assert "line 9" in shown and "line 4" in shown and "line 3" not in shown


def test_dns_lists_the_records_and_says_so_when_there_are_none():
    assert brain.format_result("dns", {"domain": "a.example", "records": []}) == "There are no DNS records."
    text = brain.format_result("dns", {"domain": "a.example", "records": [{"type": "A", "name": "@", "value": "203.0.113.5"}]})
    assert text.startswith("DNS records of a.example:") and "A  @  203.0.113.5" in text


def test_mailboxes_show_size_quota_and_percentage_and_unlimited_when_there_is_no_quota():
    assert brain.format_result("email_quota", {"mailboxes": []}) == "You have no mailboxes."
    text = brain.format_result("email_quota", {"mailboxes": [{"address": "a@x.example", "used_mb": 10, "quota_mb": 100, "percent_used": 10},
                                                              {"address": "b@x.example", "used_mb": 5, "quota_mb": 0}]})
    assert "a@x.example: 10 MB of 100 MB (10%)" in text and "b@x.example: 5 MB of unlimited MB" in text and "(None%)" not in text


def test_invoices_and_services_read_as_a_short_list_or_a_kind_empty_answer():
    assert brain.format_result("invoices", {"invoices": []}) == "You have no invoices yet."
    text = brain.format_result("invoices", {"invoices": [{"number": "", "status": "draft", "total": "10.00", "currency": "USD", "due_date": ""},
                                                          {"number": "INV-000001", "status": "unpaid", "total": "99.00", "currency": "USD", "due_date": "2026-11-01"}]})
    assert "Draft: draft, 10.00 USD" in text and "INV-000001: unpaid, 99.00 USD, due 2026-11-01" in text
    assert brain.format_result("services", {"hosting_plans": []}) == "You have no active hosting plan yet."
    plans = brain.format_result("services", {"hosting_plans": [{"domain": "a.example", "plan": "", "status": "active", "websites": [{"domain": "blog.a.example"}]}]})
    assert plans == "a.example: hosting (active); websites: blog.a.example"
    assert brain.format_result("not-a-check", {}) == ""
