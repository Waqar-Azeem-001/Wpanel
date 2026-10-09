"""File Manager (H2) completions: move, copy into another folder, permissions - the sandbox's rules and the screen."""
import pytest
from django.core.management import call_command

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.hosting import control
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.sandbox import Sandbox, check_mode
from apps.hosting.models import HostingAccount
from apps.products import services as product_services
from apps.products.models import ProductType

pytestmark = pytest.mark.django_db


@pytest.fixture
def box(tmp_path):
    box = Sandbox(tmp_path / "site", meta_file=tmp_path / "state" / "permissions.json")
    box.make_directory("public_html")
    box.make_directory("backup")
    box.write_file("public_html/index.html", b"<h1>hi</h1>")
    return box


def modes(box, folder=""):
    return {e.name: e.permissions for e in box.list_files(folder)}


# --- The permission rules ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mode,is_dir,expected", [("644", False, "0644"), ("0600", False, "0600"), ("664", False, "0664"), ("755", True, "0755"),
                                                  ("0700", True, "0700"), ("775", True, "0775")])
def test_sensible_permissions_are_accepted_and_tidied_to_four_digits(mode, is_dir, expected):
    assert check_mode(mode, is_dir) == expected


@pytest.mark.parametrize("mode,is_dir", [("", False), ("abc", False), ("64", False), ("77777", False), ("888", False), ("-644", False), ("644\n", False) if False else ("6 44", False),
                                         ("777", False), ("666", False), ("757", True), ("4755", False), ("1777", True),
                                         ("444", False), ("400", False), ("500", True), ("655", True)])
def test_unsafe_or_self_locking_permissions_are_refused(mode, is_dir):
    with pytest.raises(HostingError):
        check_mode(mode, is_dir)


def test_files_and_folders_start_with_the_usual_permissions_and_can_be_changed(box):
    assert modes(box)["public_html"] == "0755"
    assert modes(box, "public_html")["index.html"] == "0644"
    box.set_permissions("public_html/index.html", "600")
    box.set_permissions("backup", "700")
    assert modes(box, "public_html")["index.html"] == "0600" and modes(box)["backup"] == "0700"


def test_permissions_follow_a_file_when_it_is_moved_copied_or_deleted(box):
    box.set_permissions("public_html/index.html", "600")
    box.rename_path("public_html/index.html", "backup/home.html")
    assert modes(box, "backup")["home.html"] == "0600"
    box.copy_path("backup/home.html", "public_html/copy.html")
    assert modes(box, "public_html")["copy.html"] == "0600" and modes(box, "backup")["home.html"] == "0600"
    box.delete_path("backup/home.html")
    box.write_file("backup/home.html", b"new")
    assert modes(box, "backup")["home.html"] == "0644"  # a new file does not inherit a deleted one's permission


def test_a_folders_permissions_move_with_its_contents(box):
    box.set_permissions("public_html/index.html", "600")
    box.rename_path("public_html", "site")
    assert modes(box, "site")["index.html"] == "0600"


def test_permissions_cannot_be_set_on_the_root_outside_the_site_or_on_what_does_not_exist(box):
    for target in ("", "nothing.txt", "../x", "/etc/passwd"):
        with pytest.raises(HostingError):
            box.set_permissions(target, "644")
    with pytest.raises(HostingError, match="Anyone-can-write"):
        box.set_permissions("public_html/index.html", "666")
    assert modes(box, "public_html")["index.html"] == "0644"


def test_the_permissions_file_lives_outside_the_website_folder(box, tmp_path):
    box.set_permissions("public_html/index.html", "600")
    assert (tmp_path / "state" / "permissions.json").exists()
    assert not [e for e in box.list_files("") if "permissions" in e.name]


# --- The screen ---------------------------------------------------------------------------------------------------------------

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


def furl(account, tail=""):
    return f"/account/hosting/{account.pk}/manage/files/{tail}"


def names(owner, account, path=""):
    return {e.name: e for e in control.perform(owner, account, "files", "list_files", path)}


def test_the_listing_shows_a_permissions_column_and_move_copy_and_permission_buttons(client, owner, account):
    client.force_login(owner)
    page = client.get(furl(account), {"path": "public_html"}).content.decode()
    assert "Permissions" in page and "0644" in page and 'data-bs-target="#fm-move"' in page and 'data-bs-target="#fm-chmod"' in page
    assert 'id="fm-move"' in page and 'id="fm-chmod"' in page and "Into folder" in page


def test_move_a_file_into_another_folder_and_copy_it_into_another_folder(client, owner, account):
    client.force_login(owner)
    act = furl(account, "do/")
    client.post(act, {"action": "mkdir", "path": "", "name": "archive"})
    client.post(act, {"action": "move", "path": "public_html", "item": "public_html/robots.txt", "to_folder": "archive"})
    assert "robots.txt" in names(owner, account, "archive") and "robots.txt" not in names(owner, account, "public_html")
    client.post(act, {"action": "copy", "path": "archive", "item": "archive/robots.txt", "new_name": "robots-copy.txt", "to_folder": "public_html"})
    assert "robots-copy.txt" in names(owner, account, "public_html") and "robots.txt" in names(owner, account, "archive")
    client.post(act, {"action": "copy", "path": "archive", "item": "archive/robots.txt", "new_name": "again.txt", "to_folder": ""})
    assert "again.txt" in names(owner, account, "archive")  # an empty folder means the same folder, as before


def test_a_move_to_a_missing_folder_or_outside_the_site_is_refused_with_a_message(client, owner, account):
    client.force_login(owner)
    act = furl(account, "do/")
    for bad in ("nowhere", "../../etc", "/etc"):
        page = client.post(act, {"action": "move", "path": "public_html", "item": "public_html/robots.txt", "to_folder": bad}, follow=True).content.decode()
        assert "alert" in page
        assert "robots.txt" in names(owner, account, "public_html")


def test_change_permissions_from_the_screen_and_a_bad_one_is_explained(client, owner, account):
    client.force_login(owner)
    act = furl(account, "do/")
    client.post(act, {"action": "chmod", "path": "public_html", "item": "public_html/index.html", "mode": "600"})
    assert names(owner, account, "public_html")["index.html"].permissions == "0600"
    bad = client.post(act, {"action": "chmod", "path": "public_html", "item": "public_html/index.html", "mode": "777"}, follow=True).content.decode()
    assert "Anyone-can-write" in bad and names(owner, account, "public_html")["index.html"].permissions == "0600"
    assert "Use a permission" in client.post(act, {"action": "chmod", "path": "", "item": "public_html/index.html", "mode": "rwx"}, follow=True).content.decode()


def test_moves_and_permission_changes_are_audited_with_the_path_and_mode(client, owner, account):
    client.force_login(owner)
    act = furl(account, "do/")
    client.post(act, {"action": "chmod", "path": "public_html", "item": "public_html/index.html", "mode": "640"})
    client.post(act, {"action": "mkdir", "path": "", "name": "archive"})
    client.post(act, {"action": "move", "path": "public_html", "item": "public_html/robots.txt", "to_folder": "archive"})
    events = list(AuditEvent.objects.filter(action="hosting.control").values_list("metadata", flat=True))
    assert any(e.get("operation") == "set_permissions" and e.get("mode") == "640" and e.get("path") == "public_html/index.html" for e in events)
    assert any(e.get("operation") == "rename_path" and e.get("to") == "archive/robots.txt" for e in events)


def test_a_full_disk_stops_copying_zipping_and_extracting_too(owner, account, monkeypatch):
    from apps.hosting.adapters.demo import DemoAdapter

    monkeypatch.setattr(DemoAdapter, "get_usage", lambda self, username: {"disk_used_mb": 20480, "disk_limit_mb": 20480, "bandwidth_used_mb": 0,
                                                                          "bandwidth_limit_mb": 1})
    from apps.core.exceptions import ServiceError

    for operation, args in (("copy_path", ("public_html/index.html", "public_html/x.html")), ("archive_paths", (["public_html"], "x.zip")),
                            ("extract_archive", ("x.zip", ""))):
        with pytest.raises(ServiceError, match="disk space is full"):
            control.perform(owner, account, "files", operation, *args)
