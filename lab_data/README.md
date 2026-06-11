# lab_data/

Каталог для лабораторных данных по диагностике сервопривода.

Этот каталог исключён из git (см. корневой `.gitignore`), кроме
`.gitkeep` и этого `README.md`. Все собранные данные хранятся **локально**.

## Структура

```
lab_data/
  stand_passport.csv     # паспорт стенда (создаётся один раз)
  experiments.csv        # журнал всех экспериментов (одна строка = одна сессия)
  raw/                   # сырая телеметрия:
    <session_id>.sqlite3       # SQLite, который пишет core/telemetry.TelemetryLogger
    telemetry_<session_id>.csv # стандартизированный CSV для статьи (export_lab_csv)
```

## Как наполнять

1. **Паспорт стенда** — один раз на стенд:
   ```bash
   python -m tools.create_stand_passport \
     --out lab_data/stand_passport.csv \
     --stand-id stand-01 \
     --operator "Иванов И.И." \
     --drive-model "ASD-B3-0421-E" \
     --drive-serial "DRV-12345" \
     --motor-model "ECM-B3M-CA0604RS1" \
     --motor-serial "MOT-67890" \
     --connect          # опционально: попробует прочитать rated_* из привода
   ```
2. **Запись эксперимента** — каждый раз при сборе данных:
   ```bash
   python -m tools.start_lab_session \
     --experiment-id A1_HOLDING_SERVO_ON \
     --session-id A1_R01_$(date +%Y%m%d_%H%M%S) \
     --duration 120 \
     --db lab_data/raw/A1_R01.sqlite3 \
     --experiment-type holding \
     --regime-label holding \
     --risk-label normal \
     --load-type no_load \
     --direction none \
     --target-velocity 0 \
     --operator-comment "Servo ON, shaft stationary"
   ```
   Альтернатива — вкладка «Лабораторная запись» в UI.
3. **Экспорт в CSV** для статьи:
   ```bash
   python -m tools.export_lab_csv \
     --db lab_data/raw/A1_R01.sqlite3 \
     --out lab_data/raw/telemetry_A1_R01.csv \
     --experiment-id A1_HOLDING_SERVO_ON \
     --session-id A1_R01
   ```
4. **Валидация CSV** перед публикацией датасета:
   ```bash
   python -m tools.validate_lab_csv lab_data/raw/telemetry_A1_R01.csv
   ```

## Источник правды для схем

Все колонки и допустимые значения меток описаны в `core/csv_schema.py`.
Не редактируйте CSV-файлы руками так, чтобы нарушить порядок/состав колонок —
валидатор это поймает и забракует.

## Что вводится вручную, что автоматически

| Поле                                   | Источник                                |
|---------------------------------------|-----------------------------------------|
| `drive_model`, `drive_serial`         | вручную по шильдику                     |
| `motor_model`, `motor_serial`         | вручную по шильдику                     |
| `rated_current_A`                     | SDO `0x6075` (мА → А), если подключено  |
| `rated_torque_Nm`                     | SDO `0x6076` (мН·м → Н·м), если подключено |
| `max_motor_speed_rpm`                 | SDO `0x6080`, если подключено           |
| `experiment_id`, `session_id`, метки  | вручную (CLI или UI)                    |
| `ts`, `statusword`, `position`, ...   | автоматически из `samples` (SQLite)     |
