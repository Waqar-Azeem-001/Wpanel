# N2: Native node enrolment

How a fresh Linux server becomes a trusted WHPanel node (ADR 16): **one-time token, then its own certificate**.

## What is built and what is not

| Part | State |
|---|---|
| Internal CA, certificate signing, one-time tokens, inventory, heartbeat, renewal, revocation (`pki.py`, `enrollment.py`) | Built, tested (73 tests in `apps/agent`) |
| Staff screen `/staff/nodes/` (add node, show the command once, status, capacity, revoke) | Built, tested |
| Server type **Native node** (`ServerKind.NATIVE`) | Added. It uses the manual adapter, so nothing is provisioned on it until N3 |
| Enrol / heartbeat / renew in the agent | Implemented in Go (`enroll`, heartbeat, certificate renewal); tested against live Django with a pretend machine only |
| Mutual TLS on the wire | **Not exercised.** WHPanel checks the certificate fingerprint a reverse proxy passes (`X-Client-Cert-Fingerprint`) when `AGENT_REQUIRE_CLIENT_CERT=True`. The proxy configuration (nginx/Apache `ssl_verify_client` with our CA) is deployment work and has not been tried against a real proxy |
| Certificate revocation list | Not built: revoking a node stops its token and fingerprint being accepted by WHPanel; a proxy that only checks the CA would still let the TLS handshake finish |
| Rate limiting of failed enrolments | Not built (tokens are 256-bit random, so guessing is not realistic, but add limits at the proxy) |

## Flow

1. Staff: **Native nodes → Add a native node** (name, hostname, region). WHPanel creates a server of type Native node and a token like `whe_…`, valid 24 hours,
   shown **once** with the command `wh-agent enroll --panel <url> --token <token>`. Only a hash is stored. A new invitation replaces the old one.
2. On the fresh server the agent makes its own private key and a certificate request (the key never leaves the node), collects its details, and calls
   `POST /api/agent/v1/enroll/` with `{token, csr, node: {...}, agent_version}`.
3. WHPanel validates everything *before* spending the token: details, key strength (RSA ≥ 2048 or P-256/P-384), CSR self-signature. A bad request leaves the
   token usable. A good one consumes it in the same transaction that creates the identity, so it works exactly once. Failed attempts are audited.
4. WHPanel signs a **client-auth-only** certificate (90 days), named `node-<id>` by WHPanel (the name the agent asked for is ignored), and returns the
   certificate, the CA certificate, the command-signing public key and a long random API token (hash stored).
5. The agent polls for work and sends heartbeats. Each call carries the API token; with `AGENT_REQUIRE_CLIENT_CERT=True` the fingerprint of the verified client
   certificate must also match the one issued to that node.

### What the node reports (validated, never trusted blindly)

hostname, IP addresses (parsed as real IPs), OS name, CPU cores, RAM, disk, capabilities (`web php database ssl dns mail backup metrics` only), agent version.
Region comes from staff. Capacity (allowed websites) is set by staff. Heartbeats carry CPU %, memory, disk, load, site count and per-service state
(`up`/`degraded`/`down`); out-of-range values or unknown states are refused and unknown keys are dropped.

**Health** shown to staff: `healthy`, `degraded`, `down`, `offline` (no contact for 3 minutes), `pending`, `revoked`.

### Renewal and revocation

`POST /api/agent/v1/renew/` with a new CSR swaps the certificate; the old fingerprint stops matching at once. **Revoke** (staff) clears the token, marks the node
revoked, cancels its queued commands and deletes unused invitations; the server can then be re-invited and enrols as a new identity.

## Endpoints

| Call | Auth |
|---|---|
| `POST /api/agent/v1/enroll/` | the one-time token in the body |
| `POST /api/agent/v1/heartbeat/`, `renew/`, `poll/`, `jobs/<id>/result/` | `Authorization: Agent <token>` (+ fingerprint header when required) |

## Settings

* `AGENT_SIGNING_KEY` (N1) – production must set it.
* `AGENT_REQUIRE_CLIENT_CERT` – default off; turn on only when the proxy verifies client certificates against the CA and **strips any `X-Client-Cert-Fingerprint` sent by clients**.
* The CA is created on first use and its key is stored encrypted with `CREDENTIALS_ENCRYPTION_KEY`; back that key up, because losing it means re-enrolling every node.
