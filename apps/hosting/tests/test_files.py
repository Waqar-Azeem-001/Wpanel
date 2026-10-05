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


# --- Trash, zip and extract (sandbox) ---------------------------------------------------------------------------

def test_deleting_moves_to_the_trash_and_restore_puts_it_back(box):
    box.write_file("public_html/a.txt", b"keep me")
    item_id = box.trash_path("public_html/a.txt")
    assert "a.txt" not in [e.name for e in box.list_files("public_html")]
    [item] = box.list_trash()
    assert item.item_id == item_id and item.name == "a.txt" and item.original_path == "public_html/a.txt"
    assert box.restore_trash(item_id) == "public_html/a.txt"
    assert box.read_file("public_html/a.txt", max_bytes=100) == b"keep me" and box.list_trash() == []


def test_the_trash_folder_is_hidden_reserved_and_never_searched(box):
    box.trash_path("public_html/index.html")
    assert ".trash" not in [e.name for e in box.list_files("")]
    for attempt in (lambda: box.list_files(".trash"), lambda: box.read_file(".trash/.index.json", max_bytes=999),
                    lambda: box.delete_path(".trash"), lambda: box.rename_path(".trash", "x")):
        with pytest.raises(HostingError):
            attempt()
    assert box.search_files("", "index") == []  # the trashed file is not found by name either


def test_restoring_when_the_name_is_taken_or_the_folder_is_gone_never_overwrites(box):
    box.write_file("public_html/a.txt", b"old")
    first = box.trash_path("public_html/a.txt")
    box.write_file("public_html/a.txt", b"new")
    restored = box.restore_trash(first)
    assert restored == "public_html/a.txt (restored)"
    assert box.read_file("public_html/a.txt", max_bytes=10) == b"new"
    box.make_directory("public_html/tmp")
    box.write_file("public_html/tmp/b.txt", b"b")
    second = box.trash_path("public_html/tmp/b.txt")
    box.delete_path("public_html/tmp")
    assert box.restore_trash(second) == "b.txt"  # its folder is gone: back at the top, not lost


def test_purge_one_and_empty_the_trash(box):
    one = box.trash_path("public_html/index.html")
    box.purge_trash(one)
    assert box.list_trash() == []
    with pytest.raises(HostingError):
        box.purge_trash(one)
    box.write_file("x.txt", b"1")
    box.trash_path("x.txt")
    box.purge_trash(None)
    assert box.list_trash() == []
    with pytest.raises(HostingError):
        box.restore_trash("../escape")


def test_zip_then_extract_round_trips_files_and_folders(box):
    box.make_directory("public_html/css")
    box.write_file("public_html/css/a.css", b"body{}")
    count = box.archive_paths(["public_html"], "site.zip")
    assert count >= 3 and box.read_file("site.zip", max_bytes=10_000_000)[:2] == b"PK"
    box.extract_archive("site.zip", "unpacked")
    assert box.read_file("unpacked/public_html/css/a.css", max_bytes=100) == b"body{}"
    with pytest.raises(HostingError):
        box.archive_paths(["public_html"], "site.zip")  # never overwrites
    with pytest.raises(HostingError):
        box.archive_paths(["public_html"], "no-ending")
    with pytest.raises(HostingError):
        box.archive_paths([""], "root.zip")  # the main folder as a whole


def test_an_archive_never_contains_itself(box):
    import zipfile

    box.archive_paths(["public_html"], "public_html/self.zip")
    with zipfile.ZipFile(box.root / "public_html" / "self.zip") as archive:
        assert not [n for n in archive.namelist() if n.endswith("self.zip")]


def _evil_zip(path, name, data=b"x", symlink=False):
    import zipfile

    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo(name)
        if symlink:
            info.external_attr = 0o120777 << 16
        archive.writestr(info, data)


@pytest.mark.parametrize("name", ["../evil.txt", "a/../../evil.txt", "/abs.txt", "C:/evil.txt", ".trash/x", "ok/../../e"])
def test_extracting_refuses_an_archive_with_an_unsafe_member_before_writing_anything(box, tmp_path, name):
    _evil_zip(box.root / "bad.zip", name)
    with pytest.raises(HostingError):
        box.extract_archive("bad.zip", "out")
    assert not (tmp_path / "evil.txt").exists()
    assert not (box.root / "out").exists()  # nothing at all was created


def test_extracting_refuses_links_conflicts_and_non_archives(box):
    import zipfile

    _evil_zip(box.root / "link.zip", "l", b"/etc/passwd", symlink=True)
    with pytest.raises(HostingError):
        box.extract_archive("link.zip", "out")
    with zipfile.ZipFile(box.root / "dup.zip", "w") as archive:
        archive.writestr("index.html", "from zip")
    box.make_directory("site")
    box.write_file("site/index.html", b"mine")
    with pytest.raises(HostingError):
        box.extract_archive("dup.zip", "site")  # would overwrite
    assert box.read_file("site/index.html", max_bytes=100) == b"mine"
    box.write_file("fake.zip", b"not really a zip")
    with pytest.raises(HostingError):
        box.extract_archive("fake.zip", "out2")


def test_zip_bombs_are_refused_by_size(box, monkeypatch):
    import zipfile

    from apps.hosting.adapters import sandbox

    monkeypatch.setattr(sandbox, "MAX_ARCHIVE_BYTES", 100)
    with zipfile.ZipFile(box.root / "big.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("zeros.bin", bytes(5000))
    with pytest.raises(HostingError):
        box.extract_archive("big.zip", "out")


# --- Trash, zip and extract on the screens -----------------------------------------------------------------------

def test_delete_goes_to_the_trash_page_where_it_can_be_restored_or_emptied(client, owner, account):
    from apps.hosting import control

    client.force_login(owner)
    act = url(account, "do/")
    client.post(act, {"action": "delete", "path": "public_html", "item": "public_html/robots.txt"})
    assert "robots.txt" not in client.get(url(account), {"path": "public_html"}).content.decode()
    trash = client.get(url(account, "trash/")).content.decode()
    assert "robots.txt" in trash and "Restore" in trash and "Empty trash" in trash
    [item] = control.perform(owner, account, "files", "list_trash")
    client.post(act, {"action": "restore", "trash_id": item.item_id})
    assert "robots.txt" in client.get(url(account), {"path": "public_html"}).content.decode()
    client.post(act, {"action": "delete", "ids": ["public_html/robots.txt", "logs"], "path": ""})
    assert len(control.perform(owner, account, "files", "list_trash")) == 2
    client.post(act, {"action": "empty_trash"})
    assert control.perform(owner, account, "files", "list_trash") == []


def test_zip_selected_items_and_extract_them_again(client, owner, account):
    client.force_login(owner)
    act = url(account, "do/")
    client.post(act, {"action": "zip", "ids": ["public_html"], "path": "", "archive_name": "backup"})
    page = client.get(url(account)).content.decode()
    assert "backup.zip" in page and "Extract backup.zip" in page
    client.post(act, {"action": "extract", "path": "", "item": "backup.zip"})
    inner = client.get(url(account), {"path": "backup/public_html"}).content.decode()
    assert "index.html" in inner


def test_the_file_manager_shows_visible_actions_not_a_hidden_menu(client, owner, account):
    client.force_login(owner)
    page = client.get(url(account), {"path": "public_html"}).content.decode()
    for label in ("Edit index.html", "Download index.html", "Rename index.html", "Copy index.html", "Delete index.html"):
        assert label in page, label
    assert "dropdown-menu" not in page.split("fm-table")[1] and "data-fm-selection" in page and "Zip" in page


def test_trash_and_zip_changes_are_audited(client, owner, account):
    client.force_login(owner)
    client.post(url(account, "do/"), {"action": "delete", "path": "", "item": "logs"})
    client.post(url(account, "do/"), {"action": "zip", "ids": ["public_html"], "path": ""})
    operations = set(AuditEvent.objects.filter(action="hosting.control").values_list("metadata__operation", flat=True))
    assert {"trash_path", "archive_paths"} <= operations
