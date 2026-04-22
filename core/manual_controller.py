import time
from core import servo_commands

# ==== Флаги ====
flags = {
    "power": False,
}
_last_flags = flags.copy()

# ==== Циклические задачи ====
_tasks = []  # {"func": callable, "dt": float, "last": float, "active": bool}

# ==== Единичные задачи ====
_one_shots = []  # просто список функций
    

def set_flag(name: str, value: bool):
    """UI вызывает для изменения состояния"""
    if name in flags:
        flags[name] = value
    else:
        print(f"[manual_controller] Warning: флаг {name} не существует!")


def schedule(func, dt: float):
    """Запускает функцию циклически с периодом dt"""
    task = {"func": func, "dt": dt, "last": 0, "active": True}
    _tasks.append(task)
    return task  # вернём ссылку для отключения


def cancel(task):
    """Останавливает циклическую задачу"""
    task["active"] = False


def oneshot(func, *args, **kwargs):
    """Запланировать единичное действие"""
    _one_shots.append((func, args, kwargs))


def run(stop_event, master):
    print("Ручное управление ЗАПУЩЕНО")

    global _last_flags
    while not stop_event.is_set():
        now = time.time()

        # ===== Циклические задачи =====
        for task in _tasks:
            if task["active"] and (now - task["last"] >= task["dt"]):
                try:
                    result = task["func"](master)
                    # if result is not None:
                    #     print(f"[manual_controller] {task['func'].__name__} → {result}")
                except Exception as e:
                    print(f"[manual_controller] Ошибка в {task['func'].__name__}: {e}")
                task["last"] = now

        # ===== Единичные задачи =====
        while _one_shots:
            func, args, kwargs = _one_shots.pop(0)
            try:
                result = func(master, *args, **kwargs)
                print(f"[manual_controller] oneshot {func.__name__} выполнен → {result}")
            except Exception as e:
                print(f"[manual_controller] Ошибка oneshot {func.__name__}: {e}")

        # ===== Событийные действия =====
        for name, value in flags.items():
            if value != _last_flags[name]:
                if name == "power":
                    if value:
                        servo_commands.POWER_ON(master)
                    else:
                        servo_commands.POWER_OFF(master)

                print(f"[manual_controller] {name} изменился: {value}")
                _last_flags[name] = value

        time.sleep(0.05)

    print("Ручное управление: ЗАВЕРШЕНО")
