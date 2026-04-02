# Claude Code Task: Dahua Camera Email → Telegram Forwarder

## Project Overview

Build a lightweight Python service that receives emails from Dahua IP cameras (forwarded by Postfix via pipe transport) and sends attached images to a Telegram chat via Bot API.

**Architecture:** Postfix receives SMTP → pipes email to Python script → script parses MIME → extracts image attachments → sends each image to Telegram via `sendPhoto` API.

No web framework. No database. No daemon. Just a script invoked by Postfix per incoming email.

## Directory Structure

```
./email2tg/
├── forward.py          # Main script, invoked by Postfix pipe
├── config.env          # Telegram bot token, chat_id, log settings
├── config.env.example  # Template with placeholder values
├── requirements.txt    # requests, python-dotenv
├── install.sh          # Setup script (venv, permissions, Postfix config hints)
├── test_email.py       # Unit tests with sample .eml files
├── samples/            # Sample .eml files for testing
│   └── dahua_motion.eml
├── logs/               # Log directory
└── README.md           # Full setup instructions
```

## Detailed Requirements

### forward.py — Main Script

1. **Input**: Reads raw email from stdin (Postfix pipes it).
2. **Parsing**: Use Python `email` stdlib (`email.message_from_bytes`, `email.policy.default`).
3. **Attachment extraction**:
   - Walk all MIME parts.
   - Extract parts where `Content-Type` starts with `image/` (Dahua typically sends JPEG).
   - Also handle `application/octet-stream` with `.jpg`/`.jpeg`/`.png` filename — Dahua sometimes mislabels.
   - Decode from base64 via `part.get_payload(decode=True)`.
4. **Telegram sending**:
   - Use `requests.post` to `https://api.telegram.org/bot{TOKEN}/sendPhoto`.
   - Send each image as multipart form upload (`files={'photo': (filename, image_bytes, content_type)}`).
   - Include caption with: camera name (parsed from `Subject` or `From`), timestamp (from `Date` header), and detection type if available in subject.
   - If more than 10 images in one email, use `sendMediaGroup` (batch up to 10 per group).
5. **Error handling**:
   - If Telegram API returns non-200, log error and retry once after 2 seconds.
   - If no images found in email, log warning with subject/from/date for debugging (don't send anything to Telegram).
   - All errors must be logged, script must exit 0 (so Postfix doesn't bounce the email).
   - **Critical**: Script must ALWAYS exit 0 regardless of errors. Postfix treats non-zero as delivery failure and will bounce/retry.
6. **Logging**:
   - Use Python `logging` module.
   - Log to `/opt/dahua-telegram/logs/forward.log`.
   - Log rotation: use `RotatingFileHandler`, 5MB max, keep 3 files.
   - Log level configurable via `config.env`.
   - Log: timestamp, from, subject, number of images found, Telegram API response status.
7. **Configuration** (via `config.env`, loaded with `python-dotenv`):
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
   TELEGRAM_CHAT_ID=-100123456789
   LOG_LEVEL=INFO
   LOG_DIR=/opt/dahua-telegram/logs
   # Optional: filter by sender
   ALLOWED_SENDERS=camera1@local,camera2@local
   ```
8. **Security**:
   - If `ALLOWED_SENDERS` is set, only process emails from those addresses (case-insensitive match on From header).
   - Validate image payload size: skip any attachment >20MB (Telegram limit).
   - Sanitize all filenames before using in API calls.
9. **Shebang**: `#!/opt/dahua-telegram/venv/bin/python3` — script runs inside its own venv.

### install.sh — Setup Script

1. Create `./email2tg/` directory structure (we run this from a user's folder.
2. Create Python venv at `/opt/dahua-telegram/venv/`.
3. Install dependencies from `requirements.txt` into venv.
4. Create `config.env` from `config.env.example` if not exists.
5. Create `logs/` directory.
6. Set ownership: `dahua-telegram` user (create if not exists) or fall back to `nobody`.
7. Set permissions: script executable, config readable only by owner.
8. Print clear instructions for:
   - Postfix configuration (what to add to `main.cf`, `virtual`, `/etc/aliases`).
   - DNS records needed (MX, A, SPF).
   - How to create Telegram bot and get chat_id.
   - How to test with: `cat samples/dahua_motion.eml | /opt/dahua-telegram/forward.py`

### test_email.py — Tests

Use `unittest`. Test cases:
1. Parse sample Dahua email with 2 JPEG attachments → extracts 2 images.
2. Email with no attachments → logs warning, no Telegram call.
3. Email with `application/octet-stream` + `.jpg` filename → correctly identified as image.
4. Email from non-allowed sender → skipped when `ALLOWED_SENDERS` is set.
5. Oversized attachment (>20MB) → skipped with warning.
6. Telegram API mock: verify correct endpoint, payload structure, caption format.
7. Multiple images (>10) → triggers `sendMediaGroup` batching.

### samples/dahua_motion.eml

Create a realistic sample email mimicking Dahua camera output:
- From: `camera1@local`
- Subject: `Alarm Event: Motion Detection [Camera1] 2024-01-15 14:30:00`
- Multipart MIME with 2 small JPEG attachments (can be tiny 1x1 pixel test JPEGs, base64 encoded).
- Include typical Dahua headers and body text.

### README.md

Full setup guide covering:

1. **Prerequisites**: Ubuntu 22, Postfix installed, Python 3.10+, domain with DNS access.
2. **DNS Configuration**:
   ```
   # Replace YOUR_VPS_IP with actual IP
   mib.photo.       IN  MX   10  mail.mib.photo.
   mail.mib.photo.  IN  A        YOUR_VPS_IP
   mib.photo.       IN  TXT      "v=spf1 ip4:YOUR_VPS_IP -all"
   ```
3. **Telegram Bot Setup**:
   - Message @BotFather → `/newbot` → save token.
   - Create group/channel → add bot → send a message.
   - Get chat_id: `curl https://api.telegram.org/bot<TOKEN>/getUpdates`.
4. **Installation**: Run `install.sh`.
5. **Postfix Configuration**:
   ```
   # /etc/postfix/main.cf — add to existing config:
   virtual_alias_domains = mib.photo
   virtual_alias_maps = hash:/etc/postfix/virtual

   # /etc/postfix/virtual
   dahua@mib.photo  dahua-cam

   # /etc/aliases — add line:
   dahua-cam: "|/opt/dahua-telegram/forward.py"

   # Apply:
   postmap /etc/postfix/virtual
   newaliases
   systemctl reload postfix
   ```
6. **Testing**:
   - Pipe test: `cat samples/dahua_motion.eml | sudo -u nobody /opt/dahua-telegram/forward.py`
   - SMTP test: `swaks --to dahua@mib.photo --from test@test.com --attach samples/test.jpg --server localhost`
   - Check logs: `tail -f /opt/dahua-telegram/logs/forward.log`
7. **Dahua Camera Configuration**:
   - SMTP server: `mail.mib.photo` (or VPS IP directly)
   - SMTP port: 25 (Dahua cameras often don't support TLS for outbound SMTP)
   - Sender: anything (or configure per-camera for identification)
   - Recipient: `dahua@mib.photo`
   - Enable: attach snapshot to email
8. **Troubleshooting**: Common issues (Postfix permissions, script not executable, Telegram token wrong, DNS propagation).

## Technical Constraints

- Python 3.10+ (Ubuntu 22 ships 3.10).
- Only external dependency: `requests`, `python-dotenv`.
- No async needed — script runs per email, processes synchronously, exits.
- No database, no state, no daemon.
- Must handle Postfix's execution environment: limited PATH, possibly running as `nobody` user.
- Stdin may be empty or malformed — handle gracefully, always exit 0.

## Out of Scope (explicitly do NOT build)

- Web UI or dashboard.
- Email storage or archival.
- Video handling (Dahua sends snapshots, not video, via email).
- TLS/DKIM/DMARC setup for mib.photo (existing Postfix config handles this).
- Telegram bot command handling (bot only sends, doesn't receive commands).
