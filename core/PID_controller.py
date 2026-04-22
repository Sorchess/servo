# core/PID_controller.py
import time
from utils import config
from core import servo_commands


class PIDController:
    def __init__(self, stop_flag, mode_controller, setpoint=300.0):
        self.stop_flag = stop_flag
        self.mode_controller = mode_controller
        self.setpoint = setpoint

        # коэффициенты PID
        self.kp = 6.0
        self.ki = 0.0
        self.kd = 6.0

        # пределы выхода
        self.max_output = 1500
        self.min_output = -1500

        # внутренние переменные
        self.integral = 0.0
        self.last_error = 0.0
        self.last_time = None

        # экспоненциальный фильтр
        self.alpha = 0.3
        self.filtered_output = 0.0

    def compute(self, current_value):
        """Рассчёт управляющего воздействия для одной оси (X)"""
        now = time.time()
        dt = now - self.last_time if self.last_time else 0.01
        self.last_time = now
        print("dt   ",dt)

        error = self.setpoint - current_value

        # интеграл
        self.integral += error * dt

        # производная
        derivative = (error - self.last_error) / dt if dt > 0 else 0.0
        self.last_error = error

        # PID
        output = self.kp * error + self.ki * self.integral + self.kd * derivative

        # масштабирование

        # ограничение выхода
        output = max(min(output, self.max_output), self.min_output)

        # фильтрация
        self.filtered_output = (
            self.alpha * output + (1 - self.alpha) * self.filtered_output
        )


        return self.filtered_output

    def run(self):
        """Основной цикл ПИД-регулятора"""
        while not self.stop_flag.is_set():
            ball_pos = self.mode_controller.get_ball_position()

            if ball_pos is not None:
                x, _ = ball_pos
                print('x     ', x)
                control_signal = self.compute(x)
                control_signal_for_servo = int(control_signal*config.PRECESION_SCALER)

                # передаём сигнал в master (например, в привод)
                master = self.mode_controller.get_master()
                if master:
                    servo_commands.MOVE_AXIS_TO(master, control_signal_for_servo)

            time.sleep(config.PID_DT)
