# ESP32 on th-server-1

Run the manual **Deploy ESP32 with management runner** workflow. The default
inspect-only mode prints configuration variable names, never values. Set
`deploy=true` with a full, successfully published TH-ESP32 commit SHA to deploy.
No separate ESP32 runner is required.

Deployment reuses `/srv/th-esp32/app/.env`, the `th-esp32` Compose project and
`th-esp32_data` volume. It pins the source Compose file and image to the selected
commit, binds port 5081 to loopback, and enables the retained `esp.th.io.vn`
Nginx site only after the application smoke test succeeds. Public pages, eight
tools and the firmware SHA-256 are checked before recording success. Failed
public verification restores the previous site symlink, retaining application
data. Private command diagnostics are stored under
`~/th-platform/state/esp32-deployment/last-command-error.log`.

The existing Portfolio and host access/monitoring services are checked after
deployment. Google login on a real account and USB flashing on real hardware
still require interactive testing. ESP32 uses its own SQLite volume; this
workflow does not restore a deleted volume from a backup or use the Portfolio
Drive broker.
