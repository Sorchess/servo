# ui/tabs/manual_tab.py
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QLineEdit
from PyQt6.QtCore import Qt
from core import manual_controller
from core import servo_commands
from utils import config


class ManualTab(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller

        layout = QVBoxLayout()

        # --- Кнопка ручного управления ---
        self.btn_manual = QPushButton("Ручное управление")
        self.btn_manual.setCheckable(True)
        self.status_manual = QLabel("Ручное управление: выключено")
        layout.addWidget(self.btn_manual)
        layout.addWidget(self.status_manual)

        # --- Кнопка питания привода ---
        self.btn_power = QPushButton("Питание привода")
        self.btn_power.setCheckable(True)
        self.status_power = QLabel("Питание привода: выключено")
        layout.addWidget(self.btn_power)
        layout.addWidget(self.status_power)

        # --- Чтение позиции ---
        layout.addWidget(QLabel("Позиция привода:"))
        self.pos_display = QLineEdit()
        self.pos_display.setReadOnly(True)
        layout.addWidget(self.pos_display)

        # --- Поле для ввода позиции и кнопка перемещения ---
        self.input_position = QLineEdit()
        self.input_position.setPlaceholderText("Введите позицию")
        self.btn_move = QPushButton("Переместить ось")
        self.status_move = QLabel("Статус: ожидание")
        layout.addWidget(QLabel("Перемещение оси:"))
        layout.addWidget(self.input_position)
        layout.addWidget(self.btn_move)
        layout.addWidget(self.status_move)



        self.setLayout(layout)

        # Подключаем сигналы
        self.btn_manual.toggled.connect(self.update_manual_mode)
        self.btn_power.toggled.connect(self.update_power_mode)
        self.btn_move.clicked.connect(self.move_axis)

        self.task_pos = manual_controller.schedule(self.read_position, dt=0.1)

    def update_manual_mode(self, checked: bool):
        """Переключение ручного управления"""
        if checked:
            self.controller.set_mode("manual")
            self.status_manual.setText("Ручное управление: включено")
            self.btn_manual.setStyleSheet("background-color: lightgreen;")
        else:
            self.controller.set_mode(None)
            self.status_manual.setText("Ручное управление: выключено")
            self.btn_manual.setStyleSheet("")

    def update_power_mode(self, checked: bool):

        """Переключение питания"""
        if checked:
            manual_controller.set_flag("power", True)
            print("⚡ Питание привода ВКЛ")
            self.status_power.setText("Питание привода: включено")
            self.btn_power.setStyleSheet("background-color: lightgreen;")
        else:
            manual_controller.set_flag("power", False)
            print("🔌 Питание привода ВЫКЛ")
            self.status_power.setText("Питание привода: выключено")
            self.btn_power.setStyleSheet("")



    def read_position(self, master):
            """Обёртка для чтения позиции и обновления UI"""
            pos = servo_commands.READ_POS_SCALE(master)
            self.pos_display.setText(str(pos))
            return pos

    def move_axis(self):
        """Единичное перемещение оси"""
        text = self.input_position.text()
        try:
            value = int(text)
            scaled_value = int(value*config.PRECESION_SCALER)
        except ValueError:
            self.status_move.setText("Ошибка: введите число")
            return

        manual_controller.oneshot(servo_commands.MOVE_AXIS_TO, scaled_value)
        self.status_move.setText(f"Команда отправлена: {value}")