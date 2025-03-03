# -*- coding: utf-8 -*-

import asyncio
import socket
import json
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging
import os
import time
from collections import deque
import matplotlib.pyplot as plt
from itertools import islice

from config import *

#Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('log/bot.log'),  # Запись в файл
        logging.StreamHandler()              # Вывод в консоль
    ]
)

#Инициализация бота, диспетчера
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

#Обработчик ошибок
@dp.errors_handler()
async def error_bot(update: types.Update, exception: Exception):
    print(f'Update: {update} \nException: {exception}')
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

                # Проверяем инициализацию server_stats
#                for ip in server_stats:
#                    if not all(key in server_stats[ip] for key in ("total_checks", "successful_checks", "failed_checks")):
#                        server_stats[ip] = {"total_checks": 0, "successful_checks": 0, "failed_checks": 0}

                logging.info("Статусы и статистика успешно загружены из файла.")
        except Exception as e:
            logging.error(f"Ошибка при загрузке статусного файла: {e}")
    else:
        # Инициализируем, если файла нет
        server_status = {server["ip"]: {"status": True, "response_time": None} for server in SERVERS}
        server_stats = {server["ip"]: deque(maxlen=10000) for server in SERVERS}
        logging.info("Файл статусов не найден, создано пустое состояние.")


def save_status():
    """Сохраняет текущий статус и статистику серверов в файл."""
    try:
        data = {
            "server_status": server_status,
            "server_stats": {
                ip: list(stats) for ip, stats in server_stats.items()
            },  # Преобразуем `deque` в списки
        }
        with open(STATUS_FILE, "w") as file:
            json.dump(data, file, indent=4)
        logging.info("Статусы и статистика успешно сохранены.")
    except Exception as e:
        logging.error(f"Ошибка при сохранении статусного файла: {e}")


def clean_old_stats():
    """
    Удаляет проверки, которые старше 24 часов.
    """
    now = time.time()
    for ip, stats in server_stats.items():
        old_count = 0
        while stats and stats[0][0] < now - MONITORING_WINDOW:
            stats.popleft()
            old_count += 1
        if old_count > 0:
            logging.info(f"Удалено {old_count} старых записей для IP {ip}")
            
        # Рассчитываем успешные, неудачные проверки заново
        total_checks = len(stats)
        successful_checks = sum(1 for _, status in stats if status)
        failed_checks = total_checks - successful_checks

        # Логирование обновленной статистики
        logging.info(f"Для IP {ip}: всего проверок - {total_checks}, успешных - {successful_checks}, неудачных - {failed_checks}")


async def check_server(server, retries=3, delay=1):
    """
    Проверяет доступность сервера через указанный порт (443) с использованием socket с несколькими повторами.

    Возвращает:
    - "status": True/False (доступен/недоступен)
    - "response_time": время отклика (в миллисекундах) или None, если сервер недоступен
    - "retries": Количество повторных попыток.
    - "delay": Пауза между повторными попытками (в секундах).
    """
    ip = server["ip"]
    for attempt in range(retries):

        try:
            start_time = asyncio.get_event_loop().time()  # Засекаем время начала проверки

            # Создаем сокет для подключения
            with socket.create_connection((ip, PORT), timeout=5) as conn:
                response_time = round((asyncio.get_event_loop().time() - start_time) * 1000)  # Время в мс
                conn.close()
                return {"ip": ip, "status": True, "response_time": response_time}
        except (socket.timeout, socket.error):
            if attempt < retries - 1:
                await asyncio.sleep(delay)  # Пауза перед повторной попыткой
            else:
                # Если retries исчерпаны, считаем сервер недоступным
                return {"ip": ip, "status": False, "response_time": None}


async def check_servers_availability():
    """
    Асинхронно проверяет доступность всех серверов и отправляет уведомления в случае изменений статуса.
    """
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
            logging.info(f"Сервер {server_name} ({ip}) стал доступен в {time.ctime()}. Время отклика: {response_time} мс")
            await bot.send_message(
                chat_id=CHAT_ID,
                text=f"✅ Сервер {next(s['name'] for s in SERVERS if s['ip'] == ip)} ({ip}) снова доступен!\n\n⏱ Время ответа: {result['response_time']} мс",
                disable_notification=True  # Тихое уведомление
            )
        elif not new_status and server_status[ip]["status"]:
            # Сервер стал недоступен
            logging.info(f"Сервер {server_name} ({ip}) недоступен в {time.ctime()}")
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
        await message.answer('Это приватный бот, нехуй тебе здесь делать.\n\nВаш ID: '+ str(message.chat.id))
    else:
        await message.answer('Привет! Я на связи, всё путём 😉\n\nЕсли что забыл, ебани /help')
        
#Обработчик команды /help
@dp.message_handler(commands=["help"])
async def send_welcome(message: types.Message):
    adm = users
    if message.chat.id not in adm:
        await message.answer('Я же сказал, нехуй тебе здесь делать!')
    else:
        await message.answer('/status - текущий статус серверов\n\n/stats - общая статистика доступности серверов\n\n/graph - графики доступности серверов')

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

        # Данные доступности сервера из статистики
#        stats = server_stats.get(ip, deque(maxlen=max_checks))  # Последние max_checks записей
        stats = server_stats.get(ip, deque())  # Получаем данные (все проверки)
        stats = list(islice(stats, max(len(stats) - max_checks, 0), len(stats)))  # Оставляем только последние max_checks записей
        timestamps = [time.strftime('%H:%M:%S', time.localtime(ts)) for ts, _ in stats]  # Время на оси X
        availability = [1 if status else 0 for _, status in stats]  # Преобразование True/False в 1/0

        # Обработка отсутствия данных
        if not availability:
            timestamps = ['Нет данных']
            availability = [0]

        # Добавляем вертикальный отступ (offset) для текущего сервера
        offset = idx * offset_step
        availability_with_offset = [a + offset for a in availability]  # Смещение значений

        # Построение ступенчатого графика
        plt.step(timestamps, availability_with_offset, label=name, color=colors[idx % len(colors)], where='post')

        # Добавляем подписи рядом с каждой линией
        if timestamps:
            plt.text(len(timestamps) // 2, offset + 0.9, f' {name}', color=colors[idx % len(colors)], fontsize=10, ha='center', va='center')

    # Настройки отображения
    plt.title('Доступность серверов с вертикальными отступами', fontsize=16)
    plt.xlabel('Время проверки', fontsize=12)
    plt.ylabel('Доступность', fontsize=12)
    plt.xticks(rotation=45, fontsize=10)  # Поворот подписей временной оси
    plt.yticks([], [])  # Убираем автоматически создаваемые метки на оси Y
    plt.grid(axis='x', linestyle='--', alpha=0.7)

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


#Обработчик кнопок с построение графиков (временные диапазоны)
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

#    plt.figure(figsize=(12, 10))  # Размер изображения
    plt.figure(figsize=(23, 10))  # Размер изображения

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

        # Преобразование данных в удобный для построения формат
        timestamps = [time.strftime('%H:%M', time.localtime(ts)) for ts, _ in reduced_stats]
        availability = [1 if status else 0 for _, status in reduced_stats]

        # Если данных недостаточно, добавляем заглушки
        if not availability:
            timestamps = ['Нет данных']
            availability = [0]

        # Добавляем вертикальный офсет (отступ по Y) для сервера
        offset = idx * offset_step
        availability_with_offset = [a + offset for a in availability]

        # Строим график
        plt.step(timestamps, availability_with_offset, label=name, color=colors[idx % len(colors)], where='post')

        # Добавляем подпись рядом с сервером
        if timestamps:
            plt.text(len(timestamps) // 2, offset + 0.9, f' {name}', color=colors[idx % len(colors)], fontsize=10, ha='center', va='center')

    # Настройки графика
    plt.title(f'Доступность серверов за последние {hours} часов', fontsize=16)
    plt.xlabel('Время проверки', fontsize=12)
    plt.ylabel('Доступность', fontsize=12)
    plt.xticks(fontsize=8, rotation=45)  # Поворот временных меток
    plt.yticks([], [])  # Убираем автоматические метки на оси Y
    plt.grid(axis='x', linestyle='--', alpha=0.7)

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
        await message.answer('Я ничего не обрабатываю, кроме команд /status, /stats, /graph')

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
