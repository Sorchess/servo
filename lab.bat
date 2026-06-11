@echo off
REM ============================================================
REM  lab.bat — единый диспетчер лабораторных CLI-команд.
REM
REM  Никакой бизнес-логики: только зовёт уже существующие
REM  python -m tools.* и pytest. Управление приводом (Servo ON/OFF,
REM  motion) этим скриптом НЕ выполняется — по требованиям ТЗ.
REM
REM  Запускать из корня проекта: C:\Users\stepan\Projects\servo
REM ============================================================

setlocal EnableExtensions EnableDelayedExpansion

REM --- выбор интерпретатора Python ---
REM Сначала пробуем локальный .venv (надёжнее, чем activate.bat: не зависит
REM от состояния PATH в текущем окне cmd). Потом py-launcher. И только
REM в крайнем случае — голый "python", который в Windows часто оказывается
REM Microsoft-Store-stub-ом и молча падает с печатью "Python".
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    where py >nul 2>nul
    if not errorlevel 1 (
        set "PY=py -3"
    ) else (
        set "PY=python"
    )
)
echo [lab] using interpreter: %PY%

REM --- гарантируем структуру каталогов lab_data ---
if not exist "lab_data"          mkdir "lab_data"
if not exist "lab_data\raw"      mkdir "lab_data\raw"
if not exist "lab_data\exports"  mkdir "lab_data\exports"
if not exist "lab_data\runners"  mkdir "lab_data\runners"

set "CMD=%~1"
if "%CMD%"=="" goto :help
shift

if /I "%CMD%"=="help"             goto :help
if /I "%CMD%"=="-h"               goto :help
if /I "%CMD%"=="--help"           goto :help
if /I "%CMD%"=="test"             goto :test
if /I "%CMD%"=="passport"         goto :passport
if /I "%CMD%"=="passport-connect" goto :passport_connect
if /I "%CMD%"=="record"           goto :record
if /I "%CMD%"=="dry-record"       goto :dry_record
if /I "%CMD%"=="export"           goto :export
if /I "%CMD%"=="validate"         goto :validate
if /I "%CMD%"=="validate-all"     goto :validate_all
if /I "%CMD%"=="run"              goto :run
if /I "%CMD%"=="auto"             goto :auto
if /I "%CMD%"=="dry-auto"         goto :dry_auto
if /I "%CMD%"=="sid"              goto :sid_cmd

REM --- готовые пресеты по плану статьи ---
if /I "%CMD%"=="A1" goto :preset_A1
if /I "%CMD%"=="A2" goto :preset_A2
if /I "%CMD%"=="A3" goto :preset_A3
if /I "%CMD%"=="A4" goto :preset_A4
if /I "%CMD%"=="B1" goto :preset_B1
if /I "%CMD%"=="B2" goto :preset_B2
if /I "%CMD%"=="B3" goto :preset_B3
if /I "%CMD%"=="B4" goto :preset_B4
if /I "%CMD%"=="B5" goto :preset_B5
if /I "%CMD%"=="C1" goto :preset_C1
if /I "%CMD%"=="C2" goto :preset_C2
if /I "%CMD%"=="C3" goto :preset_C3
if /I "%CMD%"=="C4" goto :preset_C4
if /I "%CMD%"=="C5" goto :preset_C5
if /I "%CMD%"=="D1" goto :preset_D1
if /I "%CMD%"=="D2" goto :preset_D2
if /I "%CMD%"=="D3" goto :preset_D3
if /I "%CMD%"=="E1" goto :preset_E1
if /I "%CMD%"=="E2" goto :preset_E2

echo [lab] Unknown command: %CMD%
echo Use: lab help
exit /b 1

REM ============================================================
:help
echo.
echo  lab.bat — диспетчер лабораторных команд
echo  ------------------------------------------------------------
echo  Базовое:
echo    lab test                          pytest по лабораторному слою
echo    lab sid ^<EXP^> ^<REP^>               напечатать SID = EXP_RNN_YYYYMMDD_HHMMSS
echo.
echo  Паспорт стенда:
echo    lab passport                      без подключения (поля rated_* пустые)
echo    lab passport-connect              с автозаполнением из EtherCAT
echo.
echo  Запись (готовая команда: одно нажатие = запись + строка в журнал):
echo    lab record  ^<EXP^> ^<REP^> ^<regime^> ^<risk^> ^<load^> ^<dir^> ^<vel^> ^<dur_s^> ^<anom^> "^<comment^>"
echo        пример: lab record A1 01 holding normal no_load none 0 120 0 "Servo ON, hold"
echo    lab dry-record ...                то же, но без подключения к приводу (только запись метаданных)
echo.
echo  Экспорт и валидация:
echo    lab export   ^<SID^> ^<regime^> ^<risk^> ^<load^> ^<dir^> ^<vel^> ^<anom^> "^<comment^>"
echo        ищет lab_data\raw\^<SID^>.sqlite3, кладёт CSV в lab_data\exports\^<SID^>.csv
echo    lab validate ^<CSV^>                один файл
echo    lab validate-all                  все CSV в lab_data\exports
echo.
echo  Полный цикл одной командой (record + export + validate):
echo    lab run ^<EXP^> ^<REP^> ^<regime^> ^<risk^> ^<load^> ^<dir^> ^<vel^> ^<dur_s^> ^<anom^> "^<comment^>"
echo.
echo  АКТИВНЫЙ оркестратор (сам Servo ON + motion + Servo OFF):
echo    lab auto     ^<EXP^> ^<REP^> ^<scenario^> ^<regime^> ^<risk^> ^<load^> ^<dir^> ^<vel^> ^<acc^> ^<dur_s^> ^<anom^> "^<comment^>"
echo    lab dry-auto ...               то же, но без подключения к приводу
echo      сценарии: hold ^| move_then_hold ^| rotate_const ^| rotate_reversal ^| ramp_up_down ^| cyclic
echo.
echo  Готовые пресеты по плану (повторность передаётся параметром):
echo    lab A1 ^<REP^>          Servo ON, удержание, 120 с
echo    lab A2 ^<REP^>          удержание после перемещения, 120 с
echo    lab A3 ^<REP^>          вращение в одну сторону, малая скорость, 90 с
echo    lab A4 ^<REP^>          вращение в обе стороны, 90 с
echo    lab B1 ^<REP^>          медленный разгон/торможение, 90 с
echo    lab B2 ^<REP^>          средний разгон/торможение, 90 с
echo    lab B3 ^<REP^>          резкий, но допустимый разгон, 90 с
echo    lab B4 ^<REP^>          реверс, 90 с
echo    lab B5 ^<REP^>          циклический туда-обратно, 300 с
echo    lab C1..C5 ^<REP^>      ступени скорости 300/600/900/1200/1500 RPM, 60 с
echo    lab D1..D3 ^<REP^>      длительные циклические записи, 600 с
echo    lab E1..E2 ^<REP^>      нагрузочные опыты (только после согласования)
echo.
echo  Белые списки:
echo    regime: idle holding acceleration constant_speed deceleration reversal cyclic
echo            load_low load_medium synthetic_anomaly
echo    risk  : normal transition elevated_load pre_emergency_proxy fault
echo    anom  : 0 ^| 1
echo.
exit /b 0

REM ============================================================
:test
%PY% -m pytest -q tests\test_lab_layer.py
exit /b %ERRORLEVEL%

REM ============================================================
:sid_cmd
REM lab sid <EXP> <REP>  ->  печатает SID
set "EXP=%~1"
set "REP=%~2"
if "%EXP%"=="" ( echo [lab sid] нужны EXP и REP & exit /b 1 )
if "%REP%"=="" ( echo [lab sid] нужны EXP и REP & exit /b 1 )
call :make_ts
set "SID=%EXP%_R%REP%_%TS%"
echo %SID%
exit /b 0

REM ============================================================
REM Внутренняя процедура: вычислить TS = YYYYMMDD_HHMMSS
:make_ts
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%a"
goto :eof

REM ============================================================
:passport
%PY% -m tools.create_stand_passport ^
  --out lab_data\stand_passport.csv ^
  --stand-id stand-01 ^
  --operator "Stepan" ^
  --drive-model ASD-B3-0421-E ^
  --drive-serial DRV-12345 ^
  --motor-model ECM-B3M-CA0604RS1 ^
  --motor-serial MOT-67890
exit /b %ERRORLEVEL%

:passport_connect
%PY% -m tools.create_stand_passport ^
  --out lab_data\stand_passport.csv ^
  --stand-id stand-01 ^
  --operator "Stepan" ^
  --drive-model ASD-B3-0421-E ^
  --drive-serial DRV-12345 ^
  --motor-model ECM-B3M-CA0604RS1 ^
  --motor-serial MOT-67890 ^
  --connect
exit /b %ERRORLEVEL%

REM ============================================================
REM lab record <EXP> <REP> <regime> <risk> <load> <dir> <vel> <dur_s> <anom> "<comment>"
:record
call :parse_args %*
if errorlevel 1 exit /b 1
call :make_ts
set "SID=%EXP%_R%REP%_%TS%"
set "DB=lab_data\raw\%SID%.sqlite3"
echo [lab record] SID=%SID%
echo [lab record] DB =%DB%
%PY% -m tools.start_lab_session ^
  --experiment-id %EXP% ^
  --session-id   %SID% ^
  --device-id    delta-asda-b3-e ^
  --db "%DB%" ^
  --experiments-csv lab_data\experiments.csv ^
  --duration %DUR% ^
  --period-ms 100 ^
  --experiment-type %REGIME% ^
  --regime-label %REGIME% ^
  --risk-label   %RISK% ^
  --load-type    %LOAD% ^
  --direction    %DIR% ^
  --target-velocity %VEL% ^
  --operator-comment "%COMMENT%" ^
  --is-artificial-anomaly %ANOM%
exit /b %ERRORLEVEL%

REM То же, но без подключения к приводу (для отладки CLI / dry-run).
:dry_record
call :parse_args %*
if errorlevel 1 exit /b 1
call :make_ts
set "SID=%EXP%_R%REP%_%TS%"
set "DB=lab_data\raw\%SID%.sqlite3"
echo [lab dry-record] SID=%SID%
%PY% -m tools.start_lab_session ^
  --experiment-id %EXP% ^
  --session-id   %SID% ^
  --device-id    delta-asda-b3-e ^
  --db "%DB%" ^
  --experiments-csv lab_data\experiments.csv ^
  --duration %DUR% ^
  --period-ms 100 ^
  --experiment-type %REGIME% ^
  --regime-label %REGIME% ^
  --risk-label   %RISK% ^
  --load-type    %LOAD% ^
  --direction    %DIR% ^
  --target-velocity %VEL% ^
  --operator-comment "%COMMENT%" ^
  --is-artificial-anomaly %ANOM% ^
  --no-connect
exit /b %ERRORLEVEL%

REM ============================================================
REM lab export <SID> <regime> <risk> <load> <dir> <vel> <anom> "<comment>"
:export
set "SID=%~1"
set "REGIME=%~2"
set "RISK=%~3"
set "LOAD=%~4"
set "DIR=%~5"
set "VEL=%~6"
set "ANOM=%~7"
set "COMMENT=%~8"
if "%SID%"==""    ( echo [lab export] нужен SID & exit /b 1 )
if "%REGIME%"=="" ( echo [lab export] нужен regime & exit /b 1 )
if "%RISK%"==""   ( echo [lab export] нужен risk & exit /b 1 )
set "DB=lab_data\raw\%SID%.sqlite3"
set "CSV=lab_data\exports\telemetry_%SID%.csv"
if not exist "%DB%" (
    echo [lab export] не найден %DB%
    exit /b 1
)
REM EXPERIMENT_ID = первая часть SID до "_R" — для удобства.
for /f "tokens=1 delims=_" %%a in ("%SID%") do set "EXP=%%a"
%PY% -m tools.export_lab_csv ^
  --db "%DB%" ^
  --out "%CSV%" ^
  --experiment-id %EXP% ^
  --session-id %SID% ^
  --device-id delta-asda-b3-e ^
  --experiment-type %REGIME% ^
  --regime-label %REGIME% ^
  --risk-label   %RISK% ^
  --load-type    %LOAD% ^
  --direction    %DIR% ^
  --target-velocity %VEL% ^
  --operator-comment "%COMMENT%" ^
  --is-artificial-anomaly %ANOM%
exit /b %ERRORLEVEL%

REM ============================================================
:validate
set "CSV=%~1"
if "%CSV%"=="" ( echo [lab validate] нужен путь к CSV & exit /b 1 )
%PY% -m tools.validate_lab_csv "%CSV%"
exit /b %ERRORLEVEL%

:validate_all
set "ANY=0"
for %%F in (lab_data\exports\*.csv) do (
    set "ANY=1"
    echo --- %%F ---
    %PY% -m tools.validate_lab_csv "%%F"
)
if "%ANY%"=="0" echo [lab validate-all] в lab_data\exports пусто
exit /b 0

REM ============================================================
REM lab run <EXP> <REP> <regime> <risk> <load> <dir> <vel> <dur_s> <anom> "<comment>"
:run
call :parse_args %*
if errorlevel 1 exit /b 1
call :make_ts
set "SID=%EXP%_R%REP%_%TS%"
set "DB=lab_data\raw\%SID%.sqlite3"
set "CSV=lab_data\exports\telemetry_%SID%.csv"

echo ============================================================
echo [lab run] EXP=%EXP%  REP=%REP%  SID=%SID%
echo [lab run] regime=%REGIME%  risk=%RISK%  load=%LOAD%  dir=%DIR%  vel=%VEL%  dur=%DUR%s  anom=%ANOM%
echo ============================================================

echo [lab run] (1/3) RECORD
%PY% -m tools.start_lab_session ^
  --experiment-id %EXP% ^
  --session-id   %SID% ^
  --device-id    delta-asda-b3-e ^
  --db "%DB%" ^
  --experiments-csv lab_data\experiments.csv ^
  --duration %DUR% ^
  --period-ms 100 ^
  --experiment-type %REGIME% ^
  --regime-label %REGIME% ^
  --risk-label   %RISK% ^
  --load-type    %LOAD% ^
  --direction    %DIR% ^
  --target-velocity %VEL% ^
  --operator-comment "%COMMENT%" ^
  --is-artificial-anomaly %ANOM%
if errorlevel 1 ( echo [lab run] RECORD FAILED & exit /b 1 )

echo [lab run] (2/3) EXPORT -^> %CSV%
%PY% -m tools.export_lab_csv ^
  --db "%DB%" ^
  --out "%CSV%" ^
  --experiment-id %EXP% ^
  --session-id %SID% ^
  --device-id delta-asda-b3-e ^
  --experiment-type %REGIME% ^
  --regime-label %REGIME% ^
  --risk-label   %RISK% ^
  --load-type    %LOAD% ^
  --direction    %DIR% ^
  --target-velocity %VEL% ^
  --operator-comment "%COMMENT%" ^
  --is-artificial-anomaly %ANOM%
if errorlevel 1 ( echo [lab run] EXPORT FAILED & exit /b 1 )

echo [lab run] (3/3) VALIDATE %CSV%
%PY% -m tools.validate_lab_csv "%CSV%"
exit /b %ERRORLEVEL%

REM ============================================================
REM lab auto <EXP> <REP> <scenario> <regime> <risk> <load> <dir> <vel> <acc> <dur_s> <anom> "<comment>"
REM Активный оркестратор: сам поднимает Servo ON, выполняет движение,
REM пишет телеметрию, опускает Servo OFF. Завершает экспортом + валидацией.
:auto
call :parse_auto_args %*
if errorlevel 1 exit /b 1
call :make_ts
set "SID=%EXP%_R%REP%_%TS%"
set "DB=lab_data\raw\%SID%.sqlite3"
set "CSV=lab_data\exports\telemetry_%SID%.csv"

echo ============================================================
echo [lab auto] EXP=%EXP%  REP=%REP%  SID=%SID%
echo [lab auto] scenario=%SCEN%  regime=%REGIME%  risk=%RISK%  load=%LOAD%
echo [lab auto] dir=%DIR%  vel=%VEL%  acc=%ACC%  dur=%DUR%s  anom=%ANOM%
echo ============================================================

echo [lab auto] (1/3) RUN ORCHESTRATOR
%PY% -m tools.run_experiment ^
  --experiment-id %EXP% ^
  --session-id   %SID% ^
  --device-id    delta-asda-b3-e ^
  --db "%DB%" ^
  --experiments-csv lab_data\experiments.csv ^
  --scenario %SCEN% ^
  --duration %DUR% ^
  --period-ms 100 ^
  --target-rpm %VEL% ^
  --acceleration %ACC% ^
  --deceleration %ACC% ^
  --experiment-type %REGIME% ^
  --regime-label %REGIME% ^
  --risk-label   %RISK% ^
  --load-type    %LOAD% ^
  --direction    %DIR% ^
  --operator-comment "%COMMENT%" ^
  --is-artificial-anomaly %ANOM%
if errorlevel 1 ( echo [lab auto] RUN FAILED & exit /b 1 )

echo [lab auto] (2/3) EXPORT -^> %CSV%
%PY% -m tools.export_lab_csv ^
  --db "%DB%" ^
  --out "%CSV%" ^
  --experiment-id %EXP% ^
  --session-id %SID% ^
  --device-id delta-asda-b3-e ^
  --experiment-type %REGIME% ^
  --regime-label %REGIME% ^
  --risk-label   %RISK% ^
  --load-type    %LOAD% ^
  --direction    %DIR% ^
  --target-velocity %VEL% ^
  --operator-comment "%COMMENT%" ^
  --is-artificial-anomaly %ANOM%
if errorlevel 1 ( echo [lab auto] EXPORT FAILED & exit /b 1 )

echo [lab auto] (3/3) VALIDATE %CSV%
%PY% -m tools.validate_lab_csv "%CSV%"
exit /b %ERRORLEVEL%

REM То же, но без подключения к приводу: оркестратор пишет только строку
REM в журнал, exports/validate не выполняются (БД нет).
:dry_auto
call :parse_auto_args %*
if errorlevel 1 exit /b 1
call :make_ts
set "SID=%EXP%_R%REP%_%TS%"
set "DB=lab_data\raw\%SID%.sqlite3"
echo [lab dry-auto] SID=%SID%
%PY% -m tools.run_experiment ^
  --experiment-id %EXP% ^
  --session-id   %SID% ^
  --device-id    delta-asda-b3-e ^
  --db "%DB%" ^
  --experiments-csv lab_data\experiments.csv ^
  --scenario %SCEN% ^
  --duration %DUR% ^
  --period-ms 100 ^
  --target-rpm %VEL% ^
  --acceleration %ACC% ^
  --deceleration %ACC% ^
  --experiment-type %REGIME% ^
  --regime-label %REGIME% ^
  --risk-label   %RISK% ^
  --load-type    %LOAD% ^
  --direction    %DIR% ^
  --operator-comment "%COMMENT%" ^
  --is-artificial-anomaly %ANOM% ^
  --no-connect
exit /b %ERRORLEVEL%

REM ============================================================
REM Парсер позиционных аргументов для auto / dry-auto.
REM
REM ВНИМАНИЕ: %* в cmd.exe НЕ учитывает shift в основном теле, поэтому
REM при прямом вызове "lab.bat auto ..." первым аргументом окажется
REM "auto"/"dry-auto". При call :auto из пресетов префикса нет.
REM Аккуратно поглощаем оба случая.
REM
REM Ожидаемый порядок (без префикса команды):
REM   %1=EXP %2=REP %3=scenario %4=regime %5=risk %6=load %7=dir
REM   %8=vel %9=acc %10=dur %11=anom %12="comment"
:parse_auto_args
if /I "%~1"=="auto"     shift
if /I "%~1"=="dry-auto" shift
set "EXP=%~1"
set "REP=%~2"
set "SCEN=%~3"
set "REGIME=%~4"
set "RISK=%~5"
set "LOAD=%~6"
set "DIR=%~7"
set "VEL=%~8"
set "ACC=%~9"
shift & shift & shift & shift & shift & shift & shift & shift & shift
set "DUR=%~1"
set "ANOM=%~2"
set "COMMENT=%~3"
if "%EXP%"==""    ( echo [lab auto] missing EXP      & exit /b 1 )
if "%REP%"==""    ( echo [lab auto] missing REP      & exit /b 1 )
if "%SCEN%"==""   ( echo [lab auto] missing scenario & exit /b 1 )
if "%REGIME%"=="" ( echo [lab auto] missing regime   & exit /b 1 )
if "%RISK%"==""   ( echo [lab auto] missing risk     & exit /b 1 )
if "%LOAD%"==""   ( echo [lab auto] missing load     & exit /b 1 )
if "%DIR%"==""    ( echo [lab auto] missing dir      & exit /b 1 )
if "%VEL%"==""    set "VEL=0"
if "%ACC%"==""    set "ACC=1000"
if "%DUR%"==""    ( echo [lab auto] missing duration & exit /b 1 )
if "%ANOM%"==""   set "ANOM=0"
if "%COMMENT%"=="" set "COMMENT="
exit /b 0

REM ============================================================
REM Парсер позиционных аргументов для record / dry-record / run:
REM   %1=EXP %2=REP %3=regime %4=risk %5=load %6=dir %7=vel %8=dur %9=anom %10="comment"
:parse_args
set "EXP=%~1"
set "REP=%~2"
set "REGIME=%~3"
set "RISK=%~4"
set "LOAD=%~5"
set "DIR=%~6"
set "VEL=%~7"
set "DUR=%~8"
set "ANOM=%~9"
shift & shift & shift & shift & shift & shift & shift & shift & shift
set "COMMENT=%~1"
if "%EXP%"==""    ( echo [lab] missing EXP    & exit /b 1 )
if "%REP%"==""    ( echo [lab] missing REP    & exit /b 1 )
if "%REGIME%"=="" ( echo [lab] missing regime & exit /b 1 )
if "%RISK%"==""   ( echo [lab] missing risk   & exit /b 1 )
if "%LOAD%"==""   ( echo [lab] missing load   & exit /b 1 )
if "%DIR%"==""    ( echo [lab] missing dir    & exit /b 1 )
if "%VEL%"==""    set "VEL=0"
if "%DUR%"==""    ( echo [lab] missing duration & exit /b 1 )
if "%ANOM%"==""   set "ANOM=0"
if "%COMMENT%"=="" set "COMMENT="
exit /b 0

REM ============================================================
REM ===============   ПРЕСЕТЫ ПО ПЛАНУ СТАТЬИ   ================
REM Каждый пресет принимает один параметр — номер повторности RNN
REM (например: lab A1 01).
REM ============================================================

REM Пресеты теперь используют активный оркестратор (:auto), а не пассивный
REM start_lab_session. Привод сам поднимается, выполняет движение и опускается.
REM Сценарии: hold / move_then_hold / rotate_const / rotate_reversal /
REM           ramp_up_down / cyclic (см. tools/run_experiment.py).

:preset_A1
call :auto A1_HOLDING_SERVO_ON %~1 hold            holding         normal              no_load        none 0    1000 120 0 "A1: Servo ON, shaft stationary"
exit /b %ERRORLEVEL%

:preset_A2
call :auto A2_HOLD_AFTER_MOVE  %~1 move_then_hold  holding         normal              no_load        none 0    1000 120 0 "A2: hold position after move"
exit /b %ERRORLEVEL%

:preset_A3
call :auto A3_ROTATE_ONE_DIR   %~1 rotate_const    constant_speed  normal              no_load        cw   300  1000  90 0 "A3: rotate CW low speed"
exit /b %ERRORLEVEL%

:preset_A4
call :auto A4_ROTATE_BOTH_DIRS %~1 rotate_reversal reversal        normal              no_load        both 300  1000  90 0 "A4: rotate both directions"
exit /b %ERRORLEVEL%

:preset_B1
call :auto B1_ACC_DEC_SLOW     %~1 ramp_up_down    acceleration    normal              no_load        cw   600   500  90 0 "B1: slow acc/dec"
exit /b %ERRORLEVEL%

:preset_B2
call :auto B2_ACC_DEC_MEDIUM   %~1 ramp_up_down    acceleration    transition          no_load        cw   900  1500  90 0 "B2: medium acc/dec"
exit /b %ERRORLEVEL%

:preset_B3
call :auto B3_ACC_DEC_SHARP    %~1 ramp_up_down    acceleration    elevated_load       no_load        cw   1200 3000  90 0 "B3: sharp but allowed acc/dec"
exit /b %ERRORLEVEL%

:preset_B4
call :auto B4_REVERSAL         %~1 rotate_reversal reversal        transition          no_load        both 600  1000  90 0 "B4: reversal"
exit /b %ERRORLEVEL%

:preset_B5
call :auto B5_CYCLIC           %~1 cyclic          cyclic          transition          no_load        both 600  1000 300 0 "B5: cyclic back-and-forth"
exit /b %ERRORLEVEL%

:preset_C1
call :auto C1_SPEED_300        %~1 rotate_const    constant_speed  normal              no_load        cw   300  1000  60 0 "C1: 300 rpm"
exit /b %ERRORLEVEL%

:preset_C2
call :auto C2_SPEED_600        %~1 rotate_const    constant_speed  normal              no_load        cw   600  1000  60 0 "C2: 600 rpm"
exit /b %ERRORLEVEL%

:preset_C3
call :auto C3_SPEED_900        %~1 rotate_const    constant_speed  normal              no_load        cw   900  1000  60 0 "C3: 900 rpm"
exit /b %ERRORLEVEL%

:preset_C4
call :auto C4_SPEED_1200       %~1 rotate_const    constant_speed  transition          no_load        cw   1200 1000  60 0 "C4: 1200 rpm"
exit /b %ERRORLEVEL%

:preset_C5
call :auto C5_SPEED_1500       %~1 rotate_const    constant_speed  transition          no_load        cw   1500 1000  60 0 "C5: 1500 rpm"
exit /b %ERRORLEVEL%

:preset_D1
call :auto D1_LONG_CYCLE_LOW   %~1 cyclic          cyclic          normal              no_load        both 600  1000 600 0 "D1: long cycle, low speed"
exit /b %ERRORLEVEL%

:preset_D2
call :auto D2_LONG_CYCLE_MED   %~1 cyclic          cyclic          transition          no_load        both 900  1000 600 0 "D2: long cycle, medium speed"
exit /b %ERRORLEVEL%

:preset_D3
call :auto D3_LONG_CYCLE_HARD  %~1 cyclic          cyclic          elevated_load       no_load        both 1200 1500 600 0 "D3: long cycle, frequent acc/dec"
exit /b %ERRORLEVEL%

:preset_E1
call :auto E1_LOAD_LOW_INERTIA %~1 rotate_const    load_low        elevated_load       inertia_small  cw   600  1000 120 0 "E1: small inertia load (approved)"
exit /b %ERRORLEVEL%

:preset_E2
call :auto E2_LOAD_MED_INERTIA %~1 rotate_const    load_medium     pre_emergency_proxy inertia_medium cw   600  1000 120 0 "E2: medium inertia, pre-emergency proxy"
exit /b %ERRORLEVEL%
