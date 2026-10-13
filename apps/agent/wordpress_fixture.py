"""
Test support for the WordPress installer: a small, deterministic stand-in for wordpress.org's download and for WP-CLI, plus a token source that counts instead of being random.
Nothing here is WordPress itself; it has the same shape (a ``wordpress/`` folder with ``wp-includes/version.php`` and so on) so the installer's real code path can be exercised.
Imported only by tests, the golden scenarios and the pretend machine.
"""
import gzip
import hashlib
import io
import tarfile

from .node import wordpress as wp

VERSION = "6.7.1"
FILES = {
    "index.php": "<?php\n/* Front to the blog */\nrequire __DIR__ . '/wp-blog-header.php';\n",
    "wp-blog-header.php": "<?php\n",
    "wp-config-sample.php": "<?php\n// sample\n",
    "wp-admin/install.php": "<?php\n",
    "wp-includes/version.php": f"<?php\n$wp_version = '{VERSION}';\n",
    "wp-content/index.php": "<?php\n// Silence is golden.\n",
}
CLI_BYTES = b"#!/usr/bin/env php\n<?php // pretend wp-cli\n"


def archive_bytes(extra=None, evil=False):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name, text in {**FILES, **(extra or {})}.items():
            data = text.encode()
            info = tarfile.TarInfo(f"wordpress/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if evil:
            bad = tarfile.TarInfo("wordpress/../../escape.php")
            bad.size = 1
            tar.addfile(bad, io.BytesIO(b"x"))
    zipped = io.BytesIO()
    with gzip.GzipFile(fileobj=zipped, mode="wb", mtime=0, filename="") as handle:
        handle.write(raw.getvalue())
    return zipped.getvalue()


def make_fetch(archive=None, tamper=False, down=False):
    """A ``fetch(url)`` that serves the fixed addresses; ``tamper`` makes the published checksum wrong, ``down`` makes every download fail."""
    archive = archive if archive is not None else archive_bytes()
    table = {wp.WP_ARCHIVE: archive, wp.WP_CHECKSUM: (("0" * 40) if tamper else hashlib.sha1(archive).hexdigest()).encode() + b"  latest.tar.gz\n",
             wp.CLI_PHAR: CLI_BYTES, wp.CLI_CHECKSUM: hashlib.sha512(CLI_BYTES).hexdigest().encode() + b"  wp-cli.phar\n"}

    def fetch(url):
        if down:
            raise OSError("network is unreachable")
        if url not in table:
            raise OSError(f"unexpected address {url}")
        return table[url]

    fetch.requested = table
    return fetch


class CountingTokens:
    """The same strings every run: tok00001..., padded to the length asked for."""

    def __init__(self):
        self.n = 0

    def __call__(self, length):
        self.n += 1
        return f"tok{self.n:05d}".ljust(length, "x")[:length]
