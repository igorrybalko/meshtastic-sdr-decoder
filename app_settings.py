"""Persistent GUI settings for Meshtastic SDR Decoder.

These settings are intentionally not connected to the packet decoder yet.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from PySide6.QtCore import QStandardPaths


def settings_path() -> Path:
    """Return a per-user settings path, outside the source-code directory."""
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppConfigLocation
    )
    if not base:
        base = str(Path.home() / ".config" / "MeshtasticSDRDecoder")
    directory = Path(base)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "settings.json"


def default_settings() -> dict:
    return {
        "ui": {
            "region": None,
            "modem_preset": None,
            "frequency_slot": 0,
            "gain": "Auto",
            "source": "sdr",
            "capture_path": "",
        },
        "channels": [],
        "pki": {
            "own_node_id": "",
            "private_key": "",
            "peer_node_id": "",
            "peer_public_key": "",
        },
        "integrations": {
            "telegram": {
                "enabled": False,
                "bot_token": "",
                "chat_id": "",
            },
        },
    }


def load_settings() -> dict:
    path = settings_path()
    if not path.exists():
        return default_settings()

    try:
        with path.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
        if not isinstance(loaded, dict):
            raise ValueError("Settings root must be a JSON object")
    except (OSError, json.JSONDecodeError, ValueError):
        # Do not overwrite a damaged file automatically; preserve it for recovery.
        raise

    settings = default_settings()
    ui = loaded.get("ui")
    if isinstance(ui, dict):
        settings["ui"].update(ui)
    pki = loaded.get("pki")
    if isinstance(pki, dict):
        settings["pki"].update(pki)
        
    integrations = loaded.get("integrations")
    if isinstance(integrations, dict):
        telegram = integrations.get("telegram")
        if isinstance(telegram, dict):
            settings["integrations"]["telegram"].update(telegram)
        
    channels = loaded.get("channels")
    if isinstance(channels, list):
        settings["channels"] = [
            row for row in channels if isinstance(row, dict)
        ]
    return settings


def save_settings(settings: dict) -> None:
    path = settings_path()
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(settings, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(temporary, path)
