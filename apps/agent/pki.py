"""
WHPanel's own certificate authority for node identity (N2, ADR 16).

The CA key is created on first use and kept encrypted in the database. A node generates its own private key and sends only a certificate request
(CSR), so its private key never leaves it. The certificate WHPanel signs is for TLS *client* authentication only and lives for 90 days.
"""
import datetime
import secrets

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from apps.core import crypto

from .models import CertificateAuthority

CERT_DAYS = 90
CA_YEARS = 10


class CsrError(ValueError):
    pass


def _ca():
    row = CertificateAuthority.objects.first()
    if row is None:
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "WHPanel"), x509.NameAttribute(NameOID.COMMON_NAME, "WHPanel node CA")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=365 * CA_YEARS))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False, key_encipherment=False,
                                             data_encipherment=False, key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
                .sign(key, hashes.SHA256()))
        pem_key = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        row = CertificateAuthority.objects.create(certificate_pem=cert.public_bytes(serialization.Encoding.PEM).decode(), key_encrypted=crypto.encrypt(pem_key))
    return row


def ca_certificate_pem():
    return _ca().certificate_pem


def fingerprint(cert):
    return cert.fingerprint(hashes.SHA256()).hex()


def sign_csr(csr_pem, node_id):
    """Check a node's certificate request and sign it. The subject the node asked for is ignored: WHPanel names the certificate itself."""
    try:
        csr = x509.load_pem_x509_csr(csr_pem.encode() if isinstance(csr_pem, str) else csr_pem)
    except (ValueError, TypeError, AttributeError):
        raise CsrError("The certificate request is not valid.") from None
    if not csr.is_signature_valid:
        raise CsrError("The certificate request is not signed by its own key.")
    public = csr.public_key()
    if isinstance(public, rsa.RSAPublicKey):
        if public.key_size < 2048:
            raise CsrError("RSA keys must be at least 2048 bits.")
    elif isinstance(public, ec.EllipticCurvePublicKey):
        if public.curve.name not in ("secp256r1", "secp384r1"):
            raise CsrError("Use the P-256 or P-384 curve.")
    else:
        raise CsrError("Use an RSA or elliptic-curve key.")
    row = _ca()
    ca_cert = x509.load_pem_x509_certificate(row.certificate_pem.encode())
    ca_key = serialization.load_pem_private_key(crypto.decrypt(row.key_encrypted).encode(), password=None)
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "WHPanel"),
                                                              x509.NameAttribute(NameOID.COMMON_NAME, f"node-{node_id}")]))
            .issuer_name(ca_cert.subject).public_key(public).serial_number(secrets.randbits(120) + 1)
            .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=CERT_DAYS))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    return {"pem": cert.public_bytes(serialization.Encoding.PEM).decode(), "fingerprint": fingerprint(cert), "serial": format(cert.serial_number, "x"),
            "expires_at": cert.not_valid_after_utc}
