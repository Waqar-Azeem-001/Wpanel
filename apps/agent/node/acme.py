"""
Getting certificates (N5, ADR 08). ``FakeAcme`` makes real (self-signed) certificates with the dates and names asked for, so everything that reads and installs
them is tested for real. ``RealAcme`` runs ``certbot`` for Let's Encrypt over HTTP-01 and is **untested against the real Let's Encrypt**.
"""
import datetime
import subprocess

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .system import SystemError_


class AcmeError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def make_certificate(names, days, issuer="Fake ACME CA", issued=None):
    """(fullchain PEM, private key PEM): a self-signed certificate for ``names`` valid for ``days`` days from ``issued``."""
    key = ec.generate_private_key(ec.SECP256R1())
    start = issued or datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer)])).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(start).not_valid_after(start + datetime.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in names]), critical=False).sign(key, hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM).decode(),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode())


class FakeAcme:
    def __init__(self, days=90):
        self.days, self.calls, self.fail_with = days, [], None
        self.issued_at = None  # tests can set a past date to make a certificate old

    def obtain(self, names, webroot, method):
        self.calls.append((list(names), webroot, method))
        if self.fail_with:
            raise AcmeError(*self.fail_with)
        return make_certificate(names, self.days, issued=self.issued_at)


class RealAcme:
    """UNTESTED. Uses certbot's webroot mode, then reads the files it wrote."""

    def __init__(self, email):
        self.email = email

    def obtain(self, names, webroot, method):
        if method != "http-01":
            raise AcmeError("not_implemented", "Only HTTP-01 validation is available on this node.")
        argv = ["certbot", "certonly", "--webroot", "-w", webroot, "--non-interactive", "--agree-tos", "--email", self.email, "--cert-name", names[0],
                "--keep-until-expiring"]
        for name in names:
            argv += ["-d", name]
        done = subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=280, check=False)
        if done.returncode != 0:
            tail = (done.stderr or done.stdout).strip().splitlines()[-1:] or ["certbot failed"]
            raise AcmeError("acme_failed", tail[0][:300])
        try:
            with open(f"/etc/letsencrypt/live/{names[0]}/fullchain.pem", encoding="utf-8") as chain, open(f"/etc/letsencrypt/live/{names[0]}/privkey.pem", encoding="utf-8") as key:
                return chain.read(), key.read()
        except OSError as exc:
            raise SystemError_("certbot finished but its files could not be read") from exc
