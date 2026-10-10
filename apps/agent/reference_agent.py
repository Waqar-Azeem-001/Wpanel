"""
A small Python agent that speaks the whole protocol against a fake machine. It exists to prove the protocol end to end in tests and to give the
Go agent (``agent/``) a behaviour to match. It changes nothing on the computer it runs on: the "machine" is a dictionary.
"""
from . import envelope


class FakeMachine:
    def __init__(self):
        self.sites, self.databases, self.backups = {}, {}, {}
        self.executed = []  # every command actually carried out, in order

    def run(self, command, p):
        self.executed.append(command)
        if command == "CREATE_WEBSITE":
            self.sites[p["domain"]] = {"account": p["account"], "php": p["php_version"], "ssl": None}
            return "created", {"root": f"/home/{p['account']}/sites/{p['domain']}"}
        if command == "DELETE_WEBSITE":
            self.sites.pop(p["domain"], None)
            return "deleted", {}
        if command == "CREATE_DATABASE":
            self.databases[p["name"]] = p["account"]
            return "created", {}
        if command == "CHANGE_PHP_VERSION":
            if p["domain"] not in self.sites:
                raise LookupError("No such website on this node.")
            self.sites[p["domain"]]["php"] = p["php_version"]
            return "changed", {}
        if command == "ISSUE_SSL":
            if p["domain"] not in self.sites:
                raise LookupError("No such website on this node.")
            self.sites[p["domain"]]["ssl"] = p["method"]
            return "issued", {}
        if command == "CREATE_MAILBOX":
            return "created", {"address": f"{p['local_part']}@{p['domain']}"}
        if command == "CREATE_BACKUP":
            backup_id = f"20261010T000000Z-{len(self.backups):08x}"
            self.backups[backup_id] = p["account"]
            return "created", {"backup_id": backup_id}
        if command == "RESTORE_BACKUP":
            if self.backups.get(p["backup_id"]) != p["account"]:
                raise LookupError("No such backup for this account.")
            return "restored", {}
        raise LookupError("Not implemented.")


class NativeMachine(FakeMachine):
    """Website commands run through the real node handlers (apps.agent.node) against a pretend Linux machine; the rest stays the simple fake."""

    WEBSITE = ("CREATE_WEBSITE", "DELETE_WEBSITE", "SUSPEND_WEBSITE", "UNSUSPEND_WEBSITE", "CHANGE_PHP_VERSION", "SET_LIMITS")
    DATABASE = ("CREATE_DATABASE", "DELETE_DATABASE", "CREATE_DB_USER", "SET_DB_PASSWORD", "DELETE_DB_USER", "GRANT_DB_ACCESS", "DB_INVENTORY", "DB_TABLES",
                "DB_DESCRIBE", "DB_BROWSE", "BACKUP_DATABASE", "RESTORE_DATABASE")

    SSL = ("ISSUE_SSL", "REMOVE_SSL", "SSL_STATUS", "SET_SSL_OPTIONS")

    def __init__(self, system=None):
        from .node.system import FakeSystem
        from .node.website import Websites

        super().__init__()
        self.system = system or FakeSystem()
        self.websites = Websites(self.system)
        from .node.databases import Databases
        from .node.mariadb import FakeMariadb

        self.engine = FakeMariadb()
        self.databases = Databases(self.system, self.engine)
        from .node.acme import FakeAcme
        from .node.ssl import Ssl

        self.acme = FakeAcme()
        self.ssl = Ssl(self.system, self.acme)

    def run(self, command, p):
        if command in self.WEBSITE:
            self.executed.append(command)
            return self.websites.run(command, p)
        if command in self.SSL:
            self.executed.append(command)
            return self.ssl.run(command, p)
        if command in self.DATABASE:
            self.executed.append(command)
            return self.databases.run(command, p)
        return super().run(command, p)


class ReferenceAgent:
    version = "0.1.0"

    def __init__(self, client, token, node_id, public_key, machine=None):
        self.client, self.token, self.node_id, self.public_key = client, token, node_id, public_key
        self.machine, self.seen = machine or FakeMachine(), envelope.ReplayGuard()

    def _post(self, url, body=None):
        return self.client.post(url, data=body or {}, content_type="application/json", HTTP_AUTHORIZATION="Agent " + self.token,
                                HTTP_X_AGENT_VERSION=self.version)

    def poll_once(self):
        """Fetch one job, check it, do it, report. Returns the job id handled, or None."""
        reply = self._post("/api/agent/v1/poll/")
        if reply.status_code != 200 or not reply.json()["job"]:
            return None
        env = reply.json()["job"]
        try:
            job = envelope.verify(env, public_key=self.public_key, node=self.node_id, seen=self.seen)
        except envelope.EnvelopeError as exc:
            self.report_refusal(env, str(exc))
            return None
        try:
            code, data = self.machine.run(job["command"], job["params"])
            result = {"ok": True, "code": code, "message": "", "data": data}
        except LookupError as exc:
            result = {"ok": False, "code": "not_found", "message": str(exc), "data": {}}
        except Exception as exc:  # a node-side handler failure (HandlerError) carries its own code
            if not hasattr(exc, "code"):
                raise
            result = {"ok": False, "code": exc.code, "message": exc.message, "data": {}}
        self._post(f"/api/agent/v1/jobs/{job['job']}/result/", result)
        return job["job"]

    def report_refusal(self, env, why):
        """A command that fails verification is reported as refused; the job id is only readable once the signature is known good, so a forged one is just dropped."""
        import base64
        import json

        try:
            job_id = json.loads(base64.b64decode(env["payload"]))["job"]
        except Exception:
            return
        if "signature" not in why:
            self._post(f"/api/agent/v1/jobs/{job_id}/result/", {"ok": False, "code": "refused", "message": why[:200], "data": {}})


def make_csr(kind="ec"):
    """What a fresh agent does first: make its own private key and a certificate request. Returns (private key PEM, CSR PEM)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, rsa

    key = ec.generate_private_key(ec.SECP256R1()) if kind == "ec" else rsa.generate_private_key(65537, 1024 if kind == "weak" else 2048)
    csr = x509.CertificateSigningRequestBuilder().subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "whatever-i-want")])).sign(key, hashes.SHA256())
    return (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
            csr.public_bytes(serialization.Encoding.PEM).decode())


INVENTORY = {"hostname": "node1.example.com", "ip_addresses": ["203.0.113.10", "2001:db8::10"], "os_name": "AlmaLinux 9.4", "cpu_cores": 8, "ram_mb": 32768,
             "disk_gb": 500, "capabilities": ["web", "php", "database"]}
