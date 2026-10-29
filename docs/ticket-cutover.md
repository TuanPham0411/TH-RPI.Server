# TH.Ticket

The application is served at https://ticket.th.io.vn from TuanPham0411/TH-Ticket with runner th-ticket.

Active resources use ticket consistently:
- Server application: ~/apps/ticket
- Compose project and media volume: ticket, ticket_media
- PostgreSQL database and private schema: ticket, ticket_private
- Application routes: /api/ticket
- Runtime database password variable: TICKET_DB_PASSWORD
- Google Drive folder: th-data/ticket

Data transition preserves account password hashes, native Drive file IDs, attachment bytes and ordered authenticated history. A cold recovery and exact row comparison are required before removing retired resources.

Run deploy/ticket-cleanup.py to review the cleanup plan, then use --apply after recovery verification. Root-owned Nginx changes require the administrator's sudo authentication. Remove retired Cloudflare routes, DNS entries and OAuth redirect URIs after checking Ticket login and attachments.
