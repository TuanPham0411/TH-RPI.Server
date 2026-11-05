# GoT deployment on th-server-1

Run **Deploy GoT with management runner** in inspect-only mode first. Enable
`deploy` with a tested immutable TH-GoT SHA to pull and start the ARM64 image.
The workflow reuses `/srv/th-got/app/.env` and `th-got_data`, binds port 5082
to loopback and enables only the retained `got.th.io.vn` site after local smoke
tests pass. Failed public verification rolls back that site symlink.

Web checks cover the landing/play pages, 18 games, 10 topics, sessions API and
Vietnamese/English speech. Portfolio, ESP32 and host services are checked too.
Private diagnostics and deployment metadata are in
`~/th-platform/state/got-deployment/`.

Web health does not establish Drive readiness. When the volume has no stored
Drive grant, sign in as the configured administrator at
`https://got.th.io.vn/admin/resources` and connect the owner/editor's Google
Drive account. Initialize the resources if required. Then rerun this workflow;
it runs the real media smoke test when a Drive connection exists. Metadata
records `webReady` separately from `mediaReady`; `ready` requires media too.
No other application's token is substituted for this user consent.


## Learning Garden and display copies

The learning release is `675f414f6c92e812e67afba3edf414344dc4fbdc`. It adds four age profiles, guidance for all 18 games, age-based mechanics and a bounded, regenerable display cache in the existing data volume. Original images stay on Drive; do not reimport or replace the verified canonical set.

After deploying the pinned image, run **Warm and verify GoT display cache** on the management runner. `per_topic=12` prepares initial cards; `per_topic=1200` walks all topic cards and the complete 1,274-file canonical inventory. The workflow verifies repeat HTTP requests are local cache hits, reports actual disk bytes and checks existing services. It never edits Drive files. Large warmups run sequentially to limit load on the Pi. Normal requests also create display copies on demand; new Drive versions replace the mapping on the next check (60 seconds).
