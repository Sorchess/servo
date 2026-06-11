import sqlite3
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.ensemble import IsolationForest
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.preprocessing import StandardScaler

# ==========================================
# 1. ЗАГРУЗКА И ПОДГОТОВКА ДАННЫХ
# ==========================================
conn = sqlite3.connect('telemetry.sqlite3')
query = "SELECT velocity as speed, current_A as current FROM samples"
df_real = pd.read_sql_query(query, conn)
conn.close()

df_real = df_real.dropna()
df_real['current'] = df_real['current'].abs()

# ==========================================
# 2. DATA AUGMENTATION (Расширение данных)
# ==========================================
augmented_dfs = []
for i in range(100):
    df_copy = df_real.copy()
    # Добавляем случайный шум
    df_copy['speed'] += np.random.normal(0, 5.0, len(df_copy))
    df_copy['current'] += np.random.normal(0, 0.01, len(df_copy))
    df_copy['current'] = df_copy['current'].abs()
    augmented_dfs.append(df_copy)
    
df_large = pd.concat(augmented_dfs, ignore_index=True)
df_large['label'] = 1 # Норма

# ==========================================
# 3. ВНЕДРЕНИЕ АНОМАЛИЙ (Симуляция поломок)
# ==========================================
np.random.seed(42)
N_ANOMALIES = 150 # Увеличим количество аномалий для красивой статистики
anomaly_indices = np.random.choice(df_large.index, size=N_ANOMALIES, replace=False)

for idx in anomaly_indices:
    # Имитируем клин: скорость падает, ток резко возрастает
    df_large.at[idx, 'current'] = np.random.uniform(0.4, 0.9) 
    df_large.at[idx, 'speed'] *= np.random.uniform(0.1, 0.4)
    df_large.at[idx, 'label'] = -1

# ==========================================
# 4. ОБУЧЕНИЕ МОДЕЛИ И ПРЕДСКАЗАНИЕ
# ==========================================
scaler = StandardScaler()
X_train = scaler.fit_transform(df_real[['speed', 'current']])
X_test = scaler.transform(df_large[['speed', 'current']])

# Настраиваем contamination пропорционально количеству аномалий + небольшой запас на шум
expected_outliers_fraction = (N_ANOMALIES / len(df_large)) * 1.5 

model = IsolationForest(contamination=expected_outliers_fraction, random_state=42)
model.fit(X_train)

y_true = df_large['label']
y_pred = model.predict(X_test)
df_large['predicted'] = y_pred

# ==========================================
# 5. ВИЗУАЛИЗАЦИЯ (Matplotlib & Seaborn)
# ==========================================
# Настраиваем стиль графиков
sns.set_theme(style="darkgrid")
fig, axes = plt.subplots(1, 2, figsize=(16, 6))
fig.suptitle('Результаты работы Isolation Forest (Слой 1: Детекция)', fontsize=18, fontweight='bold')

# --- График 1: Диаграмма рассеяния (Scatter Plot) ---
# Рисуем нормальные данные
sns.scatterplot(
    ax=axes[0],
    data=df_large[df_large['predicted'] == 1], 
    x='speed', y='current', 
    color='#2ecc71', label='Норма (Предсказано)', 
    alpha=0.5, s=10
)
# Рисуем найденные аномалии
sns.scatterplot(
    ax=axes[0],
    data=df_large[df_large['predicted'] == -1], 
    x='speed', y='current', 
    color='#e74c3c', label='Аномалия (Предсказано)', 
    alpha=0.9, s=30, edgecolor='black'
)
axes[0].set_title('Пространство признаков (Скорость vs Ток)', fontsize=14)
axes[0].set_xlabel('Скорость (Velocity)', fontsize=12)
axes[0].set_ylabel('Потребляемый ток (Current_A)', fontsize=12)
axes[0].legend()

# --- График 2: Матрица ошибок (Confusion Matrix) ---
cm = confusion_matrix(y_true, y_pred, labels=[-1, 1])
# cm[0,0] = True Positive (Аномалия-Аномалия)
# cm[0,1] = False Negative (Аномалия-Норма)
# cm[1,0] = False Positive (Норма-Аномалия)
# cm[1,1] = True Negative (Норма-Норма)

# Преобразуем для красивого отображения (меняем порядок, чтобы было логично)
# Строки: Факт, Столбцы: Прогноз
cm_display = pd.DataFrame(
    cm, 
    index=['Факт: Аномалия', 'Факт: Норма'], 
    columns=['Прогноз: Аномалия', 'Прогноз: Норма']
)

sns.heatmap(cm_display, annot=True, fmt='d', cmap='Blues', ax=axes[1], annot_kws={"size": 14})
axes[1].set_title('Матрица ошибок (Confusion Matrix)', fontsize=14)

plt.tight_layout()
plt.show()

# ==========================================
# 6. ВЫВОД СТАТИСТИКИ В КОНСОЛЬ
# ==========================================
print("\n" + "="*40)
print("СТАТИСТИКА ДЛЯ ПРЕЗЕНТАЦИИ")
print("="*40)
report = classification_report(y_true, y_pred, target_names=['Аномалия (-1)', 'Норма (1)'], output_dict=True)

# Для машинного обучения "Аномалия" — это класс Positive
print(f"Полнота (Recall)     : {report['Аномалия (-1)']['recall']*100:.1f}%  <-- % найденных поломок")
print(f"Точность (Precision) : {report['Аномалия (-1)']['precision']*100:.1f}%  <-- % правильных тревог")
print(f"F1-Score             : {report['Аномалия (-1)']['f1-score']:.2f}")
print("="*40)