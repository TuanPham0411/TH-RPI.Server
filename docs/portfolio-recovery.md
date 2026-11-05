# Recover Portfolio after a server reset

Use the manual **Recover Portfolio from retained Drive storage** workflow in
TH-RPI.Server on runner `th-server-1` with label `th-server-bootstrap`.

The default inspection mode reconnects the recorded immutable storage broker using
private files already retained under `~/th-platform/config`. It verifies that the
Portfolio journal and encrypted server schema snapshot exist. No credentials are
printed, and no Portfolio database or public site is created by inspection mode.

After inspection succeeds, select `restore=true`. Recovery uses the Portfolio
release recorded in the authenticated Drive schema snapshot, preserves the private
`~/apps/portfolio/.env`, and creates the canonical database/media volumes. It
refuses to apply a schema dump over an existing nonempty database. The existing
storage operator replays the baseline and later Drive operations, validates
foreign keys and verifies downloaded media SHA-256 checksums. The site remains
unpublished unless existing users, an administrator and the main content row are
restored. A Drive-first write-through probe and local health/content checks must
pass before re-enabling only the retained `th.io.vn` Nginx symlink. Failed public
checks restore the previous route and retain recovered data for diagnosis.

Recovery progress and command error details stay under the private directory
`~/th-platform/state/portfolio-recovery`. A completed restore can be retried for
application/routing checks without overwriting the recovered database. A failed
partial restore with nonempty schema requires operator investigation; the script
will not erase volumes or drop tables to hide the failure.

This restores Portfolio only. Shared media volumes required by the canonical
broker may be recreated empty for other applications, but AI/Ticket application
containers and databases are not deployed. The previous automatic in-place broker
upgrade is now manual to prevent a competing Ticket-dependent deployment during
fresh recovery. SSH, networking, monitor and HDMI services are preserved and
checked after the recovery job.

If the retained broker release has a slow cold start, `broker_release` can select a
newly tested/published immutable TH-RPI.Server image. The old deployment metadata
is kept until the replacement broker authenticates and the recovery schema is
verified. Log descriptor snapshots and bounded parallel reads reduce repeated
Drive metadata calls; startup cache replay remains ordered, verifies every event
checksum/decryption and checkpoints once per application instead of rewriting the
whole mapping file after every historical operation.
