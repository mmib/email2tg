from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from email.message import EmailMessage

import forward


def sample_config(**overrides):
    config = {
        "telegram_bot_token": "token",
        "telegram_chat_id": "-100123",
        "log_level": "INFO",
        "log_dir": str(Path.cwd() / "logs"),
        "allowed_senders": set(),
        "max_attachment_size": forward.DEFAULT_MAX_ATTACHMENT_SIZE,
    }
    config.update(overrides)
    return config


class ForwardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample_email = (Path(__file__).parent / "samples" / "dahua_motion.eml").read_bytes()

    def setUp(self):
        for handler in list(forward.LOGGER.handlers):
            handler.close()
            forward.LOGGER.removeHandler(handler)

    def test_extracts_two_images_from_sample_email(self):
        message = forward.parse_message(self.sample_email)
        images = forward.extract_images(message)
        self.assertEqual(2, len(images))
        self.assertEqual("image/jpeg", images[0]["content_type"])
        self.assertEqual("image/jpeg", images[1]["content_type"])

    @patch.dict(os.environ, {}, clear=True)
    def test_load_config_reads_config_env_from_script_directory(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as temp_env:
            temp_env.write(
                "TELEGRAM_BOT_TOKEN=test-token\n"
                "TELEGRAM_CHAT_ID=-123\n"
                "LOG_LEVEL=DEBUG\n",
            )
            env_path = Path(temp_env.name)
        try:
            config = forward.load_config(env_path)
        finally:
            env_path.unlink(missing_ok=True)

        self.assertEqual("test-token", config["telegram_bot_token"])
        self.assertEqual("-123", config["telegram_chat_id"])
        self.assertEqual("DEBUG", config["log_level"])

    @patch.dict(os.environ, {}, clear=True)
    @patch("forward.load_dotenv", return_value=False)
    def test_load_config_works_without_python_dotenv(self, _load_dotenv_mock):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as temp_env:
            temp_env.write(
                "TELEGRAM_BOT_TOKEN=fallback-token\n"
                "TELEGRAM_CHAT_ID=-999\n",
            )
            env_path = Path(temp_env.name)
        try:
            config = forward.load_config(env_path)
        finally:
            env_path.unlink(missing_ok=True)

        self.assertEqual("fallback-token", config["telegram_bot_token"])
        self.assertEqual("-999", config["telegram_chat_id"])

    @patch("forward.log_event")
    def test_no_attachments_logs_warning_and_skips_telegram(self, log_event):
        raw = (
            b"From: camera1@local\n"
            b"Subject: No image\n"
            b"Date: Mon, 15 Jan 2024 14:30:00 +0000\n"
            b"Content-Type: text/plain; charset=utf-8\n\n"
            b"Plain body\n"
        )
        session = Mock()

        result = forward.process_email(raw, config=sample_config(), session=session)

        self.assertEqual(0, result)
        session.post.assert_not_called()
        log_event.assert_called()
        self.assertIn("No images found", log_event.call_args.args[1])

    def test_octet_stream_jpg_is_treated_as_image(self):
        raw = (
            b"From: camera1@local\n"
            b"Subject: Octet stream image\n"
            b"MIME-Version: 1.0\n"
            b"Content-Type: multipart/mixed; boundary=x\n\n"
            b"--x\n"
            b"Content-Type: application/octet-stream; name=\"snap.jpg\"\n"
            b"Content-Disposition: attachment; filename=\"snap.jpg\"\n"
            b"Content-Transfer-Encoding: base64\n\n"
            b"QUJD\n"
            b"--x--\n"
        )
        message = forward.parse_message(raw)
        images = forward.extract_images(message)
        self.assertEqual(1, len(images))
        self.assertEqual("image/jpeg", images[0]["content_type"])

    @patch("forward.log_event")
    def test_non_allowed_sender_is_skipped(self, log_event):
        session = Mock()

        forward.process_email(
            self.sample_email,
            config=sample_config(allowed_senders={"camera2@local"}),
            session=session,
        )

        session.post.assert_not_called()
        self.assertIn("Skipping sender not in allowlist", log_event.call_args.args[1])

    def test_empty_allowed_senders_allows_any_sender(self):
        session = Mock()
        session.post.return_value = Mock(status_code=200, text="ok")

        result = forward.process_email(
            self.sample_email,
            config=sample_config(allowed_senders=set()),
            session=session,
        )

        self.assertEqual(0, result)
        self.assertEqual(2, session.post.call_count)

    @patch("forward.log_event")
    def test_oversized_attachment_is_skipped(self, log_event):
        message = EmailMessage()
        message["From"] = "camera1@local"
        message["Subject"] = "Big image"
        message.set_content("Body")
        message.add_attachment(
            b"x" * 10,
            maintype="image",
            subtype="jpeg",
            filename="big.jpg",
        )

        images = forward.extract_images(message, max_attachment_size=5)
        self.assertEqual([], images)
        self.assertIn("Skipping oversized attachment", log_event.call_args.args[1])

    @patch("forward.time.sleep")
    def test_telegram_api_retry_uses_expected_payload(self, sleep_mock):
        response_fail = Mock(status_code=500, text="bad")
        response_ok = Mock(status_code=200, text="ok")
        session = Mock()
        session.post.side_effect = [response_fail, response_ok, response_ok]

        result = forward.process_email(self.sample_email, config=sample_config(), session=session)

        self.assertEqual(0, result)
        self.assertEqual(3, session.post.call_count)
        first_call = session.post.call_args_list[0]
        self.assertIn("/sendPhoto", first_call.args[0])
        self.assertEqual("-100123", first_call.kwargs["data"]["chat_id"])
        self.assertIn("Alarm Event: Motion Detection", first_call.kwargs["data"]["caption"])
        sleep_mock.assert_called_once_with(2)

    def test_more_than_ten_images_uses_media_group_batches(self):
        images = [
            {"filename": f"img-{i}.jpg", "bytes": b"abc", "content_type": "image/jpeg"}
            for i in range(11)
        ]
        message = forward.parse_message(self.sample_email)
        session = Mock()
        session.post.return_value = Mock(status_code=200, text="ok")

        delivered = forward.deliver_images(images, message, sample_config(), session=session)

        self.assertEqual(2, delivered)
        self.assertEqual(2, session.post.call_count)
        first_call = session.post.call_args_list[0]
        self.assertIn("/sendMediaGroup", first_call.args[0])
        media = json.loads(first_call.kwargs["data"]["media"])
        self.assertEqual(10, len(media))
        self.assertIn("caption", media[0])
        self.assertEqual(10, len(first_call.kwargs["files"]))

    @patch("forward.log_event")
    def test_missing_requests_dependency_does_not_crash(self, log_event):
        with patch.object(forward, "REQUESTS_MODULE", None):
            result = forward.process_email(self.sample_email, config=sample_config(), session=None)

        self.assertEqual(0, result)
        self.assertTrue(
            any("module=requests" in call.args[1] for call in log_event.call_args_list),
        )

    @patch("forward.Path.mkdir")
    @patch("forward.RotatingFileHandler")
    def test_setup_logging_falls_back_to_tmp_log_dir(self, handler_cls, mkdir_mock):
        handler = logging.StreamHandler()
        handler_cls.side_effect = [OSError("no access"), handler]

        logger = forward.setup_logging("/no-permission/logs", "INFO")

        self.assertIs(logger.handlers[0], handler)
        attempted_paths = [call.args[0] for call in handler_cls.call_args_list]
        self.assertEqual(Path("/no-permission/logs/forward.log"), attempted_paths[0])
        self.assertEqual(Path("/tmp/email2tg/logs/forward.log"), attempted_paths[1])


if __name__ == "__main__":
    unittest.main()
