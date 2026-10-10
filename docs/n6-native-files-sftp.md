
# N6: Native files and SFTP

Goal: the customer's File Manager (H2) and FTP screen (H8) work unchanged on a native node, and SFTP follows the N0 decision (ADR 09: SFTP in an OpenSSH chroot, no plain FTP).

## What is built and what is not

| Part | State |
|---|---|
| `NativeFiles` / `NativeFtp`: the same `FilesCapability` / `FtpCapability` as cPanel and demo | Built, tested through `control.perform` and the real File Manager and FTP pages |
| Node file handlers on **real folders** (list, read, write, mkdir, rename, copy, chmod, delete, zip, unzip, trash, restore, purge, search) with the sandbox: relative paths only, no `..`, **no symbolic links followed**, root cannot be removed, unsafe permissions refused | Built, tested on real directories in a temporary folder (so the path and link rules are exercised for real) |
| Real `chmod` on Linux | Written; on Windows (where tests run) permissions use the side file, so **real chmod was not exercised** |
| **Running file commands as the account's own Linux user** (`run_as_account` + `file_worker`) | **Written, never run** (Linux only). Without it the node would do file operations as root, which must not be used in production |
| SFTP logins: system user sharing the account's uid, in group `whp-sftp`, chrooted by one `Match Group` block, `internal-sftp` only, bind-mounted folder through a systemd mount unit, password via `chpasswd` on standard input | Built, tested against `FakeSystem`. **Never run on a real sshd**; bind mounts and chroot behaviour untried |
| Login for the main account itself | **Not built.** Customers use the extra SFTP logins from the FTP screen |
| Plain FTP | **Not offered** (the screen shows SFTP, port 22) |
| SSH keys for SFTP logins | Not built (passwords only) |
| Go agent | Written in Go (`agent/`) for every command of this stage, checked against the Python reference by golden scenarios and against live Django end to end on a pretend machine. **The real-machine paths have never run on a real node** (see agent/README.md) |

## Rules worth knowing

* The file area of an account is `/home/<account>/sites` (the customer sees `acme.com/public_html/...`). The account's home itself is owned by root, as OpenSSH requires for chroots; the web, log and temp
  folders below it belong to the account.
* **File content travels inside commands**, so it is limited to **3 MB per file** here (editor, uploads and downloads). Bigger files get the message "Use SFTP for big files". Content is removed from
  WHPanel's job record when the command finishes (`***`), listings/contents/trash lists are wiped as soon as the screen has read them, and none of it reaches the audit trail.
* Paths and file names are the one place the agent accepts free text (`relpath`, `query`, base64 `content`), and only for `FILE_*` and `SFTP_*` commands (a test enforces this). They name things inside the sandbox;
  they never reach a shell.
* A full disk or quota gives the usual "Your disk space is full" message.
* SFTP logins are named `<account>_<name>` (2 to 15 lowercase letters/digits), the folder must exist, the count follows the plan's FTP-accounts limit (`resource_limits.ftp_accounts`, enforced on the node),
  passwords are 12 to 128 characters. Suspending an account locks its logins (sessions already open end on their own); reactivating unlocks; terminating removes logins, mounts and chroot folders.
* sshd is reloaded only after `sshd -t` passes, and only when the managed config changed.

## Prerequisites on a node (for the installer, N10)

Group `whp-sftp`; `Include /etc/ssh/sshd_config.d/*.conf` in sshd; `/srv/whp/sftp`; and for file work the Python worker able to import `apps.agent.node` (or its Go replacement).

## Tests

`apps/agent/tests/test_native_files.py`: every file operation on real folders, binary content, path-escape attempts (7), link refusal, protected root and unsafe modes, archive/extract/trash/search, the size limit, disk-full, no content kept,
other users refused, the real File Manager and FTP pages, the worker's behaviour for a missing account, SFTP create/list/password/delete, one sshd reload, name/folder/count rules, other account's login, rollback after a failure,
suspend/unsuspend/terminate, and the typed/sandboxed command rules.
