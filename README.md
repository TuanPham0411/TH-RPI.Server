# TH-Server storage platform

Canonical home for shared Drive storage, capture protocol, recovery and deployment.
Applications vendor the small `storage/client.mjs`/operator bridge at build time;
they do not own Drive credentials or implement a second storage protocol.

## Write ordering

1. SQL runs inside an uncommitted PostgreSQL transaction. Row triggers record exact
   before/after JSON text, including changes made by SQL functions and background jobs.
2. The broker streams referenced media to `th-data/<application>/files/`, verifies
   checksums, writes versioned JSON events, and writes a final commit receipt.
3. The application acknowledges that receipt in its transaction and commits the
   local PostgreSQL projection. Failed Drive writes roll back the transaction.

Each database uses an advisory transaction lock and an expected Drive parent.
If Drive committed but the local commit failed, the mismatch blocks subsequent
transactions. Recover/reconcile the projection rather than guessing or losing the
Drive operation. A Drive outage intentionally prevents successful saves.

The broker has no public TCP listener and no Docker socket. Each application has
its own bearer token over a Unix socket. Source media volumes are read-only to the
broker. Runtime configuration and existing TICKET authorization are copied into the
private `~/th-platform/config/` directory by the management deployment job.

## Recovery and sensitive data

Ordinary before/after data is stored as raw JSON text to preserve large integers
and PostgreSQL types exactly. Authentication/account rows and secret-containing
rows are AES-256-GCM encrypted. The recovery wrapping secret is the original Google
OAuth client secret; preserve it separately in a password manager before rotating
the OAuth client. Do not publish platform config, tokens or credentials to Git.

The operator can rebuild a dedicated empty database from the latest baseline and
following commits, verify foreign keys, restore sequence positions, and download
media by SHA-256. It refuses a nonempty target. Existing production databases and
volumes are never erased by deployment. Keep independent off-device backups too.

## Rollout

The platform workflow builds/tests on GitHub-hosted runners, publishes a pinned
AMD64/ARM64 image, then deploys only the broker via `th-server`. This step does NOT
switch application providers. Application migration requires a stopped writer,
local pg_dump, capture installation, acknowledged Drive baseline, and new images
using the storage override. Use each application's existing project name/volumes.

Google Photos requires a separate Picker authorization and is Portfolio-only.
Drive authorization does not grant Google Photos access. Photos integration must
not treat expiring Google image URLs as a durable backup.

## Tests

`npm ci && npm test`; provide `TEST_DATABASE_URL` ending in `_test` to run the
PostgreSQL capture/rollback/restore integration test. Tests use isolated databases
and an in-memory Drive transport. Live Drive writes are verified during rollout;
unit tests alone do not establish successful production deployment.

AI owner sessions are managed centrally over the private storage socket. Google
sign-in uses PKCE and a browser-bound state, verifies the configured Drive owner's
email, and persists only hashed session identifiers in encrypted server ledger
records. Revocation is durable before logout is acknowledged. Configure the OAuth
redirect `https://ai.th.io.vn/api/oauth/callback`; no Drive or Google token reaches
the browser. AI prompts remain local until the owner explicitly saves a draft.

`deploy/recovery-drill.py` verifies recovery nightly using the deployed release.
It reads schema definitions and migration catalogs from the encrypted server
snapshot on Drive into a uniquely named, disposable database, then restores all
business rows and referenced files from Drive. Downloaded files go to the temporary operator container, never over the
live media volume. Foreign keys and file checksums must pass; when the live Drive
head remains unchanged, all rows must match exactly. The disposable database is
removed afterward. Results are recorded under `~/th-platform/state/`.

The off-device recovery kit must include `~/th-platform/config/recovery-key.txt`
(or the original OAuth client secret), the repository releases recorded in each
baseline, and access to the Drive account. Store that key in your password manager;
keeping the only key on the Pi would make encrypted account/configuration rows
unrecoverable after device loss. This is application-data recovery, not a disk
image of the operating system.

Server configuration is checked every 30 minutes; changed configuration, database
schema SQL and migration catalogs are stored inside encrypted JSON on Drive. The
nightly recovery workflow takes a fresh snapshot first and uses those remote
definitions, rather than relying on a surviving production schema. This schedule
protects host configuration separately from synchronous application saves.


## HDMI console

TH-Server includes a local operations console on tty1: resources, application health, network/Tailscale status, GitHub runners and USB partitions. Network and USB administration require the thadmin sudo password at the physical keyboard. See [console instructions](console/README.md). Immutable ARM64/AMD64 Docker distribution: `ghcr.io/tuanpham0411/th-rpi-console:<commit>`; the display itself runs natively through systemd to access the Pi terminal.


## n8n operations

`n8n/` owns installation, fallback swap, scheduled encrypted Drive backups and isolated restore verification for the n8n workspace at `ai.th.io.vn`. Application Compose configuration and the workflow live in `TH-AI.Agent/n8n/`.

n8n uses the dedicated `th_n8n` PostgreSQL database. Unlike the other application projections, n8n saves locally and is backed up every 30 minutes; it does not use Drive-first transaction acknowledgement. Backups include a consistent database snapshot, workflow JSON, configuration and n8n volume, encrypted in `th-data/ai-agent/n8n/backups/`. A verified upload manifest is written last. Initial deployment downloads and restores a snapshot into a disposable database before public cutover.

See [n8n operator source](n8n/) and [workspace operations](https://github.com/TuanPham0411/TH-AI.Agent/blob/main/n8n/README.md). Keep the recovery wrapping key independently of the Pi. No provider API credentials are committed to either repository.
