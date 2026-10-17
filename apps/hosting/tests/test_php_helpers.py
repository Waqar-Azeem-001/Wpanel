"""The one-file PHP helpers: an expiry inside the file, and a sweep that removes the ones left behind."""
import shutil
import subprocess
import time

import pytest

from apps.hosting import care_scanner, php_helpers, server_tools, wp_clone, wp_installer, wp_tool

pytestmark = pytest.mark.django_db


def test_every_helper_template_gets_the_expiry_guard_and_a_time():
    for module in (care_scanner, server_tools, wp_clone, wp_installer, wp_tool):
        filled = php_helpers.fill(module.PHP, now=1_000)
        assert "const WHP_EXPIRES = %d;" % (1_000 + php_helpers.LIFETIME_SECONDS) in filled
        assert filled.index("WHP_EXPIRES) {") < filled.index("hash_equals(WHP_TOKEN_HASH"), module.__name__  # the expiry is checked before the token
        assert "__EXPIRES__" not in filled


@pytest.mark.skipif(shutil.which("php") is None, reason="PHP is not installed here")
def test_the_hardened_helpers_are_still_valid_php(tmp_path):
    for module in (care_scanner, server_tools, wp_clone, wp_installer, wp_tool):
        file = tmp_path / f"{module.__name__.split('.')[-1]}.php"
        file.write_text(php_helpers.fill(module.PHP).replace("__TOKEN_HASH__", "x").replace("__RESULT__", "r.json").replace("__CONFIG__", "e30=").replace("__PROGRESS__", "p.json"))
        done = subprocess.run(["php", "-l", str(file)], capture_output=True, text=True)
        assert done.returncode == 0, (module.__name__, done.stdout)


def test_a_generated_helper_carries_its_expiry():
    file_name, result, token, source = wp_tool.build("inventory")
    assert "const WHP_EXPIRES = " in source and "__EXPIRES__" not in source and time.time() < int(source.split("WHP_EXPIRES = ")[1].split(";")[0]) <= time.time() + php_helpers.LIFETIME_SECONDS + 5


def test_the_sweep_names_only_helpers_and_only_old_ones():
    assert php_helpers.HELPER_NAME.match("whp-wp-0a1b2c3d4e5f6071.php")
    assert php_helpers.HELPER_NAME.match("whp-install-abcdef0123.php")
    assert not php_helpers.HELPER_NAME.match("whp-site-options.php") and not php_helpers.HELPER_NAME.match("index.php") and not php_helpers.HELPER_NAME.match("whp-wp-1.json")
    from datetime import datetime, timedelta, timezone

    now = time.time()
    two_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    assert php_helpers._age(two_hours_ago, now) >= php_helpers.SWEEP_AFTER_SECONDS
    assert php_helpers._age(datetime.now(timezone.utc).isoformat(), now) < php_helpers.SWEEP_AFTER_SECONDS
    assert php_helpers._age("not a date", now) > php_helpers.SWEEP_AFTER_SECONDS
