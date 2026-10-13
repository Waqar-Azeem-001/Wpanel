"""The one-file WordPress installer: its shape and safety properties. Running it for real needs PHP, a database and the internet; that was done by hand on 2026-10-13 against
wordpress.org with PHP 8.2 and MariaDB 10.4 (installed WordPress, wrong token refused, installer removed itself, no password in the result file). Here: what can be checked anywhere."""
import base64
import hashlib
import json
import shutil
import subprocess

import pytest

from apps.hosting import wp_installer

ARGS = dict(domain="acme.com", scheme="https", db="acct_wp", db_user="acct_wp", db_password="DbPass-123456", title="Acme Shop", admin_user="siteadmin",
            admin_email="owner@gmail.example.com", admin_password="Kq7vRt2mXzLp-91")


def test_the_file_holds_only_the_hash_of_its_token_and_the_config_in_one_blob():
    name, result, token, source = wp_installer.build(**ARGS)
    assert name.startswith("whp-install-") and name.endswith(".php") and result.startswith("whp-install-") and result.endswith(".json")
    assert token not in source and hashlib.sha256(token.encode()).hexdigest() in source
    blob = source.split("base64_decode('")[1].split("'")[0]
    config = json.loads(base64.b64decode(blob))
    assert config["domain"] == "acme.com" and config["url"] == "https://acme.com" and config["db"] == "acct_wp" and config["admin_password"] == ARGS["admin_password"]
    assert "Kq7vRt2mXzLp-91" not in source  # not readable in clear text in the file either


def test_each_build_is_different_and_unguessable():
    first, second = wp_installer.build(**ARGS), wp_installer.build(**ARGS)
    assert first[0] != second[0] and first[2] != second[2] and len(first[2]) >= 40


def test_it_removes_itself_first_checks_the_download_and_refuses_a_busy_folder():
    source = wp_installer.build(**ARGS)[3]
    assert source.index("@unlink(__FILE__)") < source.index("$archive = whp_get(")  # gone before anything is downloaded
    assert "hash_equals(WHP_TOKEN_HASH" in source and "sha1($archive) !== $sum" in source and "not_empty" in source and "whp_undo()" in source
    assert "wordpress.org/latest.zip" in source and "http://" not in source.replace("https://", "")


@pytest.mark.skipif(shutil.which("php") is None, reason="PHP is not installed here")
def test_the_generated_file_is_valid_php(tmp_path):
    path = tmp_path / "installer.php"
    path.write_text(wp_installer.build(**ARGS)[3], encoding="utf-8")
    done = subprocess.run(["php", "-l", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
