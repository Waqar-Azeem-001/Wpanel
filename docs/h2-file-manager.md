# H2: File Manager

**Source:** next-generation roadmap, H2. **Date:** 2026-10-06. Local only.

## Rule 1 inspection
Nothing for files existed: no model, view, adapter method or screen. Only H0's files contract and allowlist (no copy or
search yet). Reused: the hosting-control gateway (permission, allowlist, live-account check, audit), the workspace menu
and header (H1), the demo provider.

## What was built
- **Sandbox** (`apps/hosting/adapters/sandbox.py`): the one safety layer for any provider that keeps files on a disk we can
  reach (demo now, a native node's agent later). Paths are relative; absolute paths, drive letters, `..`, backslashes,
  NUL and control characters are refused outright; the resolved location must stay inside the website's folder; no
  existing path component may be a symbolic link; names are plain; the main folder cannot be renamed, copied or deleted;
  a folder cannot be moved or copied into itself; collisions are refused; overwrite must be asked for.
- **Contract v1** gained `copy_path` and `search_files` (read-only, capped at 200 results and 5000 entries visited).
- **Demo provider**: now implements Files for real, one folder per account under `demo_hosting_files/` (git-ignored,
  setting `DEMO_FILES_ROOT`), created with a few sample files on first use. `demo_hosting_data --remove` deletes them.
- **Screens** (`views_files.py`, templates `files.html`, `file_edit.html`): browse with breadcrumbs and "up one folder",
  new folder, new file (opens the editor), upload (10 MB limit), rename, copy, delete (with the confirmation prompt),
  download (attachment, `nosniff`, 25 MB limit), text editor (files up to 1 MB that are valid UTF-8 text; binaries are
  refused and offered as a download), search by name. Every action goes through the gateway; changes are audited with the
  path (never the content).
- Menu: "File manager" is a real link for accounts whose provider supports files; others still show "Soon".

## Verified
36 new tests: the sandbox (traversal and symlink cases, name rules, collisions, main-folder protection, size limits, search)
and the screens end to end (create, upload, rename, copy, delete, edit, download, search, breadcrumbs, traversal attempts in
every parameter never reach a file outside the website, a stranger gets 404, over-limit uploads refused, audit holds no
content). Hosting, client-area, crawler, link-integrity, design and navigation suites pass. Looked at in a real browser at
1440 px (one layout bug found and fixed: the site's script wrapped the toolbar buttons in a form footer bar; opted out).

## Update 2026-10-06: owner feedback and the second pass
The first screen hid Download/Delete in a three-dot menu that was also cut off by the table's scroll area. Rebuilt:
- **Visible row actions** (Edit, Download, Rename, Copy, Extract for .zip, Delete) as icon buttons with tooltips and
  accessible names; Rename and Copy open a small dialog. No dropdown inside the table any more.
- **Selection bar**: tick items (or the header box) and a bar appears with an optional archive name, **Zip** and **Delete**.
- **Trash**: delete now moves to a hidden, reserved `.trash` folder; a Trash page lists items with Restore, Delete
  forever and Empty trash. Restoring never overwrites (a taken name becomes "name (restored)"; a missing folder puts the
  item back at the top). The trash is invisible to browsing, search and every other operation.
- **Zip and extract** in the sandbox: archives never overwrite and never include themselves; extraction is all or nothing
  and refuses unsafe member paths (`..`, absolute, drive letters, the trash), symbolic links, name conflicts, non-zips,
  more than 5000 entries or 200 MB unpacked (zip bombs).
- Contract v1 grew: `archive_paths`, `extract_archive`, `trash_path`, `list_trash`, `restore_trash`, `purge_trash`.
- 54 file tests now (18 added), browser-checked with no console errors.

## Deferred inside H2 (listed in the roadmap but not built yet)
Permissions editing, drag-and-drop upload, bulk copy and move, automatic emptying of old trash, folder size, and
checking at 375 / 768 px. The cPanel provider cannot offer files until H14 and a verified WHM adapter; a native node
needs the N track.
