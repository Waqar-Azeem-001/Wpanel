# ADR 09: SFTP and FTP

**Status:** Proposed  
**Roadmap:** N0 (area 9 of 20)

## Context
Customers expect FTP; SFTP is safer and always available through SSH software.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| SFTP only, OpenSSH internal-sftp in a chroot per tenant, key and password login, no shell | 5 | 5 | 4 | 4 | 3 | 5 | 4 |
| Pure-FTPd or vsftpd, explicit TLS, virtual users | 3 | 4 | 3 | 4 | 5 | 3 | 3 |
| Both, FTP optional per plan | 4 | 4 | 3 | 4 | 5 | 3 | 3 |

- **SFTP only**: One encrypted protocol, one port, nothing new to maintain; some old clients only speak FTP.
- **Pure-FTPd or vsftpd**: What cPanel offers; passive-port ranges and TLS quirks cause support work.
- **Both**: Most compatible; double the surface.

## Recommendation
SFTP in an OpenSSH chroot for every tenant from the start (extra FTP logins map to chrooted SFTP users limited to a folder, as H8 already models). Add TLS-only FTP later only if customer demand proves it.

## Consequences
Migrating customers told to use FTP need a short note. The FTP screen already says to prefer SFTP.

## Open questions
Whether FTP is needed at launch.
