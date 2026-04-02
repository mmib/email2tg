# email2tg

Lightweight Python service that receives Dahua camera emails from Postfix pipe transport and forwards image attachments to a Telegram chat.

## Repository Layout

- `forward.py`: parses MIME email from `stdin`, filters attachments, and sends snapshots to Telegram.
- `test_email.py`: `unittest` coverage for parsing, filtering, retries, and media-group batching.
- `samples/dahua_motion.eml`: realistic fixture for local testing.
- `config.env.example`: configuration template.
- `install.sh`: installs the service into `/opt/dahua-telegram`.

The checked-in `forward.py` uses `#!/usr/bin/env python3` so it runs from the repo. During installation, `install.sh` rewrites the deployed `/opt/dahua-telegram/forward.py` shebang to the venv interpreter.

## Prerequisites

- Ubuntu 22 or similar Linux host with Postfix.
- Python 3.10+.
- A Telegram bot token and destination `chat_id`.
- DNS control for the receiving mail domain.

## DNS Configuration

```dns
mib.photo.       IN  MX   10  mail.mib.photo.
mail.mib.photo.  IN  A        YOUR_VPS_IP
mib.photo.       IN  TXT      "v=spf1 ip4:YOUR_VPS_IP -all"
```

## Telegram Bot Setup

1. Message `@BotFather` and create a bot with `/newbot`.
2. Add the bot to the target group or channel and send one message there.
3. Fetch the chat id:

```bash
curl https://api.telegram.org/bot<TOKEN>/getUpdates
```

## Installation

```bash
chmod +x install.sh
python3 -m pip install -r requirements.txt
./install.sh
cp config.env.example config.env
```

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `config.env`. `ALLOWED_SENDERS` is optional: leave it empty to accept mail from any sender, or set a comma-separated allowlist such as `camera1@local,camera2@local`. The script loads `config.env` from the same directory as `forward.py`.

`MESSAGE_FORMAT` controls the Telegram caption text. Default value:

```text
%{from} [%{subject}] -> %{to}
%.2000{text}
%{image}
```

Available params:

- `from`: sender address
- `to`: receiver address
- `subject`: email subject
- `text`: best text body (`plain`, otherwise HTML converted to text)
- `plain`: plain text body
- `html`: HTML body
- `image`: image filename

Use `%.N{name}` to truncate a field, for example `%.2000{text}`.

## Postfix Configuration

Add to `/etc/postfix/main.cf`:

```conf
virtual_alias_domains = mib.photo
virtual_alias_maps = hash:/etc/postfix/virtual
```

Add to `/etc/postfix/virtual`:

```conf
dahua@mib.photo  dahua-cam
```

Add to `/etc/aliases`:

```conf
dahua-cam: "|/opt/dahua-telegram/forward.py"
```

Apply changes:

```bash
postmap /etc/postfix/virtual
newaliases
systemctl reload postfix
```

## Testing

Pipe test email into the script:

```bash
cat samples/dahua_motion.eml | python3 forward.py
python3 -m unittest -v test_email.py
tail -f /opt/dahua-telegram/logs/forward.log
```

If the configured log directory is not writable, the script falls back to `/tmp/email2tg/logs/forward.log` before using stderr.

SMTP smoke test:

```bash
swaks --to dahua@mib.photo --from test@test.com --attach samples/test.jpg --server localhost
```

## Dahua Camera Settings

- SMTP server: `mail.mib.photo`
- SMTP port: `25`
- Recipient: `dahua@mib.photo`
- Enable snapshot attachments in alarm emails

## Troubleshooting

- If Postfix reports pipe failures, verify `/opt/dahua-telegram/forward.py` is executable.
- If Telegram delivery fails, confirm bot membership and `chat_id`.
- If mail arrives but nothing sends, inspect `ALLOWED_SENDERS`. An empty value allows all senders; a non-empty value only accepts exact sender matches.
- Check `/opt/dahua-telegram/logs/forward.log` or `/tmp/email2tg/logs/forward.log` for parsing, filtering, and Telegram API errors.
