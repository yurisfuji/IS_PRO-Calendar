"""
Модуль для управления историей изменений в системе планирования работ.
Обеспечивает функциональность отмены/повтора действий и контроль версий.
"""

import sqlite3
from typing import Dict, List, Tuple, Optional
from datetime import datetime
import streamlit as st
from streamlit_shortcuts import add_shortcuts, shortcut_button

MAX_HISTORY_VERSIONS = 50


class HistoryManager:
    """Класс для управления историей изменений jobs"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._setup_database()

    def _setup_database(self):
        """Инициализирует таблицы для работы с историей"""
        cursor = self.conn.cursor()

        # Таблица истории изменений
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS jobs_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                version INTEGER NOT NULL,
                order_id INTEGER NOT NULL,
                equipment_id INTEGER NOT NULL,
                duration_hours REAL NOT NULL,
                hour_offset REAL NOT NULL DEFAULT 0,
                start_date TEXT,
                status TEXT NOT NULL,
                is_locked BOOLEAN NOT NULL DEFAULT 0,
                changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                operation_type TEXT CHECK(operation_type IN ('INSERT', 'UPDATE', 'DELETE', 'SNAPSHOT')),
                user_action TEXT,
                FOREIGN KEY (job_id) REFERENCES jobs (id) ON DELETE CASCADE
            )
        ''')

        # Таблица управления версиями
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS history_versions (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                current_version INTEGER NOT NULL DEFAULT 0,
                max_version INTEGER NOT NULL DEFAULT 0,
                max_history_depth INTEGER NOT NULL DEFAULT 50,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Инициализация версий
        cursor.execute(f'''
            INSERT OR IGNORE INTO history_versions 
            (id, current_version, max_version, max_history_depth) 
            VALUES (1, 0, 0, {MAX_HISTORY_VERSIONS})
        ''')

        # Индексы для производительности
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_jobs_history_job_version 
            ON jobs_history(job_id, version)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_jobs_history_version 
            ON jobs_history(version)
        ''')

        self.conn.commit()

        # Создаем начальный снимок, если история пуста
        self._create_initial_snapshot()

    def _create_initial_snapshot(self):
        """Создает начальный снимок существующих данных при первом запуске"""
        cursor = self.conn.cursor()

        # Проверяем, есть ли уже записи в истории
        cursor.execute('SELECT COUNT(*) FROM jobs_history')
        history_count = cursor.fetchone()[0]

        # Если история пуста и в jobs есть данные, создаем начальный снимок
        if history_count == 0:
            cursor.execute('SELECT COUNT(*) FROM jobs')
            jobs_count = cursor.fetchone()[0]

            if jobs_count > 0:
                # Устанавливаем версию 1 как текущую и максимальную
                cursor.execute('''
                    UPDATE history_versions 
                    SET max_version = 1, current_version = 1
                    WHERE id = 1
                ''')

                # Сохраняем снимок как версию 1
                cursor.execute('''
                    INSERT INTO jobs_history (
                        job_id, version, order_id, equipment_id, duration_hours, 
                        hour_offset, start_date, status, is_locked, operation_type, user_action
                    )
                    SELECT 
                        id, 1, order_id, equipment_id, duration_hours, 
                        hour_offset, start_date, status, is_locked,
                        'SNAPSHOT', 'Начальное состояние'
                    FROM jobs
                ''')

                self.conn.commit()
                st.toast("💾 Создан начальный снимок данных", icon="✅")

    def create_snapshot(self, description: str = None) -> int:
        """
        Создает снимок текущего состояния всех jobs

        Args:
            description: Описание действия пользователя

        Returns:
            Номер созданной версии
        """
        cursor = self.conn.cursor()

        try:
            # Увеличиваем максимальную версию
            cursor.execute('''
                UPDATE history_versions 
                SET max_version = max_version + 1
                WHERE id = 1
            ''')

            cursor.execute('SELECT max_version FROM history_versions WHERE id = 1')
            new_version = cursor.fetchone()[0]

            # Сохраняем текущее состояние всех jobs
            cursor.execute('''
                INSERT INTO jobs_history (
                    job_id, version, order_id, equipment_id, duration_hours, 
                    hour_offset, start_date, status, is_locked, operation_type, user_action
                )
                SELECT 
                    id, ?, order_id, equipment_id, duration_hours, 
                    hour_offset, start_date, status, is_locked,
                    'SNAPSHOT', ?
                FROM jobs
            ''', (new_version, description))

            # Обновляем текущую версию
            cursor.execute('''
                UPDATE history_versions 
                SET current_version = ?
                WHERE id = 1
            ''', (new_version,))

            # Очищаем старую историю
            self._cleanup_old_history()

            self.conn.commit()
            return new_version

        except Exception as e:
            self.conn.rollback()
            st.error(f"Ошибка при создании снимка: {e}")
            return 0

    def _cleanup_old_history(self):
        """Очищает старые записи истории"""
        cursor = self.conn.cursor()
        cursor.execute('''
            DELETE FROM jobs_history 
            WHERE version < (
                SELECT max_version - max_history_depth 
                FROM history_versions 
                WHERE id = 1
            )
        ''')

    def get_history_state(self) -> Dict:
        """
        Возвращает текущее состояние истории

        Returns:
            Словарь с информацией о состоянии истории
        """
        cursor = self.conn.cursor()
        cursor.execute('SELECT current_version, max_version, max_history_depth FROM history_versions WHERE id = 1')
        result = cursor.fetchone()

        if not result:
            return {
                'current_version': 0,
                'max_version': 0,
                'can_undo': False,
                'can_redo': False
            }

        current_version, max_version, max_history_depth = result

        # Рассчитываем минимальную доступную версию с учетом глубины истории
        min_available_version = max(1, max_version - max_history_depth + 1)

        # Отмена возможна только если текущая версия > минимальной доступной
        can_undo = current_version > min_available_version
        can_redo = current_version < max_version

        return {
            'current_version': current_version,
            'max_version': max_version,
            'min_available_version': min_available_version,
            'can_undo': can_undo,
            'can_redo': can_redo
        }

    def undo(self) -> bool:
        """
        Откатывает изменения на одну версию назад

        Returns:
            True если откат выполнен успешно
        """
        cursor = self.conn.cursor()

        # Получаем информацию о доступных версиях
        history_state = self.get_history_state()
        current_version = history_state['current_version']
        min_available_version = history_state['min_available_version']

        # Не позволяем откатиться ниже минимальной доступной версии
        if current_version <= min_available_version:
            return False

        target_version = current_version - 1
        return self._restore_to_version(target_version)

    def redo(self) -> bool:
        """
        Повторяет изменения на одну версию вперед

        Returns:
            True если повтор выполнен успешно
        """
        cursor = self.conn.cursor()

        cursor.execute('SELECT current_version, max_version FROM history_versions WHERE id = 1')
        current_version, max_version = cursor.fetchone()

        if current_version >= max_version:
            return False

        target_version = current_version + 1
        return self._restore_to_version(target_version)

    def _restore_to_version(self, target_version: int) -> bool:
        """
        Восстанавливает состояние до указанной версии

        Args:
            target_version: Целевая версия для восстановления

        Returns:
            True если восстановление выполнено успешно
        """
        cursor = self.conn.cursor()

        try:
            cursor.execute('BEGIN TRANSACTION')

            # Очищаем текущую таблицу jobs
            cursor.execute('DELETE FROM jobs')

            # Восстанавливаем состояние на момент целевой версии
            # Если target_version = 0, таблица jobs останется пустой
            if target_version >= 1:  # Ключевое изменение: >= 1 вместо > 0
                cursor.execute('''
                    INSERT INTO jobs (id, order_id, equipment_id, duration_hours, 
                                    hour_offset, start_date, status, is_locked)
                    SELECT 
                        jh1.job_id,
                        jh1.order_id,
                        jh1.equipment_id, 
                        jh1.duration_hours,
                        jh1.hour_offset,
                        jh1.start_date,
                        jh1.status,
                        jh1.is_locked
                    FROM jobs_history jh1
                    WHERE jh1.version = ?
                        AND jh1.operation_type != 'DELETE'
                        AND jh1.id = (
                            SELECT MAX(jh2.id)
                            FROM jobs_history jh2
                            WHERE jh2.job_id = jh1.job_id 
                                AND jh2.version <= ?
                        )
                    GROUP BY jh1.job_id
                    HAVING MAX(jh1.version) <= ?
                ''', (target_version, target_version, target_version))

            # Обновляем текущую версию
            cursor.execute('''
                UPDATE history_versions 
                SET current_version = ?
                WHERE id = 1
            ''', (target_version,))

            cursor.execute('COMMIT')
            return True

        except Exception as e:
            cursor.execute('ROLLBACK')
            st.error(f"Ошибка при восстановлении версии {target_version}: {e}")
            return False

    def get_current_version(self) -> int:
        """Возвращает текущую версию"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT current_version FROM history_versions WHERE id = 1')
        result = cursor.fetchone()
        return result[0] if result else 0

    def get_job_history(self, job_id: int) -> List[Tuple]:
        """
        Возвращает историю изменений конкретной работы

        Args:
            job_id: ID работы

        Returns:
            Список записей истории
        """
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT 
                jh.version,
                jh.operation_type,
                jh.duration_hours,
                jh.hour_offset,
                jh.start_date,
                jh.status,
                jh.is_locked,
                jh.changed_at,
                jh.user_action,
                o.name as order_name,
                e.name as equipment_name
            FROM jobs_history jh
            LEFT JOIN orders o ON jh.order_id = o.id
            LEFT JOIN equipment e ON jh.equipment_id = e.id
            WHERE jh.job_id = ?
            ORDER BY jh.version DESC, jh.changed_at DESC
        ''', (job_id,))

        return cursor.fetchall()

    def get_version_history(self) -> List[Tuple]:
        """
        Возвращает историю версий

        Returns:
            Список версий с информацией
        """
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT 
                version,
                COUNT(*) as job_count,
                MIN(changed_at) as created_at,
                GROUP_CONCAT(DISTINCT user_action) as actions
            FROM jobs_history 
            WHERE operation_type = 'SNAPSHOT'
            GROUP BY version
            ORDER BY version DESC
        ''')

        return cursor.fetchall()

    def cleanup_history(self, keep_versions: int = 50) -> int:
        """
        Очищает старую историю

        Args:
            keep_versions: Количество версий для сохранения

        Returns:
            Количество удаленных записей
        """
        cursor = self.conn.cursor()

        cursor.execute('''
            UPDATE history_versions 
            SET max_history_depth = ?
            WHERE id = 1
        ''', (keep_versions,))

        cursor.execute('''
            DELETE FROM jobs_history 
            WHERE version < (
                SELECT max_version - max_history_depth 
                FROM history_versions 
                WHERE id = 1
            ) AND version > 0  -- Не удаляем версию 0 если она есть
        ''')

        self.conn.commit()
        return cursor.rowcount

    def get_history_stats(self) -> Dict:
        """
        Возвращает статистику по истории

        Returns:
            Словарь со статистикой
        """
        cursor = self.conn.cursor()

        cursor.execute('SELECT COUNT(*) FROM jobs_history')
        total_records = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(DISTINCT version) FROM jobs_history')
        total_versions = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(DISTINCT job_id) FROM jobs_history')
        total_jobs = cursor.fetchone()[0]

        cursor.execute('''
            SELECT operation_type, COUNT(*) 
            FROM jobs_history 
            GROUP BY operation_type
        ''')
        operations = dict(cursor.fetchall())

        return {
            'total_records': total_records,
            'total_versions': total_versions,
            'total_jobs': total_jobs,
            'operations': operations
        }

    def initialize_with_current_data(self, description: str = "Начальное состояние"):
        """
        Принудительно создает снимок текущих данных
        Полезно при миграциях или первом запуске
        """
        cursor = self.conn.cursor()

        cursor.execute('SELECT COUNT(*) FROM jobs')
        jobs_count = cursor.fetchone()[0]

        if jobs_count == 0:
            return 0

        return self.create_snapshot(description)


# Функции для удобной интеграции с Streamlit
def setup_history_manager(conn: sqlite3.Connection) -> HistoryManager:
    """Создает и возвращает экземпляр HistoryManager"""
    manager = HistoryManager(conn)

    # Принудительно создаем начальный снимок при запуске приложения
    # если в jobs есть данные, но история пуста
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM jobs_history')
    history_count = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM jobs')
    jobs_count = cursor.fetchone()[0]

    if jobs_count > 0 and history_count == 0:
        manager._create_initial_snapshot()

    return manager


def show_history_controls(history_manager: HistoryManager):
    """
    Показывает элементы управления историей в Streamlit

    Args:
        history_manager: Экземпляр HistoryManager
    """
    history_state = history_manager.get_history_state()

    col1, col2, col3, col4 = st.sidebar.columns(4)

    with col2:
        if shortcut_button("↶", ['ctrl+z', 'ctrl+я'], hint=False,
                           disabled=not history_state['can_undo'],
                           use_container_width=True,
                           help="Отменить последнее действие (CTRL+Z)" if history_state['can_undo'] else "Нет действий для отмены"):
            if history_manager.undo():
                st.rerun()
            else:
                st.sidebar.error("Не удалось выполнить отмену")

    with col3:
        if shortcut_button("↷", ['ctrl+y', 'ctrl+н'],
                           disabled=not history_state['can_redo'],
                           use_container_width=True, hint=False,
                           help="Повторить отмененное действие  (CTRL+Y)" if history_state['can_redo'] else "Нет действий для повтора"):
            if history_manager.redo():
                st.rerun()
            else:
                st.sidebar.error("Не удалось выполнить повтор")

    # # Информация о версии
    # version_info = f"**Версия:** {history_state['current_version']}"
    # if history_state['max_version'] > 0:
    #     version_info += f"/{history_state['max_version']}"
    #
    # st.sidebar.write(version_info)
    #
    # # Показываем информацию о доступном диапазоне
    # if history_state['max_version'] > 0:
    #     available_range = f"Доступно: v{history_state['min_available_version']}-v{history_state['max_version']}"
    #     st.sidebar.caption(available_range)
    #
    # # Показываем подсказку о состоянии
    # if history_state['current_version'] == 0:
    #     st.sidebar.caption("⏸️ История не инициализирована")
    # elif history_state['current_version'] == history_state['min_available_version']:
    #     st.sidebar.caption("📋 Начало доступной истории")
    #
    # # Расширенная информация (по требованию)
    # with st.sidebar.expander("Детали истории"):
    #     stats = history_manager.get_history_stats()
    #     st.write(f"Всего записей: {stats['total_records']}")
    #     st.write(f"Уникальных работ: {stats['total_jobs']}")
    #     st.write(f"Диапазон версий: {history_state['min_available_version']}-{history_state['max_version']}")
    #
    #     # Настройка глубины истории
    #     current_depth = history_state['max_version'] - history_state['min_available_version'] + 1
    #     new_depth = st.slider(
    #         "Глубина истории (версий)",
    #         min_value=10,
    #         max_value=200,
    #         value=current_depth,
    #         help="Количество сохраняемых версий истории"
    #     )
    #
    #     if new_depth != current_depth:
    #         if st.button("💾 Применить новую глубину"):
    #             history_manager.cleanup_history(new_depth)
    #             st.success(f"Глубина истории установлена: {new_depth} версий")
    #             st.rerun()
    #
    #     # Кнопка очистки истории
    #     if st.button("🧹 Очистить старую историю", use_container_width=True):
    #         deleted = history_manager.cleanup_history(10)
    #         st.success(f"Удалено {deleted} старых записей")
    #         st.rerun()


def auto_save_snapshot(history_manager: HistoryManager, action_description: str = None):
    """
    Автоматически создает снимок перед критическими изменениями

    Args:
        history_manager: Экземпляр HistoryManager
        action_description: Описание выполняемого действия
    """
    try:
        version = history_manager.create_snapshot(action_description)
        if version > 0:
            st.toast(f"💾 Сохранено в истории (v{version})", icon="✅")
    except Exception as e:
        st.error(f"Ошибка при сохранении истории: {e}")
