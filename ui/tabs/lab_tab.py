# ui/tabs/lab_tab.py
# Вкладка «Лабораторная запись»: метаданные эксперимента, паспорт стенда,
# управление логгером телеметрии и экспорт стандартизированного CSV.
#
# Вкладка НЕ управляет приводом: никакого Servo ON/OFF, никаких команд
# движения и никаких "опасных" автоматических сценариев. Оператор сам
# приводит стенд в нужный режим во вкладке «Ручное управление», а здесь
# только записывает телеметрию + метаданные.

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLabel, QPushButton, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QPlainTextEdit, QFileDialog, QMessageBox, QGroupBox, QCheckBox,
)

from core import ethercat_driver
from core.csv_schema import ALLOWED_REGIME_LABELS, ALLOWED_RISK_LABELS
from core.lab_session import LabSessionMetadata, append_experiment
from core.lab_export import export_samples_to_csv
from core.stand_passport import build_passport_with_autofill
from core.telemetry import TelemetryLogger


DEFAULT_LAB_DIR = Path("lab_data")
DEFAULT_RAW_DIR = DEFAULT_LAB_DIR / "raw"
DEFAULT_EXPERIMENTS_CSV = DEFAULT_LAB_DIR / "experiments.csv"
DEFAULT_STAND_PASSPORT_CSV = DEFAULT_LAB_DIR / "stand_passport.csv"


class LabTab(QWidget):
    """Лабораторная вкладка: запись + разметка + экспорт."""

    def __init__(self, controller):
        super().__init__()
        self.mode_controller = controller
        self.logger: TelemetryLogger | None = None
        self._current_db: Path | None = None
        self._t_started: datetime | None = None

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(1000)

    # ---------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # --- Метаданные эксперимента ---
        meta_box = QGroupBox("Метаданные эксперимента")
        meta_form = QFormLayout(meta_box)

        self.experiment_id_edit = QLineEdit("A1_HOLDING_SERVO_ON")
        self.session_id_edit = QLineEdit(
            f"A1_R01_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        self.device_id_edit = QLineEdit("delta-asda-b3-e")
        self.experiment_type_edit = QLineEdit("holding")

        self.regime_combo = QComboBox()
        self.regime_combo.addItems(sorted(ALLOWED_REGIME_LABELS))
        self.regime_combo.setCurrentText("holding")

        self.risk_combo = QComboBox()
        self.risk_combo.addItems(sorted(ALLOWED_RISK_LABELS))
        self.risk_combo.setCurrentText("normal")

        self.load_type_edit = QLineEdit("no_load")
        self.direction_combo = QComboBox()
        self.direction_combo.addItems(["none", "cw", "ccw", "both"])

        self.target_position_edit = QLineEdit("")
        self.target_velocity_edit = QLineEdit("0")
        self.target_torque_edit = QLineEdit("")
        self.acc_edit = QLineEdit("")
        self.dec_edit = QLineEdit("")

        self.operator_edit = QLineEdit("")
        self.comment_edit = QPlainTextEdit("Servo ON, shaft stationary")
        self.comment_edit.setFixedHeight(60)

        self.artificial_anomaly_cb = QCheckBox("is_artificial_anomaly")
        self.artificial_anomaly_cb.setChecked(False)

        meta_form.addRow("experiment_id:",   self.experiment_id_edit)
        meta_form.addRow("session_id:",      self.session_id_edit)
        meta_form.addRow("device_id:",       self.device_id_edit)
        meta_form.addRow("experiment_type:", self.experiment_type_edit)
        meta_form.addRow("regime_label:",    self.regime_combo)
        meta_form.addRow("risk_label:",      self.risk_combo)
        meta_form.addRow("load_type:",       self.load_type_edit)
        meta_form.addRow("direction:",       self.direction_combo)
        meta_form.addRow("target_position:", self.target_position_edit)
        meta_form.addRow("target_velocity:", self.target_velocity_edit)
        meta_form.addRow("target_torque:",   self.target_torque_edit)
        meta_form.addRow("acceleration_cmd:", self.acc_edit)
        meta_form.addRow("deceleration_cmd:", self.dec_edit)
        meta_form.addRow("operator:",        self.operator_edit)
        meta_form.addRow("operator_comment:", self.comment_edit)
        meta_form.addRow("",                 self.artificial_anomaly_cb)

        root.addWidget(meta_box)

        # --- Параметры записи ---
        rec_box = QGroupBox("Запись телеметрии")
        rec_grid = QGridLayout(rec_box)

        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(1, 3600)
        self.duration_spin.setValue(120)
        self.duration_spin.setSuffix(" с")

        self.period_spin = QSpinBox()
        self.period_spin.setRange(50, 2000)
        self.period_spin.setSingleStep(50)
        self.period_spin.setValue(100)
        self.period_spin.setSuffix(" мс")

        self.db_path_edit = QLineEdit(str(
            DEFAULT_RAW_DIR / f"{self.session_id_edit.text()}.sqlite3"
        ))
        self.db_browse_btn = QPushButton("…")
        self.db_browse_btn.clicked.connect(self._browse_db)

        self.start_btn = QPushButton("▶ Старт записи")
        self.stop_btn = QPushButton("■ Стоп")
        self.stop_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._on_start)
        self.stop_btn.clicked.connect(self._on_stop)

        self.status_lbl = QLabel("Статус: не записываю")
        self.status_lbl.setStyleSheet("color: gray;")

        rec_grid.addWidget(QLabel("duration:"), 0, 0)
        rec_grid.addWidget(self.duration_spin, 0, 1)
        rec_grid.addWidget(QLabel("period:"),  0, 2)
        rec_grid.addWidget(self.period_spin,   0, 3)
        rec_grid.addWidget(QLabel("db:"),      1, 0)
        rec_grid.addWidget(self.db_path_edit,  1, 1, 1, 2)
        rec_grid.addWidget(self.db_browse_btn, 1, 3)
        rec_grid.addWidget(self.start_btn,     2, 0)
        rec_grid.addWidget(self.stop_btn,      2, 1)
        rec_grid.addWidget(self.status_lbl,    2, 2, 1, 2)

        root.addWidget(rec_box)

        # --- Экспорт CSV / Журнал ---
        exp_box = QGroupBox("Экспорт CSV и журнал experiments.csv")
        exp_row = QHBoxLayout(exp_box)

        self.export_btn = QPushButton("Экспорт телеметрии -> CSV")
        self.export_btn.clicked.connect(self._on_export)
        self.export_btn.setToolTip(
            "Читает указанный SQLite и пишет стандартизированный CSV."
        )

        self.journal_btn = QPushButton("Добавить в experiments.csv")
        self.journal_btn.clicked.connect(self._on_journal)
        self.journal_btn.setToolTip(
            "Добавляет строку в журнал на основе текущих метаданных "
            "(без обращения к приводу)."
        )

        exp_row.addWidget(self.export_btn)
        exp_row.addWidget(self.journal_btn)
        exp_row.addStretch(1)
        root.addWidget(exp_box)

        # --- Паспорт стенда ---
        pass_box = QGroupBox("Паспорт стенда")
        pass_grid = QGridLayout(pass_box)

        self.stand_id_edit = QLineEdit("stand-01")
        self.drive_model_edit = QLineEdit("")
        self.drive_serial_edit = QLineEdit("")
        self.motor_model_edit = QLineEdit("")
        self.motor_serial_edit = QLineEdit("")
        self.passport_btn = QPushButton("Сохранить паспорт стенда")
        self.passport_btn.clicked.connect(self._on_save_passport)

        pass_grid.addWidget(QLabel("stand_id:"),     0, 0)
        pass_grid.addWidget(self.stand_id_edit,      0, 1)
        pass_grid.addWidget(QLabel("drive_model:"),  1, 0)
        pass_grid.addWidget(self.drive_model_edit,   1, 1)
        pass_grid.addWidget(QLabel("drive_serial:"), 1, 2)
        pass_grid.addWidget(self.drive_serial_edit,  1, 3)
        pass_grid.addWidget(QLabel("motor_model:"),  2, 0)
        pass_grid.addWidget(self.motor_model_edit,   2, 1)
        pass_grid.addWidget(QLabel("motor_serial:"), 2, 2)
        pass_grid.addWidget(self.motor_serial_edit,  2, 3)
        pass_grid.addWidget(self.passport_btn,       3, 0, 1, 4)
        pass_grid.addWidget(
            QLabel(
                "<i>rated_current / rated_torque / max_motor_speed читаются "
                "автоматически из привода (если подключён).</i>"
            ),
            4, 0, 1, 4,
        )
        root.addWidget(pass_box)

        root.addStretch(1)

    # ---------------------------------------------------------------- helpers
    def _get_controller(self):
        if hasattr(self.mode_controller, "get_master"):
            return self.mode_controller.get_master()
        return None

    def _build_metadata(self) -> LabSessionMetadata | None:
        meta = LabSessionMetadata(
            experiment_id=self.experiment_id_edit.text().strip(),
            session_id=self.session_id_edit.text().strip(),
            device_id=self.device_id_edit.text().strip(),
            experiment_type=self.experiment_type_edit.text().strip(),
            regime_label=self.regime_combo.currentText(),
            risk_label=self.risk_combo.currentText(),
            load_type=self.load_type_edit.text().strip(),
            direction=self.direction_combo.currentText(),
            target_position=self.target_position_edit.text().strip(),
            target_velocity=self.target_velocity_edit.text().strip(),
            target_torque=self.target_torque_edit.text().strip(),
            acceleration_cmd=self.acc_edit.text().strip(),
            deceleration_cmd=self.dec_edit.text().strip(),
            operator_comment=self.comment_edit.toPlainText().strip(),
            is_artificial_anomaly=int(self.artificial_anomaly_cb.isChecked()),
        )
        errors = meta.validate()
        if errors:
            QMessageBox.warning(self, "Метаданные",
                                "Не могу собрать метаданные:\n- " + "\n- ".join(errors))
            return None
        return meta

    def _browse_db(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Файл SQLite для записи", self.db_path_edit.text(),
            "SQLite (*.sqlite3 *.db)"
        )
        if path:
            self.db_path_edit.setText(path)

    # ---------------------------------------------------------------- actions
    def _on_start(self):
        controller = self._get_controller()
        if not isinstance(controller, ethercat_driver.EtherCATController):
            QMessageBox.warning(
                self, "Запись",
                "Сначала подключитесь к приводу во вкладке «Подключение»."
            )
            return
        meta = self._build_metadata()
        if meta is None:
            return

        db_path = Path(self.db_path_edit.text().strip())
        db_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            self.logger = TelemetryLogger(
                controller,
                db_path=str(db_path),
                period_s=self.period_spin.value() / 1000.0,
            )
            self.logger.start()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Запись", f"Не удалось запустить: {e}")
            self.logger = None
            return

        self._current_db = db_path
        self._t_started = datetime.now()
        self._meta_for_journal = meta

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_lbl.setText(f"Статус: пишу в {db_path}")
        self.status_lbl.setStyleSheet("color: green;")

    def _on_stop(self):
        if self.logger is None:
            return
        try:
            self.logger.stop()
        except Exception as e:  # noqa: BLE001
            print(f"[lab_tab] logger.stop error: {e}")
        self.logger = None

        # Записать в журнал
        if self._t_started is not None and self._current_db is not None:
            date_start = self._t_started.strftime("%Y-%m-%d %H:%M:%S")
            date_end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            duration_s = (datetime.now() - self._t_started).total_seconds()
            try:
                append_experiment(
                    DEFAULT_EXPERIMENTS_CSV,
                    self._meta_for_journal,
                    file_name=self._current_db.name,
                    date_start=date_start,
                    date_end=date_end,
                    operator=self.operator_edit.text().strip(),
                    drive_model=self.drive_model_edit.text().strip(),
                    motor_model=self.motor_model_edit.text().strip(),
                    duration_s=f"{duration_s:.3f}",
                    sample_rate_hz=1000.0 / max(self.period_spin.value(), 1),
                    stop_condition="ui_stop",
                    session_status="ok",
                )
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "Журнал",
                                    f"Не удалось записать в experiments.csv:\n{e}")

        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_lbl.setText("Статус: остановлено")
        self.status_lbl.setStyleSheet("color: gray;")

    def _on_export(self):
        meta = self._build_metadata()
        if meta is None:
            return
        db_path = Path(self.db_path_edit.text().strip())
        if not db_path.exists():
            QMessageBox.warning(self, "Экспорт",
                                f"SQLite не найден: {db_path}")
            return
        default_csv = db_path.parent / f"telemetry_{db_path.stem}.csv"
        out, _ = QFileDialog.getSaveFileName(
            self, "Сохранить CSV", str(default_csv), "CSV (*.csv)"
        )
        if not out:
            return
        try:
            result = export_samples_to_csv(db_path, out, meta)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Экспорт", f"Ошибка: {e}")
            return
        QMessageBox.information(
            self, "Экспорт",
            f"Готово.\nrows={result.row_count}\n"
            f"duration={result.duration_s:.3f}s\n{result.csv_path}",
        )

    def _on_journal(self):
        meta = self._build_metadata()
        if meta is None:
            return
        try:
            append_experiment(
                DEFAULT_EXPERIMENTS_CSV,
                meta,
                file_name=Path(self.db_path_edit.text()).name,
                operator=self.operator_edit.text().strip(),
                drive_model=self.drive_model_edit.text().strip(),
                motor_model=self.motor_model_edit.text().strip(),
                sample_rate_hz=1000.0 / max(self.period_spin.value(), 1),
                stop_condition="manual_journal_entry",
                session_status="ok",
            )
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Журнал", f"Ошибка: {e}")
            return
        QMessageBox.information(self, "Журнал",
                                f"Добавлено в {DEFAULT_EXPERIMENTS_CSV}")

    def _on_save_passport(self):
        controller = self._get_controller()
        if not isinstance(controller, ethercat_driver.EtherCATController):
            controller = None  # допустимо — поля останутся пустыми
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить паспорт стенда",
            str(DEFAULT_STAND_PASSPORT_CSV), "CSV (*.csv)"
        )
        if not path:
            return
        try:
            passport = build_passport_with_autofill(
                controller=controller,
                stand_id=self.stand_id_edit.text().strip(),
                operator=self.operator_edit.text().strip(),
                drive_model=self.drive_model_edit.text().strip(),
                drive_serial=self.drive_serial_edit.text().strip(),
                motor_model=self.motor_model_edit.text().strip(),
                motor_serial=self.motor_serial_edit.text().strip(),
            )
            passport.write_csv(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Паспорт", f"Ошибка: {e}")
            return
        msg = (f"Паспорт сохранён: {path}\n"
               f"rated_current_A={passport.rated_current_A or '—'}\n"
               f"rated_torque_Nm={passport.rated_torque_Nm or '—'}\n"
               f"max_motor_speed_rpm={passport.max_motor_speed_rpm or '—'}")
        if not (passport.rated_current_A and passport.rated_torque_Nm and
                passport.max_motor_speed_rpm):
            msg += ("\n\nЧасть полей не удалось прочитать автоматически — "
                    "впишите вручную по шильдику.")
        QMessageBox.information(self, "Паспорт", msg)

    # ---------------------------------------------------------------- timer
    def _refresh_status(self):
        if self.logger is not None and self._t_started is not None:
            elapsed = (datetime.now() - self._t_started).total_seconds()
            limit = self.duration_spin.value()
            self.status_lbl.setText(
                f"Статус: пишу в {self._current_db}  "
                f"({elapsed:.1f}/{limit:.1f} с)"
            )
            if elapsed >= limit:
                # Автоматическая остановка по таймеру
                self._on_stop()

    # ---------------------------------------------------------------- lifecycle
    def shutdown(self):
        if self.logger is not None:
            try:
                self.logger.stop()
            except Exception:
                pass
            self.logger = None
