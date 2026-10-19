"""The public status page: open to everyone, says what is wrong in plain words, and never shows internal detail."""
import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.core import status

pytestmark = pytest.mark.django_db


def test_the_status_page_is_public_and_says_all_is_well_when_it_is(client):
    cache.clear()
    page = client.get(reverse("status"))
    html = page.content.decode()
    assert page.status_code == 200 and "System status" in html and "Customer portal" in html and "Operational" in html
    assert "public" in page["Cache-Control"]


def test_a_down_component_changes_the_headline_and_a_failing_probe_shows_as_checking(client, monkeypatch):
    cache.clear()
    monkeypatch.setattr(status, "_servers", lambda: "down")
    html = client.get(reverse("status")).content.decode()
    assert "We are fixing an outage" in html and "Outage" in html
    cache.clear()

    def boom():
        raise RuntimeError("secret internal address 10.0.0.5")

    monkeypatch.setattr(status, "_servers", boom)
    html = client.get(reverse("status")).content.decode()
    assert "Checking" in html and "10.0.0.5" not in html and "secret" not in html


def test_the_footer_links_to_the_status_page(client):
    assert reverse("status") in client.get("/").content.decode()
