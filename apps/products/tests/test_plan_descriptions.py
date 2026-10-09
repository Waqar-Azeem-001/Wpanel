"""A plan's description keeps its marketing lines and takes its feature lines from the server."""
from apps.products import services


def limits_of(**features):
    return services.features_of(features)[0]


def test_only_the_feature_lines_are_replaced_and_the_rest_stays_in_order():
    old = ("10 GB NVMe storage\n50 GB bandwidth\n4 databases\n8 email accounts\n5 subdomains\nFree domain name\nFree SSL certificate\n"
           "cPanel and webmail\n1 GB RAM")
    merged = services.merge_description(old, limits_of(disk_mb=20480, bandwidth_mb=None, databases=5, email_accounts=10, subdomains=2, addon_domains=1))
    lines = merged.splitlines()
    assert lines[:6] == ["20 GB storage", "Unlimited bandwidth", "5 databases", "10 email accounts", "2 subdomains", "1 addon domain"]
    assert lines[6:] == ["Free domain name", "Free SSL certificate", "cPanel and webmail", "1 GB RAM"]  # RAM is not something a server package says


def test_it_works_on_an_empty_description_and_doing_it_twice_changes_nothing():
    limits = limits_of(disk_mb=20480, databases=5)
    once = services.merge_description("", limits)
    assert once.splitlines() == ["20 GB storage", "5 databases"]
    assert services.merge_description(once, limits) == once


def test_a_resellers_web_space_and_domains_lines_are_recognised():
    merged = services.merge_description("20 GB web space\n10 domains\n24/7 support", limits_of(disk_mb=40960, addon_domains=20))
    assert merged.splitlines() == ["40 GB storage", "20 addon domains", "24/7 support"]


def test_words_that_only_look_like_features_are_left_alone():
    merged = services.merge_description("Fast support\nFree migration\nDaily backups", limits_of(disk_mb=1024))
    assert merged.splitlines() == ["1 GB storage", "Fast support", "Free migration", "Daily backups"]
