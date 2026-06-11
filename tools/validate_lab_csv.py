# tools/validate_lab_csv.py
# CLI: проверить CSV лабораторной телеметрии на соответствие схеме.
#
# Пример:
#   python -m tools.validate_lab_csv lab_data/raw/telemetry_A1_R01_20260612_103000.csv
#
# Код возврата:
#   0 — валидно (могут быть warning'и);
#   1 — есть критические ошибки.

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path

from core.csv_schema import (
    TELEMETRY_COLUMNS,
    TELEMETRY_REQUIRED_DATA_COLUMNS,
    ALLOWED_REGIME_LABELS,
    ALLOWED_RISK_LABELS,
    ALLOWED_IS_ARTIFICIAL_ANOMALY,
)


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    row_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


def validate_csv(path: str | Path) -> ValidationReport:
    """Проверить CSV-файл лабораторной телеметрии. Не бросает исключения."""
    report = ValidationReport()
    path = Path(path)
    if not path.exists():
        report.add_error(f"file not found: {path}")
        return report

    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            report.add_error("file is empty")
            return report

        header = [h.strip() for h in header]
        # 1. наличие всех колонок из схемы
        missing = [c for c in TELEMETRY_COLUMNS if c not in header]
        if missing:
            report.add_error(f"missing columns: {missing}")
        extra = [c for c in header if c not in TELEMETRY_COLUMNS]
        if extra:
            report.add_warning(f"extra columns (not in schema): {extra}")

        # 2. порядок колонок — мягкий warning (валидный, но нестандартный)
        if header[: len(TELEMETRY_COLUMNS)] != list(TELEMETRY_COLUMNS):
            report.add_warning(
                "column order differs from the canonical order in csv_schema"
            )

        # Если обязательных колонок нет — дальше анализ построчно бессмысленен.
        if missing:
            return report

        # Колонки данных, которые должны существовать (требование валидатора)
        missing_required_data = [c for c in TELEMETRY_REQUIRED_DATA_COLUMNS
                                 if c not in header]
        if missing_required_data:
            report.add_error(
                f"missing required data columns: {missing_required_data}"
            )

        # Индексы интересующих нас колонок
        idx = {c: header.index(c) for c in TELEMETRY_COLUMNS if c in header}

        prev_t: float | None = None
        for row_no, row in enumerate(reader, start=2):  # 2 = первая строка данных
            if not row:
                continue
            if len(row) < len(header):
                report.add_error(f"row {row_no}: column count mismatch")
                continue
            report.row_count += 1

            # t монотонно возрастает
            t_raw = row[idx["t"]]
            try:
                t_val = float(t_raw) if t_raw != "" else None
            except ValueError:
                report.add_error(f"row {row_no}: t={t_raw!r} is not a number")
                t_val = None
            if t_val is not None and prev_t is not None and t_val < prev_t:
                report.add_error(
                    f"row {row_no}: t is not monotonically increasing "
                    f"({t_val} < {prev_t})"
                )
            if t_val is not None:
                prev_t = t_val

            # experiment_id и session_id не пустые
            if not row[idx["experiment_id"]].strip():
                report.add_error(f"row {row_no}: experiment_id is empty")
            if not row[idx["session_id"]].strip():
                report.add_error(f"row {row_no}: session_id is empty")

            # regime_label / risk_label
            regime = row[idx["regime_label"]].strip()
            if regime not in ALLOWED_REGIME_LABELS:
                report.add_error(
                    f"row {row_no}: regime_label={regime!r} not in allowed set"
                )
            risk = row[idx["risk_label"]].strip()
            if risk not in ALLOWED_RISK_LABELS:
                report.add_error(
                    f"row {row_no}: risk_label={risk!r} not in allowed set"
                )

            # is_artificial_anomaly == 0 или 1
            iaa = row[idx["is_artificial_anomaly"]].strip()
            if iaa not in ALLOWED_IS_ARTIFICIAL_ANOMALY:
                report.add_error(
                    f"row {row_no}: is_artificial_anomaly={iaa!r} must be 0 or 1"
                )

            # warning: для нормальных записей error_code != 0
            err_raw = row[idx["error_code"]].strip()
            if risk == "normal" and err_raw not in ("", "0"):
                report.add_warning(
                    f"row {row_no}: risk=normal but error_code={err_raw!r}"
                )

    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="validate_lab_csv",
        description="Проверка CSV лабораторной телеметрии.",
    )
    p.add_argument("path", help="CSV-файл для проверки.")
    p.add_argument("--quiet", action="store_true",
                   help="Не печатать warning'и (только errors и итог).")
    args = p.parse_args(argv)

    report = validate_csv(args.path)
    if report.errors:
        print("ERRORS:")
        for e in report.errors:
            print(f"  - {e}")
    if report.warnings and not args.quiet:
        print("WARNINGS:")
        for w in report.warnings:
            print(f"  - {w}")
    print(f"rows={report.row_count}, "
          f"errors={len(report.errors)}, warnings={len(report.warnings)}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
