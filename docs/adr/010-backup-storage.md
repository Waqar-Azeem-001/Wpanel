# ADR 10: Backup storage and retention

**Status:** Accepted 2026-10-10 as proposed: restic to S3-compatible storage; the vendor is chosen when N8 starts (it does not block N1 to N7)  
**Roadmap:** N0 (area 10 of 20)

## Context
N8: encrypted, remote, with retention, integrity checks, restore tests and access control. H9 already models real, test-restored backups.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| restic to S3-compatible object storage (Contabo Object Storage, Backblaze B2 or Wasabi), client-side encryption, deduplication | 5 | 4 | 4 | 4 | 4 | 5 | 5 |
| BorgBackup to a remote server over SSH | 5 | 4 | 3 | 4 | 3 | 4 | 4 |
| Plain rsync/tar to a remote disk | 2 | 3 | 4 | 3 | 3 | 3 | 3 |
| Filesystem snapshots (LVM, ZFS, Btrfs) shipped off the node | 4 | 4 | 2 | 5 | 3 | 3 | 4 |

- **restic to S3-compatible object storage**: One static binary, encrypted and deduplicated, checks (`restic check`), mounts for single-file restore; cheap storage.
- **BorgBackup to a remote server over SSH**: Excellent too; needs a backup server you run.
- **Plain rsync/tar to a remote disk**: Simple, no dedup or encryption.
- **Filesystem snapshots**: Fast restores, needs a snapshot-capable filesystem (conflicts with XFS quotas, ADR 04).

## Recommendation
restic to S3-compatible object storage in a different provider or region from the node, with a repository per node, keys held by WHPanel's secret store, a retention policy per plan, a nightly integrity check and a monthly automatic test restore of a sample tenant (as H9's 'tested restore').

## Consequences
Object storage is a recurring cost per GB. A tenant's backup can be restored without touching others. Backup access is separate from node access, so a node compromise cannot delete its own backups (use object lock or an append-only credential).

## Open questions
Which object storage vendor, and the retention per plan.
