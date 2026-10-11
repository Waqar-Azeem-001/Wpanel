"""Builds a small cPanel ``pkgacct`` archive for the tests (cpmove-<user>.tar.gz), laid out the way the format is documented. NOT taken from a real server."""
import gzip
import io
import tarfile
from pathlib import Path

DUMP = """-- MySQL dump
CREATE TABLE `orders` (
  `id` int(11) NOT NULL,
  `customer` varchar(60) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB;
INSERT INTO `orders` VALUES (1,'Ann'),(2,'O\\'Neil'),(3,NULL);
"""
GRANTS = """GRANT USAGE ON *.* TO '{user}_shop'@'localhost' IDENTIFIED BY PASSWORD '*2470C0C06DEE42FD1618BB99005ADCA2EC9D1E19';
GRANT ALL PRIVILEGES ON `{user}\\_shop`.* TO '{user}_shop'@'localhost';
"""


def build(path, user="acmeuser", domain="acme.com", php="ea-php74", addon=False, cron=True, mail=True, evil=False, files=None):
    members = {
        f"cp/{user}": f"DNS={domain}\nPLAN=Basic\nCONTACTEMAIL=owner@gmail.example.com\n",
        "userdata/main": f"main_domain: {domain}\naddon_domains:\n" + ("  - blog.org\n" if addon else "") + "sub_domains:\nparked_domains:\n",
        f"userdata/{domain}": f"documentroot: /home/{user}/public_html\nphpversion: {php}\n",
        "homedir/public_html/index.php": "<?php echo 'hi';",
        "homedir/public_html/wp-content/uploads/a.jpg": "JPEGDATA",
        "homedir/private/config.ini": "secret=1",
        "homedir/.cpanel/caches/x": "cache",
        "homedir/logs/access.log": "log",
        f"mysql/{user}_shop.sql": DUMP,
        "mysql.sql": GRANTS.format(user=user),
        f"dnszones/{domain}.db": f"$TTL 14400\n{domain}. IN SOA ns1.{domain}. root.{domain}. (1 3600 600 86400 3600)\n",
    }
    if cron:
        members[f"cron/{user}"] = "# comment\n*/5 * * * * php /home/x/cron.php\n"
    if mail:
        members.update({
            f"homedir/mail/{domain}/info/cur/1.msg": "From: a@b.example.com\nSubject: Hello\n\nbody",
            f"homedir/mail/{domain}/info/new/2.msg": "From: c@d.example.com\nSubject: Second\n\nbody",
            f"homedir/etc/{domain}/shadow": "info:$6$salt$hashhashhash:19000::::::\nbroken:*:19000::::::\n",
            f"va/{domain}": f"sales@{domain}: info@{domain}\n",
        })
    members.update(files or {})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name, text in members.items():
            data = text.encode() if isinstance(text, str) else text
            info = tarfile.TarInfo(f"cpmove-{user}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo(f"cpmove-{user}/homedir/public_html/passwd-link")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tar.addfile(link)
        if evil:
            bad = tarfile.TarInfo(f"cpmove-{user}/../../escape.txt")
            bad.size = 1
            tar.addfile(bad, io.BytesIO(b"x"))
    zipped = io.BytesIO()
    with gzip.GzipFile(fileobj=zipped, mode="wb", mtime=0, filename="") as gz:  # fixed header: the same bytes (and checksum) every time, so the Go agent can be compared against it
        gz.write(raw.getvalue())
    path.write_bytes(zipped.getvalue())
    return path
