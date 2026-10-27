import json

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFileDialog,
    QLineEdit,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QHeaderView,
    QTableWidget,
    QVBoxLayout,
    QWidget,
    QTableWidgetItem,
    QMessageBox,
    QSpinBox,
)

from PySide6.QtCore import QDateTime, Qt

from config import SAMPLE_RATE_HZ

from meshtastic_config import (
    MODEM_PRESETS,
    REGIONS,
    REGION_PRESETS,
    get_num_slots,
    get_slot_frequency,
)

from stream_worker import SDRWorker
from capture_analysis_worker import CaptureAnalysisWorker

from app_settings import default_settings, load_settings, save_settings
from settings_dialog import SettingsDialog


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Meshtastic SDR Decoder")
        self.resize(1000, 700)
        
        self.settings_load_error = None
        try:
            self.app_settings = load_settings()
        except Exception as exc:
            # Preserve a malformed settings file rather than silently overwriting it.
            self.app_settings = default_settings()
            self.settings_load_error = str(exc)

        self.setup_ui()
        self.update_presets()
        self.update_slots()
        self.worker = None
        
        self._restore_ui_settings()
        
        self.update_source_mode()
        
        self._connect_settings_autosave()

        if self.settings_load_error:
            QMessageBox.warning(
                self,
                "Settings could not be loaded",
                "The settings file could not be read, so default values are in use. "
                "The existing file has not been overwritten.\n\n"
                + self.settings_load_error,
            )
        

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)

        # Analysis source: live RTL-SDR or a previously recorded IQ capture.
        source_group = QGroupBox("Analysis source")
        source_layout = QFormLayout()
        self.source_combo = QComboBox()
        self.source_combo.addItem("Live RTL-SDR", "sdr")
        self.source_combo.addItem("Recorded IQ file (.npy)", "file")
        self.source_combo.currentIndexChanged.connect(self.update_source_mode)
        source_layout.addRow("Source:", self.source_combo)

        file_row = QHBoxLayout()
        self.capture_path_edit = QLineEdit()
        self.capture_path_edit.setPlaceholderText("Select a complex IQ .npy file...")
        self.browse_capture_button = QPushButton("Browse...")
        self.browse_capture_button.clicked.connect(self.browse_capture_file)
        file_row.addWidget(self.capture_path_edit)
        file_row.addWidget(self.browse_capture_button)
        self.capture_file_row = QWidget()
        self.capture_file_row.setLayout(file_row)
        source_layout.addRow("Capture file:", self.capture_file_row)
        source_group.setLayout(source_layout)
        main_layout.addWidget(source_group)

        # ---------------------------------------------------------------
        # Meshtastic settings
        # ---------------------------------------------------------------

        settings_group = QGroupBox("Meshtastic")
        settings_layout = QFormLayout()

        # Region
        self.region_combo = QComboBox()

        for region_name in REGIONS:
            self.region_combo.addItem(
                REGIONS[region_name]["name"],
                region_name,
            )

        self.region_combo.currentIndexChanged.connect(
            self.update_presets
        )

        settings_layout.addRow(
            "Region:",
            self.region_combo,
        )

        # Modem preset
        self.preset_combo = QComboBox()

        self.preset_combo.currentIndexChanged.connect(
            self.update_slots
        )

        settings_layout.addRow(
            "Modem preset:",
            self.preset_combo,
        )

        # Frequency slot
        self.slot_combo = QComboBox()

        self.slot_combo.currentIndexChanged.connect(
            self.update_frequency
        )

        settings_layout.addRow(
            "Frequency slot:",
            self.slot_combo,
        )

        # Frequency
        self.frequency_label = QLabel("—")

        settings_layout.addRow(
            "Frequency:",
            self.frequency_label,
        )

        # Bandwidth
        self.bandwidth_label = QLabel("—")

        settings_layout.addRow(
            "Bandwidth:",
            self.bandwidth_label,
        )

        # Spreading factor
        self.sf_label = QLabel("—")

        settings_layout.addRow(
            "Spreading factor:",
            self.sf_label,
        )

        # Coding rate
        self.cr_label = QLabel("—")

        settings_layout.addRow(
            "Coding rate:",
            self.cr_label,
        )

        settings_group.setLayout(settings_layout)
        main_layout.addWidget(settings_group)

        # ---------------------------------------------------------------
        # SDR settings
        # ---------------------------------------------------------------

        self.sdr_group = QGroupBox("SDR")
        sdr_layout = QFormLayout()

        self.gain_combo = QComboBox()

        self.gain_combo.addItems([
            "Auto",
            "0 dB",
            "12 dB",
            "20 dB",
            "30 dB",
            "40 dB",
            "48 dB",
        ])

        sdr_layout.addRow(
            "Gain:",
            self.gain_combo,
        )

        self.sdr_group.setLayout(sdr_layout)
        main_layout.addWidget(self.sdr_group)

        # ---------------------------------------------------------------
        # Controls
        # ---------------------------------------------------------------

        controls_layout = QHBoxLayout()

        self.start_button = QPushButton("Start")
        self.stop_button = QPushButton("Stop")
        self.clear_button = QPushButton("Clear")
        
        self.settings_button = QPushButton("Settings...")
        self.settings_button.clicked.connect(self.open_settings_dialog)
        
        self.start_button.clicked.connect(
            self.start_sdr
        )

        self.stop_button.clicked.connect(
            self.stop_sdr
        )
        
        self.clear_button.clicked.connect(self.clear_packets)

        self.stop_button.setEnabled(False)

        controls_layout.addWidget(self.start_button)
        controls_layout.addWidget(self.stop_button)
        controls_layout.addWidget(self.clear_button)
        controls_layout.addWidget(self.settings_button)
        controls_layout.addStretch()

        main_layout.addLayout(controls_layout)

        # ---------------------------------------------------------------
        # Status
        # ---------------------------------------------------------------

        status_layout = QHBoxLayout()

        status_layout.addWidget(QLabel("Status:"))

        self.status_label = QLabel("Stopped")

        status_layout.addWidget(self.status_label)
        status_layout.addStretch()

        main_layout.addLayout(status_layout)

        # ---------------------------------------------------------------
        # Packets
        # ---------------------------------------------------------------

        packets_group = QGroupBox("Packets")
        packets_layout = QVBoxLayout()

        self.packet_table = QTableWidget()
        self.packet_table.setColumnCount(10)
        self.packet_table.setHorizontalHeaderLabels([
            "Time",
            "Packet ID",
            "From",
            "To",
            "Channel",
            "Hops",
            "Type",
            "Application",
            "Message",
            "Details",
        ])

        self.packet_table.setRowCount(0)

        self.packet_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        
        self.packet_table.setWordWrap(True)
        self.packet_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        
        header = self.packet_table.horizontalHeader()

        # По умолчанию ширину можно менять вручную.
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

        # Столбцы с длинным текстом получают оставшееся место.
        header.setSectionResizeMode(
            8, QHeaderView.ResizeMode.Stretch
        )  # Message

        header.setSectionResizeMode(
            9, QHeaderView.ResizeMode.Stretch
        )  # Details

        # Начальная ширина коротких столбцов.
        self.packet_table.setColumnWidth(0, 85)   # Time
        self.packet_table.setColumnWidth(1, 110)  # Packet ID
        self.packet_table.setColumnWidth(2, 100)  # From
        self.packet_table.setColumnWidth(3, 100)  # To
        self.packet_table.setColumnWidth(4, 120)  # Channel
        self.packet_table.setColumnWidth(5, 55)   # Hops
        self.packet_table.setColumnWidth(6, 110)  # Type
        self.packet_table.setColumnWidth(7, 140)  # Application

        packets_layout.addWidget(self.packet_table)

        packets_group.setLayout(packets_layout)

        main_layout.addWidget(packets_group)
        
        
    def _restore_ui_settings(self):
        """Restore previously selected UI values after combo boxes are populated."""
        ui = self.app_settings.get("ui", {})

        def select_data(combo, value):
            if value is None:
                return
            index = combo.findData(value)
            if index >= 0:
                combo.setCurrentIndex(index)

        select_data(self.region_combo, ui.get("region"))
        # Region change repopulates modem presets and slots.
        self.update_presets()
        select_data(self.preset_combo, ui.get("modem_preset"))
        self.update_slots()
        select_data(self.slot_combo, ui.get("frequency_slot"))
        select_data(self.source_combo, ui.get("source"))
        select_data(self.gain_combo, ui.get("gain"))
        self.capture_path_edit.setText(str(ui.get("capture_path", "")))

    def _connect_settings_autosave(self):
        """Save UI selections when the user changes them."""
        self.region_combo.currentIndexChanged.connect(self.save_ui_settings)
        self.preset_combo.currentIndexChanged.connect(self.save_ui_settings)
        self.slot_combo.currentIndexChanged.connect(self.save_ui_settings)
        self.gain_combo.currentIndexChanged.connect(self.save_ui_settings)
        self.source_combo.currentIndexChanged.connect(self.save_ui_settings)
        self.capture_path_edit.editingFinished.connect(self.save_ui_settings)

    def save_ui_settings(self, *_):
        ui = self.app_settings.setdefault("ui", {})
        ui.update({
            "region": self.region_combo.currentData(),
            "modem_preset": self.preset_combo.currentData(),
            "frequency_slot": self.slot_combo.currentData() or 0,
            "gain": self.gain_combo.currentText(),
            "source": self.source_combo.currentData(),
            "capture_path": self.capture_path_edit.text().strip(),
        })
        try:
            save_settings(self.app_settings)
        except OSError as exc:
            self.status_label.setText(f"Could not save settings: {exc}")

    def open_settings_dialog(self):
        dialog = SettingsDialog(self.app_settings, self)
        if dialog.exec():
            self.app_settings = dialog.settings()
            self.save_ui_settings()
            try:
                save_settings(self.app_settings)
                self.status_label.setText("Settings saved")
            except OSError as exc:
                QMessageBox.critical(
                    self, "Settings could not be saved", str(exc)
                )
        

    # -------------------------------------------------------------------
    # Meshtastic configuration
    # -------------------------------------------------------------------

    def update_presets(self):
        """Update modem presets when region changes."""

        region_name = self.region_combo.currentData()

        if not region_name:
            return

        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()

        for preset_name in REGION_PRESETS.get(region_name, []):
            self.preset_combo.addItem(
                preset_name,
                preset_name,
            )

        self.preset_combo.blockSignals(False)

        self.update_slots()

    def update_slots(self):
        """Update frequency slots when preset changes."""

        region_name = self.region_combo.currentData()
        preset_name = self.preset_combo.currentData()

        if not region_name or not preset_name:
            return

        self.slot_combo.blockSignals(True)
        self.slot_combo.clear()

        num_slots = get_num_slots(
            region_name,
            preset_name,
        )

        for slot in range(num_slots):
            frequency = get_slot_frequency(
                region_name,
                preset_name,
                slot,
            )

            self.slot_combo.addItem(
                f"Slot {slot} — {frequency:.6f} MHz",
                slot,
            )

        self.slot_combo.blockSignals(False)

        self.update_frequency()

    def update_frequency(self):
        """Update displayed frequency and modem parameters."""

        region_name = self.region_combo.currentData()
        preset_name = self.preset_combo.currentData()
        slot = self.slot_combo.currentData()

        if (
            not region_name
            or not preset_name
            or slot is None
        ):
            return

        frequency = get_slot_frequency(
            region_name,
            preset_name,
            slot,
        )

        preset = MODEM_PRESETS[preset_name]

        self.frequency_label.setText(
            f"{frequency:.6f} MHz"
        )

        self.bandwidth_label.setText(
            f"{preset['bandwidth']} kHz"
        )

        self.sf_label.setText(
            f"SF{preset['spreading_factor']}"
        )

        self.cr_label.setText(
            f"4/{preset['coding_rate']}"
        )
        
        
    def on_packet_decoded(self, packet):
        row = self.packet_table.rowCount()
        self.packet_table.insertRow(row)

        packet_time = QDateTime.currentDateTime().toString("HH:mm:ss")

        packet_id = packet.get("packet_id")
        packet_id_text = (
            f"0x{packet_id:08X}"
            if packet_id is not None
            else "—"
        )

        sender = f"!{packet['from']:08x}" if "from" in packet else "—"
        recipient = f"!{packet['to']:08x}" if "to" in packet else "—"

        channel_name = packet.get("channel_name")
        if not channel_name:
            channel_hash = packet.get("channel")
            channel_name = (
                f"Unknown (0x{channel_hash:02X})"
                if channel_hash is not None
                else "—"
            )

        hops = packet.get("hops")
        hops_text = str(hops) if hops is not None else "—"

        packet_type = packet.get(
            "type",
            packet.get("result", "UNKNOWN"),
        )
        application = packet.get("application", "unknown")
        message = packet.get("text", "")
        
        details = packet.get("details") or {}

        details_text = json.dumps(
            details,
            ensure_ascii=False,
        )

        values = [
            packet_time,
            packet_id_text,
            sender,
            recipient,
            channel_name,
            hops_text,
            packet_type,
            application,
            message,
            details_text,
        ]

        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))

            if column == 9:
                item.setToolTip(str(value))

            self.packet_table.setItem(row, column, item)
            
        self.packet_table.resizeRowToContents(row)
        self.packet_table.scrollToBottom()
        
    
    # -------------------------------------------------------------------
    # SDR
    # -------------------------------------------------------------------

    def update_source_mode(self, *_):
        """Show controls relevant to the selected analysis source."""
        file_mode = self.source_combo.currentData() == "file"
        self.capture_file_row.setVisible(file_mode)
        self.sdr_group.setEnabled(not file_mode)
        self.frequency_label.setEnabled(True)
        self.start_button.setText("Analyze file" if file_mode else "Start")

    def browse_capture_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select recorded IQ capture",
            "",
            "NumPy IQ files (*.npy);;All files (*)",
        )
        if path:
            self.capture_path_edit.setText(path)

    def start_sdr(self):
        """Start live reception or analyze a saved IQ capture."""
        if self.worker is not None:
            return

        if self.source_combo.currentData() == "file":
            self.start_file_analysis()
            return

        frequency_text = self.frequency_label.text()

        if frequency_text == "—":
            return

        region_name = self.region_combo.currentData()
        preset_name = self.preset_combo.currentData()
        slot = self.slot_combo.currentData()
        
        preset = MODEM_PRESETS[preset_name]
        bandwidth = preset["bandwidth"] * 1000
        spreading_factor = preset["spreading_factor"]
        coding_rate = preset["coding_rate"]

        if (
            not region_name
            or not preset_name
            or slot is None
        ):
            return

        center_freq = (
            get_slot_frequency(
                region_name,
                preset_name,
                slot,
            )
            * 1_000_000
        )

        gain_text = self.gain_combo.currentText()

        if gain_text == "Auto":
            gain = "auto"
        else:
            gain = float(
                gain_text.replace(" dB", "")
            )
            
        print(f"GUI selected gain: {gain!r}")

        self.status_label.setText(
            "Starting..."
        )

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

        self.worker = SDRWorker(
            center_freq=center_freq,
            sample_rate=SAMPLE_RATE_HZ,
            gain=gain,
            bandwidth=bandwidth,
            spreading_factor=spreading_factor,
            coding_rate=coding_rate,
        )

        self.worker.status.connect(
            self.on_sdr_status
        )

        self.worker.error.connect(
            self.on_sdr_error
        )

        self.worker.iq_block.connect(
            self.on_iq_block
        )
        
        self.worker.packet_decoded.connect(self.on_packet_decoded)

        self.worker.finished.connect(
            self.on_worker_finished
        )

        self.worker.start()


    def start_file_analysis(self):
        """Analyze the selected .npy file in a background thread."""
        capture_path = self.capture_path_edit.text().strip()
        if not capture_path:
            self.status_label.setText("Select an IQ capture file first")
            return

        region_name = self.region_combo.currentData()
        preset_name = self.preset_combo.currentData()
        if not region_name or not preset_name:
            self.status_label.setText("Select a region and modem preset")
            return

        preset = MODEM_PRESETS[preset_name]
        bandwidth = preset["bandwidth"] * 1000
        spreading_factor = preset["spreading_factor"]
        coding_rate = preset["coding_rate"]

        print(f"[GUI] Starting saved-capture analysis: {capture_path}", flush=True)
        self.status_label.setText("Preparing file analysis...")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        

        self.worker = CaptureAnalysisWorker(
            capture_path=capture_path,
            bandwidth=bandwidth,
            spreading_factor=spreading_factor,
            sample_rate=SAMPLE_RATE_HZ,
            coding_rate=coding_rate
        )
        self.worker.status.connect(self.on_sdr_status)
        self.worker.error.connect(self.on_sdr_error)
        self.worker.packet_decoded.connect(self.on_packet_decoded)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()

    def stop_sdr(self):
        """Stop RTL-SDR stream."""

        if self.worker is None:
            return

        self.status_label.setText(
            "Stopping..."
        )

        self.worker.stop()

    def on_sdr_status(self, status):
        self.status_label.setText(status)

    def on_sdr_error(self, message):
        print(f"[ERROR] {message}", flush=True)
        self.status_label.setText(f"Error: {message}")

        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def on_worker_finished(self):
        finished_worker = self.worker
        was_file_analysis = isinstance(finished_worker, CaptureAnalysisWorker)
        self.worker = None

        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

        if was_file_analysis:
            self.status_label.setText("File analysis finished")
        else:
            self.status_label.setText("Stopped")

    def on_iq_block(self, samples):
        """Receive one IQ block from RTL-SDR."""

        # Пока только показываем факт получения данных.
        # Декодер подключим следующим этапом.

        self.status_label.setText(
            f"Receiving IQ: {len(samples):,} samples"
        )
        
    
    def clear_packets(self):
        """Clear the packet table."""
        self.packet_table.setRowCount(0)