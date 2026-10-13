"""'Create website' through the whole chain on a native node: the customer's request, the signed job, the node doing the work, and the screen hearing the result."""
import pytest

from apps.agent import wordpress_fixture as fixture
from apps.agent.models import AgentJob
from apps.agent.reference_agent import NativeMachine
from apps.agent.tests.test_native_website import make_node, order, world  # noqa: F401
from apps.hosting import site_apps
from apps.hosting.models import AppState

pytestmark = pytest.mark.django_db
PASSWORD = "Kq7vRt2mXzLp-91"


def native_site(world, tmp_path, **fetch):  # noqa: F811
    manager, client, product = world
    product.resource_limits = {"disk_mb": 2048, "databases": 3}
    product.save()
    server, ref = make_node(manager, "web-1")
    ref.machine = NativeMachine(files_dir=tmp_path, wordpress_fetch=fixture.make_fetch(**fetch) if fetch else None)
    account = order(manager, client, product, server)
    ref.poll_once()
    account.refresh_from_db()
    docroot = tmp_path / account.username / "sites" / "acme.com" / "public_html"
    docroot.mkdir(parents=True, exist_ok=True)
    (docroot / "index.html").write_text("placeholder")
    return account, ref, client.contacts.get().user, docroot


def start(owner, account):
    return site_apps.start_wordpress(owner, account, title="Acme Shop", admin_user="siteadmin", admin_email="owner@gmail.example.com", admin_password=PASSWORD)


def test_the_customers_wordpress_request_runs_on_the_node_and_the_screen_hears_back(world, tmp_path):  # noqa: F811
    account, ref, owner, docroot = native_site(world, tmp_path)
    app = start(owner, account)
    assert app.state == AppState.INSTALLING
    job = AgentJob.objects.get(command="INSTALL_WORDPRESS")
    assert ref.poll_once() == job.pk
    app.refresh_from_db()
    assert app.state == AppState.READY and app.version == fixture.VERSION and app.admin_url.endswith("/wp-admin/")
    assert (docroot / "wp-config.php").exists()
    stored = repr(list(AgentJob.objects.values_list("params", "result_data", "result_message")))
    assert PASSWORD not in stored  # hidden in the stored job, never in the result


def test_a_failed_installation_is_shown_as_failed_with_a_reason_and_can_be_retried(world, tmp_path):  # noqa: F811
    account, ref, owner, docroot = native_site(world, tmp_path, tamper=True)
    start(owner, account)
    ref.poll_once()
    app = site_apps.current(account)
    assert app.state == AppState.FAILED and "checksum" in app.error
    assert not (docroot / "wp-config.php").exists() and not ref.machine.engine.dbs  # left as it was
    ref.machine.wordpress.fetch = fixture.make_fetch()  # the download is good the second time
    start(owner, account)
    ref.poll_once()
    assert site_apps.current(account).state == AppState.READY


def test_a_server_without_the_installer_never_queues_anything(world, tmp_path):  # noqa: F811
    from apps.core.exceptions import ServiceError
    from apps.products.models import Server, ServerKind

    account, ref, owner, docroot = native_site(world, tmp_path)
    Server.objects.filter(pk=account.server_id).update(kind=ServerKind.MANUAL)
    with pytest.raises(ServiceError):
        start(owner, account)
    assert not AgentJob.objects.filter(command="INSTALL_WORDPRESS").exists()
