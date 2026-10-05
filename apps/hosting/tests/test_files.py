"""File Manager (H2): the sandbox that keeps every path inside a website's folder, and the customer screens."""
import os

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.sandbox import Sandbox, clean_name, clean_relative
from apps.hosting.models import HostingAccount
from apps.products import services as product_services
from apps.products.models import ProductType

pytestmark = pytest.mark.django_db


# --- The sandbox itself (pure, no database needed) -------------------------------------------------------------

@pytest.fixture
def box(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "public_html").mkdir()
    (root / "public_html" / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")  # a file next to the root: must stay unreachable
    return Sandbox(root)


@pytest.mark.parametrize("bad", ["../secret.txt", "a/../../secret.txt", "/etc/passwd", "C:/Windows", "a\\b", "a\x00b",
                                 "public_html/../../secret.txt", "..", "a/b\nc", "x" * 2000])
def test_unsafe_paths_are_refused_outright(box, bad):
    with pytest.raises(HostingError):
        clean_relative(bad)
    with pytest.raises(HostingError):
        box.list_files(bad)
    with pytest.raises(HostingError):
        box.read_file(bad, max_bytes=1000)


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b", "c:x", "bad\x01name", "n" * 300])
def test_names_must_be_plain(name):
    with pytest.raises(HostingError):
        clean_name(name)


def test_dot_and_empty_parts_are_normalised(box):
    assert clean_relative("public_html//./index.html") == "public_html/index.html"
    assert clean_relative("") == "" and clean_relative(None) == ""


def test_a_symbolic_link_is_never_followed(box, tmp_path):
    link = box.root / "escape"
    try:
        os.symlink(tmp_path, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links need extra privileges on this machine")
    with pytest.raises(HostingError):
        box.list_files("escape")
    with pytest.raises(HostingError):
        box.read_file("escape/secret.txt", max_bytes=1000)
    assert "escape" not in [e.name for e in box.list_files("")]  # not even listed


def test_listing_puts_folders_first_and_reports_sizes(box):
    box.write_file("a.txt", b"abc")
    entries = box.list_files("")
    assert [e.name for e in entries] == ["public_html", "a.txt"] and entries[0].is_dir and entries[1].size_bytes == 3


def test_create_write_read_rename_copy_delete(box):
    box.make_directory("public_html/css")
    box.write_file("public_html/css/a.css", b"body{}")
    assert box.read_file("public_html/css/a.css", max_bytes=100) == b"body{}"
    box.rename_path("public_html/css/a.css", "public_html/css/b.css")
    box.copy_path("public_html/css", "public_html/css2")
    assert box.read_file("public_html/css2/b.css", max_bytes=100) == b"body{}"
    box.delete_path("public_html/css")
    assert [e.name for e in box.list_files("public_html")] == ["css2", "index.html"]


def test_overwrite_must_be_asked_for_and_collisions_are_refused(box):
    with pytest.raises(HostingError):
        box.write_file("public_html/index.html", b"x")  # exists, no overwrite
    box.write_file("public_html/index.html", b"x", overwrite=True)
    with pytest.raises(HostingError):
        box.make_directory("public_html")
    with pytest.raises(HostingError):
        box.rename_path("public_html/index.html", "public_html")  # name already taken
    with pytest.raises(HostingError):
        box.write_file("missing/dir/a.txt", b"x")  # no parent folder, and none is created


def test_the_main_folder_cannot_be_renamed_copied_or_deleted_and_a_folder_cannot_enter_itself(box):
    for operation in (lambda: box.delete_path(""), lambda: box.rename_path("", "x"), lambda: box.copy_path("", "x")):
        with pytest.raises(HostingError):
            operation()
    box.make_directory("public_html/sub")
    with pytest.raises(HostingError):
        box.rename_path("public_html", "public_html/sub/inside")
    with pytest.raises(HostingError):
        box.copy_path("public_html", "public_html/sub/inside")


def test_size_limit_and_folder_vs_file_confusions(box):
    with pytest.raises(HostingError):
        box.read_file("public_html/index.html", max_bytes=3)
    with pytest.raises(HostingError):
        box.read_file("public_html", max_bytes=100)
    with pytest.raises(HostingError):
        box.list_files("public_html/index.html")


def test_search_finds_names_anywhere_below_and_needs_a_term(box):
    box.make_directory("public_html/deep")
    box.write_file("public_html/deep/Index-copy.html", b"x")
    found = [e.path for e in box.search_files("", "index")]
    assert found == ["public_html/deep/Index-copy.html", "public_html/index.html"] or set(found) == {
        "public_html/deep/Index-copy.html", "public_html/index.html"}
    with pytest.raises(HostingError):
        box.search_files("", "  ")


# --- The screens ----------------------------------------------------------------------------------------------

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


def url(account, tail=""):
    return f"/account/hosting/{account.pk}/manage/files/{tail}"


def test_the_main_folder_lists_the_sample_website(client, owner, account):
    client.force_login(owner)
    page = client.get(url(account)).content.decode()
    assert "File manager" in page and "public_html" in page and "logs" in page and "Upload" in page


def test_opening_a_folder_shows_breadcrumbs_and_the_files_in_it(client, owner, account):
    client.force_login(owner)
    page = client.get(url(account), {"path": "public_html"}).content.decode()
    assert "index.html" in page and "style.css" not in page and "Up one folder" in page
    assert 'aria-current="page">public_html<' in page


def test_the_account_without_a_files_provider_gets_the_not_available_page(client, owner, manager, account):
    from apps.products.models import Server

    Server.objects.filter(kind="demo").update(kind="manual")  # a provider with no files capability
    client.force_login(owner)
    page = client.get(url(account)).content.decode()
    assert "not available for your hosting server yet" in page and "Upload" not in page


def test_create_folder_and_file_upload_rename_copy_delete_end_to_end(client, owner, account):
    client.force_login(owner)
    act = url(account, "do/")
    client.post(act, {"action": "mkdir", "path": "public_html", "name": "img"})
    client.post(act, {"action": "upload", "path": "public_html/img",
                      "file": SimpleUploadedFile("logo.png", b"\x89PNG-bytes")})
    page = client.get(url(account), {"path": "public_html/img"}).content.decode()
    assert "logo.png" in page
    client.post(act, {"action": "rename", "path": "public_html/img", "item": "public_html/img/logo.png",
                      "new_name": "brand.png"})
    client.post(act, {"action": "copy", "path": "public_html/img", "item": "public_html/img/brand.png",
                      "new_name": "brand2.png"})
    page = client.get(url(account), {"path": "public_html/img"}).content.decode()
    assert "brand.png" in page and "brand2.png" in page and "logo.png" not in page
    client.post(act, {"action": "delete", "path": "public_html/img", "item": "public_html/img/brand2.png"})
    page = client.get(url(account), {"path": "public_html/img"}).content.decode()
    assert "brand2.png" not in page and "brand.png" in page


def test_download_returns_the_bytes_as_an_attachment(client, owner, account):
    client.force_login(owner)
    response = client.get(url(account, "download/"), {"path": "public_html/robots.txt"})
    assert response.status_code == 200 and response.content.startswith(b"User-agent")
    assert response["Content-Disposition"].startswith("attachment") and response["X-Content-Type-Options"] == "nosniff"


def test_editing_a_text_file_saves_new_content_and_a_binary_is_refused(client, owner, account):
    client.force_login(owner)
    page = client.get(url(account, "edit/"), {"path": "public_html/index.html"}).content.decode()
    assert "<textarea" in page and "Your website is ready" in page
    client.post(url(account, "edit/"), {"path": "public_html/index.html", "content": "<h1>Changed</h1>\r\n"})
    assert "Changed" in client.get(url(account, "edit/"), {"path": "public_html/index.html"}).content.decode()
    client.post(url(account, "do/"), {"action": "upload", "path": "", "file": SimpleUploadedFile("pic.bin", b"\xff\xfe\x00\x01")})
    response = client.get(url(account, "edit/"), {"path": "pic.bin"}, follow=True)
    assert "not text" in response.content.decode()


def test_search_lists_matches_from_subfolders(client, owner, account):
    client.force_login(owner)
    page = client.get(url(account), {"q": "style"}).content.decode()
    assert "style.css" in page and "public_html/css" in page


def test_traversal_in_any_parameter_never_reaches_a_file_outside_the_website(client, owner, account, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("TOP-SECRET", encoding="utf-8")
    client.force_login(owner)
    for attempt in ("../secret.txt", "../../secret.txt", "/etc/passwd", "public_html/../../secret.txt", "C:/Windows/win.ini"):
        for route in ("download/", "edit/"):
            response = client.get(url(account, route), {"path": attempt}, follow=True)
            assert b"TOP-SECRET" not in response.content and b"root:" not in response.content, (route, attempt)
        assert client.get(url(account), {"path": attempt}, follow=True).status_code == 200
    client.post(url(account, "do/"), {"action": "delete", "path": "", "item": "../secret.txt"})
    client.post(url(account, "do/"), {"action": "rename", "path": "", "item": "robots.txt", "new_name": "../../escaped.txt"})
    assert outside.exists() and not (tmp_path / "escaped.txt").exists()


def test_a_stranger_cannot_see_or_change_another_customers_files(client, customer, account):
    client.force_login(customer)
    assert client.get(url(account)).status_code == 404
    assert client.post(url(account, "do/"), {"action": "delete", "path": "", "item": "logs"}).status_code == 404
    assert client.get(url(account, "download/"), {"path": "robots.txt"}).status_code == 404


def test_uploads_over_the_limit_are_refused_and_changes_are_audited_without_content(client, owner, account, monkeypatch):
    from apps.hosting import files

    client.force_login(owner)
    monkeypatch.setattr(files, "MAX_UPLOAD_BYTES", 10)
    client.post(url(account, "do/"), {"action": "upload", "path": "", "file": SimpleUploadedFile("big.txt", b"x" * 50)})
    assert "big.txt" not in client.get(url(account)).content.decode()
    client.post(url(account, "edit/"), {"path": "public_html/index.html", "content": "classified-text-123"})
    events = AuditEvent.objects.filter(action="hosting.control")
    assert events.filter(metadata__operation="write_file", metadata__path="public_html/index.html").exists()
    assert "classified-text-123" not in str(list(events.values_list("metadata", flat=True)))
