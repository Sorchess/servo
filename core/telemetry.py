# core/telemetry.py
# Сбор телеметрии с привода Delta ASDA-B3-E и запись в SQLite.
#
# Читает через SDO стандартные объекты CiA 402 плюс несколько Delta-
# специфичных (DC bus voltage и т.п.). Частота опроса ~10 Гц по умолчанию.
# PDO-потоку из EtherCATController не мешает — SDO и PDO у pysoem
# разнесены.

import sqlite3
import struct
import threading
import time
from pathlib import Path

from core import ethercat_driver


# ---- Описание сигналов ----------------------------------------------------
# (SDO index, subindex, struct-формат, имя колонки, описание)
# Форматы struct: <h int16, <H uint16, <i int32, <I uint32, <b int8
SIGNALS = [
    (0x6041, 0, '<H', 'statusword',       'CiA402 Statusword'),
    (0x6061, 0, '<b', 'mode_display',     'Mode of Operation display'),
    (0x6064, 0, '<i', 'position',         'Position actual value (inc)'),
    (0x606C, 0, '<i', 'velocity',         'Velocity actual value (RPM на Delta)'),
    (0x6077, 0, '<h', 'torque',           'Torque actual (0.1% от номинала)'),
    (0x6078, 0, '<h', 'current',          'Current actual (0.1% от номинала)'),
    (0x603F, 0, '<H', 'error_code',       'Error code'),
    # Номинальные величины — для пересчёта 0x6077/0x6078 в Н·м и амперы
    (0x6075, 0, '<I', 'rated_current',    'Rated current (mA)'),
    (0x6076, 0, '<I', 'rated_torque',     'Rated torque (mN·m)'),
    (0x6080, 0, '<I', 'max_motor_speed',  'Max motor speed (RPM)'),
    # Delta P0-09 / P0-10 — настраиваемые мониторы.
    # Перед сэмплированием логгер пишет в P0-17/P0-18 коды:
    #   14 = DC bus voltage (V), 16 = IGBT temperature (°C).
    (0x2009, 0, '<i', 'dc_bus_voltage',   'P0-09 (cfg=14): DC bus voltage (V)'),
    (0x200A, 0, '<i', 'drive_temp',       'P0-10 (cfg=16): IGBT temperature (°C)'),
]

# Конфигурация мониторов Delta P0-17 / P0-18 (адрес SDO, код мониторинга).
# На B3-E реально работают только эти два кода — остальные мониторы
# либо read-only (P0-22..P0-24), либо возвращают мусор / зеркалят DC bus.
DELTA_MONITOR_SETUP = [
    (0x2011, 14),   # P0-17 -> код 14 (DC bus voltage) -> P0-09
    (0x2012, 16),   # P0-18 -> код 16 (IGBT temp)      -> P0-10
]


SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts                REAL PRIMARY KEY,
    statusword        INTEGER,
    mode_display      INTEGER,
    position          INTEGER,
    velocity          INTEGER,
    torque            INTEGER,
    current           INTEGER,
    error_code        INTEGER,
    rated_current     INTEGER,
    rated_torque      INTEGER,
    max_motor_speed   INTEGER,
    dc_bus_voltage    INTEGER,
    drive_temp        INTEGER,
    current_A         REAL,
    torque_Nm         REAL
);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);
"""


class TelemetryLogger:
    """Поток, периодически читающий сигналы и пишущий в SQLite."""

    DEFAULT_DB = Path("telemetry.sqlite3")

    def __init__(self, controller, db_path=None, period_s=0.1, signals=None):
        if not isinstance(controller, ethercat_driver.EtherCATController):
            raise TypeError("TelemetryLogger требует EtherCATController")
        self.controller = controller
        self.slave = controller.slave
        self.db_path = Path(db_path) if db_path else self.DEFAULT_DB
        self.period = period_s
        self.signals = signals if signals is not None else SIGNALS

        self._stop = threading.Event()
        self._thread = None
        self._conn = None
        self._available = None   # список сигналов, которые удалось прочитать
        self.error = None

    # ---- DB ----
    def _open_db(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.executescript(SCHEMA)
        conn.commit()
        return conn

    # ---- настройка Delta-мониторов ----
    def _setup_delta_monitors(self):
        """Прописать в P0-17/P0-18 коды 14/16 — DC bus voltage и IGBT temp.

        Это один раз перед опросом. Если привод не поддерживает запись —
        отваливается молча, соответствующие сигналы останутся пустыми.
        """
        for addr, code in DELTA_MONITOR_SETUP:
            try:
                self.slave.sdo_write(addr, 0, struct.pack('<H', code))
                print(f"[telemetry] monitor {hex(addr)} <- {code}")
            except Exception as e:
                try:
                    # на некоторых прошивках это 32-bit
                    self.slave.sdo_write(addr, 0, struct.pack('<i', code))
                    print(f"[telemetry] monitor {hex(addr)} <- {code} (32b)")
                except Exception as e2:
                    print(f"[telemetry] skip monitor {hex(addr)}: {e2}")

    # ---- чтение SDO ----
    def _probe(self):
        """Один раз пробуем прочитать каждый сигнал; оставляем доступные."""
        self._setup_delta_monitors()
        available = []
        for idx, sub, fmt, name, desc in self.signals:
            try:
                raw = self.slave.sdo_read(idx, sub)
                # у некоторых объектов длина может не совпасть с форматом
                need = struct.calcsize(fmt)
                if len(raw) < need:
                    print(f"[telemetry] skip {name} (got {len(raw)}B, need {need})")
                    continue
                struct.unpack(fmt, raw[:need])[0]
                available.append((idx, sub, fmt, name, desc))
            except Exception as e:
                print(f"[telemetry] skip {name} ({hex(idx)}): {e}")
        self._available = available
        print(f"[telemetry] tracking {len(available)} signals: "
              f"{[s[3] for s in available]}")

    def _read_sample(self):
        sample = {'ts': time.time()}
        for idx, sub, fmt, name, _desc in self._available:
            try:
                raw = self.slave.sdo_read(idx, sub)
                need = struct.calcsize(fmt)
                sample[name] = struct.unpack(fmt, raw[:need])[0]
            except Exception:
                sample[name] = None
        # ---- derived physical values ----
        # 0x6078 current: per-mille от rated_current (в мА)
        cur_pm = sample.get('current')
        rated_i = sample.get('rated_current')
        if cur_pm is not None and rated_i:
            sample['current_A'] = cur_pm * rated_i / 1_000_000.0
        else:
            sample['current_A'] = None
        # 0x6077 torque: per-mille от rated_torque (в мН·м)
        trq_pm = sample.get('torque')
        rated_t = sample.get('rated_torque')
        if trq_pm is not None and rated_t:
            sample['torque_Nm'] = trq_pm * rated_t / 1_000_000.0
        else:
            sample['torque_Nm'] = None
        return sample

    def _insert(self, sample):
        cols = ['ts', 'statusword', 'mode_display', 'position', 'velocity',
                'torque', 'current', 'error_code',
                'rated_current', 'rated_torque', 'max_motor_speed',
                'dc_bus_voltage', 'drive_temp',
                'current_A', 'torque_Nm']
        values = [sample.get(c) for c in cols]
        placeholders = ','.join(['?'] * len(cols))
        self._conn.execute(
            f"INSERT OR REPLACE INTO samples ({','.join(cols)}) "
            f"VALUES ({placeholders})",
            values,
        )

    # ---- поток ----
    def _run(self):
        try:
            self._conn = self._open_db()
            self._probe()
            last_commit = time.time()
            while not self._stop.is_set():
                t0 = time.time()
                sample = self._read_sample()
                self._insert(sample)
                # коммит пачками раз в секунду
                if t0 - last_commit > 1.0:
                    self._conn.commit()
                    last_commit = t0
                dt = self.period - (time.time() - t0)
                if dt > 0:
                    time.sleep(dt)
            self._conn.commit()
        except Exception as e:
            self.error = e
            print(f"[telemetry] error: {e}")
        finally:
            if self._conn:
                try:
                    self._conn.commit()
                    self._conn.close()
                except Exception:
                    pass

    # ---- API ----
    def start(self):
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name='telemetry')
        self._thread.start()
        print(f"[telemetry] started, db={self.db_path}, period={self.period}s")

    def stop(self):
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._thread = None
        print("[telemetry] stopped")

    def latest(self, n=1):
        """Удобный доступ к последним n записям (для UI)."""
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                "SELECT * FROM samples ORDER BY ts DESC LIMIT ?", (n,)
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()
