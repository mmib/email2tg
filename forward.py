#!/usr/bin/env python3
"""Forward Dahua camera snapshots from email to Telegram."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime, parseaddr
from pathlib import Path
import re
import sys
import time
from typing import Any

try:
    import requests as REQUESTS_MODULE
    from requests import RequestException
except ImportError:
    REQUESTS_MODULE = None

    class RequestException(Exception):
        """Fallback exception when requests is unavailable."""


try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:
        return False

LOGGER = logging.getLogger("email2tg")
DEFAULT_MAX_ATTACHMENT_SIZE = 20 * 1024 * 1024
DEFAULT_MESSAGE_FORMAT = "%{from} [%{subject}] -> %{to}\n%.2000{text}\n%{image}"
SCRIPT_DIR = Path(__file__).resolve().parent
_FILENAME_CLEAN_RE = re.compile(r"[^A-Za-z0-9._-]+")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MESSAGE_VAR_RE = re.compile(r"%(\.(\d+))?\{([a-z_]+)\}")


def default_log_dir() -> str:
    if SCRIPT_DIR == Path("/opt/dahua-telegram"):
        return str(SCRIPT_DIR / "logs")
    return str(SCRIPT_DIR / "logs")


def load_env_file(path: str | os.PathLike[str]) -> dict[str, Any]:
    env_path = Path(path)
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {"exists": False, "readable": False, "loaded_keys": []}
    except OSError:
        return {"exists": True, "readable": False, "loaded_keys": []}

    loaded_keys: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded_keys.append(key)
    return {"exists": True, "readable": True, "loaded_keys": loaded_keys}


def load_config(env_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    env_file = Path(env_path) if env_path else SCRIPT_DIR / "config.env"
    if env_path:
        load_dotenv(env_path)
        env_info = load_env_file(env_path)
    else:
        load_dotenv(env_file)
        env_info = load_env_file(env_file)
        load_dotenv()

    missing_keys = [
        key
        for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
        if not os.getenv(key, "").strip()
    ]
    return {
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        "log_level": os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        "log_dir": os.getenv("LOG_DIR", default_log_dir()).strip(),
        "message_format": os.getenv("MESSAGE_FORMAT", DEFAULT_MESSAGE_FORMAT).replace("\\n", "\n"),
        "allowed_senders": {
            item.strip().lower()
            for item in os.getenv("ALLOWED_SENDERS", "").split(",")
            if item.strip()
        },
        "max_attachment_size": DEFAULT_MAX_ATTACHMENT_SIZE,
        "config_source": str(env_file),
        "config_exists": env_info["exists"],
        "config_readable": env_info["readable"],
        "config_loaded_keys": env_info["loaded_keys"],
        "missing_required_config": missing_keys,
    }


def setup_logging(log_dir: str, log_level: str) -> logging.Logger:
    logger = LOGGER
    logger.setLevel(getattr(logging, log_level, logging.INFO))
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s from=%(from_addr)s subject=%(subject)s %(message)s"
    )

    handler: logging.Handler | None = None
    candidate_dirs = [Path(log_dir), Path("/tmp/email2tg/logs")]
    for candidate_dir in candidate_dirs:
        try:
            candidate_dir.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                candidate_dir / "forward.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
            )
            break
        except OSError:
            continue

    if handler is None:
        handler = logging.StreamHandler(sys.stderr)

    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def log_event(
    level: int,
    message: str,
    *,
    subject: str = "-",
    from_addr: str = "-",
    **fields: Any,
) -> None:
    extra = {"subject": subject or "-", "from_addr": from_addr or "-"}
    if fields:
        message = f"{message} " + " ".join(f"{key}={value}" for key, value in fields.items())
    LOGGER.log(level, message, extra=extra)


def parse_message(raw_email: bytes):
    return BytesParser(policy=policy.default).parsebytes(raw_email)


def extract_sender(header_value: str) -> str:
    return parseaddr(header_value)[1].lower()


def sanitize_filename(filename: str | None, fallback: str = "image.bin") -> str:
    candidate = Path(filename or fallback).name
    cleaned = _FILENAME_CLEAN_RE.sub("_", candidate).strip("._")
    return cleaned or fallback


def looks_like_image_part(part) -> bool:
    content_type = part.get_content_type().lower()
    if content_type.startswith("image/"):
        return True

    filename = (part.get_filename() or "").lower()
    return content_type == "application/octet-stream" and filename.endswith(
        (".jpg", ".jpeg", ".png")
    )


def extract_images(message, max_attachment_size: int = DEFAULT_MAX_ATTACHMENT_SIZE) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for part in message.walk():
        if part.is_multipart() or not looks_like_image_part(part):
            continue

        payload = part.get_payload(decode=True) or b""
        filename = sanitize_filename(part.get_filename(), "camera-image.jpg")
        if len(payload) > max_attachment_size:
            log_event(
                logging.WARNING,
                "Skipping oversized attachment",
                subject=message.get("Subject", ""),
                from_addr=extract_sender(message.get("From", "")),
                filename=filename,
                size=len(payload),
            )
            continue

        content_type = part.get_content_type()
        if content_type == "application/octet-stream":
            suffix = Path(filename).suffix.lower()
            if suffix in {".jpg", ".jpeg"}:
                content_type = "image/jpeg"
            elif suffix == ".png":
                content_type = "image/png"

        images.append(
            {
                "filename": filename,
                "content_type": content_type,
                "bytes": payload,
            }
        )
    return images


def html_to_text(html: str) -> str:
    text = _HTML_TAG_RE.sub(" ", html)
    return " ".join(text.split())


def extract_message_bodies(message) -> dict[str, str]:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    for part in message.walk():
        if part.is_multipart():
            continue
        if part.get_content_disposition() == "attachment":
            continue

        content_type = part.get_content_type()
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")

        if content_type == "text/plain":
            plain_parts.append(text.strip())
        elif content_type == "text/html":
            html_parts.append(text.strip())

    plain = "\n".join(part for part in plain_parts if part)
    html = "\n".join(part for part in html_parts if part)
    text = plain or html_to_text(html)
    return {
        "plain": plain,
        "html": html,
        "text": text,
    }


def render_message_format(template: str, values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        max_len = match.group(2)
        key = match.group(3)
        value = values.get(key, "")
        if max_len:
            return value[: int(max_len)]
        return value

    rendered = _MESSAGE_VAR_RE.sub(replace, template)
    return rendered.strip()


def build_caption(message, image: dict[str, Any], config: dict[str, Any]) -> str:
    bodies = extract_message_bodies(message)
    values = {
        "from": extract_sender(message.get("From", "")) or "",
        "to": parseaddr(message.get("To", ""))[1],
        "subject": (message.get("Subject") or "").strip(),
        "text": bodies["text"],
        "plain": bodies["plain"],
        "html": bodies["html"],
        "image": image["filename"],
    }
    return render_message_format(config["message_format"], values)


def send_request(
    url: str,
    *,
    data: dict[str, Any],
    files: Any = None,
    timeout: int = 30,
    session: Any = None,
):
    session = session or REQUESTS_MODULE
    if session is None:
        log_event(logging.ERROR, "Python dependency missing module=requests")
        return None

    for attempt in range(2):
        try:
            response = session.post(url, data=data, files=files, timeout=timeout)
        except RequestException as exc:
            response = None
            if attempt == 0:
                log_event(logging.ERROR, f"Telegram request failed error={exc}")
                time.sleep(2)
                continue
            raise

        if response.status_code == 200:
            return response

        log_event(
            logging.ERROR,
            "Telegram API error",
            status=response.status_code,
            body=response.text[:200],
        )
        if attempt == 0:
            time.sleep(2)
            continue
        return response
    return None


def send_single_photo(image: dict[str, Any], caption: str, config: dict[str, Any], session: Any = None):
    url = f"https://api.telegram.org/bot{config['telegram_bot_token']}/sendPhoto"
    data = {"chat_id": config["telegram_chat_id"], "caption": caption}
    files = {
        "photo": (
            image["filename"],
            image["bytes"],
            image["content_type"],
        )
    }
    return send_request(url, data=data, files=files, session=session)


def send_media_group(images: list[dict[str, Any]], caption: str, config: dict[str, Any], session: Any = None):
    url = f"https://api.telegram.org/bot{config['telegram_bot_token']}/sendMediaGroup"
    media = []
    files: dict[str, tuple[str, bytes, str]] = {}
    for index, image in enumerate(images):
        attach_name = f"photo{index}"
        media_item = {"type": "photo", "media": f"attach://{attach_name}"}
        if index == 0:
            media_item["caption"] = caption
        media.append(media_item)
        files[attach_name] = (image["filename"], image["bytes"], image["content_type"])

    data = {"chat_id": config["telegram_chat_id"], "media": json.dumps(media)}
    return send_request(url, data=data, files=files, session=session)


def deliver_images(images: list[dict[str, Any]], message, config: dict[str, Any], session: Any = None) -> int:
    responses = []
    if len(images) > 10:
        for start in range(0, len(images), 10):
            batch = images[start : start + 10]
            caption = build_caption(message, batch[0], config)
            responses.append(send_media_group(batch, caption, config, session=session))
    else:
        for image in images:
            caption = build_caption(message, image, config)
            responses.append(send_single_photo(image, caption, config, session=session))
    return sum(1 for response in responses if response is not None and response.status_code == 200)


def process_email(raw_email: bytes, config: dict[str, Any] | None = None, session: Any = None) -> int:
    config = config or load_config()
    setup_logging(config["log_dir"], config["log_level"])

    if not config.get("config_exists", True):
        log_event(
            logging.ERROR,
            "Config file missing",
            path=config.get("config_source", ""),
        )
    elif not config.get("config_readable", True):
        log_event(
            logging.ERROR,
            "Config file unreadable",
            path=config.get("config_source", ""),
        )

    if config.get("missing_required_config"):
        log_event(
            logging.ERROR,
            "Required config missing",
            path=config.get("config_source", ""),
            missing_keys=",".join(config["missing_required_config"]),
            loaded_keys=",".join(config.get("config_loaded_keys", [])) or "-",
        )

    if not raw_email:
        log_event(logging.WARNING, "Received empty stdin payload")
        return 0

    try:
        message = parse_message(raw_email)
    except Exception as exc:  # noqa: BLE001
        log_event(logging.ERROR, f"Failed to parse email error={exc}")
        return 0

    subject = message.get("Subject", "")
    from_addr = extract_sender(message.get("From", ""))
    date_header = message.get("Date", "")

    if config["allowed_senders"] and from_addr not in config["allowed_senders"]:
        log_event(
            logging.INFO,
            "Skipping sender not in allowlist",
            subject=subject,
            from_addr=from_addr,
            date=date_header,
        )
        return 0

    try:
        images = extract_images(message, max_attachment_size=config["max_attachment_size"])
        if not images:
            log_event(
                logging.WARNING,
                "No images found",
                subject=subject,
                from_addr=from_addr,
                date=date_header,
            )
            return 0

        if not config["telegram_bot_token"] or not config["telegram_chat_id"]:
            log_event(
                logging.ERROR,
                "Telegram configuration missing",
                subject=subject,
                from_addr=from_addr,
            )
            return 0

        delivered = deliver_images(images, message, config, session=session)
        log_event(
            logging.INFO,
            "Processed email",
            subject=subject,
            from_addr=from_addr,
            images_found=len(images),
            delivered=delivered,
        )
    except Exception as exc:  # noqa: BLE001
        log_event(
            logging.ERROR,
            f"Unhandled processing error error={exc}",
            subject=subject,
            from_addr=from_addr,
        )
    return 0


def main() -> int:
    raw_email = sys.stdin.buffer.read()
    return process_email(raw_email)


if __name__ == "__main__":
    sys.exit(main())
