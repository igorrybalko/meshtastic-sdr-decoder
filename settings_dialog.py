"""Settings dialog: editable channels and one PKI node pair.

The values are persisted only; decoder integration is a separate next step.
"""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QCheckBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from crypto import load_pki_peers


class SettingsDialog(QDialog):
    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Meshtastic settings")
        self.resize(760, 520)
        self._original = deepcopy(settings)
        self._settings = deepcopy(settings)

        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs)

        # Channels tab
        channels_page = QWidget()
        channels_layout = QVBoxLayout(channels_page)
        channels_layout.addWidget(QLabel(
            "Channel index is the MeshPacket channel number (usually 0–7). "
            "PSK is stored as entered; parsing and decryption will be connected later."
        ))
        self.channels_table = QTableWidget(0, 3)
        self.channels_table.setHorizontalHeaderLabels(
            ["Channel index", "Name", "PSK (hex/Base64)"]
        )
        self.channels_table.horizontalHeader().setStretchLastSection(True)
        self.channels_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.channels_table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
            | QTableWidget.EditTrigger.SelectedClicked
        )
        channels_layout.addWidget(self.channels_table)
        channel_buttons = QHBoxLayout()
        add_channel = QPushButton("Add channel")
        remove_channel = QPushButton("Remove selected")
        add_channel.clicked.connect(self.add_channel)
        remove_channel.clicked.connect(self.remove_channel)
        channel_buttons.addWidget(add_channel)
        channel_buttons.addWidget(remove_channel)
        channel_buttons.addStretch()
        channels_layout.addLayout(channel_buttons)
        tabs.addTab(channels_page, "Channels")

        # PKI tab
        pki_page = QWidget()
        pki_layout = QVBoxLayout(pki_page)

        # Собственные параметры узла
        pki_form = QFormLayout()

        self.own_node_id = QLineEdit()
        self.own_node_id.setPlaceholderText("e.g. xxxxxxxx")

        self.private_key = QLineEdit()
        self.private_key.setPlaceholderText("Base64 or 64 hexadecimal characters")
        self.private_key.setEchoMode(QLineEdit.EchoMode.Password)

        pki_form.addRow("My node ID:", self.own_node_id)
        pki_form.addRow("My private key:", self.private_key)

        pki_layout.addLayout(pki_form)

        # Таблица соседних узлов
        pki_layout.addWidget(QLabel("Peer nodes:"))

        self.peers_table = QTableWidget(0, 4)

        self.peers_table.setHorizontalHeaderLabels([
            "Node ID",
            "Public key",
            "Short name",
            "Long name",
        ])

        self.peers_table.horizontalHeader().setStretchLastSection(True)

        self.peers_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )

        self.peers_table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
            | QTableWidget.EditTrigger.SelectedClicked
        )

        pki_layout.addWidget(self.peers_table)

        # Кнопки управления узлами
        peer_buttons = QHBoxLayout()

        add_peer = QPushButton("Add node")
        remove_peer = QPushButton("Remove selected")

        add_peer.clicked.connect(self.add_peer)
        remove_peer.clicked.connect(self.remove_peer)

        peer_buttons.addWidget(add_peer)
        peer_buttons.addWidget(remove_peer)
        peer_buttons.addStretch()

        pki_layout.addLayout(peer_buttons)

        # Предупреждение
        warning = QLabel(
            "Note: these fields are saved locally in settings.json as plain text. "
            "Anyone with access to your Windows account files may be able to read them."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #a66a00;")

        pki_layout.addWidget(warning)

        tabs.addTab(pki_page, "PKI")

        # Integrations tab — basic Telegram Bot API settings.
        integrations_page = QWidget()
        integrations_layout = QVBoxLayout(integrations_page)
        telegram_form = QFormLayout()
        self.telegram_enabled = QCheckBox("Publish decoded messages to Telegram")
        self.telegram_bot_token = QLineEdit()
        self.telegram_bot_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.telegram_bot_token.setPlaceholderText("Token from @BotFather")
        self.telegram_chat_id = QLineEdit()
        self.telegram_chat_id.setPlaceholderText("@channel_name or chat ID")
        telegram_form.addRow("Telegram:", self.telegram_enabled)
        telegram_form.addRow("Bot token:", self.telegram_bot_token)
        telegram_form.addRow("Chat ID:", self.telegram_chat_id)
        integrations_layout.addLayout(telegram_form)

        telegram_warning = QLabel(
            "The bot must be an administrator of the target channel with "
            "permission to post messages. The token is saved in the local "
            "settings.json file as plain text; do not share that file or "
            "commit it to Git."
        )
        telegram_warning.setWordWrap(True)
        telegram_warning.setStyleSheet("color: #a66a00;")
        integrations_layout.addWidget(telegram_warning)
        integrations_layout.addStretch()
        tabs.addTab(integrations_page, "Integrations")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._populate()
        
        
    def _append_peer(self, node_id="", peer=None):
        peer = peer or {}

        row = self.peers_table.rowCount()
        self.peers_table.insertRow(row)

        values = [
            node_id,
            peer.get("public_key", ""),
            peer.get("short_name", ""),
            peer.get("long_name", ""),
        ]

        for column, value in enumerate(values):
            self.peers_table.setItem(
                row,
                column,
                QTableWidgetItem(str(value))
            )
            
            
    def add_peer(self):
        self._append_peer()

        row = self.peers_table.rowCount() - 1
        self.peers_table.setCurrentCell(row, 0)
        self.peers_table.editItem(
            self.peers_table.item(row, 0)
        )


    def remove_peer(self):
        rows = sorted(
            {
                index.row()
                for index in self.peers_table.selectedIndexes()
            },
            reverse=True,
        )

        for row in rows:
            self.peers_table.removeRow(row)
            

    def _populate(self):
        
        integrations = self._settings.get("integrations", {})
        telegram = integrations.get("telegram", {}) if isinstance(integrations, dict) else {}
        self.telegram_enabled.setChecked(bool(telegram.get("enabled", False)))
        self.telegram_bot_token.setText(str(telegram.get("bot_token", "")))
        self.telegram_chat_id.setText(str(telegram.get("chat_id", "")))
        
        pki = self._settings.get("pki", {})
        self.own_node_id.setText(str(pki.get("own_node_id", "")))
        self.private_key.setText(str(pki.get("private_key", "")))
        
        for node_id, peer in pki.get("peers", {}).items():
            self._append_peer(node_id, peer)
        
        
        for channel in self._settings.get("channels", []):
            self._append_channel(channel)     

    def _append_channel(self, channel=None):
        channel = channel or {"index": 0, "name": "", "psk": ""}
        row = self.channels_table.rowCount()
        self.channels_table.insertRow(row)
        values = [channel.get("index", 0), channel.get("name", ""), channel.get("psk", "")]
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            if column == 0:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.channels_table.setItem(row, column, item)

    def add_channel(self):
        self._append_channel()
        self.channels_table.setCurrentCell(self.channels_table.rowCount() - 1, 0)
        self.channels_table.editItem(self.channels_table.item(self.channels_table.rowCount() - 1, 1))

    def remove_channel(self):
        rows = sorted({index.row() for index in self.channels_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.channels_table.removeRow(row)

    def accept(self):
        channels = []
        try:
            for row in range(self.channels_table.rowCount()):
                index_item = self.channels_table.item(row, 0)
                name_item = self.channels_table.item(row, 1)
                psk_item = self.channels_table.item(row, 2)
                index = int(index_item.text().strip()) if index_item and index_item.text().strip() else 0
                if not 0 <= index <= 255:
                    raise ValueError(f"Channel index in row {row + 1} must be between 0 and 255")
                name = name_item.text().strip() if name_item else ""
                psk = psk_item.text().strip() if psk_item else ""
                channels.append({"index": index, "name": name, "psk": psk})
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid channel", str(exc))
            return
        
        peers = {}

        try:
            for row in range(self.peers_table.rowCount()):
                node_id_item = self.peers_table.item(row, 0)
                public_key_item = self.peers_table.item(row, 1)
                short_name_item = self.peers_table.item(row, 2)
                long_name_item = self.peers_table.item(row, 3)

                node_id = (
                    node_id_item.text().strip().lstrip("!")
                    if node_id_item else ""
                )

                public_key = (
                    public_key_item.text().strip()
                    if public_key_item else ""
                )

                short_name = (
                    short_name_item.text().strip()
                    if short_name_item else ""
                )

                long_name = (
                    long_name_item.text().strip()
                    if long_name_item else ""
                )

                # Полностью пустая строка таблицы пропускается.
                if not any([node_id, public_key, short_name, long_name]):
                    continue

                if not node_id or not public_key:
                    raise ValueError(
                        f"Node ID and public key are required in row {row + 1}"
                    )

                node_id = f"{int(node_id, 16):08X}"

                if node_id in peers:
                    raise ValueError(
                        f"Duplicate Node ID: {node_id}"
                    )

                peers[node_id] = {
                    "public_key": public_key,
                    "short_name": short_name,
                    "long_name": long_name,
                }

            # Проверяем корректность всех публичных ключей.
            load_pki_peers(peers)

        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Invalid PKI node", str(exc))
            return

        self._settings["channels"] = channels
        self._settings["pki"] = {
            "own_node_id": self.own_node_id.text().strip(),
            "private_key": self.private_key.text().strip(),
            "peers": peers,
        }
        self._settings.setdefault("integrations", {})
        self._settings["integrations"]["telegram"] = {
            "enabled": self.telegram_enabled.isChecked(),
            "bot_token": self.telegram_bot_token.text().strip(),
            "chat_id": self.telegram_chat_id.text().strip(),
        }
        super().accept()

    def settings(self) -> dict:
        return deepcopy(self._settings)
