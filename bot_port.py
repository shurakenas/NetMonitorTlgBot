# -*- coding: utf-8 -*-

import asyncio
import socket
import json
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ParseMode
import logging
import os
import time
from collections import deque
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.dates import MinuteLocator, DateFormatter
from datetime import datetime
from itertools import islice


from config import *

#Настройка общего логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('log/main.log', mode='w'),  # Запись в файл
        logging.StreamHandler()              # Вывод в консоль
    ]
)

#Для логирования в разные файлы (указывается в функциях)
def setup_function_logger(logfile):
    logger = logging.getLogger(logfile)
    logger.setLevel(logging.INFO)

    # Отключаем передачу логов в родительский (глобальный) логгер
    logger.propagate = False

    # Удаляем ранее добавленные обработчики, если они уже существуют
    if logger.handlers:
        logger.handlers.clear()

    # Настройка нового обработчика для записи логов в файл
    file_handler = logging.FileHandler(logfile, mode='a')
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)

    return logger

#Для логирования в разные файлы (указывается в функциях)
def setup_function_logger1(logfile1):
    logger = logging.getLogger(logfile1)
    logger.setLevel(logging.INFO)

    # Отключаем передачу логов в родительский (глобальный) логгер
    logger.propagate = False

    # Удаляем ранее добавленные обработчики, если они уже существуют
    if logger.handlers:
        logger.handlers.clear()

    # Настройка нового обработчика для записи логов в файл
    file_handler = logging.FileHandler(logfile1, mode='w')
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)

    return logger

#Инициализация бота, диспетчера
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

#Обработчик ошибок
@dp.errors_handler()
async def error_bot(update: types.Update, e: Exception):
    logging.error(f'Exception: {str(e)}')
    await bot.send_message(chat_id=CHAT_ID, text='Какая-то ошибка, проверьте логи!')
    return True

# Список серверов для мониторинга
SERVERS = [
    {"name": "MyNameServer", "ip": "MyIP"},
    {"name": "MyNameServer", "ip": "MyIP"},
]

# Порт, который будет проверяться
PORT = 443

# Последние 24 часа
MONITORING_WINDOW = 24 * 60 * 60

# Файл для сохранения статусов и статистики
STATUS_FILE = "status.json"

# Хранилище данных для мониторинга
server_status = {server["ip"]: {"status": True, "response_time": None} for server in SERVERS}
server_stats = {server["ip"]: deque(maxlen=10000) for server in SERVERS}

def load_status():
    """Загружает статус серверов и статистику из файла."""
    function_logger = setup_function_logger('log/bot.log')
    global server_status, server_stats
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r") as file:
                data = json.load(file)
                server_status = data.get("server_status", server_status)

                # Преобразование списков обратно в deque
                stats = data.get("server_stats", {})
                server_stats = {
                    ip: deque(entries, maxlen=10000) for ip, entries in stats.items()
                }

                for ip in server_stats:
                    if not isinstance(server_stats[ip], deque) or len(server_stats[ip]) == 0:
                        server_stats[ip] = deque(maxlen=10000)

                function_logger.info(f"Статусы и статистика успешно загружены из файла.")
        except Exception as e:
            function_logger.error(f"Ошибка при загрузке статусного файла: {e}")
    else:
        # Инициализируем, если файла нет
        server_status = {server["ip"]: {"status": True, "response_time": None} for server in SERVERS}
        server_stats = {server["ip"]: deque(maxlen=10000) for server in SERVERS}
        function_logger.info("Файл статусов не найден, создано пустое состояние.")


def save_status():
    """Сохраняет текущий статус и статистику серверов в файл."""
    function_logger = setup_function_logger('log/bot.log')
    try:
        data = {
            "server_status": server_status,
            "server_stats": {
                ip: list(stats) for ip, stats in server_stats.items()
            },  # Преобразуем `deque` в списки
        }
        with open(STATUS_FILE, "w") as file:
            json.dump(data, file, indent=4)
#        function_logger.info(f"Статусы и статистика успешно сохранены.")
    except Exception as e:
        function_logger.error(f"Ошибка при сохранении статусного файла: {e}")


def clean_old_stats():
    """
    Удаляет проверки, которые старше 24 часов.
    """
    function_logger = setup_function_logger1('log/CleanOldStats.log')
    now = time.time()
    for ip, stats in server_stats.items():
        old_count = 0
        server_name = next(server['name'] for server in SERVERS if server['ip'] == ip)
        while stats and stats[0][0] < now - MONITORING_WINDOW:
            stats.popleft()
            old_count += 1
        if old_count > 0:
            function_logger.info(f"Удалено {old_count} старых записей для {server_name} ({ip})")
        else:
            function_logger.info(f"Нечего удалять для {server_name} ({ip})")

        # Рассчитываем успешные, неудачные проверки заново
        total_checks = len(stats)
        successful_checks = sum(1 for _, status in stats if status)
        failed_checks = total_checks - successful_checks

        # Логирование обновленной статистики
        function_logger.info(f"Для {server_name} ({ip}): всего проверок - {total_checks}, успешных - {successful_checks}, неудачных - {failed_checks}")


async def check_server(server, retries=3, delay=1):
    """
    Проверяет доступность сервера через указанный порт (443) с использованием socket с несколькими попытками, если первая была неудачная.

    Возвращает:
    - "status": True/False (доступен/недоступен)
    - "response_time": время отклика (в миллисекундах) или None, если сервер недоступен
    - "retries": Количество повторных попыток.
    - "delay": Пауза между повторными попытками (в секундах).
    """
    function_logger = setup_function_logger('log/bot.log')
    ip = server["ip"]
    server_name = next(server['name'] for server in SERVERS if server['ip'] == ip)
    for attempt in range(retries):

        try:
            start_time = asyncio.get_event_loop().time()  # Засекаем время начала проверки

            # Создаем сокет для подключения
            with socket.create_connection((ip, PORT), timeout=5) as conn:
                response_time = round((asyncio.get_event_loop().time() - start_time) * 1000)  # Время в мс
                conn.close()
                return {"ip": ip, "status": True, "response_time": response_time}
        except (socket.timeout, socket.error) as e:
            function_logger.info(f"Попытка подключения {attempt + 1} к серверу {server_name} ({ip}) не удалась: {str(e)}")
            if attempt < retries - 1:
                await asyncio.sleep(delay)  # Пауза перед повторной попыткой
            else:
                # Если retries исчерпаны, считаем сервер недоступным
                return {"ip": ip, "status": False, "response_time": None}


async def check_servers_availability():
    """
    Асинхронно проверяет доступность всех серверов и отправляет уведомления в случае изменений статуса.
    """
    function_logger = setup_function_logger('log/bot.log')
    global server_status, server_stats

    tasks = [check_server(server) for server in SERVERS]  # Создаем асинхронные задачи для каждого сервера
    results = await asyncio.gather(*tasks)  # Выполняем задачи параллельно

    for result in results:
        ip = result["ip"]
        new_status = result["status"]
        response_time = result["response_time"]
        server_name = next(server['name'] for server in SERVERS if server['ip'] == ip)


        # Сохраняем проверку в очередь статистики
        server_stats[ip].append((time.time(), new_status))

        # Если статус изменился, уведомляем и обновляем хранилище статусов
        if new_status and not server_status[ip]["status"]:
            # Сервер снова доступен
            function_logger.info(f"Сервер {server_name} ({ip}) стал доступен в {time.ctime()}. Время отклика: {response_time} мс")
            await bot.send_message(
                chat_id=CHAT_ID,
                text=f"✅ Сервер {next(s['name'] for s in SERVERS if s['ip'] == ip)} ({ip}) снова доступен!\n\n⏱ Время ответа: {result['response_time']} мс",
                disable_notification=True  # Тихое уведомление
            )
        elif not new_status and server_status[ip]["status"]:
            # Сервер стал недоступен
            function_logger.info(f"Сервер {server_name} ({ip}) недоступен в {time.ctime()}")
            await bot.send_message(
                chat_id=CHAT_ID,
                text=f"⚠️ Сервер {next(s['name'] for s in SERVERS if s['ip'] == ip)} ({ip}) недоступен!",
                disable_notification=True  # Тихое уведомление
            )

        # Обновляем статус сервера
        server_status[ip] = {"status": new_status, "response_time": response_time}

    # Очистка старых данных
    clean_old_stats()

    # Сохраняем изменения после проверки
    save_status()


def calculate_stats(ip):
    """
    Рассчитывает статистику доступности для сервера за последние 24 часа.
    """
    now = time.time()
    checks = [status for timestamp, status in server_stats[ip] if timestamp >= now - MONITORING_WINDOW]
    total_checks = len(checks)
    successful_checks = sum(checks)
    failed_checks = total_checks - successful_checks
    availability = round((successful_checks / total_checks) * 100, 2) if total_checks > 0 else 0
    return total_checks, successful_checks, failed_checks, availability


#Обработчик команды /start
@dp.message_handler(commands=["start"])
async def send_welcome(message: types.Message):
    adm = users
    if message.chat.id not in adm:
        await message.answer('Это приватный бот, нехуй тебе здесь делать.\n\nВаш ID внесен в базу, это означает, что Вам ПИЗДА 🤪\n\nВаш ID: '+ str(message.chat.id))
        logging.info(f"Пользователь {message.chat.id} вызвал команду /start.")
    else:
        await message.answer('Здарова, ёпта! Я на связи, всё путём 😉\n\nЕсли что-то забыл, ебани /help')

#Обработчик команды /help
@dp.message_handler(commands=["help"])
async def send_welcome(message: types.Message):
    adm = users
    if message.chat.id not in adm:
        await message.answer('Я же сказал, нехуй тебе здесь делать!')
    else:
        await message.answer('/status - текущий статус серверов\n\n/stats - общая статистика доступности серверов\n\n/graph - графики доступности серверов\n\n/log - просмотр логов')

# Функция для отправки длинных сообщений частями (без разрыва строк)
async def send_long_message(chat_id, lines, max_message_length=4000):
    chunk = []  # Массив строк для текущего блока
    current_length = 0  # Общая длина текущего блока

    for line in lines:
        line_length = len(line)
        # Если добавление строки превышает лимит, отправляем текущий блок
        if current_length + line_length > max_message_length:
            await bot.send_message(chat_id, "\n".join(chunk))
            chunk = []  # Очищаем текущий блок
            current_length = 0  # Сбрасываем длину блока

        chunk.append(line)  # Добавляем строку в текущий блок
        current_length += line_length

    # Отправляем оставшиеся строки в финальном блоке
    if chunk:
        await bot.send_message(chat_id, "\n".join(chunk))

#Обработчик команды /log (получаем логи из файла в чат)
@dp.message_handler(commands=['log'])
async def send_logs(message: types.Message):
    adm = users
    if message.chat.id not in adm:
        await message.answer('Я же сказал, нехуй тебе здесь делать!')
    else:
        # Достаем список файлов логов
        log_files = [f for f in os.listdir('log') if f.endswith('.log')]
        if not log_files:
            await message.answer("Нет доступных файлов логов.")
            return

        # Создаем кнопки для выбора файла
        file_buttons = [InlineKeyboardButton(f, callback_data=f"file_{f}") for f in log_files]
        file_kb = InlineKeyboardMarkup(row_width=2).add(*file_buttons)
        await message.answer("Выберите файл логов:", reply_markup=file_kb)

# Обработка выбора файла
@dp.callback_query_handler(lambda callback: callback.data.startswith('file_'))
async def process_file_selection(callback_query: types.CallbackQuery):
    selected_file = callback_query.data.split('file_')[1]

    # Сохраняем выбранный файл в состоянии пользователя
    user_id = callback_query.from_user.id
    callback_query.message.selected_file = selected_file

    # Создаем кнопки для выбора: все логи или фильтрация по числу и часу
    mode_buttons = [
        InlineKeyboardButton("Все логи", callback_data=f"mode_all_{selected_file}"),
        InlineKeyboardButton("Фильтр по числу и часу", callback_data=f"mode_filtered_{selected_file}"),
    ]
    mode_kb = InlineKeyboardMarkup(row_width=1).add(*mode_buttons)
    await callback_query.message.answer("Выберите режим отображения логов:", reply_markup=mode_kb)

# Обработка выбора режима (все логи или фильтрация)
@dp.callback_query_handler(lambda callback: callback.data.startswith('mode_'))
async def process_mode_selection(callback_query: types.CallbackQuery):
    mode, selected_file = callback_query.data.split('_')[1], callback_query.data.split('_')[-1]

    if mode == "all":
        # Считать все данные из файла логов
        if os.path.exists(f"log/{selected_file}"):
            try:
                with open(f"log/{selected_file}", 'r', encoding='utf-8') as file:
                    logs = file.readlines()
                await callback_query.message.answer(f"Все логи из файла {selected_file}:")
                await send_long_message(callback_query.message.chat.id, logs)
            except Exception as e:
                await callback_query.message.answer(f"Ошибка при обработке файла логов: {e}")
        else:
            print(selected_file)
            await callback_query.message.answer("Файл логов не найден.")
    elif mode == "filtered":
        # Создаем инлайн кнопки для выбора числа (1-31)
        days_buttons = [InlineKeyboardButton(str(i), callback_data=f"day_{i}_{selected_file}") for i in range(1, 32)]
        days_kb = InlineKeyboardMarkup(row_width=7).add(*days_buttons)
        await callback_query.message.answer("Выберите число:", reply_markup=days_kb)

# Обработка выбора числа
@dp.callback_query_handler(lambda callback: callback.data.startswith('day_'))
async def process_day(callback_query: types.CallbackQuery):
    callback_data = callback_query.data.split('_')
    day = callback_data[1].zfill(2)  # Преобразуем число в формат "01", "02", и т.д.
    selected_file = callback_data[2]
    # Создаем кнопки для выбора часа (0–23)
    hours_buttons = [InlineKeyboardButton(f"{i}:00", callback_data=f"hour_{i}_{day}_{selected_file}") for i in range(24)]
    hours_kb = InlineKeyboardMarkup(row_width=6).add(*hours_buttons)
    await callback_query.message.answer(f"Вы выбрали {day} число. Теперь выберите час:", reply_markup=hours_kb)

# Обработка выбора часа и отправка логов
@dp.callback_query_handler(lambda callback: callback.data.startswith('hour_'))
async def process_hour(callback_query: types.CallbackQuery):
    callback_data = callback_query.data.split('_')
    hour = int(callback_data[1])  # Час
    day = callback_data[2]  # Число месяца
    selected_file = callback_data[3]  # Файл логов

    if os.path.exists(f"log/{selected_file}"):
        try:
            # Открываем файл с логами
            with open(f"log/{selected_file}", 'r', encoding='utf-8') as file:
                logs = file.readlines()

            # Фильтруем логи по числу и часу
            filtered_logs = [log for log in logs if f"-{day} " in log and log.split()[1].startswith(f"{hour:02}:")]

            if filtered_logs:
                # Отправляем логи в чат
                await callback_query.message.answer(f"Логи за {day} число и {hour:02}:00 час из файла {selected_file}:")
                await send_long_message(callback_query.message.chat.id, filtered_logs)
            else:
                await callback_query.message.answer(f"Нет данных логов за {day} число и {hour:02}:00 час в файле {selected_file}.")
        except Exception as e:
            await callback_query.message.answer(f"Ошибка при обработке файла логов: {e}")
    else:
        await callback_query.message.answer('Файл логов не найден.')

#Обработчик команды /status для проверки текущего состояния серверов
@dp.message_handler(commands=["status"])
async def send_status(message: types.Message):
    adm = users
    if message.chat.id not in adm:
        await message.answer('Я же сказал, нехуй тебе здесь делать!')
    else:
        global server_status
        status_message = "📊 Текущий статус серверов:\n\n"
        for server in SERVERS:
            ip = server["ip"]
            status = "✅ Доступен" if server_status[ip]["status"] else "❌ Недоступен"
            response_time = (
                f"⏱ {server_status[ip]['response_time']} мс"
                if server_status[ip]["response_time"] is not None
                else "N/A"
            )
            status_message += f"{server['name']} ({ip}): {status} ({response_time})\n"

        await message.answer(status_message)

#Обработчик команды /stats
@dp.message_handler(commands=["stats"])
async def send_stats(message: types.Message):
    """
    Реагирует на команду /stats для отображения общей статистики доступности серверов.
    """
    adm = users
    if message.chat.id not in adm:
        await message.answer('Я же сказал, нехуй тебе здесь делать!')
    else:
        global server_stats

        # Формируем сообщение статистики
        stats_message = "📊 Общая статистика серверов:\n\n"
        for server in SERVERS:
            ip = server["ip"]
            stats = server_stats.get(ip, {"total_checks": 0, "successful_checks": 0, "failed_checks": 0})

            # Рассчитываем метрики
            total_checks = len(stats)
            successful_checks = sum(1 for ts, status in stats if status)  # Количество успешных проверок
            failed_checks = total_checks - successful_checks  # Количество неудачных проверок
            availability = round((successful_checks / total_checks) * 100, 2) if total_checks > 0 else "N/A"

            stats_message += (
                f"💻 {server['name']} ({ip}):\n"
                f"  ✅ Успешных проверок: {successful_checks}\n"
                f"  ❌ Неудачных проверок: {failed_checks}\n"
                f"  📈 Доступность: {availability}%\n"
                f"  🔄 Всего проверок: {total_checks}\n\n"
            )

        await message.answer(stats_message)

#Обработчик команды /graph (создание кнопок)
@dp.message_handler(commands=["graph"])
async def send_graph1(message: types.Message):
    """
    Создает ступенчатый график доступности серверов с учетом критических событий (изменений состояния).
    """
    adm = users
    if message.chat.id not in adm:
        await message.answer("Я же сказал, нехуй тебе здесь делать!")
        return

    # Создаём клавиатуру для выбора временного диапазона
    keyboard = InlineKeyboardMarkup(row_width=2)  # Располагаем кнопки в 2 столбика
    buttons = [
        InlineKeyboardButton(text="1 час", callback_data="graph_1h"),
        InlineKeyboardButton(text="6 часов", callback_data="graph_6h"),
        InlineKeyboardButton(text="12 часов", callback_data="graph_12h"),
        InlineKeyboardButton(text="24 часа", callback_data="graph_24h"),
        InlineKeyboardButton(text="последние 50 проверок", callback_data="last_50pr"),
    ]
    keyboard.add(*buttons)

    # Отправляем сообщение с кнопками
    await message.answer("📊 Выберите временной диапазон для графика:", reply_markup=keyboard)

#Обработчик кнопки (последние 50 проверок)
@dp.callback_query_handler(text="last_50pr")
async def send_eng(callback: types.CallbackQuery):
    """
    Создает ступенчатый график доступности серверов с вертикальными отступами на оси Y.
    """

    global server_stats

    # Подготовка данных
    server_names = [server["name"] for server in SERVERS]
    colors = ['blue', 'green', 'orange', 'red', 'purple', 'cyan', 'magenta']  # Цвета для серверов
    max_checks = 50  # Отобразим последние 50 проверок
    offset_step = 1.5  # Отступ по оси Y между серверами

    plt.figure(figsize=(16, 10))  # Размер изображения

    # Построение ступенчатых графиков для каждого сервера
    for idx, server in enumerate(SERVERS):
        ip = server["ip"]
        name = server["name"]

        stats = server_stats.get(ip, deque())
#        logging.info(f"Сервер {server['name']} ({ip}): всего записей: {len(stats)}")

        stats = server_stats.get(ip, deque())  # Получаем данные (все проверки)
        stats = list(islice(stats, max(len(stats) - max_checks, 0), len(stats)))  # Оставляем только последние max_checks записей
#        logging.info(f"{name} ({ip}): Загружено {len(stats)} записей для отображения на графике")

        timestamps = [datetime.fromtimestamp(ts) for ts, _ in stats]  # Преобразование меток времени в формат datetime вместо строк

        availability = [1 if status else 0 for _, status in stats]  # Теперь заполняем переменную на основе stats

        # Обработка отсутствия данных
        if not availability:
#            timestamps = ['Нет данных']
            timestamps = [datetime.now()]  # Используем текущее время для пустых данных
            availability = [0]

        offset = idx * offset_step  # Добавляем вертикальный отступ (offset) для текущего сервера
        availability_with_offset = [a + offset for a in availability]  # Смещение значений

        # Построение ступенчатого графика
        plt.step(timestamps, availability_with_offset, label=name, color=colors[idx % len(colors)], where='post')

        # Добавляем подписи рядом с каждой линией
        if timestamps:
            center_idx = len(timestamps) // 2  # Центр временных меток
            plt.text(timestamps[center_idx], offset + 1.1, f' {name}', color=colors[idx % len(colors)], fontsize=10, ha='center', va='bottom')

    # Настройки отображения
    plt.title('Доступность серверов с вертикальными отступами (последние 50 проверок)', fontsize=16)
    plt.xlabel('Время проверки', fontsize=12)
    plt.ylabel('Доступность', fontsize=12)
    plt.xticks(rotation=45, fontsize=10)  # Поворот подписей временной оси
    plt.yticks([], [])  # Убираем автоматически создаваемые метки на оси Y
    plt.grid(axis='x', linestyle='--', alpha=0.7)
    plt.gca().xaxis.set_major_locator(mdates.MinuteLocator(interval=2))  # Каждая 2 минута
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))  # Формат HH:MM:SS
    plt.gcf().autofmt_xdate()  # Автоматически форматируем метки времени

    # Легенда для графика
    plt.legend(title='Сервера', fontsize=10)

    # Сохранение графика
    graph_path = 'server_availability_graph.png'
    plt.tight_layout()  # Автоматическое исправление границ
    plt.savefig(graph_path, dpi=300)
    plt.close()

    # Отправка графика в Telegram
    with open(graph_path, 'rb') as photo:
        await bot.send_photo(
            chat_id=callback.message.chat.id,
            photo=photo,
            caption='📊 График доступности серверов с вертикальными отступами (последние 50 проверок)'
        )

    # Удаляем файл после отправки
    os.remove(graph_path)


#Обработчик кнопок с построение графиков (временные диапазаны)
@dp.callback_query_handler(lambda call: call.data.startswith("graph_"))
async def send_graph_callback(call: types.CallbackQuery):
    """
    Обрабатывает выбор кнопки для отображения графика с указанным диапазоном времени.
    """
    # Определяем временной диапазон из callback_data
    time_ranges = {
        "graph_1h": 1,
        "graph_6h": 6,
        "graph_12h": 12,
        "graph_24h": 24,
    }
    range_key = call.data
    if range_key not in time_ranges:
        await call.answer("Неверный выбор!", show_alert=True)
        return

    hours = time_ranges[range_key]  # Получаем количество часов
    time_range = hours * 60 * 60

    global server_stats

    # Подготовка графика
    server_names = [server["name"] for server in SERVERS]
    colors = ['blue', 'green', 'orange', 'red', 'purple', 'cyan', 'magenta']
    offset_step = 1.5  # Отступ по оси Y между серверами

    plt.figure(figsize=(16, 10))  # Размер изображения
#    plt.figure(figsize=(23, 10))  # Размер изображения

    now = time.time()

    # Построение графика для каждого сервера
    for idx, server in enumerate(SERVERS):
        ip = server["ip"]
        name = server["name"]

        # Получаем данные за указанный диапазон времени
        stats = server_stats.get(ip, deque())
        filtered_stats = [(ts, status) for ts, status in stats if ts >= now - time_range]

        # Алгоритм редукции, сохраняющий изменения доступности
        reduced_stats = []
        last_status = None
        step = max(1, len(filtered_stats) // 60)  # Интервал редукции (оставляем максимум 120 точек)
        for i, (timestamp, status) in enumerate(filtered_stats):
            # Сохраняем первую точку, каждую step-ю точку или изменения состояния
            if i % step == 0 or status != last_status:
                reduced_stats.append((timestamp, status))
                last_status = status

        timestamps = [datetime.fromtimestamp(ts) for ts, _ in reduced_stats]  # Преобразуем временные метки в формат datetime

        availability = [1 if status else 0 for _, status in reduced_stats]  # Теперь заполняем переменную на основе stats

        # Если данных недостаточно, добавляем заглушки
        if not availability:
#            timestamps = ['Нет данных']
            timestamps = [datetime.now()]  # Используем текущее время в качестве заглушки
            availability = [0]

        # Добавляем вертикальный офсет (отступ по Y) для сервера
        offset = idx * offset_step
        availability_with_offset = [a + offset for a in availability]

        # Строим график
        plt.step(timestamps, availability_with_offset, label=name, color=colors[idx % len(colors)], where='post')

        # Добавляем подпись рядом с сервером
        if timestamps:
            center_idx = len(timestamps) // 2  # Центр временных меток
            plt.text(timestamps[center_idx], offset + 1.1, f' {name}', color=colors[idx % len(colors)], fontsize=10, ha='center', va='bottom')

    # Настройки графика
    plt.title(f'Доступность серверов за последние {hours} часов', fontsize=16)
    plt.xlabel('Время проверки', fontsize=12)
    plt.ylabel('Доступность', fontsize=12)
    plt.xticks(rotation=45, fontsize=10)  # Поворот временных меток
    plt.yticks([], [])  # Убираем автоматические метки на оси Y
    plt.grid(axis='x', linestyle='--', alpha=0.7)
    if hours == 24:
        plt.gca().xaxis.set_major_locator(mdates.MinuteLocator(interval=60))  # Каждые 60 минут
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))  # Формат отображения: ЧЧ:ММ
    elif hours == 12:
        plt.gca().xaxis.set_major_locator(mdates.MinuteLocator(interval=30))  # Каждые 30 минут
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))  # Формат отображения: ЧЧ:ММ
    elif hours == 6:
        plt.gca().xaxis.set_major_locator(mdates.MinuteLocator(interval=10))  # Каждые 10 минут
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))  # Формат отображения: ЧЧ:ММ
    else:
        plt.gca().xaxis.set_major_locator(mdates.MinuteLocator(interval=2))  # Каждые 2 минуты
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))  # Формат отображения: ЧЧ:ММ
    plt.gcf().autofmt_xdate()  # Автоматическое форматирование меток времени

    # Добавляем легенду
    plt.legend(title='Сервера', fontsize=10)

    # Сохраняем график
    graph_path = 'server_availability_graph.png'
    plt.tight_layout()  # Исправление границ
    plt.savefig(graph_path, dpi=300)
    plt.close()

    # Отправляем график в Telegram
    with open(graph_path, 'rb') as photo:
        await bot.send_photo(
            chat_id=call.message.chat.id,
            photo=photo,
            caption=f'📊 График доступности серверов за последние {hours} часов'
        )

    # Удаляем локальный файл графика
    os.remove(graph_path)

#Обработчик текста
@dp.message_handler(content_types=['text'])
async def text(message: types.Message):
    adm = users
    if message.chat.id not in adm:
        await message.answer('Я же сказал, нехуй тебе здесь делать!')
    else:
        await message.answer('Ну ты чё ебанулся? Я же ничего не обрабатываю, кроме определенных команд.\n\nЕсли что-то забыл, ебани /help')

async def scheduled_monitoring():
    """
    Периодическая проверка доступности серверов.
    """
    while True:
        await check_servers_availability()
        await asyncio.sleep(60)  # Проверяем каждые 60 секунд


if __name__ == "__main__":
    # Загружаем сохраненные данные
    load_status()
    # Запускаем задачу мониторинга в фоне
    loop = asyncio.get_event_loop()
    loop.create_task(scheduled_monitoring())

    # Запускаем бота
    executor.start_polling(dp, skip_updates=True)
