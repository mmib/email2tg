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
_FILENAME_CLEAN_RE = re.compile(r"[^A-Za-z0-9._-]+")


def load_config(env_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    if env_path:
        load_dotenv(env_path)
    else:
        load_dotenv()

    return {
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        "log_level": os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        "log_dir": os.getenv("LOG_DIR", "/opt/dahua-telegram/logs").strip(),
        "allowed_senders": {
            item.strip().lower()
            for item in os.getenv("ALLOWED_SENDERS", "").split(",")
            if item.strip()
        },
        "max_attachment_size": DEFAULT_MAX_ATTACHMENT_SIZE,
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

    try:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            Path(log_dir) / "forward.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
        )
    except OSError:
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


def build_caption(message) -> str:
    subject = (message.get("Subject") or "Camera alert").strip()
    sender = extract_sender(message.get("From", "")) or "unknown"
    date_header = (message.get("Date") or "").strip()
    timestamp = date_header
    if date_header:
        try:
            timestamp = parsedate_to_datetime(date_header).isoformat(sep=" ", timespec="seconds")
        except (TypeError, ValueError, IndexError, OverflowError):
            timestamp = date_header
    return f"{subject}\nFrom: {sender}\nDate: {timestamp}"


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
    caption = build_caption(message)
    responses = []
    if len(images) > 10:
        for start in range(0, len(images), 10):
            responses.append(send_media_group(images[start : start + 10], caption, config, session=session))
    else:
        for image in images:
            responses.append(send_single_photo(image, caption, config, session=session))
    return sum(1 for response in responses if response is not None and response.status_code == 200)


def process_email(raw_email: bytes, config: dict[str, Any] | None = None, session: Any = None) -> int:
    config = config or load_config()
    setup_logging(config["log_dir"], config["log_level"])

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
