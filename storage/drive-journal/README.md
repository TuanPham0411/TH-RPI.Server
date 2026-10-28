# Drive journal foundation — not enabled in production

This is a tested storage protocol foundation, not a deployed replacement for the
three applications' database paths. Do not switch providers based on these files.

`Journal` writes attachment bytes, a JSON version, and finally an immutable commit
receipt. Each acknowledged save is verified by reading back the receipt. The local
database/cache is updated by an application adapter only after this completes.
Retry an uncertain operation with its ORIGINAL id and exact original payload.
Deletion writes a tombstone and retains previous versions. Restore validates the
receipt, JSON and attachment hashes and rejects missing or branched history.

`GoogleDriveTransport` resolves real Drive folders under an OAuth-accessible root,
uses resumable upload, checks checksums, paginates listings, and refuses duplicate
paths rather than guessing. Use MES's existing `driveToken` function in its own
process as `accessToken`; never copy credentials to Git or the browser. A root
created using the chat connector is not necessarily accessible via MES drive.file.

## Operational limits before rollout

- Exactly ONE writer process per application is required. The in-process queue is
  not a distributed lock. Prevent multiple service replicas externally.
- The initial adapter buffers attachment bytes and verifies full history; it is
  NOT suitable for production video/large archives yet. Streaming, resumable status
  recovery and a durable local submission queue must precede media rollout.
- Application routes, scheduled workers, auth changes and SQL procedures are NOT
  wired into this module. Their existing local writes continue unchanged.
- Conflict resolution, durable pending operation persistence, schema migration,
  encrypted credentials export, account ownership checks, Drive quota handling,
  consistent existing-data import, full restore drills and Google Photos selection
  are still rollout gates. No direct Google Photos URL longevity is assumed.
- A Drive failure must never be turned into a successful application save. A
  committed Drive operation whose local projection failed is pending projection,
  not a new failed save; replay must be idempotent.
- Keep existing databases/volumes and independent encrypted backups. No automatic
  cleanup or remote delete is implemented here.

Run deterministic protocol tests: `node --test store.test.mjs` (Node 22+).
These tests use an in-memory transport; live Google access is not tested.

## Intended layout

`th-data/{ai-agent,portfolio,mes-ticket,server}/records/<id>/versions/<operation>.json`

`th-data/<application>/records/<id>/commits/<operation>.json`

`th-data/<application>/files/<operation>/<file-id>`

Human file names and MIME types are retained in JSON. Drive IDs and checksums are
part of the eventual local projection. Session tokens and plaintext passwords do
not belong in ordinary record JSON. Store server secrets only in encrypted backup.
