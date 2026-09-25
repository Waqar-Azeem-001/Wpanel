"""Fixtures: a client with an owner, a support agent, a department, and small valid uploads."""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.roles import Role
from apps.clients import services as client_services
from apps.support.models import Department

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
JPG = b"\xff\xd8\xff\xe0" + b"0" * 32
PDF = b"%PDF-1.4\n" + b"0" * 32


@pytest.fixture(autouse=True)
def private_media(settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = str(tmp_path / "private")
    return tmp_path / "private"


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def agent(staff):
    return staff(Role.SUPPORT_AGENT)


@pytest.fixture
def client_obj(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "last_name": "Lovelace",
                                                   "email": "ada@acme.test", "company_name": "Acme Ltd"})


@pytest.fixture
def owner(client_obj):
    return client_obj.contacts.get(role="owner").user


@pytest.fixture
def other_client(manager):
    return client_services.create_client(manager, {"first_name": "Bob", "email": "bob@other.test"})


@pytest.fixture
def stranger(other_client):
    return other_client.contacts.get().user


@pytest.fixture
def technical():
    return Department.objects.get(slug="technical")


def upload(name="shot.png", content=PNG):
    return SimpleUploadedFile(name, content)
