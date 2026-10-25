"""The server-side deploy scripts: they parse, and the restricted entry points refuse anything but what the keys are for. (Only the refusals run here; the real work needs the server.)"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[2] / "deploy" / "server"
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="needs bash")


def run(args, command):
    env = {**os.environ, "SSH_ORIGINAL_COMMAND": command}
    return subprocess.run(args, input=b"", capture_output=True, env=env, timeout=30)


@needs_bash
@pytest.mark.parametrize("name", ["whpanel-deploy", "whpanel-deploy-entry", "whpanel-notify"])
def test_the_shell_scripts_parse(name):
    assert subprocess.run([BASH, "-n", str(SERVER / name)], capture_output=True).returncode == 0


@needs_bash
@pytest.mark.parametrize("command", ["", "ls /", "bash", "deploy; rm -rf /", "deploy abc!", "deploy xyz123", "deploy abcdef1 extra", "notify --tests ok"])
def test_the_deploy_key_cannot_run_anything_but_deploy(command):
    result = run([BASH, str(SERVER / "whpanel-deploy-entry")], command)
    assert result.returncode == 2 and (b"may only run" in result.stderr or b"hexadecimal" in result.stderr), result.stderr


@pytest.mark.parametrize("command,why", [
    ("", "may only run"), ("deploy", "may only run"), ("notify --tests", "needs a value"), ("notify --shell x", "not allowed"),
    ("notify --message 'a; rm -rf /'", "not allowed"), ("notify --message '$(id)'", "not allowed"), ("notify --sha `id`", "not allowed"),
])
def test_the_notify_key_can_only_send_plain_notices(command, why):
    result = run([sys.executable, str(SERVER / "whpanel-notify-entry")], command)
    assert result.returncode == 2 and why.encode() in result.stderr, result.stderr


@needs_bash
def test_the_deploy_script_refuses_a_bad_option_and_a_bad_commit():
    assert run([BASH, str(SERVER / "whpanel-deploy"), "--nonsense"], "").returncode == 2
    assert run([BASH, str(SERVER / "whpanel-deploy"), "--sha", "not-a-hash!"], "").returncode == 2


@pytest.mark.parametrize("name", ["whpanel-deploy", "whpanel-deploy-entry", "whpanel-notify", "whpanel-notify-entry"])
def test_the_server_scripts_have_unix_line_endings_and_start_with_their_interpreter(name):
    raw = (SERVER / name).read_bytes()
    assert b"\r" not in raw, "a carriage return in a script makes the server say: No such file or directory"
    assert raw.startswith((b"#!/bin/bash\n", b"#!/usr/bin/env python3\n"))
