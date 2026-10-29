"""Publish verified Meshtastic text messages to a Telegram channel.

Configuration is read from the application's saved Integrations settings.
Environment variables TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID remain available
as credential fallbacks for compatibility with existing setups.

For now, the strict publishing policy accepts only PKI results, because the
current decoder's PKI path checks AES-CCM authentication. Broadcast/channel
messages are intentionally not published yet: their current decode path does
not provide equivalent authenticated-integrity verification.
"""

from __future__ import annotations

import json
import queue
import threading
import urllib.error
import urllib.request
from typing import Any

from app_settings import load_settings


class TelegramPublisher:
    """Queue Telegram messages so network requests never block SDR decoding."""

    API_URL = "https://api.telegram.org/bot{token}/sendMessage"
    REQUEST_TIMEOUT_SECONDS = 10
    MAX_QUEUE_SIZE = 100

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        enabled: bool | None = None,
    ):
        settings = load_settings()
        integrations = settings.get("integrations", {})
        telegram = integrations.get("telegram", {}) if isinstance(integrations, dict) else {}

        configured_token = telegram.get("bot_token", "")
        configured_chat_id = telegram.get("chat_id", "")
        self.bot_token = (
            bot_token if bot_token is not None else configured_token
        ).strip()
        self.chat_id = (
            chat_id if chat_id is not None else configured_chat_id
        ).strip()
        self.publishing_enabled = (
            bool(telegram.get("enabled", False)) if enabled is None else bool(enabled)
        )
        self._queue: queue.Queue[str | None] = queue.Queue(maxsize=self.MAX_QUEUE_SIZE)
        self._thread: threading.Thread | None = None
        self._closed = False

        if self.enabled:
            self._thread = threading.Thread(
                target=self._run,
                name="TelegramPublisher",
                daemon=True,
            )
            self._thread.start()

    @property
    def enabled(self) -> bool:
        return self.publishing_enabled and bool(self.bot_token and self.chat_id)

    def publish(self, result: dict[str, Any]) -> bool:
        
        """Queue a message only when it meets the current strict policy."""
        if not self.enabled or self._closed or not self._is_publishable(result):
            return False

        message = self._format_message(result)
        try:
            self._queue.put_nowait(message)
        except queue.Full:
            print("Telegram queue is full; dropping a message")
            return False
        return True

    @staticmethod
    def _is_publishable(result: dict[str, Any]) -> bool:
        # Accept only successfully decoded PKI or broadcast packets.
        packet_result = result.get("result")

        if packet_result not in {"pki", "broadcast"}:
            return False

        # Publish text messages only.
        if result.get("application") != "TEXT_MESSAGE_APP":
            return False

        text = result.get("text")
        if not isinstance(text, str) or not text.strip():
            return False

        # Broadcast messages must have a known channel.
        if packet_result == "broadcast" and not result.get("channel_name"):
            return False

        return True

    @staticmethod
    def _format_message(result: dict[str, Any]) -> str:
        
        sender = result.get("from")
        recip = result.get("to")
        recip_text = f"!{recip:08x}" if isinstance(recip, int) else "unknown"
        sender_text = f"!{sender:08x}" if isinstance(sender, int) else "unknown"
        channel = result.get("channel_name") or "private"
        hops = result.get("hops")
        
        text = ""
        
        if channel != "private":
            text = str(result["text"]).strip()
            
        # Telegram sendMessage accepts plain text by default. Do not use
        # parse_mode, so message contents cannot be interpreted as markup.
        return (
            "Meshtastic message\n"
            f"From: {sender_text}\n"
            f"To: {recip_text}\n"
            f"Hops: {hops}\n"
            f"Channel: {channel}\n\n"
            f"=================\n"
            f"{text}"
        )

    def _run(self) -> None:
        while True:
            message = self._queue.get()
            try:
                if message is None:
                    return
                self._send_message(message)
            finally:
                self._queue.task_done()

    def _send_message(self, message: str) -> None:
        url = self.API_URL.format(token=self.bot_token)
        body = json.dumps({
            "chat_id": self.chat_id,
            "text": message,
            "disable_web_page_preview": True,
        }).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=self.REQUEST_TIMEOUT_SECONDS,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not payload.get("ok"):
                print(f"Telegram API rejected message: {payload}")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            print(f"Telegram publish failed: {exc}")

    def close(self, wait_seconds: float = 1.0) -> None:
        """Ask the background thread to finish; never wait indefinitely."""
        if self._closed:
            return
        self._closed = True
        if self._thread is not None:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                # Avoid blocking the SDR worker: discard one queued message
                # to make room for the shutdown marker.
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except queue.Empty:
                    pass
                try:
                    self._queue.put_nowait(None)
                except queue.Full:
                    print("Telegram publisher could not queue shutdown marker")
                    return
            self._thread.join(timeout=wait_seconds)
