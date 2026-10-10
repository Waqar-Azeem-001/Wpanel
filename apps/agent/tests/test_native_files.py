"""Native files and SFTP (N6): the customer's File Manager and FTP screens working through the node's agent, on real folders in a temporary directory."""
import errno
import io
import json
import os

import pytest
from django.urls import reverse

from apps.agent import services as agent
from apps.agent.models import AgentJob
from apps.agent.node import files as node_files
from apps.agent.node import file_worker
from apps.agent.node.sftp import SSHD_CONF
from apps.audit.models import AuditEvent
from apps.hosting import control, services as hosting
from apps.hosting.adapters.base import HostingError
from apps.hosting.models import HostingStatus
from apps.agent.reference_agent import NativeMachine

from .test_native_website import make_node, order, world  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def site(world, tmp_path):  # noqa: F811
    manager, client, product = world
    product.resource_limits = {"disk_mb": 2048, "ftp_accounts": 2}
    product.save()
    server, ref = make_node(manager)
    ref.machine = NativeMachine(files_dir=tmp_path)
    account = order(manager, client, product, server)
    ref.poll_once()
    account.refresh_from_db()
    assert account.status == HostingStatus.ACTIVE
    web = tmp_path / account.username / "sites" / "acme.com" / "public_html"
    web.mkdir(parents=True)
    (web / "index.html").write_text("<h1>hi</h1>")
    agent.inline_agent = ref.poll_once
    yield manager, account, ref, client.contacts.get().user, tmp_path / account.username / "sites"
    agent.inline_agent = None


def f(owner, account, op, *args, **kw):
    return control.perform(owner, account, "files", op, *args, **kw)


# --- files -----------------------------------------------------------------------------------------------------------------------------

def test_the_file_manager_operations_work_through_the_node(site):
    manager, account, ref, owner, root = site
    listing = f(owner, account, "list_files", "acme.com/public_html")
    assert [e.name for e in listing] == ["index.html"] and listing[0].size_bytes == len("<h1>hi</h1>")
    f(owner, account, "make_directory", "acme.com/public_html/css")
    f(owner, account, "write_file", "acme.com/public_html/css/a.css", b"body{}", overwrite=False)
    assert f(owner, account, "read_file", "acme.com/public_html/css/a.css", max_bytes=1000) == b"body{}"
    with pytest.raises(Exception, match="already exists"):
        f(owner, account, "write_file", "acme.com/public_html/css/a.css", b"x", overwrite=False)
    f(owner, account, "write_file", "acme.com/public_html/css/a.css", b"body{color:red}", overwrite=True)
    f(owner, account, "copy_path", "acme.com/public_html/css", "acme.com/public_html/css2")
    f(owner, account, "rename_path", "acme.com/public_html/css2", "acme.com/public_html/styles")
    names = [e.name for e in f(owner, account, "list_files", "acme.com/public_html")]
    assert names == ["css", "styles", "index.html"]  # folders first
    assert (root / "acme.com/public_html/styles/a.css").read_bytes() == b"body{color:red}"  # really on disk
    f(owner, account, "delete_path", "acme.com/public_html/styles")
    assert not (root / "acme.com/public_html/styles").exists()


def test_binary_content_survives_the_trip(site):
    manager, account, ref, owner, root = site
    data = bytes(range(256)) * 40
    f(owner, account, "write_file", "acme.com/public_html/b.bin", data, overwrite=False)
    assert f(owner, account, "read_file", "acme.com/public_html/b.bin", max_bytes=100000) == data


@pytest.mark.parametrize("path", ["../outside", "/etc/passwd", "a/../../b", "acme.com/../../x", "bad\\name", "c:/x", "a\x00b"])
def test_paths_that_try_to_leave_the_account_are_refused(site, path):
    manager, account, ref, owner, root = site
    with pytest.raises(Exception):
        f(owner, account, "read_file", path, max_bytes=100)
    with pytest.raises(Exception):
        f(owner, account, "write_file", path, b"x", overwrite=True)
    assert not (root.parent.parent / "outside").exists()


def test_a_link_pointing_outside_is_not_followed(site, tmp_path):
    manager, account, ref, owner, root = site
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    try:
        os.symlink(secret, root / "acme.com/public_html/leak.txt")
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot make symbolic links")
    with pytest.raises(Exception, match="Links are not followed"):
        f(owner, account, "read_file", "acme.com/public_html/leak.txt", max_bytes=1000)
    assert "leak.txt" not in [e.name for e in f(owner, account, "list_files", "acme.com/public_html")]


def test_the_main_folder_cannot_be_removed_and_unsafe_permissions_are_refused(site):
    manager, account, ref, owner, root = site
    with pytest.raises(Exception):
        f(owner, account, "delete_path", "")
    with pytest.raises(Exception):
        f(owner, account, "rename_path", "", "other")
    for mode in ("777", "666", "4755", "abc", "600777"):
        with pytest.raises(Exception):
            f(owner, account, "set_permissions", "acme.com/public_html/index.html", mode)
    f(owner, account, "set_permissions", "acme.com/public_html/index.html", "600")
    shown = next(e for e in f(owner, account, "list_files", "acme.com/public_html") if e.name == "index.html")
    assert shown.permissions == "0600"


def test_archive_extract_trash_restore_purge_and_search(site):
    manager, account, ref, owner, root = site
    base = "acme.com/public_html"
    f(owner, account, "write_file", base + "/notes.txt", b"hello", overwrite=False)
    assert f(owner, account, "archive_paths", [base + "/notes.txt", base + "/index.html"], base + "/site.zip") == 2
    f(owner, account, "make_directory", base + "/unzipped")
    assert f(owner, account, "extract_archive", base + "/site.zip", base + "/unzipped") == 2
    assert (root / base / "unzipped/notes.txt").read_bytes() == b"hello"
    assert [e.name for e in f(owner, account, "search_files", base, "notes")] == ["notes.txt", "notes.txt"]
    item = f(owner, account, "trash_path", base + "/notes.txt")
    trash = f(owner, account, "list_trash")
    assert [t.original_path for t in trash] == [base + "/notes.txt"] and trash[0].item_id == item
    assert f(owner, account, "restore_trash", item) == base + "/notes.txt"
    other = f(owner, account, "trash_path", base + "/site.zip")
    f(owner, account, "purge_trash", other)
    assert f(owner, account, "list_trash") == []
    f(owner, account, "trash_path", base + "/notes.txt")
    f(owner, account, "purge_trash", None)  # empty everything
    assert f(owner, account, "list_trash") == []
    with pytest.raises(Exception, match="no longer in the trash"):
        f(owner, account, "restore_trash", "abcdefabcdef")


def test_big_files_are_pointed_to_sftp(site):
    manager, account, ref, owner, root = site
    with pytest.raises(Exception, match="Use SFTP"):
        f(owner, account, "write_file", "acme.com/public_html/big.bin", b"x" * 3_000_001, overwrite=False)
    (root / "acme.com/public_html/huge.bin").write_bytes(b"y" * 3_100_000)
    with pytest.raises(Exception, match="Use SFTP"):
        f(owner, account, "read_file", "acme.com/public_html/huge.bin", max_bytes=25_000_000)


def test_a_full_disk_gives_the_plain_message(site, monkeypatch):
    manager, account, ref, owner, root = site

    def full(self, rel, content, overwrite=False):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(node_files.NodeSandbox, "write_file", full)
    with pytest.raises(Exception, match="disk space is full"):
        f(owner, account, "write_file", "acme.com/public_html/x.txt", b"x", overwrite=False)


def test_file_content_is_not_kept_after_the_job_finishes(site):
    manager, account, ref, owner, root = site
    f(owner, account, "write_file", "acme.com/public_html/secret.txt", b"TOP SECRET CONTENT", overwrite=False)
    f(owner, account, "read_file", "acme.com/public_html/secret.txt", max_bytes=1000)
    f(owner, account, "list_files", "acme.com/public_html")
    blob = json.dumps([[j.params, j.result_data] for j in AgentJob.objects.all()]) + json.dumps([e.metadata for e in AuditEvent.objects.all()])
    assert "VE9QIFNFQ1JFVA" not in blob and "TOP SECRET" not in blob and "secret.txt" not in json.dumps([j.result_data for j in AgentJob.objects.filter(command="FILE_LIST")])
    assert AgentJob.objects.get(command="FILE_WRITE").params["content"] == "***"


def test_someone_elses_customer_cannot_use_the_account_files(site, make_user):
    manager, account, ref, owner, root = site
    from apps.core.exceptions import ServiceError

    with pytest.raises(ServiceError):
        f(make_user("stranger@example.com"), account, "list_files", "")


def test_the_real_file_manager_page_lists_the_nodes_files(site, client):
    manager, account, ref, owner, root = site
    client.force_login(owner)
    html = client.get(reverse("hosting_customer:files", args=[account.pk]) + "?path=acme.com/public_html").content.decode()
    assert "index.html" in html


def test_the_file_worker_reports_errors_as_data_and_never_creates_a_missing_account_folder(tmp_path, monkeypatch):
    out = io.StringIO()
    file_worker.main(io.StringIO(json.dumps({"account": "ghost123", "command": "FILE_LIST", "params": {"path": ""}})), out)
    assert json.loads(out.getvalue())["error"]["code"] == "not_found" and not os.path.exists("/home/ghost123")


# --- SFTP logins -------------------------------------------------------------------------------------------------------------------------

def ftp(owner, account, op, *args):
    return control.perform(owner, account, "ftp", op, *args)


def test_an_sftp_login_is_created_listed_changed_and_removed(site):
    manager, account, ref, owner, root = site
    system = ref.machine.system
    login = ftp(owner, account, "create_ftp_account", "dev", "Kq7vRt2mXzLp", "acme.com/public_html")
    assert login == f"{account.username}_dev"
    uid = system.users[account.username]
    assert ["useradd", "-o", "-u", str(uid), "-g", account.username, "-G", "whp-sftp", "-M", "-d", f"/srv/whp/sftp/{login}", "-s", "/usr/sbin/nologin", login] in system.commands
    assert ["chpasswd"] in system.commands and not any("Kq7vRt2mXzLp" in " ".join(c) for c in system.commands if isinstance(c, list))
    unit = system.files[f"/etc/systemd/system/srv-whp-sftp-{login}-files.mount"]
    assert f"What=/home/{account.username}/sites/acme.com/public_html" in unit and "Options=bind" in unit
    assert "ChrootDirectory /srv/whp/sftp/%u" in system.files[SSHD_CONF] and "internal-sftp" in system.files[SSHD_CONF]
    assert system.commands.index(["sshd", "-t"]) < system.commands.index(["systemctl", "reload", "sshd"])
    shown = ftp(owner, account, "list_ftp_accounts")
    assert [(a.login, a.directory) for a in shown] == [(login, "acme.com/public_html")]
    ftp(owner, account, "set_ftp_password", login, "Wp9sLn4cHdQe")
    assert system.commands.count(["chpasswd"]) == 2
    ftp(owner, account, "delete_ftp_account", login)
    assert ftp(owner, account, "list_ftp_accounts") == [] and login not in system.users and not [p for p in system.files if login in p]


def test_sshd_is_reloaded_once_not_for_every_login(site):
    manager, account, ref, owner, root = site
    ftp(owner, account, "create_ftp_account", "one", "Kq7vRt2mXzLp", "")
    ftp(owner, account, "create_ftp_account", "two", "Kq7vRt2mXzLp", "acme.com")
    assert ref.machine.system.commands.count(["systemctl", "reload", "sshd"]) == 1


def test_sftp_rules_limit_names_folders_and_count(site):
    manager, account, ref, owner, root = site
    for name in ("Dev", "1dev", "d", "dev.ops", "dev-ops", "x" * 16):
        with pytest.raises(Exception):
            ftp(owner, account, "create_ftp_account", name.lower() if name != "Dev" else "Dev!", "Kq7vRt2mXzLp", "")
    with pytest.raises(Exception, match="10 to 128"):
        ftp(owner, account, "create_ftp_account", "dev", "short", "")
    for folder in ("../..", "/etc", "acme.com/nope"):
        with pytest.raises(Exception):
            ftp(owner, account, "create_ftp_account", "dev", "Kq7vRt2mXzLp", folder)
    ftp(owner, account, "create_ftp_account", "one", "Kq7vRt2mXzLp", "")
    with pytest.raises(Exception, match="already exists"):
        ftp(owner, account, "create_ftp_account", "one", "Kq7vRt2mXzLp", "")
    ftp(owner, account, "create_ftp_account", "two", "Kq7vRt2mXzLp", "")
    with pytest.raises(Exception, match="limit"):
        ftp(owner, account, "create_ftp_account", "three", "Kq7vRt2mXzLp", "")
    options = ftp(owner, account, "ftp_options")
    assert options.port == 0 and options.secure_port == 22 and options.max_accounts == 2 and options.host == account.server.hostname


def test_a_login_of_another_account_cannot_be_changed(site):
    manager, account, ref, owner, root = site
    for op, args in (("set_ftp_password", ("otheracct_dev", "Kq7vRt2mXzLp")), ("delete_ftp_account", ("otheracct_dev",))):
        with pytest.raises(Exception, match="does not belong"):
            ftp(owner, account, op, *args)


def test_a_failed_login_creation_leaves_nothing_behind(site):
    manager, account, ref, owner, root = site
    system = ref.machine.system
    ftp(owner, account, "create_ftp_account", "ok", "Kq7vRt2mXzLp", "")  # sshd is set up by now
    system.fail_on = len(system.commands) + 6  # a step in the middle fails
    with pytest.raises(Exception):
        ftp(owner, account, "create_ftp_account", "broken", "Kq7vRt2mXzLp", "")
    system.fail_on = None
    assert f"{account.username}_broken" not in system.users and not [p for p in system.files if "broken" in p]
    assert [a.login for a in ftp(owner, account, "list_ftp_accounts")] == [f"{account.username}_ok"]


def test_suspension_locks_logins_reactivation_unlocks_and_termination_removes_them(site):
    manager, account, ref, owner, root = site
    system = ref.machine.system
    login = ftp(owner, account, "create_ftp_account", "dev", "Kq7vRt2mXzLp", "")
    hosting.suspend_account(manager, account)
    ref.poll_once()
    account.refresh_from_db()
    assert ["usermod", "--lock", login] in system.commands
    hosting.unsuspend_account(manager, account)
    ref.poll_once()
    account.refresh_from_db()
    assert ["usermod", "--unlock", login] in system.commands
    hosting.terminate_account(manager, account)
    ref.poll_once()
    assert login not in system.users and not [p for p in system.files if login in p] and system.users == {}


def test_the_real_ftp_page_offers_sftp_only(site, client):
    manager, account, ref, owner, root = site
    ftp(owner, account, "create_ftp_account", "dev", "Kq7vRt2mXzLp", "")
    client.force_login(owner)
    html = client.get(reverse("hosting_customer:ftp", args=[account.pk])).content.decode()
    assert f"{account.username}_dev" in html and "22" in html


def test_file_and_sftp_commands_are_typed_and_sandboxed():
    from apps.agent import catalog

    for bad in ({"account": "abcd1234", "path": "../x"}, {"account": "abcd1234", "path": "a:b"}, {"account": "abcd1234", "path": "x" * 2000}):
        with pytest.raises(catalog.InvalidCommand):
            catalog.validate("FILE_MKDIR", bad)
    with pytest.raises(catalog.InvalidCommand):
        catalog.validate("FILE_WRITE", {"account": "abcd1234", "path": "a", "content": "not base64!!", "overwrite": "yes"})
    with pytest.raises(catalog.InvalidCommand):
        catalog.validate("FILE_ARCHIVE", {"account": "abcd1234", "paths": [], "archive_path": "a.zip"})
    with pytest.raises(catalog.InvalidCommand):
        catalog.validate("FILE_TRASH_PURGE", {"account": "abcd1234", "item_id": "../../etc"})
    assert catalog.validate("FILE_LIST", {"account": "abcd1234", "path": ""})["path"] == ""
    _ = (HostingError,)
