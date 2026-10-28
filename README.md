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
broker. Runtime configuration and existing MES authorization are copied into the
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
