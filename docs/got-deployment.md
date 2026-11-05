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
