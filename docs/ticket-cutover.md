# TH.Ticket cutover

The application moves to https://ticket.th.io.vn and repository TuanPham0411/TH-Ticket.
The deployment accepts runners th-mes and th-mes.ticket during registration migration.

Before merging the application change:
1. Rename TH-MES.Ticket to TH-Ticket in GitHub Settings > General. Preserve the existing repository.
2. Add Cloudflare Tunnel published hostname ticket.th.io.vn -> http://localhost:80.
3. Add Google OAuth redirect URIs https://ticket.th.io.vn/api/oauth/callback and https://ticket.th.io.vn/api/mes/drive/callback. Keep old entries during migration.
4. Run deploy/ticket-cutover.py on the Pi with its adjacent ticket-nginx.conf; review its printed commands, then run them with sudo (or use --apply where passwordless sudo is configured).
5. Reconfigure the repository runner as th-mes using GitHub's fresh removal and registration tokens. Do not send tokens in chat. Stop/uninstall the old service before re-registering in its existing runner directory; reinstall/start afterward.

The original Compose project mes-ticket, PostgreSQL database/volumes, API paths and Drive history IDs remain stable. This prevents a new empty database or broken storage chain. Product labels use TH.Ticket.

Merge and deploy the application only after the new hostname route is ready. Verify HTTPS, owner login, a ticket save and attachment access. Redirect the old hostname only after verification.
