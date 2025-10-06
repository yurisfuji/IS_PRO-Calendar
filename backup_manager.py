# backup_manager.py
import os
import sqlite3
import zipfile
import tempfile
import shutil
import io
from datetime import datetime


def backup_database():
    """Создает бэкап базы данных и возвращает zip-архив"""
    try:
        # Создаем zip в памяти
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, 'w') as zipf:
            # Добавляем базу данных в архив
            if os.path.exists('production.db'):
                zipf.write('production.db', 'production.db')
            else:
                raise FileNotFoundError("Файл базы данных production.db не найден")

        zip_data = zip_buffer.getvalue()
        zip_buffer.close()

        return zip_data
    except Exception as e:
        raise Exception(f"Ошибка при создании бэкапа: {str(e)}")


def restore_database(uploaded_file):
    """Восстанавливает базу данных из загруженного zip архива"""
    try:
        # Создаем временную директорию для распаковки
        with tempfile.TemporaryDirectory() as temp_dir:
            # Сохраняем загруженный файл
            zip_path = os.path.join(temp_dir, 'backup.zip')
            with open(zip_path, 'wb') as f:
                f.write(uploaded_file.getbuffer())

            # Распаковываем архив
            with zipfile.ZipFile(zip_path, 'r') as zipf:
                zipf.extractall(temp_dir)

            # Проверяем наличие файла базы данных
            db_path = os.path.join(temp_dir, 'production.db')
            if not os.path.exists(db_path):
                raise FileNotFoundError("В архиве не найден файл production.db")

            # Закрываем текущее соединение с БД если оно есть
            if 'conn' in st.session_state:
                st.session_state.conn.close()

            # Заменяем текущую базу данных
            shutil.copy(db_path, 'production.db')

            # Очищаем историю изменений и инициализируем заново
            _clean_and_reset_history()

            # Пересоздаем соединение
            st.session_state.conn = init_db()

            # Сбрасываем кэшированные данные
            _reset_cached_data()

            return True

    except Exception as e:
        raise Exception(f"Ошибка при восстановлении бэкапа: {str(e)}")


def _clean_and_reset_history():
    """Очищает историю изменений и инициализирует начальные значения"""
    try:
        # Создаем временное соединение для очистки истории
        temp_conn = sqlite3.connect('production.db', check_same_thread=False)
        cursor = temp_conn.cursor()

        # Очищаем таблицу истории изменений
        cursor.execute('DELETE FROM jobs_history')

        # Сбрасываем счетчик версий истории
        cursor.execute('DELETE FROM history_versions')
        cursor.execute('INSERT INTO history_versions (id, current_version) VALUES (1, 0)')

        temp_conn.commit()
        temp_conn.close()

    except Exception as e:
        print(f"Предупреждение: не удалось очистить историю: {str(e)}")


def _reset_cached_data():
    """Сбрасывает кэшированные данные в session_state"""
    cache_keys = [
        'jobs_data_cache',
        'equipment_data_cache',
        'orders_data_cache',
        'jobs_data',  # Добавляем ключи из основного модуля
        'data_refresh_key'
    ]

    for key in cache_keys:
        if key in st.session_state:
            del st.session_state[key]

    # Инициализируем ключ обновления данных
    st.session_state.data_refresh_key = 0


# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('production.db', check_same_thread=False)
    cursor = conn.cursor()

    # Типы оборудования
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS equipment_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            sort_order INTEGER NOT NULL UNIQUE,
            color TEXT NOT NULL
        )
    ''')

    # Оборудование
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS equipment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            sort_order INTEGER NOT NULL UNIQUE,
            show_on_chart BOOLEAN NOT NULL DEFAULT 1,
            type_id INTEGER NOT NULL,
            FOREIGN KEY (type_id) REFERENCES equipment_types (id)
        )
    ''')

    # Заказы
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            color TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            priority_order INTEGER NOT NULL DEFAULT 0
        )
    ''')

    # Календарь
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS calendar (
            date TEXT PRIMARY KEY,
            work_hours INTEGER NOT NULL CHECK (work_hours IN (0, 8, 12, 24))
        )
    ''')

    # Работы
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            equipment_id INTEGER NOT NULL,
            duration_hours REAL NOT NULL,
            hour_offset REAL NOT NULL DEFAULT 0,
            start_date TEXT,
            status TEXT NOT NULL CHECK (status IN ('planned', 'started', 'completed')),
            is_locked BOOLEAN NOT NULL DEFAULT 0,
            FOREIGN KEY (order_id) REFERENCES orders (id),
            FOREIGN KEY (equipment_id) REFERENCES equipment (id)
        )
    ''')

    # Таблица для хранения настроек системы
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS system_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            view_mode TEXT NOT NULL DEFAULT 'week',
            pixels_per_hour INTEGER NOT NULL DEFAULT 20,
            row_height INTEGER NOT NULL DEFAULT 70,
            job_height_ratio INTEGER NOT NULL DEFAULT 80,
            chart_start_date TEXT NOT NULL DEFAULT '2024-01-01'
        )
    ''')

    # Инициализируем настройки по умолчанию если записи нет
    cursor.execute('SELECT COUNT(*) FROM system_settings')
    count = cursor.fetchone()[0]
    if count == 0:
        cursor.execute('''
            INSERT INTO system_settings (id, view_mode, pixels_per_hour, 
                                       row_height, job_height_ratio, chart_start_date)
            VALUES (1, 'week', 20, 70, 80, ?)
        ''', (datetime.now().date().isoformat(),))

    # Таблица истории изменений (если её нет)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS jobs_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            change_type TEXT NOT NULL,
            old_values TEXT,
            new_values TEXT,
            changed_at TEXT NOT NULL,
            changed_by TEXT DEFAULT 'system'
        )
    ''')

    # Таблица версий истории
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history_versions (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            current_version INTEGER NOT NULL DEFAULT 0
        )
    ''')

    # Инициализируем версию истории если записи нет
    cursor.execute('SELECT COUNT(*) FROM history_versions')
    count = cursor.fetchone()[0]
    if count == 0:
        cursor.execute('INSERT INTO history_versions (id, current_version) VALUES (1, 0)')

    conn.commit()
    return conn


# Импорт Streamlit для работы с session_state
try:
    import streamlit as st
except ImportError:
    # Заглушка для случаев когда streamlit не доступен
    class StMock:
        def __getattr__(self, name):
            return None


    st = StMock()