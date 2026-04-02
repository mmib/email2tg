# Repository Guidelines

## Project Structure & Module Organization
This repository currently contains a single specification file, [`task.md`](/home/mib/email2tg/task.md), which defines the target Python service and expected layout. The intended structure is:

- `forward.py`: Postfix pipe entrypoint that reads email from `stdin`.
- `test_email.py`: `unittest` suite for MIME parsing and Telegram delivery behavior.
- `samples/`: sample `.eml` fixtures such as `samples/dahua_motion.eml`.
- `config.env` and `config.env.example`: runtime configuration.
- `logs/`: local log output during development.

Keep parsing, Telegram API calls, and config loading in separate functions so tests can mock them independently.

## Build, Test, and Development Commands
There is no executable code checked in yet, so these commands apply once the scaffold from `task.md` is created:

- `python3 -m venv .venv && . .venv/bin/activate`: create and activate a local development environment.
- `pip install -r requirements.txt`: install `requests` and `python-dotenv`.
- `python3 -m unittest -v test_email.py`: run the unit test suite.
- `cat samples/dahua_motion.eml | python3 forward.py`: exercise the script with a fixture email.

If an `install.sh` script is added, keep it idempotent and safe to rerun.

## Coding Style & Naming Conventions
Target Python 3.10+. Use 4-space indentation, `snake_case` for functions and variables, and `UPPER_SNAKE_CASE` for environment-backed constants. Prefer small, pure helpers for parsing headers, extracting attachments, and building Telegram payloads. Use standard-library modules first; the task only permits `requests` and `python-dotenv` as external dependencies.

## Testing Guidelines
Use `unittest` and mock outbound HTTP calls so tests never hit Telegram. Name tests by behavior, for example `test_skips_oversized_attachment` or `test_batches_media_group_after_ten_images`. Cover malformed email input, sender filtering, image detection, retry behavior, and the requirement that the script always exits with status `0`.

## Commit & Pull Request Guidelines
This workspace does not currently include Git history, so follow a simple imperative style for commits: `Add MIME attachment parser` or `Test allowed sender filtering`. Keep commits focused. Pull requests should include a short summary, test evidence (`python3 -m unittest -v` output), and any Postfix or deployment implications. Include config or sample-email changes in the description when relevant.

## Security & Configuration Tips
Do not commit real bot tokens, chat IDs, or production sender lists. Keep secrets in `config.env`, keep `config.env.example` scrubbed, sanitize attachment filenames, and enforce the 20 MB attachment cap described in [`task.md`](/home/mib/email2tg/task.md).
