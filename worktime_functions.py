from datetime import datetime, timedelta
from typing import Tuple, List, Optional

import pandas as pd
import streamlit as st


def adjust_date_for_work_hours(conn, date_str: str, offset: float) -> tuple[str, float]:
    """Корректирует дату и смещение с учетом рабочих часов"""
    work_hours = get_work_hours_for_date(conn, date_str)

    # Если день выходной, ищем следующий рабочий день
    if work_hours == 0:
        next_working_day = ensure_working_day(conn, date_str)
        return next_working_day, 0.0  # Начинаем с начала рабочего дня

    # Если смещение превышает рабочий день, переходим к следующему дню
    if offset >= work_hours:
        next_date = (datetime.fromisoformat(date_str) + timedelta(days=1)).date().isoformat()
        next_work_hours = get_work_hours_for_date(conn, next_date)

        # Если следующий день выходной, ищем рабочий
        if next_work_hours == 0:
            next_working_day = ensure_working_day(conn, next_date)
            return next_working_day, 0.0

        # Корректируем смещение для следующего дня
        adjusted_offset = offset - work_hours
        return next_date, adjusted_offset

    return date_str, offset


def adjust_schedule_and_fix_conflicts(conn, start_date: str, offset: float, duration: float,
                                      equipment_id: int, job_id: Optional[int] = None,
                                      only_check: bool = True) -> bool:
    """
    Корректирует расписание и устраняет конфликты, перепланируя последующие работы.

    Args:
        conn: соединение с БД
        start_date: исходная дата начала в формате ISO
        offset: смещение от начала дня в часах
        duration: длительность работы в часах
        equipment_id: ID оборудования
        job_id: ID текущей работы (опционально)

     Returns:
        bool: True если есть конфликты, False если конфликтов нет
    """
    cursor = conn.cursor()

    # 1. Проверяем конфликты и получаем доступную дату
    available_date, available_offset = check_equipment_conflicts(
        conn, equipment_id, job_id, start_date, offset, duration
    )

    # 2. Если дата и смещение совпадают с исходными - нет конфликтов
    if available_date == start_date and abs(available_offset - offset) < 0.01:
        return False

    if not only_check:
        # 3. Вычисляем финишные параметры для исходной работы
        finish_date, daily_schedule = calculate_finish_date(
            conn, start_date, duration, offset, None
        )
        # Получаем время окончания из последнего дня расписания
        if daily_schedule:
            last_day_date, last_day_hours, last_day_offset = daily_schedule[-1]
            finish_offset = last_day_offset + last_day_hours
            # if abs(finish_offset - last_day_hours) < 0.1:
            #     finish_offset = 0
            #     finish_date = (datetime.fromisoformat(finish_date) + timedelta(days=1)).date().isoformat()
        else:
            finish_offset = 0

        # 4. Вычисляем next_start_date как finish_date + finish_offset + 0.25 часа
        finish_dt = datetime.fromisoformat(finish_date)
        next_start_dt = finish_dt + timedelta(hours=finish_offset + 0.25 if finish_offset != 0 else finish_offset)
        next_start_date = next_start_dt.date().isoformat()
        next_start_offset = next_start_dt.hour + next_start_dt.minute / 60.0

        # 5. Получаем список будущих работ, исключая текущую
        future_jobs = get_future_jobs_excluding_current_optimized(
            conn, start_date, offset, job_id, equipment_id
        )

        # 6. Перебираем работы и перепланируем их если нужно
        for future_job_id, future_duration in future_jobs:
            # Получаем текущие параметры работы
            cursor.execute(
                "SELECT start_date, hour_offset FROM jobs WHERE id = ?",
                (future_job_id,)
            )
            result = cursor.fetchone()
            if not result:
                continue

            current_start_date, current_offset = result
            current_offset = current_offset or 0.0

            # Если работа начинается после next_start_date - выходим из цикла
            current_start_dt = datetime.fromisoformat(current_start_date) + timedelta(hours=current_offset)
            if current_start_dt >= next_start_dt:
                break

            # Перепланируем работу на next_start_date
            cursor.execute(
                "UPDATE jobs SET start_date = ?, hour_offset = ? WHERE id = ?",
                (next_start_date, next_start_offset, future_job_id)
            )

            # Вычисляем новые параметры финиша для перепланированной работы
            new_finish_date, new_daily_schedule = calculate_finish_date(
                conn, next_start_date, future_duration, next_start_offset,
                None
            )

            # Обновляем next_start_date для следующей работы
            if new_daily_schedule:
                last_day_date, last_day_hours, last_day_offset = new_daily_schedule[-1]
                new_finish_offset = last_day_offset + last_day_hours
            else:
                new_finish_offset = 0

            new_finish_dt = datetime.fromisoformat(new_finish_date)
            next_start_dt = new_finish_dt + timedelta(hours=new_finish_offset + 0.25)
            next_start_date = next_start_dt.date().isoformat()
            next_start_offset = next_start_dt.hour + next_start_dt.minute / 60.0

        # Фиксируем изменения в БД
        conn.commit()

    # 7. Возвращаем исходные доступные дату и смещение
    return True


def get_future_jobs_excluding_current_optimized(conn, need_date: str, offset: float, exclude_job_id: Optional[int],
                                                equipment_id: int) -> List[Tuple[int, float]]:
    """
    Оптимизированная версия - возвращает только ID работы и длительность.
    """
    cursor = conn.cursor()

    # Базовый запрос
    query = '''
        SELECT id, start_date, duration_hours, hour_offset
        FROM jobs 
        WHERE equipment_id = ? 
          AND status IN ('planned', 'started')
    '''

    params = [equipment_id]

    # Добавляем условие исключения если нужно
    if exclude_job_id is not None:
        query += ' AND id != ?'
        params.append(exclude_job_id)

    query += ' ORDER BY start_date, hour_offset'

    cursor.execute(query, params)
    jobs = cursor.fetchall()
    future_jobs = []

    need_date_dt = datetime.fromisoformat(need_date).date()

    for job in jobs:
        job_id, start_date, duration_hours, hour_offset = job
        hour_offset = hour_offset or 0.0

        # Рассчитываем дату завершения работы
        finish_date_str, daily_schedule = calculate_finish_date(
            conn, start_date, duration_hours, hour_offset, job_id
        )
        finish_date = datetime.fromisoformat(finish_date_str).date()
        _, last_day_hours, last_day_offset = daily_schedule[-1]
        # Проверяем, что работа завершается после need_date
        if finish_date > need_date_dt or (finish_date == need_date_dt and offset < last_day_offset + last_day_hours):
            future_jobs.append((job_id, duration_hours))

    return future_jobs


def get_work_hours_for_date(conn, date_str: str) -> int:
    cursor = conn.cursor()
    cursor.execute('SELECT work_hours FROM calendar WHERE date=?', (date_str,))
    result = cursor.fetchone()
    return result[0] if result else 8


def ensure_working_day(conn, date_str: str) -> str:
    """
    Гарантированно возвращает рабочую дату.
    Если указанная дата - выходной, возвращает первый рабочий день после нее.
    Если день рабочий, возвращает ту же дату.
    """
    cursor = conn.cursor()

    # Проверяем, является ли указанная дата выходным
    cursor.execute('SELECT work_hours FROM calendar WHERE date=?', (date_str,))
    result = cursor.fetchone()

    # Если записи нет в календаре, считаем стандартным рабочим днем
    if result is None:
        return date_str

    work_hours = result[0]

    # Если день рабочий, возвращаем ту же дату
    if work_hours > 0:
        return date_str

    # Если день выходной, ищем следующий рабочий день
    current_date = datetime.fromisoformat(date_str).date()
    max_attempts = 30

    for attempt in range(max_attempts):
        current_date += timedelta(days=1)
        next_date_str = current_date.isoformat()

        cursor.execute('SELECT work_hours FROM calendar WHERE date=?', (next_date_str,))
        result = cursor.fetchone()

        # Если записи нет в календаре, считаем стандартным рабочим днем
        if result is None:
            return next_date_str

        work_hours = result[0]
        if work_hours > 0:
            return next_date_str

    return current_date.isoformat()


def calculate_finish_date(conn, start_date_str: Optional[str] = None, duration_hours: Optional[float] = None,
                          hour_offset: Optional[float] = None,
                          job_id: Optional[int] = None) -> Tuple[str, List[Tuple[str, float, float]]]:
    """
    Расчет даты завершения работы с разбивкой по дням.
    Если передан job_id - берет параметры из базы данных.
    Если job_id не передан - использует переданные параметры.
    Возвращает: (finish_date, список дней с занятыми часами и смещением)
    """

    # Если передан job_id, загружаем параметры из базы
    if job_id is not None:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT start_date, duration_hours, hour_offset, equipment_id
            FROM jobs 
            WHERE id = ?
        ''', (job_id,))

        result = cursor.fetchone()
        if not result:
            raise ValueError(f"Работа с ID {job_id} не найдена")

        start_date_str, duration_hours, hour_offset, equipment_id = result
        hour_offset = hour_offset or 0.0
    else:
        # Проверяем, что все необходимые параметры переданы
        if start_date_str is None or duration_hours is None or hour_offset is None:
            raise ValueError(
                "При job_id=None необходимо передать start_date_str, duration_hours, hour_offset и equipment_id")

        hour_offset = hour_offset or 0.0

    start_date = datetime.fromisoformat(start_date_str).date()
    remaining_hours = duration_hours
    current_date = start_date
    daily_schedule = []
    current_offset = hour_offset

    # Распределяем часы работы по дням
    first_day = True
    while remaining_hours > 0:
        date_str = current_date.isoformat()
        work_hours = get_work_hours_for_date(conn, date_str)

        if work_hours > 0:  # Рабочий день
            # Получаем текущую загрузку оборудования на этот день
            available_hours = work_hours

            if first_day:
                available_hours -= current_offset
                first_day = False

            if available_hours > 0:
                hours_today = min(available_hours, remaining_hours)
                daily_schedule.append((date_str, hours_today, current_offset))
                remaining_hours -= hours_today
                current_offset = 0

        if remaining_hours > 0:
            current_date += timedelta(days=1)

    return current_date.isoformat(), daily_schedule


def check_equipment_conflicts(conn, equipment_id: int, job_id: int, start_date_str: str, hour_offset: float,
                              duration_hours: float) -> tuple[str, float]:
    """
    Проверяет конфликты на оборудовании и возвращает доступную дату старта.
    Если есть конфликты, находит следующую доступную дату.
    """

    def get_job_time_range_with_calendar(job_start: str, job_duration: float, job_offset: float,
                                         exclude_job_id: int = None) -> tuple[datetime, datetime]:
        """Рассчитывает временной диапазон работы с учетом календаря и оборудования"""
        # Используем готовую функцию calculate_finish_date
        finish_date, daily_schedule = calculate_finish_date(conn, job_start, job_duration, job_offset,
                                                            None)
        # Начало работы с учетом смещения
        start_dt = datetime.fromisoformat(job_start)
        start_with_offset = start_dt + timedelta(hours=job_offset)

        # Конец работы - дата из calculate_finish_date + время окончания в последний день
        last_day_schedule = daily_schedule[-1] if daily_schedule else (finish_date, 0, 0)
        last_day_date = datetime.fromisoformat(last_day_schedule[0])
        last_day_hours = last_day_schedule[1]
        last_day_offset = last_day_schedule[2] if len(last_day_schedule) > 2 else 0

        # Время окончания в последний день
        end_dt = last_day_date + timedelta(hours=last_day_offset + last_day_hours)

        return start_with_offset, end_dt

    def has_time_conflict(test_start_dt: datetime, test_end_dt: datetime, conflict_start_dt: datetime,
                          conflict_end_dt: datetime) -> bool:
        """Проверяет пересечение временных интервалов"""
        return conflict_start_dt <= test_start_dt < conflict_end_dt or \
            conflict_start_dt < test_end_dt <= conflict_end_dt or \
            (test_start_dt <= conflict_start_dt and test_end_dt >= conflict_end_dt)

    # Начинаем с исходной даты
    current_date = start_date_str
    current_offset = hour_offset
    max_iterations = 100  # Защита от бесконечного цикла

    for iteration in range(max_iterations):
        # Корректируем дату и смещение с учетом рабочих часов
        adjusted_date, adjusted_offset = adjust_date_for_work_hours(conn, current_date, current_offset)

        # Получаем временной диапазон для проверяемой работы с учетом календаря
        test_start_dt, test_end_dt = get_job_time_range_with_calendar(
            adjusted_date, duration_hours, adjusted_offset, job_id
        )
        # Получаем все работы на оборудовании (кроме текущей)
        cursor = conn.cursor()
        if job_id:
            cursor.execute('''
                SELECT id, start_date, duration_hours, hour_offset, is_locked
                FROM jobs 
                WHERE equipment_id=? AND id!=? AND status IN ('planned', 'started')
                ORDER BY start_date
            ''', (equipment_id, job_id))
        else:
            cursor.execute('''
                SELECT id, start_date, duration_hours, hour_offset, is_locked
                FROM jobs 
                WHERE equipment_id=? AND status IN ('planned', 'started')
                ORDER BY start_date
            ''', (equipment_id,))

        existing_jobs = cursor.fetchall()
        has_conflict = False

        for job in existing_jobs:
            job_db_id, job_start, job_duration, job_offset, job_locked = job
            job_offset = job_offset or 0

            # Получаем временной диапазон существующей работы с учетом календаря
            conflict_start_dt, conflict_end_dt = get_job_time_range_with_calendar(
                job_start, job_duration, job_offset, job_db_id
            )

            # Проверяем конфликт
            if has_time_conflict(test_start_dt, test_end_dt, conflict_start_dt, conflict_end_dt):
                has_conflict = True

                # Начинаем после окончания конфликтующей работы + 0.25 часа
                new_start_dt = conflict_end_dt + timedelta(hours=0.25)

                # Обновляем текущую дату и смещение
                current_date = new_start_dt.date().isoformat()
                current_offset = new_start_dt.hour + new_start_dt.minute / 60.0
                break

        # Если конфликтов нет, возвращаем текущую дату
        if not has_conflict:
            return adjusted_date, adjusted_offset

        # Если прошли все итерации, возвращаем последнюю проверенную дату
        if iteration == max_iterations - 1:
            return adjusted_date, adjusted_offset

    return adjusted_date, adjusted_offset


def get_adjacent_jobs(conn, job_id):
    """Получает предыдущую и следующую работы на том же оборудовании"""
    cursor = conn.cursor()

    # Получаем информацию о текущей работе
    cursor.execute('''
        SELECT equipment_id, start_date, hour_offset 
        FROM jobs WHERE id = ?
    ''', (job_id,))
    current_job = cursor.fetchone()

    if not current_job:
        return None, None

    # Работаем с кортежем - индексы: 0=equipment_id, 1=start_date, 2=hour_offset
    equipment_id = current_job[0]
    current_start = current_job[1]
    current_offset = current_job[2] or 0

    # Находим предыдущую работу
    cursor.execute('''
        SELECT id, order_id, start_date, hour_offset 
        FROM jobs 
        WHERE equipment_id = ? AND id != ?
        AND (start_date < ? OR (start_date = ? AND (hour_offset OR 0) < ?))
        ORDER BY start_date DESC, (hour_offset OR 0) DESC 
        LIMIT 1
    ''', (equipment_id, job_id, current_start, current_start, current_offset))
    prev_job = cursor.fetchone()

    # Находим следующую работу
    cursor.execute('''
        SELECT id, order_id, start_date, hour_offset 
        FROM jobs 
        WHERE equipment_id = ? AND id != ?
        AND (start_date > ? OR (start_date = ? AND (hour_offset OR 0) > ?))
        ORDER BY start_date ASC, (hour_offset OR 0) ASC 
        LIMIT 1
    ''', (equipment_id, job_id, current_start, current_start, current_offset))
    next_job = cursor.fetchone()

    # Получаем названия заказов
    prev_order_name = None
    next_order_name = None

    if prev_job:
        cursor.execute('SELECT name FROM orders WHERE id = ?', (prev_job[1],))  # order_id по индексу 1
        prev_order_result = cursor.fetchone()
        prev_order_name = prev_order_result[0] if prev_order_result else "Неизвестный заказ"

    if next_job:
        cursor.execute('SELECT name FROM orders WHERE id = ?', (next_job[1],))  # order_id по индексу 1
        next_order_result = cursor.fetchone()
        next_order_name = next_order_result[0] if next_order_result else "Неизвестный заказ"

    prev_info = {'order_name': prev_order_name, 'id': prev_job[0]} if prev_job else None  # id по индексу 0
    next_info = {'order_name': next_order_name, 'id': next_job[0]} if next_job else None  # id по индексу 0

    return prev_info, next_info


def move_job_to_previous(conn, job_id):
    """Перемещает работу как можно ближе к предыдущей работе на том же оборудовании"""
    cursor = conn.cursor()

    # Получаем информацию о текущей работе
    cursor.execute('SELECT * FROM jobs WHERE id = ?', (job_id,))
    current_job = cursor.fetchone()

    if not current_job:
        st.error("❌ Работа не найдена")
        return False

    # Работаем с кортежем - получаем названия колонок
    cursor.execute("PRAGMA table_info(jobs)")
    columns = [column[1] for column in cursor.fetchall()]

    # Создаем словарь для удобства доступа
    current_job_dict = dict(zip(columns, current_job))

    equipment_id = current_job_dict['equipment_id']
    current_start_date = current_job_dict['start_date']
    current_offset = current_job_dict['hour_offset'] or 0
    duration = current_job_dict['duration_hours']

    # Находим предыдущую работу
    cursor.execute('''
        SELECT * FROM jobs 
        WHERE equipment_id = ? AND id != ?
        AND (start_date < ? OR (start_date = ? AND hour_offset < ?))
        ORDER BY start_date DESC, hour_offset DESC 
        LIMIT 1
    ''', (equipment_id, job_id, current_start_date, current_start_date, current_offset))

    previous_job_tuple = cursor.fetchone()

    if previous_job_tuple:
        # Создаем словарь для предыдущей работы
        previous_job = dict(zip(columns, previous_job_tuple))

        # Рассчитываем окончание предыдущей работы
        prev_finish_date, prev_schedule = calculate_finish_date(
            conn,
            previous_job['start_date'],
            previous_job['duration_hours'],
            previous_job['hour_offset'] or 0,
            previous_job['id']
        )

        # Устанавливаем новую дату начала сразу после предыдущей работы
        new_start_date = prev_finish_date
        last_day_date, last_day_hours, last_day_offset = prev_schedule[-1]
        new_offset = last_day_offset + last_day_hours
        correct_new_start_date, correct_new_offset = adjust_date_for_work_hours(conn, new_start_date, new_offset)

        # Обновляем работу
        cursor.execute('''
            UPDATE jobs 
            SET start_date = ?, hour_offset = ? 
            WHERE id = ?
        ''', (correct_new_start_date, correct_new_offset, job_id))
        conn.commit()
        return True
    else:
        st.info("ℹ️ Предыдущей работы не найдено")
        return False


def move_job_to_next(conn, job_id):
    """Перемещает работу как можно ближе к следующей работе на том же оборудовании"""
    cursor = conn.cursor()

    # Получаем информацию о текущей работе
    cursor.execute('SELECT * FROM jobs WHERE id = ?', (job_id,))
    current_job = cursor.fetchone()

    if not current_job:
        st.error("❌ Работа не найдена")
        return False

    # Работаем с кортежем - получаем названия колонок
    cursor.execute("PRAGMA table_info(jobs)")
    columns = [column[1] for column in cursor.fetchall()]

    # Создаем словарь для удобства доступа
    current_job_dict = dict(zip(columns, current_job))

    equipment_id = current_job_dict['equipment_id']
    current_start_date = current_job_dict['start_date']
    current_offset = current_job_dict['hour_offset'] or 0
    duration = current_job_dict['duration_hours']

    # Находим следующую работу
    cursor.execute('''
        SELECT * FROM jobs 
        WHERE equipment_id = ? AND id != ?
        AND (start_date > ? OR (start_date = ? AND hour_offset > ?))
        ORDER BY start_date ASC, hour_offset ASC 
        LIMIT 1
    ''', (equipment_id, job_id, current_start_date, current_start_date, current_offset))

    next_job_tuple = cursor.fetchone()

    if next_job_tuple:
        # Создаем словарь для следующей работы
        next_job = dict(zip(columns, next_job_tuple))

        diff = get_work_hours_between_jobs(conn, job_id, next_job['id'])
        # Устанавливаем новую дату начала - накануне следующей работы
        next_start_date = current_start_date
        next_offset = current_offset + diff

        correct_new_start_date, correct_new_offset = adjust_date_for_work_hours(conn, next_start_date, next_offset)

        # Обновляем работу
        cursor.execute('''
            UPDATE jobs 
            SET start_date = ?, hour_offset = ? 
            WHERE id = ?
        ''', (correct_new_start_date, correct_new_offset, job_id))
        conn.commit()
        return True
    else:
        st.info("ℹ️ Следующей работы не найдено")
        return False


def get_work_hours_between_dates(conn, start_date, start_offset, finish_date, finish_offset):
    """
    Рассчитывает количество рабочих часов между двумя датами с учетом смещений

    Args:
        conn: соединение с БД
        start_date (str): дата начала в формате 'YYYY-MM-DD'
        start_offset (float): смещение начала в часах от начала дня
        finish_date (str): дата окончания в формате 'YYYY-MM-DD'
        finish_offset (float): смещение окончания в часах от начала дня

    Returns:
        float: общее количество рабочих часов между указанными точками
    """
    cursor = conn.cursor()

    # Преобразуем даты в datetime объекты
    start_dt = datetime.fromisoformat(start_date)
    finish_dt = datetime.fromisoformat(finish_date)

    # Если даты одинаковые, рассчитываем разницу в пределах одного дня
    if start_date == finish_date:
        work_hours = get_work_hours_for_date(conn, start_date)
        if work_hours == 0:  # Выходной день
            return 0.0

        # Ограничиваем смещения рабочими часами дня
        effective_start = min(start_offset, work_hours)
        effective_finish = min(finish_offset, work_hours)

        return max(0.0, effective_finish - effective_start)

    total_hours = 0.0

    # Обрабатываем начальный день
    start_day_hours = get_work_hours_for_date(conn, start_date)
    if start_day_hours > 0:  # Рабочий день
        effective_start = min(start_offset, start_day_hours)
        hours_in_start_day = max(0.0, start_day_hours - effective_start)
        total_hours += hours_in_start_day

    # Обрабатываем конечный день
    finish_day_hours = get_work_hours_for_date(conn, finish_date)
    if finish_day_hours > 0:  # Рабочий день
        effective_finish = min(finish_offset, finish_day_hours)
        hours_in_finish_day = effective_finish
        total_hours += hours_in_finish_day

    # Обрабатываем полные дни между начальным и конечным
    current_date = start_dt + timedelta(days=1)
    while current_date.date() < finish_dt.date():
        date_str = current_date.strftime('%Y-%m-%d')
        day_hours = get_work_hours_for_date(conn, date_str)
        if day_hours > 0:  # Только рабочие дни
            total_hours += day_hours
        current_date += timedelta(days=1)

    return total_hours


def get_work_hours_between_jobs(conn, job1_id, job2_id):
    """
    Рассчитывает количество рабочих часов между двумя работами

    Args:
        conn: соединение с БД
        job1_id (int): ID первой работы
        job2_id (int): ID второй работы

    Returns:
        float: количество рабочих часов между работами
        None: если одна из работ не найдена
    """
    cursor = conn.cursor()

    # Получаем информацию о первой работе
    cursor.execute('''
        SELECT start_date, hour_offset, duration_hours 
        FROM jobs WHERE id = ?
    ''', (job1_id,))
    job1 = cursor.fetchone()

    if not job1:
        return None

    # Получаем информацию о второй работе
    cursor.execute('''
        SELECT start_date, hour_offset, duration_hours 
        FROM jobs WHERE id = ?
    ''', (job2_id,))
    job2 = cursor.fetchone()

    if not job2:
        return None

    # Работаем с кортежами
    job1_start_date = job1[0]
    job1_offset = job1[1] or 0.0
    job1_duration = job1[2]

    job2_start_date = job2[0]
    job2_offset = job2[1] or 0.0
    job2_duration = job2[2]

    # Рассчитываем время окончания первой работы
    job1_finish_date, job1_schedule = calculate_finish_date(
        conn, job1_start_date, job1_duration, job1_offset, job1_id
    )
    _, last_hours, last_offset = job1_schedule[-1]

    # Время начала второй работы
    job2_start_date_iso = job2_start_date
    job2_start_offset = job2_offset

    # Рассчитываем разницу между окончанием первой работы и началом второй
    return get_work_hours_between_dates(
        conn, job1_finish_date, last_hours+last_offset, job2_start_date_iso, job2_start_offset
    )