"""Read-only audit: для каждого raw/*.sqlite3 проверяем, что фактическая
телеметрия согласуется со строкой experiments.csv и с пресетом из lab.bat.

НИЧЕГО не пишется в raw/. Любой sqlite3.connect() — read-only через URI.
"""
from __future__ import annotations
import csv
import sqlite3
import sys
from pathlib import Path
from statistics import mean, median

# Принудительный utf-8 для stdout, чтобы не падать на cp1251.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

RAW_DIR = Path("lab_data/raw")
CSV = Path("lab_data/experiments.csv")

# Эталонные пресеты из lab.bat (vel в RPM, dur в секундах).
# (scenario, regime, risk, vel, acc, dur, dir)
PRESETS = {
    "A1_HOLDING_SERVO_ON":  ("hold",            "holding",        "normal",              "none", 0,    1000, 120),
    "A2_HOLD_AFTER_MOVE":   ("move_then_hold",  "holding",        "normal",              "none", 0,    1000, 120),
    "A3_ROTATE_ONE_DIR":    ("rotate_const",    "constant_speed", "normal",              "cw",   300,  1000,  90),
    "A4_ROTATE_BOTH_DIRS":  ("rotate_reversal", "reversal",       "normal",              "both", 300,  1000,  90),
    "B1_ACC_DEC_SLOW":      ("ramp_up_down",    "acceleration",   "normal",              "cw",   600,   500,  90),
    "B2_ACC_DEC_MEDIUM":    ("ramp_up_down",    "acceleration",   "transition",          "cw",   900,  1500,  90),
    "B3_ACC_DEC_SHARP":     ("ramp_up_down",    "acceleration",   "elevated_load",       "cw",   1200, 3000,  90),
    "B4_REVERSAL":          ("rotate_reversal", "reversal",       "transition",          "both", 600,  1000,  90),
    "B5_CYCLIC":            ("cyclic",          "cyclic",         "transition",          "both", 600,  1000, 300),
    "C1_SPEED_300":         ("rotate_const",    "constant_speed", "normal",              "cw",   300,  1000,  60),
    "C2_SPEED_600":         ("rotate_const",    "constant_speed", "normal",              "cw",   600,  1000,  60),
    "C3_SPEED_900":         ("rotate_const",    "constant_speed", "normal",              "cw",   900,  1000,  60),
    "C4_SPEED_1200":        ("rotate_const",    "constant_speed", "transition",          "cw",   1200, 1000,  60),
    "C5_SPEED_1500":        ("rotate_const",    "constant_speed", "transition",          "cw",   1500, 1000,  60),
    "D1_LONG_CYCLE_LOW":    ("cyclic",          "cyclic",         "normal",              "both", 600,  1000, 600),
    "D2_LONG_CYCLE_MED":    ("cyclic",          "cyclic",         "transition",          "both", 900,  1000, 600),
    "D3_LONG_CYCLE_HARD":   ("cyclic",          "cyclic",         "elevated_load",       "both", 1200, 1500, 600),
    "E1_LOAD_LOW_INERTIA":  ("rotate_const",    "load_low",       "elevated_load",       "cw",   600,  1000, 120),
    "E2_LOAD_MED_INERTIA":  ("rotate_const",    "load_medium",    "pre_emergency_proxy", "cw",   600,  1000, 120),
}


def load_csv():
    rows = list(csv.reader(open(CSV, encoding="utf-8")))
    hdr = rows[0]
    return {dict(zip(hdr, r))["session_id"]: dict(zip(hdr, r)) for r in rows[1:]}


def read_db_stats(path: Path):
    """Read-only через URI, чтобы случайно не модифицировать файл."""
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        cur = conn.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM samples")
        n, ts_min, ts_max = cur.fetchone()
        if not n:
            return {"n": 0}
        # velocity
        cur = conn.execute("SELECT MIN(velocity), MAX(velocity), AVG(velocity) FROM samples")
        v_min, v_max, v_avg = cur.fetchone()
        # |velocity|
        cur = conn.execute("SELECT AVG(ABS(velocity)) FROM samples")
        v_abs_avg, = cur.fetchone()
        # position
        cur = conn.execute("SELECT MIN(position), MAX(position) FROM samples")
        p_min, p_max = cur.fetchone()
        # error codes
        cur = conn.execute("SELECT COUNT(DISTINCT error_code), MAX(error_code) FROM samples")
        ec_distinct, ec_max = cur.fetchone()
        # statusword (одно значение или меняется)
        cur = conn.execute("SELECT COUNT(DISTINCT statusword), MIN(statusword), MAX(statusword) FROM samples")
        sw_distinct, sw_min, sw_max = cur.fetchone()
        # mode_display
        cur = conn.execute("SELECT DISTINCT mode_display FROM samples")
        modes = sorted({m for (m,) in cur.fetchall() if m is not None})
        # bus / temp
        cur = conn.execute("SELECT AVG(dc_bus_voltage), MIN(dc_bus_voltage), MAX(dc_bus_voltage), MAX(drive_temp) FROM samples")
        vbus_avg, vbus_min, vbus_max, t_max = cur.fetchone()
        # current / torque
        cur = conn.execute("SELECT AVG(ABS(current_A)), MAX(ABS(current_A)), AVG(ABS(torque_Nm)), MAX(ABS(torque_Nm)) FROM samples")
        cur_avg, cur_max, tq_avg, tq_max = cur.fetchone()
        # есть ли отрицательные скорости (для определения reversal/cyclic)
        cur = conn.execute("SELECT SUM(CASE WHEN velocity > 5 THEN 1 ELSE 0 END), "
                           "       SUM(CASE WHEN velocity < -5 THEN 1 ELSE 0 END), "
                           "       SUM(CASE WHEN ABS(velocity) <= 5 THEN 1 ELSE 0 END) FROM samples")
        n_pos, n_neg, n_zero = cur.fetchone()
        return dict(
            n=n, ts_min=ts_min, ts_max=ts_max, span=ts_max - ts_min,
            v_min=v_min, v_max=v_max, v_avg=v_avg, v_abs_avg=v_abs_avg,
            p_min=p_min, p_max=p_max, p_span=p_max - p_min,
            ec_distinct=ec_distinct, ec_max=ec_max,
            sw_distinct=sw_distinct, sw_min=sw_min, sw_max=sw_max,
            modes=modes,
            vbus_avg=vbus_avg, vbus_min=vbus_min, vbus_max=vbus_max, t_max=t_max,
            cur_avg=cur_avg, cur_max=cur_max, tq_avg=tq_avg, tq_max=tq_max,
            n_pos=n_pos, n_neg=n_neg, n_zero=n_zero,
        )
    finally:
        conn.close()


def expected_mode_byte(scenario):
    # pp=1, pv=3
    if scenario in ("hold", "move_then_hold", "ramp_up_down"):
        return 1
    return 3


def check(session_id: str, csv_row: dict, raw_path: Path):
    issues = []
    notes = []

    exp_key = session_id.rsplit("_R", 1)[0]
    if exp_key not in PRESETS:
        return [f"unknown preset key '{exp_key}'"], []
    scen, regime, risk, dir_, vel, acc, dur = PRESETS[exp_key]

    # ---- 1. сверка csv-метаданных с пресетом ----
    if csv_row.get("regime_label") != regime:
        issues.append(f"csv.regime={csv_row.get('regime_label')!r} != preset {regime!r}")
    if csv_row.get("risk_label") != risk:
        issues.append(f"csv.risk={csv_row.get('risk_label')!r} != preset {risk!r}")
    if csv_row.get("direction") != dir_:
        issues.append(f"csv.direction={csv_row.get('direction')!r} != preset {dir_!r}")
    csv_vel = csv_row.get("target_velocity", "") or "0"
    if int(csv_vel or 0) != vel:
        issues.append(f"csv.target_velocity={csv_vel!r} != preset {vel}")
    csv_acc = csv_row.get("acceleration_cmd", "") or "0"
    if int(csv_acc or 0) != acc:
        issues.append(f"csv.acceleration_cmd={csv_acc!r} != preset {acc}")
    csv_dur = float(csv_row.get("duration_s", "0") or 0)
    if not (dur - 1 <= csv_dur <= dur + 5):
        issues.append(f"csv.duration_s={csv_dur:.2f} вне ожидаемого {dur}±5s")
    if csv_row.get("session_status") != "ok":
        issues.append(f"csv.session_status={csv_row.get('session_status')!r}")
    if csv_row.get("stop_condition") not in ("timer",):
        notes.append(f"stop_condition={csv_row.get('stop_condition')!r} (не timer)")

    # ---- 2. читаем БД ----
    if not raw_path.exists():
        issues.append(f"raw файл отсутствует: {raw_path}")
        return issues, notes
    try:
        s = read_db_stats(raw_path)
    except sqlite3.DatabaseError as e:
        issues.append(f"sqlite error: {e}")
        return issues, notes

    if s.get("n", 0) == 0:
        issues.append("в samples нет строк")
        return issues, notes

    # ---- 3. длительность из самой телеметрии ----
    if not (dur - 2 <= s["span"] <= dur + 5):
        issues.append(f"ts-span={s['span']:.2f}s вне ожидаемого {dur}±5s")
    # ---- 4. частота ≈ 10 Hz (period=100ms) ----
    sr = s["n"] / max(s["span"], 1e-9)
    if not (8.0 <= sr <= 12.0):
        issues.append(f"sample_rate={sr:.2f} Hz вне 8..12 Hz")
    else:
        notes.append(f"sample_rate≈{sr:.2f} Hz, n={s['n']}")

    # ---- 5. error_code ----
    if s.get("ec_max") and s["ec_max"] > 0:
        issues.append(f"error_code наблюдался! max={s['ec_max']}, distinct={s['ec_distinct']}")
    else:
        notes.append("error_code==0 всё время")

    # ---- 6. statusword: должно быть OE (бит 2 = 0x04) хотя бы где-то ----
    sw_or = s["sw_min"] | s["sw_max"] if s["sw_min"] is not None and s["sw_max"] is not None else 0
    if sw_or and not (sw_or & 0x04):
        issues.append(f"OE-бит (0x04) ни разу не виден в statusword ({sw_min:#x}..{sw_max:#x})")

    # ---- 7. mode_display ----
    exp_mode = expected_mode_byte(scen)
    if s["modes"] and exp_mode not in s["modes"]:
        # ASDA-B3-E на ECAT может репортить mode_display=0 если slave не успел переключиться,
        # это предупреждение, а не error.
        notes.append(f"mode_display={s['modes']} (ожидался {exp_mode} для {scen})")

    # ---- 8. сверка скорости по сценарию ----
    if scen == "hold":
        # удержание: |v| должно быть около 0
        if s["v_abs_avg"] > 30:
            issues.append(f"hold-сценарий, но avg|v|={s['v_abs_avg']:.1f} RPM (>30)")
        else:
            notes.append(f"hold: avg|v|={s['v_abs_avg']:.1f} RPM, max|v|={max(abs(s['v_min']),abs(s['v_max']))}")
        if s["p_span"] > 5000:
            notes.append(f"hold: позиция гуляет на {s['p_span']} инкр.")
    elif scen == "move_then_hold":
        # сначала движение, потом hold; финальная позиция должна отличаться от начальной
        notes.append(f"move_then_hold: p_min={s['p_min']}, p_max={s['p_max']}, span={s['p_span']}")
        if s["p_span"] < 100:
            issues.append(f"move_then_hold: позиция почти не менялась (span={s['p_span']})")
    elif scen == "rotate_const":
        # одно направление: знак v должен соответствовать dir_
        if dir_ == "cw":
            # обычно cw → положительная или отрицательная — зависит от ориентации.
            # Главное: должна быть преимущественно одного знака и |v|~vel.
            same_sign = s["n_pos"] if s["n_pos"] >= s["n_neg"] else s["n_neg"]
            if s["n_zero"] + same_sign < 0.7 * s["n"]:
                issues.append(f"rotate_const dir=cw, но смесь знаков (pos={s['n_pos']}, neg={s['n_neg']}, ~0={s['n_zero']})")
            tol = max(50, 0.15 * vel)
            if vel > 0 and abs(s["v_abs_avg"] - vel) > tol:
                # после старта/стопа усреднение всегда ниже целевой
                notes.append(f"rotate_const: avg|v|={s['v_abs_avg']:.1f} RPM, target={vel} (Δ={abs(s['v_abs_avg']-vel):.1f})")
            else:
                notes.append(f"rotate_const: avg|v|={s['v_abs_avg']:.1f} RPM ≈ target {vel}")
            max_v = max(abs(s["v_min"]), abs(s["v_max"]))
            if max_v > vel * 1.10 + 50:
                issues.append(f"|v|_max={max_v} превышает target {vel} больше чем на 10%+50RPM")
    elif scen == "rotate_reversal":
        # обязаны быть и положительные, и отрицательные скорости
        if s["n_pos"] < 5 or s["n_neg"] < 5:
            issues.append(f"reversal: ожидались оба знака; n_pos={s['n_pos']}, n_neg={s['n_neg']}")
        else:
            notes.append(f"reversal: n_pos={s['n_pos']}, n_neg={s['n_neg']}, n_zero={s['n_zero']}")
        max_v = max(abs(s["v_min"]), abs(s["v_max"]))
        if max_v > vel * 1.10 + 50:
            issues.append(f"|v|_max={max_v} превышает target {vel} больше чем на 10%+50RPM")
    elif scen == "cyclic":
        # как reversal, но half_period 10 s
        if s["n_pos"] < 5 or s["n_neg"] < 5:
            issues.append(f"cyclic: ожидались оба знака; n_pos={s['n_pos']}, n_neg={s['n_neg']}")
        else:
            notes.append(f"cyclic: n_pos={s['n_pos']}, n_neg={s['n_neg']}, n_zero={s['n_zero']}")
        max_v = max(abs(s["v_min"]), abs(s["v_max"]))
        if max_v > vel * 1.10 + 50:
            issues.append(f"|v|_max={max_v} превышает target {vel} больше чем на 10%+50RPM")
    elif scen == "ramp_up_down":
        # PP-сценарий, позиция должна сильно меняться, скорость в обе стороны
        if s["p_span"] < 1000:
            issues.append(f"ramp_up_down: позиция не менялась (span={s['p_span']})")
        else:
            notes.append(f"ramp: p_span={s['p_span']}, v_min={s['v_min']}, v_max={s['v_max']}")

    # ---- 8b. статическая ошибка скорости для rotate_const ----
    if scen == "rotate_const" and vel > 0:
        rel = (s["v_abs_avg"] - vel) / vel * 100
        notes.append(f"rotate_const: статическая ошибка скорости {rel:+.2f}%")

    # ---- 9. абсолютные границы безопасности ----
    # Лимит на КОМАНДУ — 1500 RPM. Измеренная скорость может быть чуть выше
    # из-за квантования обратной связи привода / дискретности сэмплирования.
    # Допуск +1% (≈15 RPM) для измеренной величины.
    SPEED_LIMIT_HARD = 1500           # лимит на КОМАНДУ
    SPEED_LIMIT_MEASURED = 1515       # +1% на статическую ошибку/квантование привода
    measured_max = max(abs(s["v_min"]), abs(s["v_max"]))
    if measured_max > SPEED_LIMIT_MEASURED:
        issues.append(f"|v|_measured_max={measured_max} превышает {SPEED_LIMIT_MEASURED} "
                      f"(cmd_limit={SPEED_LIMIT_HARD} + 1% допуск)")
    if s["t_max"] and s["t_max"] > 75:
        issues.append(f"drive_temp_max={s['t_max']:.1f}°C > 75")
    if s["vbus_min"] and (s["vbus_min"] < 200 or s["vbus_max"] > 400):
        notes.append(f"dc_bus: {s['vbus_min']:.1f}..{s['vbus_max']:.1f} V")
    notes.append(f"current avg|I|={s['cur_avg']:.3f}A max={s['cur_max']:.3f}A; "
                 f"torque avg|T|={s['tq_avg']:.3f}Nm max={s['tq_max']:.3f}Nm")

    return issues, notes


def main():
    csv_map = load_csv()
    raw_files = sorted(RAW_DIR.glob("*.sqlite3"))
    # пропускаем те, для которых рядом есть -journal (значит, идёт запись)
    busy = {p.with_suffix(".sqlite3") for p in RAW_DIR.glob("*.sqlite3-journal")}
    print("=" * 78)
    print(f"Найдено {len(raw_files)} raw БД. Сейчас пишутся (есть -journal): "
          f"{[p.name for p in busy]}")
    print("=" * 78)
    n_ok = n_warn = n_bad = 0
    bad_list = []
    skipped = []
    for p in raw_files:
        if p in busy:
            print(f"\n--- SKIP (запись идёт) {p.name} ---")
            skipped.append(p.name)
            continue
        sid = p.stem
        csv_row = csv_map.get(sid, {})
        if not csv_row:
            print(f"\n--- {p.name} ---")
            print(f"  !! НЕТ строки в experiments.csv для session_id={sid}")
            n_bad += 1
            bad_list.append((sid, ["нет строки в CSV"]))
            continue
        issues, notes = check(sid, csv_row, p)
        if issues:
            n_bad += 1
            bad_list.append((sid, issues))
            print(f"\n--- {p.name}  [ISSUES]")
        else:
            if notes:
                n_warn += 1
                print(f"\n--- {p.name}  [OK]")
            else:
                n_ok += 1
                print(f"\n--- {p.name}  [OK]")
        for i in issues:
            print(f"  !! {i}")
        for n in notes:
            print(f"   . {n}")
    print()
    print("=" * 78)
    print(f"ИТОГО: ok={n_ok+n_warn}  issues={n_bad}  skipped={len(skipped)}")
    if bad_list:
        print("\nПроблемные сессии:")
        for sid, issues in bad_list:
            print(f"  * {sid}")
            for i in issues:
                print(f"      - {i}")
    if skipped:
        print(f"\nПропущены (идёт запись): {skipped}")


if __name__ == "__main__":
    main()
