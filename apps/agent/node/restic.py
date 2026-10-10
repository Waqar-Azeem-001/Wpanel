"""
The backup repository (N8, ADR 10). ``RealRestic`` drives the ``restic`` program against an S3-compatible bucket; it has **never been run**. ``FakeRestic`` keeps a repository in a
local folder with REAL encryption (AES-256-GCM, key derived from the repository password with scrypt), a manifest of every file's SHA-256, tags, and integrity checks, so all of
the logic around backups (encryption at rest, wrong-password refusal, tamper detection, retention, test restores) is exercised for real even though the storage is a folder.

Both obey the same rules: a snapshot belongs to the tags it was made with; a repository password never appears on a command line (restic reads it from the environment).
"""
import hashlib
import io
import json
import os
import secrets
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .system import SystemError_

MAGIC = b"WHPB1"


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_of(root):
    """{relative path: sha256} of every file under ``root``."""
    root = Path(root)
    return {p.relative_to(root).as_posix(): sha256_file(p) for p in sorted(root.rglob("*")) if p.is_file()}


class RepositoryError(SystemError_):
    pass


class FakeRestic:
    def __init__(self, repo_dir, password):
        self.repo, self.password = Path(repo_dir), password
        self._key = None

    # --- keys ---------------------------------------------------------------------------------------------------------------------------
    def _salt(self):
        path = self.repo / "config"
        if not path.exists():
            raise RepositoryError("The backup repository has not been created.")
        return bytes.fromhex(json.loads(path.read_text())["salt"])

    def _aes(self):
        if self._key is None:
            self._key = Scrypt(salt=self._salt(), length=32, n=2 ** 14, r=8, p=1).derive(self.password.encode())
        return AESGCM(self._key)

    def init(self):
        """Create the repository if it is not there; an existing one must accept this password."""
        if (self.repo / "config").exists():
            self._open_check()
            return False
        (self.repo / "snapshots").mkdir(parents=True, exist_ok=True)
        salt = secrets.token_bytes(16)
        (self.repo / "config").write_text(json.dumps({"version": 1, "salt": salt.hex()}))
        (self.repo / "check").write_bytes(self._seal(b"ok", salt))
        return True

    def _seal(self, data, salt=None):
        key = Scrypt(salt=salt or self._salt(), length=32, n=2 ** 14, r=8, p=1).derive(self.password.encode())
        nonce = secrets.token_bytes(12)
        return MAGIC + nonce + AESGCM(key).encrypt(nonce, data, None)

    def _open(self, blob):
        if not blob.startswith(MAGIC):
            raise RepositoryError("That is not a backup file.")
        try:
            return self._aes().decrypt(blob[5:17], blob[17:], None)
        except InvalidTag:
            raise RepositoryError("The backup could not be decrypted: wrong key, or it has been altered.") from None

    def _open_check(self):
        self._open((self.repo / "check").read_bytes())

    # --- snapshots ----------------------------------------------------------------------------------------------------------------------
    def backup(self, folder, tags):
        """Encrypt a copy of ``folder`` as a new snapshot. Returns (snapshot id, bytes stored)."""
        manifest = manifest_of(folder)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            archive.add(folder, arcname="data", recursive=True)
        snapshot_id = secrets.token_hex(8)
        meta = {"id": snapshot_id, "tags": list(tags), "time": time.time(), "manifest": manifest}
        blob = self._seal(json.dumps(meta).encode() + b"\0" + buffer.getvalue())
        (self.repo / "snapshots" / f"{snapshot_id}.enc").write_bytes(blob)
        return snapshot_id, len(blob)

    def _read(self, snapshot_id):
        path = self.repo / "snapshots" / f"{snapshot_id}.enc"
        if not path.exists():
            raise RepositoryError("That backup does not exist.")
        meta, _, payload = self._open(path.read_bytes()).partition(b"\0")
        return json.loads(meta), payload

    def meta(self, snapshot_id):
        return self._read(snapshot_id)[0]

    def snapshots(self, tag=None):
        found = []
        for path in sorted((self.repo / "snapshots").glob("*.enc")):
            meta = self._read(path.stem)[0]
            if tag is None or tag in meta["tags"]:
                found.append({"id": meta["id"], "tags": meta["tags"], "time": meta["time"], "size": path.stat().st_size})
        return sorted(found, key=lambda s: s["time"], reverse=True)

    def restore(self, snapshot_id, target):
        meta, payload = self._read(snapshot_id)
        target = Path(target)
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r") as archive:
            for member in archive.getmembers():  # never write outside the target, never follow links
                dest = (target / member.name).resolve()
                if member.issym() or member.islnk() or (target.resolve() != dest and target.resolve() not in dest.parents):
                    raise RepositoryError("The backup contains an unsafe path.")
            archive.extractall(target, filter="data") if hasattr(tarfile, "data_filter") else archive.extractall(target)  # the "data" filter also strips unsafe modes and special files
        return target / "data"

    def check(self, snapshot_id):
        """Decrypt, unpack and compare every file with the manifest made at backup time. Returns (ok, detail)."""
        try:
            meta, payload = self._read(snapshot_id)
        except RepositoryError as exc:
            return False, str(exc)
        scratch = self.repo / f".check-{secrets.token_hex(4)}"
        try:
            data = self.restore(snapshot_id, scratch)
            found = manifest_of(data)
        except Exception as exc:  # noqa: BLE001
            return False, f"The backup could not be unpacked: {exc}"
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        if found != meta["manifest"]:
            return False, "The files in the backup do not match what was backed up."
        return True, f"{len(found)} files checked"

    def forget(self, snapshot_id):
        path = self.repo / "snapshots" / f"{snapshot_id}.enc"
        if not path.exists():
            return False
        path.unlink()
        return True


class RealRestic:
    """UNTESTED. Uses the ``restic`` program; its settings (repository, keys) come from the environment file ``/etc/whp/restic.env``, never from the command line."""

    def __init__(self, env):
        self.env = {**os.environ, **env}

    def _run(self, args, timeout=3600):
        done = subprocess.run(["restic", *args], shell=False, capture_output=True, text=True, timeout=timeout, check=False, env=self.env)
        if done.returncode != 0:
            raise RepositoryError((done.stderr or done.stdout).strip().splitlines()[-1][:300] if (done.stderr or done.stdout) else "restic failed")
        return done.stdout

    def init(self):
        try:
            self._run(["cat", "config"], 60)
            return False
        except RepositoryError:
            self._run(["init"], 300)
            return True

    def backup(self, folder, tags):
        args = ["backup", "--json", str(folder)]
        for tag in tags:
            args += ["--tag", tag]
        out = self._run(args)
        summary = next((json.loads(line) for line in out.splitlines() if '"summary"' in line), {})
        return summary.get("snapshot_id", "")[:16], int(summary.get("data_added", 0))

    def snapshots(self, tag=None):
        args = ["snapshots", "--json"] + (["--tag", tag] if tag else [])
        return [{"id": s["short_id"], "tags": s.get("tags", []), "time": 0, "size": 0} for s in json.loads(self._run(args))]

    def meta(self, snapshot_id):
        return next(s for s in self.snapshots() if s["id"].startswith(snapshot_id[:8]))

    def restore(self, snapshot_id, target):
        self._run(["restore", snapshot_id, "--target", str(target)])
        return Path(target) / "data"

    def check(self, snapshot_id):
        try:
            self._run(["check", "--read-data-subset=1/10"], 3600)
            return True, "checked"
        except RepositoryError as exc:
            return False, str(exc)

    def forget(self, snapshot_id):
        self._run(["forget", "--prune", snapshot_id])
        return True
