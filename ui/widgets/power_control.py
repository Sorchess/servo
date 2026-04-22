from PyQt6.QtWidgets import QWidget, QPushButton, QVBoxLayout
from core import servo_commands

class PowerControlWidget(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.power_on = False

        self.button = QPushButton("Питание OFF")
        self.button.clicked.connect(self.toggle_power)

        layout = QVBoxLayout()
        layout.addWidget(self.button)
        self.setLayout(layout)

    def toggle_power(self):
        master = self.controller.get_master()  # ← всегда берём актуальный master
        if not master:
            print("[PowerControl] Ошибка: master не установлен")
            return

        try:
            if not self.power_on:
                servo_commands.POWER_ON(master)
                self.button.setText("Питание ON")
                self.power_on = True
                print("[PowerControl] Питание включено")
            else:
                servo_commands.POWER_OFF(master)
                self.button.setText("Питание OFF")
                self.power_on = False
                print("[PowerControl] Питание выключено")
        except Exception as e:
            print(f"[PowerControl] Ошибка питания: {e}")
