"""Settings dialog: editable channels and one PKI node pair.

The values are persisted only; decoder integration is a separate next step.
"""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)


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

        # PKI tab — one local node and one peer for the current implementation.
        pki_page = QWidget()
        pki_form = QFormLayout(pki_page)
        self.own_node_id = QLineEdit()
        self.own_node_id.setPlaceholderText("e.g. !xxxxxxxx")
        self.private_key = QLineEdit()
        self.private_key.setPlaceholderText("Base64 or 64 hexadecimal characters")
        self.private_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.peer_node_id = QLineEdit()
        self.peer_node_id.setPlaceholderText("e.g. !xxxxxxxx")
        self.peer_public_key = QLineEdit()
        self.peer_public_key.setPlaceholderText("Base64 X25519 public key")
        pki_form.addRow("My node ID:", self.own_node_id)
        pki_form.addRow("My private key:", self.private_key)
        pki_form.addRow("Peer node ID:", self.peer_node_id)
        pki_form.addRow("Peer public key:", self.peer_public_key)
        warning = QLabel(
            "Note: these fields are saved locally in settings.json as plain text. "
            "Anyone with access to your Windows account files may be able to read them. "
            "They are not used by the decoder yet."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #a66a00;")
        pki_form.addRow(warning)
        tabs.addTab(pki_page, "PKI")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._populate()

    def _populate(self):
        pki = self._settings.get("pki", {})
        self.own_node_id.setText(str(pki.get("own_node_id", "")))
        self.private_key.setText(str(pki.get("private_key", "")))
        self.peer_node_id.setText(str(pki.get("peer_node_id", "")))
        self.peer_public_key.setText(str(pki.get("peer_public_key", "")))
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

        self._settings["channels"] = channels
        self._settings["pki"] = {
            "own_node_id": self.own_node_id.text().strip(),
            "private_key": self.private_key.text().strip(),
            "peer_node_id": self.peer_node_id.text().strip(),
            "peer_public_key": self.peer_public_key.text().strip(),
        }
        super().accept()

    def settings(self) -> dict:
        return deepcopy(self._settings)
