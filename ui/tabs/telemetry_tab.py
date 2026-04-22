# ui/tabs/telemetry_tab.py
# Вкладка телеметрии: текущие значения сигналов + таблица последних записей
# + управление логгером и экспорт CSV.

import csv
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QFileDialog, QSpinBox, QComboBox, QMessageBox, QGroupBox,
)

from core import ethercat_driver
from core.telemetry import TelemetryLogger, SIGNALS

# Расчётные колонки, которых нет в SIGNALS, но они есть в БД (см. telemetry.py)
DERIVED_SIGNALS = [
    ('current_A', 'Current actual (A) = current × rated_current / 1e6'),
    ('torque_Nm', 'Torque actual (N·m) = torque × rated_torque / 1e6'),
]


DB_PATH = Path("telemetry.sqlite3")


class TelemetryTab(QWidget):
    """Вкладка мониторинга привода.

    Получает контроллер через mode_controller (как и manual_tab).
    Логгер создаётся/останавливается кнопками. UI обновляется по таймеру.
    """

    def __init__(self, controller):
        super().__init__()
        self.mode_controller = controller
        self.logger = None

        self._build_ui()

        # Таймер для обновления виджетов
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh)
        self._refresh_timer.start(500)   # 2 Гц — достаточно для чтения из БД

    # ==== UI ====
    def _build_ui(self):
        root = QVBoxLayout(self)

        # --- Control row ---
        ctrl_row = QHBoxLayout()
        self.start_btn = QPushButton("▶ Старт логирования")
        self.stop_btn = QPushButton("■ Стоп")
        self.stop_btn.setEnabled(False)

        self.period_spin = QSpinBox()
        self.period_spin.setRange(50, 2000)
        self.period_spin.setSingleStep(50)
        self.period_spin.setValue(100)
        self.period_spin.setSuffix(" мс")

        self.status_lbl = QLabel("Статус: не запущено")

        ctrl_row.addWidget(QLabel("Период:"))
        ctrl_row.addWidget(self.period_spin)
        ctrl_row.addWidget(self.start_btn)
        ctrl_row.addWidget(self.stop_btn)
        ctrl_row.addStretch(1)
        ctrl_row.addWidget(self.status_lbl)
        root.addLayout(ctrl_row)

        self.start_btn.clicked.connect(self._on_start)
        self.stop_btn.clicked.connect(self._on_stop)

        # --- Current values (grid of labels) ---
        box = QGroupBox("Текущие значения")
        grid = QGridLayout(box)
        self.value_labels = {}
        all_rows = [(name, desc) for (_i, _s, _f, name, desc) in SIGNALS] \
                   + DERIVED_SIGNALS
        for row, (name, desc) in enumerate(all_rows):
            cap = QLabel(f"<b>{name}</b>")
            cap.setToolTip(desc)
            val = QLabel("—")
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            val.setMinimumWidth(120)
            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet("color: gray;")
            grid.addWidget(cap,     row, 0)
            grid.addWidget(val,     row, 1)
            grid.addWidget(desc_lbl, row, 2)
            self.value_labels[name] = val
        root.addWidget(box)

        # --- Recent samples table + export ---
        tbl_row = QHBoxLayout()
        tbl_row.addWidget(QLabel("Показать последние:"))
        self.limit_combo = QComboBox()
        for n in ("50", "200", "1000", "5000"):
            self.limit_combo.addItem(n)
        self.limit_combo.setCurrentText("200")
        tbl_row.addWidget(self.limit_combo)

        self.refresh_btn = QPushButton("Обновить таблицу")
        self.export_btn = QPushButton("Экспорт CSV…")
        self.clear_btn = QPushButton("Очистить БД")
        tbl_row.addWidget(self.refresh_btn)
        tbl_row.addWidget(self.export_btn)
        tbl_row.addStretch(1)
        tbl_row.addWidget(self.clear_btn)
        root.addLayout(tbl_row)

        self.refresh_btn.clicked.connect(self._load_table)
        self.export_btn.clicked.connect(self._export_csv)
        self.clear_btn.clicked.connect(self._clear_db)

        self.table = QTableWidget(0, 0, self)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        root.addWidget(self.table, stretch=1)

    # ==== Actions ====
    def _get_controller(self):
        if hasattr(self.mode_controller, 'get_master'):
            return self.mode_controller.get_master()
        return None

    def _on_start(self):
        controller = self._get_controller()
        if not isinstance(controller, ethercat_driver.EtherCATController):
            QMessageBox.warning(self, "Телеметрия",
                                "Сначала подключитесь к приводу во вкладке «Подключение».")
            return
        try:
            self.logger = TelemetryLogger(
                controller,
                db_path=str(DB_PATH),
                period_s=self.period_spin.value() / 1000.0,
            )
            self.logger.start()
        except Exception as e:
            QMessageBox.critical(self, "Телеметрия", f"Не удалось запустить: {e}")
            self.logger = None
            return

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.period_spin.setEnabled(False)
        self.status_lbl.setText(f"Статус: пишу в {DB_PATH}")

    def _on_stop(self):
        if self.logger:
            try:
                self.logger.stop()
            except Exception as e:
                print(f"[telemetry_tab] stop error: {e}")
            self.logger = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.period_spin.setEnabled(True)
        self.status_lbl.setText("Статус: остановлено")

    def _clear_db(self):
        reply = QMessageBox.question(
            self, "Очистить БД",
            f"Удалить все записи из {DB_PATH}?",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if not DB_PATH.exists():
            return
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DELETE FROM samples")
            conn.commit()
            conn.close()
            self._load_table()
        except Exception as e:
            QMessageBox.critical(self, "БД", f"Ошибка: {e}")

    def _export_csv(self):
        if not DB_PATH.exists():
            QMessageBox.information(self, "Экспорт", "Нет данных для экспорта.")
            return
        default = f"telemetry_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить CSV", default, "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.execute("SELECT * FROM samples ORDER BY ts")
            cols = [d[0] for d in cur.description]
            with open(path, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(cols)
                for row in cur:
                    w.writerow(row)
            conn.close()
            QMessageBox.information(self, "Экспорт", f"Готово: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Экспорт", f"Ошибка: {e}")

    # ==== Refreshers ====
    def _refresh(self):
        self._update_current()
        # автообновление таблицы, если логгер активен
        if self.logger is not None:
            self._load_table()

    def _update_current(self):
        """Обновить «текущие значения» — читаем последнюю запись из БД."""
        if not DB_PATH.exists():
            return
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.execute("SELECT * FROM samples ORDER BY ts DESC LIMIT 1")
            row = cur.fetchone()
            cols = [d[0] for d in cur.description]
            conn.close()
        except Exception:
            return
        if not row:
            return
        data = dict(zip(cols, row))
        for name, lbl in self.value_labels.items():
            v = data.get(name)
            if v is None:
                lbl.setText("—")
                lbl.setStyleSheet("color: gray;")
            else:
                if name == 'statusword':
                    lbl.setText(f"0x{v:04X}")
                elif name in ('current_A', 'torque_Nm'):
                    lbl.setText(f"{v:.4f}")
                else:
                    lbl.setText(str(v))
                lbl.setStyleSheet("")
        age = time.time() - data['ts']
        self.status_lbl.setText(
            f"Статус: {'пишу' if self.logger else 'остановлено'}  "
            f"(последняя запись {age:.1f} с назад)"
        )

    def _load_table(self):
        if not DB_PATH.exists():
            return
        try:
            limit = int(self.limit_combo.currentText())
        except ValueError:
            limit = 200
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.execute(
                "SELECT * FROM samples ORDER BY ts DESC LIMIT ?", (limit,)
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            conn.close()
        except Exception as e:
            print(f"[telemetry_tab] load error: {e}")
            return

        # Подменим ts на читаемую дату-время + оставим исходное значение в tooltip
        display_cols = ['time'] + cols[1:]
        self.table.setColumnCount(len(display_cols))
        self.table.setHorizontalHeaderLabels(display_cols)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            ts = row[0]
            time_str = datetime.fromtimestamp(ts).strftime('%H:%M:%S.%f')[:-3]
            t_item = QTableWidgetItem(time_str)
            t_item.setToolTip(f"{ts:.3f}")
            self.table.setItem(r, 0, t_item)
            for c, val in enumerate(row[1:], start=1):
                if val is None:
                    it = QTableWidgetItem("")
                    it.setForeground(QColor(150, 150, 150))
                elif cols[c] == 'statusword' and isinstance(val, int):
                    it = QTableWidgetItem(f"0x{val:04X}")
                elif cols[c] in ('current_A', 'torque_Nm') and isinstance(val, float):
                    it = QTableWidgetItem(f"{val:.4f}")
                else:
                    it = QTableWidgetItem(str(val))
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()

    # ==== Lifecycle ====
    def shutdown(self):
        """Вызывается при закрытии окна."""
        if self.logger is not None:
            try:
                self.logger.stop()
            except Exception:
                pass
            self.logger = None
