# tools/create_stand_passport.py
# CLI: создать stand_passport.csv (с автозаполнением полей из привода, если он подключён).
#
# Пример:
#   python -m tools.create_stand_passport --out lab_data/stand_passport.csv
#
# По умолчанию НЕ пытается коннектиться к приводу (это безопасно для запуска
# на любой машине). Чтобы попытаться автозаполнить rated_current/rated_torque/
# max_motor_speed, добавьте флаг --connect и (опционально) --iface.

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.stand_passport import StandPassport, build_passport_with_autofill


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="create_stand_passport",
        description="Создание stand_passport.csv (паспорт стенда сервопривода).",
    )
    p.add_argument("--out", required=True, help="Путь к выходному CSV.")
    p.add_argument("--stand-id", default="stand-01")
    p.add_argument("--operator", default="")
    p.add_argument("--supervisor", default="")
    p.add_argument("--drive-model", default="",
                   help="Модель привода с шильдика, напр. ASD-B3-0421-E.")
    p.add_argument("--drive-serial", default="",
                   help="Серийник привода с шильдика.")
    p.add_argument("--motor-model", default="",
                   help="Модель серводвигателя с шильдика.")
    p.add_argument("--motor-serial", default="",
                   help="Серийник серводвигателя с шильдика.")
    p.add_argument("--notes", default="")
    p.add_argument("--append", action="store_true",
                   help="Дописать в файл вместо перезаписи (история стендов).")
    p.add_argument("--connect", action="store_true",
                   help="Подключиться к приводу и автозаполнить rated_*/max_motor_speed.")
    p.add_argument("--iface", default=None,
                   help="EtherCAT интерфейс (если --connect). По умолчанию из config.")
    return p


def _maybe_connect(iface: str | None):
    """Опциональное подключение к приводу. Возвращает controller либо None."""
    try:
        from core import ethercat_driver
        from utils.config import ETH_INTERFACE
    except Exception as e:  # noqa: BLE001
        print(f"[create_stand_passport] cannot import driver: {e}")
        return None
    try:
        return ethercat_driver.setup_ethercat_controller(iface or ETH_INTERFACE)
    except Exception as e:  # noqa: BLE001
        print(f"[create_stand_passport] connect failed: {e}")
        return None


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    controller = None
    if args.connect:
        controller = _maybe_connect(args.iface)
        if controller is None:
            print("[create_stand_passport] продолжаю без автозаполнения "
                  "(rated_* и max_motor_speed будут пустыми)")

    if not args.drive_model:
        print("[create_stand_passport] WARN: drive_model не задан — "
              "его нужно вписать вручную по шильдику привода.")
    if not args.drive_serial:
        print("[create_stand_passport] WARN: drive_serial не задан — "
              "его нужно вписать вручную по шильдику привода.")
    if not args.motor_model:
        print("[create_stand_passport] WARN: motor_model не задан — "
              "его нужно вписать вручную по шильдику двигателя.")
    if not args.motor_serial:
        print("[create_stand_passport] WARN: motor_serial не задан — "
              "его нужно вписать вручную по шильдику двигателя.")

    passport = build_passport_with_autofill(
        controller=controller,
        stand_id=args.stand_id,
        operator=args.operator,
        supervisor=args.supervisor,
        drive_model=args.drive_model,
        drive_serial=args.drive_serial,
        motor_model=args.motor_model,
        motor_serial=args.motor_serial,
        notes=args.notes,
    )

    out = Path(args.out)
    if args.append:
        passport.append_csv(out)
    else:
        passport.write_csv(out)

    print(f"[create_stand_passport] OK: {out.resolve()}")

    # Закроем привод, если открывали
    if controller is not None:
        try:
            from core import ethercat_driver
            ethercat_driver.close_ethercat_controller(controller)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
