# H10: Security Center

**Source:** next-generation roadmap, H10. Local only (demo provider).

## What was built
- **Contract:** a new `security` capability with 15 operations (scan result, scan, quarantine, list and restore quarantine, protected
  folders, SSH status, switch, add and delete key, integrity status, baseline and check), the reads allowlisted as read operations.
  New neutral records: `ScanResult`, `ScanFinding`, `QuarantineItem`, `ProtectedDir`, `SshStatus`, `SshKey`, `IntegrityResult`.
- **Demo provider** (`demo_security.py`), working on the account's real website files:
  - **Malware scan:** up to 5,000 files, 1 MB each: obfuscated code (`eval` of an encoded string), code that runs what a visitor
    sends, hidden scripts, well-known hacker-tool names, PHP files in upload folders, double extensions, hidden PHP files. Results are
    most severe first. A pattern check, not proof, and it never deletes.
  - **Quarantine:** moves a flagged file out of the site into the account's state folder, and can put it back (refused if a new file
    took its place or its folder is gone). Paths outside the site, the trash and folders are refused.
  - **Folder protection:** an existing folder, a login and a strong password (hash only); protecting again replaces the old login.
  - **SSH:** on or off; public keys only, checked properly (type, structure, ed25519 length, RSA at least 2048 bits), shown by
    SHA256 fingerprint; a private key is refused with a warning; five keys; SSH cannot be switched on without a key and goes off
    with the last key.
  - **File-integrity monitoring:** a baseline of every file's checksum and a check listing changed, new and removed files.
- **Screen:** a summary strip (SSL, scan, blocked IPs, SSH, protected folders, file changes), the scan with quarantine and put back,
  file changes, protected folders, SSH keys, and "Recent security events" built from the audit trail (scan, quarantine, protection, SSH,
  blocks, certificates, restores, FTP and mailbox password changes). SSL and blocked-IP numbers come from the SSL and IP tools.
- Audit records paths, logins, labels and fingerprints, never passwords or key bodies.
- Sample data: demo-shop has two flagged files in its uploads folder (harmless sample text) and a baseline.

## Verified
27 provider and screen tests (`test_security.py`): every scan rule, quarantine and restore with path attacks, folder protection
limits, SSH key validation (bad, private, short RSA, duplicates, limit), integrity changes, screens end to end, refusals as messages,
no secrets in the audit trail or page, strangers 404, the not-available page. Hosting and core suites pass.

## Deferred
Real malware signatures and scheduled scans (needs the real provider, H14), automatic quarantine, email alerts for findings,
two-factor controls for the hosting panel, and the 375 / 768 px check.
