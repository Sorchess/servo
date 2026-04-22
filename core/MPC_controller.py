import time
import numpy as np
import cvxpy as cp
from utils import config
from core import mode_controller

class MPCController:
    def __init__(self, stop_flag, setpoint=300):
        self.stop_flag = stop_flag
        self.setpoint = setpoint
        self.ball_pos = None
        self.dt = 0.05
        self.horizon = 20
        self.alpha = 0.8

        # === матрицы системы ===
        self.K = 0.79
        self.T = 0.22
        A = np.array([[0,1,0],[0,0,self.K],[0,0,-1/self.T]])
        B = np.array([[0],[0],[1/self.T]])
        C = np.array([[1,0,0]])
        D = np.array([[0]])

        self.A_d = np.eye(A.shape[0]) + A*self.dt
        self.B_d = B*self.dt
        self.C_d = C
        self.D_d = D

        self.Q = np.diag([1,0,0])*600
        self.R = np.eye(1)*10

        self.mpc = MPCSolver(self.A_d, self.B_d, self.C_d, self.horizon, self.Q, self.R, -2000, 2000)

        self.MPC_in = np.zeros((3,1))
        self.last_pos = 0
        self.filtered_output = 0
        self.MPC_out = 0
        self.compute_first_step_flag = True

    def compute(self, ball_pos, servo_pos, setpoint):
        if ball_pos is not None:
            ball_pos_x = ball_pos
        else:
            ball_pos_x = 0.0

        if self.compute_first_step_flag:
            self.last_pos = ball_pos_x
            self.compute_first_step_flag = False

        # Состояние системы: [x, x_dot, servo]
        self.MPC_in[0] = ball_pos_x
        self.MPC_in[1] = (ball_pos_x - self.last_pos)/self.dt
        self.MPC_in[2] = servo_pos
        self.last_pos = ball_pos_x

        # Формируем вектор целей
        ref = np.zeros((3, self.horizon))
        ref[0,:] = setpoint

        u_opt = self.mpc.compute_control(self.MPC_in, ref)
        if u_opt is None:
            return self.MPC_out

        raw_output = int(u_opt[0])
        self.filtered_output = self.alpha*raw_output + (1-self.alpha)*self.filtered_output
        self.MPC_out = int(self.filtered_output)
        print("Выход МПС:", self.MPC_out)
        return self.MPC_out

# ======================================================================
# Класс решения задачи оптимизации MPC
# ======================================================================

class MPCSolver:
    def __init__(self, A, B, C, horizon, Q, R, U_min=-2000, U_max=2000):
        self.A, self.B, self.C = np.array(A), np.array(B), np.array(C)
        self.horizon = horizon
        self.Q, self.R = np.array(Q), np.array(R)
        self.U_min, self.U_max = U_min, U_max

    def compute_control(self, x0, ref):
        n, m = self.A.shape[0], self.B.shape[1]
        X = cp.Variable((n, self.horizon + 1))
        U = cp.Variable((m, self.horizon))

        cost = 0
        constraints = [X[:,0] == x0.flatten()]

        for k in range(self.horizon):
            cost += cp.quad_form(X[:,k] - ref[:,k], self.Q)
            cost += cp.quad_form(U[:,k], self.R)
            constraints += [X[:,k+1] == self.A @ X[:,k] + self.B @ U[:,k]]

            if self.U_min is not None:
                constraints += [U[:,k] >= self.U_min]
            if self.U_max is not None:
                constraints += [U[:,k] <= self.U_max]

        prob = cp.Problem(cp.Minimize(cost), constraints)
        prob.solve()

        if prob.status not in ["optimal", "optimal_inaccurate"]:
            print("Оптимальное решение не найдено!")
            return None

        return U[:,0].value


def mpc_process_entry(stop_flag, ball_position, servo_position, control_signal,
                      new_data_available, control_ready_flag, setpoint):
    # Создаем контроллер ВНУТРИ процесса
    mpc = MPCController(stop_flag, setpoint.value)
    ball_pos = 0
    servo_pos = 0
    while not stop_flag.is_set():
        mpc_start = time.time()
        # Постоянно читаем АКТУАЛЬНЫЕ данные из shared memory
        with new_data_available.get_lock():
            if new_data_available.value:
                # Читаем СВЕЖИЕ данные, которые основной процесс только что записал
                with ball_position.get_lock():
                    ball_pos = ball_position.value
                with servo_position.get_lock():
                    servo_pos = servo_position.value
                new_data_available.value = False

        # Вычисления с актуальными данными...
        control = mpc.compute(ball_pos, servo_pos,setpoint.value)
        # Записываем результат, который основной процесс сразу увидит
        with control_signal.get_lock():
            control_signal.value = control
        with control_ready_flag.get_lock():
            control_ready_flag.value = True
        print("Время цикла: ", time.time()-mpc_start)

        #
        # print("МПС процесс, позиция шара: ", ball_pos)
        # print("МПС процесс, позиция привода: ", servo_pos)
