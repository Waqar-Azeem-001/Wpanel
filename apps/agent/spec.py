"""
The contract between WHPanel and the Go agent, written out as data (N1 "version compatibility").

``build_spec`` turns the command catalogue into a JSON document the Go agent embeds, so the allow-list, every parameter type, range, pattern and timeout come from ONE source. ``build_cases`` makes a large
set of valid and invalid commands with the answer Python gives for each; the Go tests run the same cases through Go's validator and must agree on every one. ``build_envelopes`` signs a handful of
envelopes (good and bad) with a fixed key for the same purpose. ``manage.py export_agent_spec`` writes the files under ``agent/spec/``; a Python test fails if they are out of date.
"""
import base64
import json
import time

from . import catalog

LOOKAHEAD = "(?=.{1,253}$)"  # Go's regular expressions have no look-ahead: the length rule is exported separately
SAMPLES = {
    "account": "abcd1234", "domain": "acme.com", "db_name": "abcd1234_shop", "db_user": "abcd1234_bob", "table": "orders", "local_part": "info", "backup_id": "20261010T000000Z-deadbeef",
    "email": "info@acme.com", "email_any": "boss@gmail.example.com", "msgid": "1791.abc.mail", "sftp_name": "dev", "trash_id": "abcdefabcdef", "mode": "644",
    "https_url": "https" + "://s3.example.net", "bucket": "whp-backups", "s3path": "node-1", "snapshot": "deadbeefdeadbeef", "secret": "Kq7vRt2mXzLp", "relpath": "a/b.txt",
    "path_list": ["a", "b/c"], "b64": "aGVsbG8=", "query": "x", "text": "hello\nworld", "archive_name": "cpmove-abcd1234.tar.gz", "sha256": "a" * 64,
}
BAD = {
    "account": ["AB", "abcd 1234", "../x", "a" * 17, "1abcdefg", "ABCD1234", "abcd1234\n"], "domain": ["a.com\n", "-a.com", "a.c", "a_b.com", "x" * 254 + ".com", "acme", ".com", "a..com"],
    "db_name": ["Shop", "a", "x" * 49, "a b", "a`b"], "db_user": ["Bob", "a", "x" * 33], "table": ["1abc", "a b", "x" * 65, "a;b"], "local_part": ["-a", "A", "", "x" * 65],
    "backup_id": ["../x", "20261010T000000Z-DEADBEEF", "20261010"], "email": ["Info@acme.com", "info@acme", "info@", "@acme.com", "a b@acme.com"], "email_any": ["x", "a@b"],
    "msgid": ["../../x", "a b", "x" * 121, ""], "sftp_name": ["Dev", "d", "x" * 16, "1dev", "dev-x"], "trash_id": ["../x", "all", "abc", "ABCDEFABCDEF"], "mode": ["8", "abc", "77777", "6"],
    "https_url": ["http" + "://x.example.net", "https" + "://", "https" + "://a b", "ftp" + "://x.net", "https" + "://X.net"], "bucket": ["UPPER", "ab", "a_b", "-ab"], "s3path": ["../x", "A", "a b", ""],
    "snapshot": ["../x", "XYZ", "abc", "g" * 8], "secret": ["short", "x" * 129, "ab\x01" * 6, "tab\there123456"],
    "relpath": ["../x", "/abs", "a\\b", "a:b", "a\x00b", "x" * 1025, "a/../b", "a" * 256], "path_list": [[], "a", ["../x"], ["a"] * 201, [1]], "b64": ["not base64!!", "a b", 5],
    "query": ["", "x" * 101, "a\x00b"], "archive_name": ["../x.tar.gz", "a.tar", "a/b.tar.gz", ".tar.gz", "x" * 101 + ".tar.gz"], "sha256": ["g" * 64, "a" * 63, "a" * 65], "text": ["a\x00b", 5, "\x7f"],
}
GENERIC_BAD = [None, 5, True, 1.5, [], {}, False]


def _pattern(kind):
    regex = catalog.PATTERNS[kind].pattern
    if regex.startswith("^" + LOOKAHEAD):
        return {"regex": "^" + regex[len("^" + LOOKAHEAD):], "max_len": 253}
    return {"regex": regex, "max_len": 0}


def build_spec():
    commands = {}
    for name, command in catalog.COMMANDS.items():
        commands[name] = {"timeout": command.timeout, "idempotent": command.idempotent, "max_attempts": command.max_attempts, "ephemeral": command.ephemeral,
                          "min_agent_version": list(command.min_agent_version),
                          "fields": {f: {"kind": r.kind, "choices": list(r.choices), "low": r.low, "high": r.high, "sensitive": r.sensitive, "required": r.required} for f, r in command.fields.items()}}
    return {"protocol": catalog.PROTOCOL_VERSION, "min_agent_version": list(catalog.MIN_AGENT_VERSION), "max_inline_bytes": catalog.MAX_INLINE_BYTES, "heal_services": list(catalog.HEAL_SERVICES),
            "patterns": {kind: _pattern(kind) for kind in catalog.PATTERNS}, "commands": commands}


def _good(rule, kind=None):
    kind = rule.kind
    if kind in ("enum",):
        return rule.choices[0]
    if kind == "enum_list":
        return [rule.choices[0]]
    if kind == "int":
        return rule.low
    if kind == "text":
        return SAMPLES["text"][:rule.high] if rule.high else ""
    if kind == "trash_id":
        return SAMPLES["trash_id"]
    return SAMPLES[kind]


def _bad_values(rule):
    out = list(GENERIC_BAD) + list(BAD.get(rule.kind, []))
    if rule.kind == "enum":
        out += ["not-a-choice", rule.choices[0] + "x"]
    elif rule.kind == "enum_list":
        out += [["nope"], [rule.choices[0], rule.choices[0]], "x", [1]]
    elif rule.kind == "int":
        out += [rule.low - 1, rule.high + 1, "5", float(rule.low), float(rule.low) + 0.5]
    elif rule.kind == "text":
        out += ["x" * (rule.high + 1)]
    return out


def build_cases():
    cases = [{"command": "NOPE", "params": {}}, {"command": None, "params": {}}, {"command": "CREATE_WEBSITE", "params": "not an object"}, {"command": "CREATE_WEBSITE", "params": None},
             {"command": "create_website", "params": {}}]
    for name, command in catalog.COMMANDS.items():
        base = {f: _good(r) for f, r in command.fields.items() if r.required}
        full = {f: _good(r) for f, r in command.fields.items()}
        cases += [{"command": name, "params": base}, {"command": name, "params": full}, {"command": name, "params": {**base, "extra": 1}}, {"command": name, "params": {**full, "": 1}}]
        for field, rule in command.fields.items():
            if rule.required:
                cases.append({"command": name, "params": {k: v for k, v in base.items() if k != field}})
            for bad in _bad_values(rule):
                cases.append({"command": name, "params": {**full, field: bad}})
            if rule.kind == "int":
                cases += [{"command": name, "params": {**full, field: rule.low}}, {"command": name, "params": {**full, field: rule.high}}]
            if rule.kind == "secret":
                cases += [{"command": name, "params": {**full, field: "x" * 12}}, {"command": name, "params": {**full, field: "x" * 128}}]
            if rule.kind in ("account", "domain"):
                cases.append({"command": name, "params": {**full, field: SAMPLES[rule.kind][0:0] + ("a" * 16 if rule.kind == "account" else "a" * 63 + ".com")}})
    for case in cases:
        try:
            catalog.validate(case["command"], case["params"])
            case["ok"] = True
        except catalog.InvalidCommand:
            case["ok"] = False
    return cases


def build_envelopes(signer, node_id=7, now=None):
    """Signed envelopes with the answer the Go verifier must give. ``signer(payload) -> envelope``; the key is fixed by the caller so the files are stable apart from the dates (which are fixed)."""
    now = int(now or 1_800_000_000)
    params = {"account": "abcd1234", "name": "shop_db"}

    def payload(**over):
        return {"v": 1, "job": 1, "correlation_id": "c", "node": node_id, "command": "CREATE_DATABASE", "params": params, "issued_at": now, "expires_at": now + 300,
                "nonce": "n" * 32, "idempotency_key": "k", **over}

    good = signer(payload())
    data = json.loads(base64.b64decode(good["payload"]))
    data["params"]["name"] = "other_db"
    forged = {"payload": base64.b64encode(json.dumps(data).encode()).decode(), "signature": good["signature"]}
    items = [("good", good, True), ("tampered payload", forged, False), ("bad signature", {**good, "signature": base64.b64encode(b"x" * 64).decode()}, False),
             ("missing signature", {"payload": good["payload"]}, False), ("empty", {}, False), ("another node", signer(payload(node=8)), False), ("wrong protocol", signer(payload(v=2)), False),
             ("short nonce", signer(payload(nonce="short")), False), ("unknown command", signer(payload(command="RUN_SHELL")), False),
             ("bad parameter", signer(payload(params={"account": "abcd1234", "name": "x;y"})), False), ("expired", signer(payload(issued_at=now - 900, expires_at=now - 600)), False),
             ("dated in the future", signer(payload(issued_at=now + 3600, expires_at=now + 4000)), False), ("extra parameter", signer(payload(params={**params, "x": 1})), False)]
    return [{"name": n, "envelope": e, "ok": ok} for n, e, ok in items]


def fixed_signer():
    """A signer with a fixed, public test key (never a production key). Returns (sign function, public key base64)."""
    import hashlib

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b"whpanel-agent-conformance-test-key").digest())
    public = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()

    def sign(payload):
        data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        return {"payload": base64.b64encode(data).decode(), "signature": base64.b64encode(key.sign(data)).decode()}

    return sign, public


_ = time
