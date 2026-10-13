"""The one-file WordPress copier behind staging, run for real: PHP copies files and a real MariaDB/MySQL database from live to staging (addresses rewritten, serialized values
kept valid, configuration for the new database) and back again (live configuration and search-engine setting kept).

Needs PHP with mysqli and a MySQL/MariaDB server. Set WHP_TEST_MYSQL to "<path to mysql client>|<port>" (the server is expected on 127.0.0.1 with user root and no password,
for example a throw-away one); without it the database part is skipped. The parts that need no database always run."""
import json
import os
import shutil
import subprocess

import pytest

from apps.hosting import wp_clone

RAW = os.environ.get("WHP_TEST_MYSQL", "")
CLIENT, _, PORT = RAW.partition("|")
needs_php = pytest.mark.skipif(shutil.which("php") is None, reason="PHP is not installed here")
needs_db = pytest.mark.skipif(not (RAW and shutil.which("php")), reason="WHP_TEST_MYSQL is not set")


def sql(statement, database=""):
    cmd = [CLIENT, "--no-defaults", "-h127.0.0.1", f"-P{PORT}", "-uroot", "-N", "-B", "--raw"] + ([database] if database else []) + ["-e", statement]
    done = subprocess.run(cmd, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def run_script(folder, name, token):
    done = subprocess.run(["php", "-r", '$_GET["t"]=$argv[2]; include $argv[1];', str(folder / name), token], capture_output=True, text=True)
    return json.loads(done.stdout) if done.stdout.strip().startswith("{") else {"ok": False, "raw": done.stdout + done.stderr}


@needs_php
def test_the_file_is_valid_php_refuses_a_wrong_token_and_removes_itself(tmp_path):
    name, result, token, source = wp_clone.build(action="create", src=".", dst="stg", from_url="http://a.test", to_url="http://s.a.test", dst_db={"name": "x", "user": "x", "password": "x"})
    (tmp_path / name).write_text(source)
    assert "No syntax errors" in subprocess.run(["php", "-l", str(tmp_path / name)], capture_output=True, text=True).stdout
    wrong = subprocess.run(["php", "-r", '$_GET["t"]="nope"; include $argv[1];', str(tmp_path / name)], capture_output=True, text=True)
    assert '"ok":false' in wrong.stdout and (tmp_path / name).exists()
    answer = run_script(tmp_path, name, token)
    assert answer["ok"] is False and "folders to copy" in answer["error"] and not (tmp_path / name).exists()  # the folders do not exist: it says so and is used up
    with pytest.raises(ValueError):
        wp_clone.build(action="wipe", src=".", dst=".", from_url="a", to_url="b")


def test_the_token_is_only_a_hash_in_the_file_and_the_target_password_is_not_in_clear_text():
    name, result, token, source = wp_clone.build(action="create", src=".", dst="stg", from_url="http://a.test", to_url="http://s.a.test",
                                                 dst_db={"name": "n", "user": "u", "password": "S3cret-pw-value"})
    assert token not in source and "S3cret-pw-value" not in source and name.startswith("whp-clone-")


CONFIG = """<?php
define( 'DB_NAME', 'whp_src' );
define( 'DB_USER', 'root' );
define( 'DB_PASSWORD', '' );
define( 'DB_HOST', '127.0.0.1:%s' );
$table_prefix = 'wp_';
"""


@needs_db
def test_create_then_push_copies_files_and_database_and_rewrites_addresses(tmp_path):
    for database in ("whp_src", "whp_stg"):
        sql(f"DROP DATABASE IF EXISTS {database}")
        sql(f"CREATE DATABASE {database} CHARACTER SET utf8mb4")
    sql("CREATE TABLE wp_options (option_id INT AUTO_INCREMENT PRIMARY KEY, option_name VARCHAR(191), option_value LONGTEXT)", "whp_src")
    sql("CREATE TABLE wp_posts (ID INT AUTO_INCREMENT PRIMARY KEY, post_content LONGTEXT, guid VARCHAR(255))", "whp_src")
    sql("CREATE TABLE other_table (id INT)", "whp_src")  # not WordPress's: not copied
    serialized = 'a:1:{s:3:"url";s:19:"http://acme.test/ab";}'
    sql("INSERT INTO wp_options (option_name, option_value) VALUES ('siteurl','http://acme.test'),('home','http://acme.test'),('blog_public','1'),"
        f"('widget','{serialized}')", "whp_src")
    sql("INSERT INTO wp_posts (post_content, guid) VALUES ('<a href=\"http://acme.test/about\">About</a> {\"u\":\"http:\\\\/\\\\/acme.test\\\\/x\"}', 'http://acme.test/?p=1')", "whp_src")

    live, stg = tmp_path / "live", tmp_path / "live" / "staging"
    stg.mkdir(parents=True)
    (live / "wp-content" / "uploads").mkdir(parents=True)
    (live / "wp-config.php").write_text(CONFIG % PORT)
    (live / "index.php").write_text("<?php // live")
    (live / "wp-content" / "uploads" / "a.jpg").write_bytes(b"\xff\xd8binary")
    (live / ".whp-quarantine").mkdir()
    (live / ".whp-quarantine" / "x.quarantined").write_text("bad")

    name, result, token, source = wp_clone.build(action="create", src=".", dst="staging", from_url="http://acme.test", to_url="http://staging.acme.test",
                                                 exclude=["staging"], dst_db={"name": "whp_stg", "user": "root", "password": ""})
    (live / name).write_text(source)
    answer = run_script(live, name, token)
    assert answer["ok"] is True, answer
    assert not (live / name).exists() and (live / result).exists()  # used up; result left for the panel
    assert answer["tables"] == 2 and answer["rows"] == 5 and answer["files"] >= 3
    # files
    assert (stg / "index.php").read_text() == "<?php // live" and (stg / "wp-content" / "uploads" / "a.jpg").read_bytes() == b"\xff\xd8binary"
    assert not (stg / "staging").exists() and not (stg / ".whp-quarantine").exists()
    # configuration
    config = (stg / "wp-config.php").read_text()
    assert "define( 'DB_NAME', 'whp_stg' )" in config and "WP_HOME', 'http://staging.acme.test'" in config and "whp_src" not in config
    # database
    assert sql("SHOW TABLES", "whp_stg").split() == ["wp_options", "wp_posts"]
    assert sql("SELECT option_value FROM wp_options WHERE option_name='siteurl'", "whp_stg") == "http://staging.acme.test"
    assert sql("SELECT option_value FROM wp_options WHERE option_name='blog_public'", "whp_stg") == "0"  # search engines asked not to index
    widget = sql("SELECT option_value FROM wp_options WHERE option_name='widget'", "whp_stg")
    assert widget == 'a:1:{s:3:"url";s:27:"http://staging.acme.test/ab";}'  # the length was recalculated
    content = sql("SELECT post_content FROM wp_posts", "whp_stg")
    assert "http://staging.acme.test/about" in content and "http:\\/\\/staging.acme.test\\/x" in content and "acme.test/about" in content
    assert sql("SELECT guid FROM wp_posts", "whp_stg") == "http://acme.test/?p=1"  # a post's guid is an identifier, not an address to move
    assert sql("SELECT option_value FROM wp_options WHERE option_name='siteurl'", "whp_src") == "http://acme.test"  # live untouched

    # the customer changes the staging site, then publishes it
    sql("UPDATE wp_posts SET post_content = 'new text http://staging.acme.test/new'", "whp_stg")
    sql("INSERT INTO wp_options (option_name, option_value) VALUES ('extra','from staging')", "whp_stg")
    (stg / "wp-content" / "new.txt").write_text("hello")
    (live / "old-only.txt").write_text("only on live")
    config_before = (live / "wp-config.php").read_text()
    name, result, token, source = wp_clone.build(action="push", src="staging", dst=".", from_url="http://staging.acme.test", to_url="http://acme.test", exclude=["staging"])
    (live / name).write_text(source)
    answer = run_script(live, name, token)
    assert answer["ok"] is True, answer
    assert sql("SELECT post_content FROM wp_posts", "whp_src") == "new text http://acme.test/new"
    assert sql("SELECT option_value FROM wp_options WHERE option_name='extra'", "whp_src") == "from staging"
    assert sql("SELECT option_value FROM wp_options WHERE option_name='siteurl'", "whp_src") == "http://acme.test"
    assert sql("SELECT option_value FROM wp_options WHERE option_name='blog_public'", "whp_src") == "1"  # the live site's own search setting is kept
    assert (live / "wp-config.php").read_text() == config_before  # the live configuration is kept
    assert (live / "wp-content" / "new.txt").read_text() == "hello" and not (live / "old-only.txt").exists()
    assert stg.exists()  # the staging folder itself is not removed by publishing

    for database in ("whp_src", "whp_stg"):
        sql(f"DROP DATABASE IF EXISTS {database}")
