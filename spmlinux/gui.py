"""Native Qt GUI for the Spektrum Programmer port.

Mirrors the structure of Horizon's Windows application:

    Dashboard        device discovery / connection / device identity
    Models           the model list (import / export / delete .srm files)
    Model Settings   edit a model file: metadata, hex editor, AS3X schema
    Schema Reference the model-memory schema extracted from the Windows app
    Protocol Log     live view of the frames sent to / received from a device
    Settings         paths and cable parameters

Requires PySide6 (Arch: `pyside6`).  The GUI is optional - everything it
does is also reachable from the `spm` command line tool.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    from PySide6.QtCore import Qt, QTimer, Signal
    from PySide6.QtGui import QAction, QFont
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
        QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
        QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
        QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
        QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
    )
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


if HAVE_QT:
    from . import protocol, transport
    from .schema import parse_text
    from .srm import SrmModel

    HERE = Path(__file__).resolve().parent
    DEFAULT_SCHEMA = HERE / "model_memory.schema.txt"


def default_models_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "spmlinux" / "Models"


if HAVE_QT:

    class HexTable(QTableWidget):
        """Editable hex view of a byte buffer."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self._data = bytearray()
            self.setColumnCount(16)
            self.setHorizontalHeaderLabels([f"{i:02X}" for i in range(16)])
            self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            self.verticalHeader().setVisible(False)
            self.cellChanged.connect(self._cell_changed)

        def set_data(self, data: bytes):
            self._data = bytearray(data)
            rows = (len(self._data) + 15) // 16
            self.setRowCount(rows)
            self.blockSignals(True)
            for r in range(rows):
                for c in range(16):
                    off = r * 16 + c
                    item = QTableWidgetItem(
                        f"{self._data[off]:02X}" if off < len(self._data) else "")
                    item.setTextAlignment(Qt.AlignCenter)
                    self.setItem(r, c, item)
            self.blockSignals(False)

        def data(self) -> bytes:
            return bytes(self._data)

        def _cell_changed(self, row: int, col: int):
            item = self.item(row, col)
            if item is None:
                return
            off = row * 16 + col
            if off >= len(self._data):
                return
            try:
                value = int(item.text(), 16)
            except ValueError:
                return
            if not 0 <= value <= 0xFF:
                return
            self._data[off] = value
            item.setText(f"{value:02X}")


    class DashboardPage(QWidget):
        message = Signal(str)

        def __init__(self, parent=None):
            super().__init__(parent)
            self.transport = None
            lay = QVBoxLayout(self)

            box = QGroupBox("Programming cable")
            form = QFormLayout(box)
            self.device_combo = QComboBox()
            self.device_combo.addItem("no FTDI device found")
            form.addRow("Device:", self.device_combo)
            self.refresh_btn = QPushButton("Refresh device list")
            self.connect_btn = QPushButton("Connect")
            self.connect_btn.setEnabled(False)
            form.addRow(self.refresh_btn, self.connect_btn)
            lay.addWidget(box)

            info = QGroupBox("Device identity")
            self.info = QTableWidget(0, 2)
            self.info.setHorizontalHeaderLabels(["Property", "Value"])
            self.info.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            self.info.verticalHeader().setVisible(False)
            form2 = QFormLayout(info)
            lay.addWidget(info)

            self.status = QLabel("Not connected.")
            lay.addWidget(self.status)
            lay.addStretch(1)

            self.refresh_btn.clicked.connect(self.refresh_devices)
            self.connect_btn.clicked.connect(self.toggle_connection)
            self.refresh_devices()

        def refresh_devices(self):
            self.device_combo.clear()
            try:
                devs = transport.list_ftdi_devices()
            except transport.TransportError as e:
                self.device_combo.addItem(f"({e})")
                devs = []
            if not devs:
                self.device_combo.addItem("no FTDI device found")
                self.connect_btn.setEnabled(False)
                return
            for d in devs:
                self.device_combo.addItem(
                    f"{d.vid:04x}:{d.pid:04x}  {d.description or ''}  "
                    f"sn={d.serial or '-'}")
            self.connect_btn.setEnabled(True)

        def toggle_connection(self):
            if self.transport is not None:
                self.transport.close()
                self.transport = None
                self.connect_btn.setText("Connect")
                self.status.setText("Not connected.")
                self.info.setRowCount(0)
                return
            try:
                self.transport = transport.FtdiTransport()
            except transport.TransportError as e:
                self.status.setText(f"Could not open the cable: {e}")
                QMessageBox.warning(self, "Spektrum Programmer",
                                    f"Could not open the cable:\n\n{e}")
                return
            self.connect_btn.setText("Disconnect")
            self.status.setText("Cable opened.  The AS3X handshake is not "
                                 "implemented yet - see REPORT.md.")
            self.info.setRowCount(4)
            for r, (k, v) in enumerate([
                    ("Transport", "FTDI D2XX (115200 8N1)"),
                    ("Device", self.device_combo.currentText()),
                    ("Serial number", "read on connect"),
                    ("Product code", "read on connect")]):
                self.info.setItem(r, 0, QTableWidgetItem(k))
                self.info.setItem(r, 1, QTableWidgetItem(v))


    class ModelsPage(QWidget):
        open_requested = Signal(str)

        def __init__(self, models_dir: Path, parent=None):
            super().__init__(parent)
            self.models_dir = models_dir
            self.models_dir.mkdir(parents=True, exist_ok=True)
            self._seed_defaults()
            lay = QVBoxLayout(self)

            self.table = QTableWidget(0, 4)
            self.table.setHorizontalHeaderLabels(
                ["Name", "Product code", "Parameter version", "Size"])
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            self.table.setSelectionBehavior(QTableWidget.SelectRows)
            self.table.setEditTriggers(QTableWidget.NoEditTriggers)
            self.table.doubleClicked.connect(self._open_selected)
            lay.addWidget(self.table)

            row = QHBoxLayout()
            for text, slot in (("Import...", self.import_model),
                               ("Export...", self.export_model),
                               ("Delete", self.delete_model),
                               ("Open in editor", self._open_selected),
                               ("Refresh", self.refresh)):
                b = QPushButton(text)
                b.clicked.connect(slot)
                row.addWidget(b)
            row.addStretch(1)
            lay.addLayout(row)
            self.refresh()

        def _seed_defaults(self):
            """Copy the models shipped with the app into the user's folder."""
            src = HERE / "defaultmodels"
            if not src.is_dir():
                return
            for f in src.glob("*.srm"):
                dst = self.models_dir / f.name
                if not dst.exists():
                    dst.write_bytes(f.read_bytes())

        def refresh(self):
            self.table.setRowCount(0)
            for path in sorted(self.models_dir.glob("*.srm")):
                try:
                    m = SrmModel.load(path)
                except Exception as e:  # noqa: BLE001
                    continue
                r = self.table.rowCount()
                self.table.insertRow(r)
                self.table.setItem(r, 0, QTableWidgetItem(m.name))
                self.table.setItem(r, 1, QTableWidgetItem(
                    f"0x{m.device_product_code:02X}"
                    if m.device_product_code is not None else "-"))
                self.table.setItem(r, 2, QTableWidgetItem(
                    f"0x{m.parameter_version:02X}"
                    if m.parameter_version is not None else "-"))
                self.table.setItem(r, 3, QTableWidgetItem(
                    f"{len(m.bank('0')) if '0' in m.raw else 0} bytes"))
                self.table.item(r, 0).setData(Qt.UserRole, str(path))

        def selected_path(self) -> Path | None:
            row = self.table.currentRow()
            if row < 0:
                return None
            item = self.table.item(row, 0)
            return Path(item.data(Qt.UserRole)) if item else None

        def import_model(self):
            files, _ = QFileDialog.getOpenFileNames(
                self, "Import model files", str(Path.home()),
                "Spektrum model (*.srm)")
            for f in files:
                src = Path(f)
                dst = self.models_dir / src.name
                try:
                    SrmModel.load(src)
                except Exception as e:  # noqa: BLE001
                    QMessageBox.warning(self, "Import failed", str(e))
                    continue
                dst.write_bytes(src.read_bytes())
            self.refresh()

        def export_model(self):
            path = self.selected_path()
            if path is None:
                return
            dst, _ = QFileDialog.getSaveFileName(
                self, "Export model", str(path), "Spektrum model (*.srm)")
            if dst:
                Path(dst).write_bytes(path.read_bytes())

        def delete_model(self):
            path = self.selected_path()
            if path is None:
                return
            if QMessageBox.question(self, "Delete model",
                                    f"Delete {path.name}?") == QMessageBox.Yes:
                path.unlink(missing_ok=True)
                self.refresh()

        def _open_selected(self):
            path = self.selected_path()
            if path:
                self.open_requested.emit(str(path))


    class EditorPage(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            lay = QVBoxLayout(self)
            self.path: Path | None = None
            self.model: SrmModel | None = None

            form = QFormLayout()
            self.name_edit = QLineEdit()
            self.pc_edit = QLineEdit()
            self.pv_edit = QLineEdit()
            form.addRow("Name:", self.name_edit)
            form.addRow("Device product code:", self.pc_edit)
            form.addRow("Parameter version:", self.pv_edit)
            lay.addLayout(form)

            self.hex = HexTable()
            lay.addWidget(self.hex)

            buttons = QHBoxLayout()
            save = QPushButton("Save")
            save.clicked.connect(self.save)
            buttons.addWidget(save)
            buttons.addStretch(1)
            lay.addLayout(buttons)

        def load(self, path: str):
            self.path = Path(path)
            self.model = SrmModel.load(self.path)
            self.name_edit.setText(self.model.name)
            self.pc_edit.setText(
                f"0x{self.model.device_product_code:02X}"
                if self.model.device_product_code is not None else "")
            self.pv_edit.setText(
                f"0x{self.model.parameter_version:02X}"
                if self.model.parameter_version is not None else "")
            self.hex.set_data(self.model.bank("0") if "0" in self.model.raw else b"")

        def save(self):
            if self.model is None or self.path is None:
                return
            self.model.name = self.name_edit.text() or "untitled model"
            for attr, edit in (("device_product_code", self.pc_edit),
                               ("parameter_version", self.pv_edit)):
                txt = edit.text().strip()
                if not txt:
                    setattr(self.model, attr, None)
                else:
                    try:
                        setattr(self.model, attr, int(txt, 0))
                    except ValueError:
                        QMessageBox.warning(self, "Save failed",
                                            f"{edit.text()!r} is not a number")
                        return
            if "0" in self.model.raw:
                self.model.set_bank("0", self.hex.data())
            self.model.save(self.path)


    class SchemaPage(QWidget):
        """The model-memory schema extracted from the Windows application."""

        def __init__(self, parent=None):
            super().__init__(parent)
            lay = QVBoxLayout(self)
            note = QLabel(
                "Model-memory schema extracted from SpmProgrammer.exe.  "
                "The offsets are those of the AS3X structure on the device; "
                "a .srm file stores the device's own model image, so the two "
                "are not the same layout.")
            note.setWordWrap(True)
            lay.addWidget(note)
            self.tree = QTreeWidget()
            self.tree.setHeaderLabels(["Field", "Type", "Offset", "Size", "Path"])
            lay.addWidget(self.tree)
            self.reload()

        def reload(self):
            self.tree.clear()
            if not DEFAULT_SCHEMA.exists():
                return
            blocks = parse_text(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
            for b in blocks:
                top = QTreeWidgetItem(self.tree, [
                    b.name, "block" if b.is_block else b.type,
                    str(b.offset), str(b.size), b.name])
                self._fill(b, top)

        def _fill(self, node, parent_item):
            for c in node.children:
                item = QTreeWidgetItem(parent_item, [
                    c.name,
                    "block" if c.is_block else (c.type or ""),
                    str(c.offset), str(c.size), c.path()])
                if c.bits:
                    item.setText(1, item.text(1) + " {" +
                                 ", ".join(f"{b.name}:{b.start}..{b.end}"
                                           for b in c.bits) + "}")
                self._fill(c, item)


    class LogPage(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            lay = QVBoxLayout(self)
            self.table = QTableWidget(0, 5)
            self.table.setHorizontalHeaderLabels(
                ["Time", "Direction", "Kind", "CRC", "Data"])
            self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
            self.table.verticalHeader().setVisible(False)
            lay.addWidget(self.table)
            clear = QPushButton("Clear")
            clear.clicked.connect(lambda: self.table.setRowCount(0))
            lay.addWidget(clear)

        def add_frame(self, direction: str, frame):
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(
                __import__("time").strftime("%H:%M:%S")))
            self.table.setItem(r, 1, QTableWidgetItem(direction))
            self.table.setItem(r, 2, QTableWidgetItem(frame.kind))
            self.table.setItem(r, 3, QTableWidgetItem(
                "ok" if frame.checksum_ok else "BAD"))
            self.table.setItem(r, 4, QTableWidgetItem(frame.raw.hex(" ")))


    class SettingsPage(QWidget):
        def __init__(self, models_dir: Path, parent=None):
            super().__init__(parent)
            lay = QVBoxLayout(self)
            form = QFormLayout()
            self.models_dir = QLineEdit(str(models_dir))
            form.addRow("Models folder:", self.models_dir)
            self.pid = QLineEdit("0x7C48")
            form.addRow("FTDI product id:", self.pid)
            self.index = QSpinBox()
            self.index.setRange(0, 15)
            form.addRow("FTDI device index:", self.index)
            self.baud = QSpinBox()
            self.baud.setRange(1200, 921600)
            self.baud.setValue(115200)
            form.addRow("Baud rate:", self.baud)
            self.bitmode = QCheckBox("set FTDI bit mode 0x88/0x20 (as the Windows app)")
            self.bitmode.setChecked(True)
            form.addRow(self.bitmode)
            lay.addLayout(form)
            lay.addStretch(1)


    class MainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Spektrum Programmer (native Linux port)")
            self.resize(1100, 720)

            self.models_dir = default_models_dir()
            self.stack = QStackedWidget()
            nav = QListWidget()
            nav.setMaximumWidth(180)
            for name in ("Dashboard", "Models", "Model Settings",
                         "Schema Reference", "Protocol Log", "Settings"):
                nav.addItem(name)

            self.dashboard = DashboardPage()
            self.models = ModelsPage(self.models_dir)
            self.editor = EditorPage()
            self.schema = SchemaPage()
            self.log = LogPage()
            self.settings = SettingsPage(self.models_dir)

            for page in (self.dashboard, self.models, self.editor,
                         self.schema, self.log, self.settings):
                self.stack.addWidget(page)

            nav.currentRowChanged.connect(self.stack.setCurrentIndex)
            nav.setCurrentRow(0)

            central = QWidget()
            lay = QHBoxLayout(central)
            lay.addWidget(nav)
            lay.addWidget(self.stack, 1)
            self.setCentralWidget(central)

            self.models.open_requested.connect(self._open_model)

            bar = QHBoxLayout()
            self.status_label = QLabel("Ready")
            bar.addWidget(self.status_label)
            bar.addStretch(1)
            quit_btn = QPushButton("Quit")
            quit_btn.clicked.connect(self.close)
            bar.addWidget(quit_btn)
            from PySide6.QtWidgets import QStatusBar
            sb = QStatusBar()
            sb.addWidget(self.status_label)
            sb.addPermanentWidget(quit_btn)
            self.setStatusBar(sb)

        def _open_model(self, path: str):
            try:
                self.editor.load(path)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "Could not open model", str(e))
                return
            self.stack.setCurrentIndex(2)
            self.status_label.setText(f"Editing {Path(path).name}")


def main(argv=None) -> int:
    if not HAVE_QT:
        print("PySide6 is required for the GUI:  pacman -S pyside6", file=sys.stderr)
        return 1
    app = QApplication(argv or sys.argv)
    app.setApplicationName("Spektrum Programmer")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
