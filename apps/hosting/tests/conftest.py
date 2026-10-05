import pytest


@pytest.fixture(autouse=True)
def demo_files_in_a_temporary_folder(settings, tmp_path):
    """The demo provider's sample website files never touch the real working folder during tests."""
    settings.DEMO_FILES_ROOT = tmp_path / "demo_files"
