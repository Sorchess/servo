# core/stand_passport.py
# Паспорт стенда: датакласс + чтение/запись stand_passport.csv.
#
# Реализует требование: если привод подключён, попытаться прочитать из SDO
# rated_current / rated_torque / max_motor_speed. Если привод не подключён —
# поля остаются пустыми. Модель привода, серийник привода, модель двигателя
# и серийник двигателя НИКОГДА не выдумываются автоматически; они вводятся
# оператором по шильдику.

from __future__ import annotations

import csv
import struct
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from core.csv_schema import STAND_PASSPORT_COLUMNS


@dataclass
class StandPassport:
    """Паспорт лабораторного стенда сервопривода.

    Поля точно соответствуют колонкам stand_passport.csv (см. csv_schema).
    Все поля строковые: CSV-паспорт — это табличный документ, а не
    телеметрия, числа здесь нужны для протокола, а не для математики.
    """

    stand_id: str = ""
    date: str = ""
    operator: str = ""
    supervisor: str = ""

    # --- ручной ввод по шильдику ---
    drive_model: str = ""
    drive_serial: str = ""
    motor_model: str = ""
    motor_serial: str = ""

    # --- может быть автоматически заполнено из SDO ---
    rated_current_A: str = ""
    rated_torque_Nm: str = ""
    max_motor_speed_rpm: str = ""

    # --- ручной ввод ---
    connection_type: str = "EtherCAT (CoE)"
    control_software: str = "servo-app"
    control_mode: str = "PP (Profile Position)"
    load_setup: str = ""
    emergency_stop: str = ""
    allowed_speed_rpm: str = ""
    allowed_acceleration: str = ""
    allowed_deceleration: str = ""
    allowed_loads: str = ""
    notes: str = ""

    # ----------------------------------------------------------------- to/from
    def as_row(self) -> dict[str, Any]:
        row = asdict(self)
        # На случай, если кто-то расширит датакласс — отдадим только колонки
        # из строгой схемы и ровно в её порядке.
        return {c: row.get(c, "") for c in STAND_PASSPORT_COLUMNS}

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "StandPassport":
        return cls(**{c: str(row.get(c, "") or "") for c in STAND_PASSPORT_COLUMNS
                      if c in {f.name for f in cls.__dataclass_fields__.values()}})

    # ----------------------------------------------------------------- CSV I/O
    def write_csv(self, path: str | Path) -> Path:
        """Записать паспорт в CSV с правильным заголовком.

        Если файл существует — он перезаписывается. Один паспорт — одна строка.
        Если хочется хранить несколько паспортов (история стендов) — используйте
        :py:meth:`append_csv`.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(STAND_PASSPORT_COLUMNS)
            w.writerow([self.as_row()[c] for c in STAND_PASSPORT_COLUMNS])
        return path

    def append_csv(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        need_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if need_header:
                w.writerow(STAND_PASSPORT_COLUMNS)
            w.writerow([self.as_row()[c] for c in STAND_PASSPORT_COLUMNS])
        return path

    @classmethod
    def read_csv(cls, path: str | Path) -> list["StandPassport"]:
        path = Path(path)
        if not path.exists():
            return []
        with path.open("r", newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            return [cls.from_row(row) for row in r]


# ---------------------------------------------------------------------------
# Автозаполнение из SDO (без побочных эффектов на привод)
# ---------------------------------------------------------------------------

# (SDO index, subindex, struct-формат, ключ для StandPassport)
# Только чтение, никаких записей. Не трогаем работу EtherCATController.
_AUTO_SDO_READS = [
    (0x6075, 0, "<I", "rated_current_mA"),
    (0x6076, 0, "<I", "rated_torque_mNm"),
    (0x6080, 0, "<I", "max_motor_speed_rpm"),
]


def read_drive_nameplate_via_sdo(controller) -> dict[str, str]:
    """Попытаться прочитать rated_current/rated_torque/max_motor_speed из привода.

    Возвращает словарь со значениями в инженерных единицах:
        rated_current_A  — амперы (Delta хранит в мА);
        rated_torque_Nm  — Н·м (Delta хранит в мН·м);
        max_motor_speed_rpm — об/мин.

    Любая ошибка чтения SDO -> поле остаётся пустым. Никогда не бросает
    исключения наружу: подключение к приводу не должно ломаться из-за
    того, что лаборатория хочет прочитать табличку.
    """
    result: dict[str, str] = {
        "rated_current_A": "",
        "rated_torque_Nm": "",
        "max_motor_speed_rpm": "",
    }
    if controller is None:
        return result
    slave = getattr(controller, "slave", None)
    if slave is None:
        return result

    raw_values: dict[str, int] = {}
    for idx, sub, fmt, key in _AUTO_SDO_READS:
        try:
            raw = slave.sdo_read(idx, sub)
            need = struct.calcsize(fmt)
            if len(raw) >= need:
                raw_values[key] = struct.unpack(fmt, raw[:need])[0]
        except Exception as e:  # noqa: BLE001 — намеренно широко
            print(f"[stand_passport] sdo_read {hex(idx)} failed: {e}")

    rated_i = raw_values.get("rated_current_mA")
    if rated_i is not None:
        result["rated_current_A"] = f"{rated_i / 1000.0:.3f}"
    rated_t = raw_values.get("rated_torque_mNm")
    if rated_t is not None:
        result["rated_torque_Nm"] = f"{rated_t / 1000.0:.3f}"
    max_v = raw_values.get("max_motor_speed_rpm")
    if max_v is not None:
        result["max_motor_speed_rpm"] = str(max_v)

    return result


def build_passport_with_autofill(
    *,
    controller=None,
    stand_id: str = "stand-01",
    operator: str = "",
    supervisor: str = "",
    drive_model: str = "",
    drive_serial: str = "",
    motor_model: str = "",
    motor_serial: str = "",
    notes: str = "",
) -> StandPassport:
    """Собрать паспорт стенда, заполнив автоматические поля из привода.

    Поля drive_model/drive_serial/motor_model/motor_serial всегда берутся
    как есть из аргументов. Они НЕ читаются автоматически: серийники
    и модели нельзя надёжно вытащить из SDO у Delta ASDA-B3-E.
    """
    pass_ = StandPassport(
        stand_id=stand_id,
        date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        operator=operator,
        supervisor=supervisor,
        drive_model=drive_model,
        drive_serial=drive_serial,
        motor_model=motor_model,
        motor_serial=motor_serial,
        notes=notes,
    )
    auto = read_drive_nameplate_via_sdo(controller)
    pass_.rated_current_A = auto["rated_current_A"]
    pass_.rated_torque_Nm = auto["rated_torque_Nm"]
    pass_.max_motor_speed_rpm = auto["max_motor_speed_rpm"]
    return pass_
