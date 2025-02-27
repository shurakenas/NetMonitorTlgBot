# -*- coding: utf-8 -*-

import asyncio
import socket
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
import logging
import os

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
    {"name": "MyNameServer", "ip": "my_ip"},
    {"name": "MyNameServer", "ip": "my_ip"},
]

# Порт, который будет проверяться
PORT = 443

# Храним статус серверов (доступен/недоступен) и время ответа
server_status = {server["ip"]: {"status": True, "response_time": None} for server in SERVERS}

async def check_server(server):
    """
    Проверяет доступность сервера через указанный порт (443) с использованием socket.

    Возвращает:
    - "status": True/False (доступен/недоступен)
    - "response_time": время отклика (в миллисекундах) или None, если сервер недоступен
    """
    ip = server["ip"]

    try:
        start_time = asyncio.get_event_loop().time()  # Засекаем время начала проверки

        # Создаем сокет для подключения
        with socket.create_connection((ip, PORT), timeout=5) as conn:
            response_time = round((asyncio.get_event_loop().time() - start_time) * 1000)  # Время в мс
            conn.close()
            return {"ip": ip, "status": True, "response_time": response_time}
    except (socket.timeout, socket.error):
        return {"ip": ip, "status": False, "response_time": None}


async def check_servers_availability():
    """
    Асинхронно проверяет доступность всех серверов и отправляет уведомления в случае изменений статуса.
    """
    global server_status

    tasks = [check_server(server) for server in SERVERS]  # Создаем асинхронные задачи для каждого сервера
    results = await asyncio.gather(*tasks)  # Выполняем задачи параллельно

    for result in results:
        ip = result["ip"]
        if result["status"]:
            # Сервер доступен
            if not server_status[ip]["status"]:  # Если он ранее был недоступен
                await bot.send_message(
                    chat_id=CHAT_ID,
                    text=f"✅ Сервер {next(s['name'] for s in SERVERS if s['ip'] == ip)} ({ip}) снова доступен!\n\n⏱ Время ответа: {result['response_time']} мс",
                    disable_notification=True  # Тихое уведомление
                )
            server_status[ip] = {"status": True, "response_time": result["response_time"]}
        else:
            # Сервер недоступен
            if server_status[ip]["status"]:  # Если он ранее был доступен
                await bot.send_message(
                    chat_id=CHAT_ID,
                    text=f"⚠️ Сервер {next(s['name'] for s in SERVERS if s['ip'] == ip)} ({ip}) недоступен!",
                    disable_notification=True  # Тихое уведомление
                )
            server_status[ip] = {"status": False, "response_time": None}


async def scheduled_monitoring():
    """
    Периодическая проверка доступности серверов.
    """
    while True:
        await check_servers_availability()
        await asyncio.sleep(60)  # Проверяем каждые 60 секунд

#Обработчик команды /start
@dp.message_handler(commands=["start"])
async def send_welcome(message: types.Message):
    adm = users
    if message.chat.id not in adm:
        await message.answer('Это приватный бот, нехуй тебе здесь делать.\n\nВаш ID: '+ str(message.chat.id))
    else:
        await message.answer('Привет! Я на связи, всё путём 😉')

#Обработчик команды /status для проверки текущего состояния серверов
@dp.message_handler(commands=["status"])
async def send_status(message: types.Message):
    adm = users
    if message.chat.id not in adm:
        await message.answer('Я же сказал, нехуй тебе здесь делать!')
    else:
        global server_status
        status_message = "Текущий статус серверов:\n\n"
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

#Обработчик текста
@dp.message_handler(content_types=['text'])
async def text(message: types.Message):
    adm = users
    if message.chat.id not in adm:
        await message.answer('Я же сказал, нехуй тебе здесь делать!')
    else:
        await message.answer('Я ничего не обрабатываю, кроме команды /status')


if __name__ == "__main__":
    # Запускаем задачу мониторинга в фоне
    loop = asyncio.get_event_loop()
    loop.create_task(scheduled_monitoring())

    # Запускаем бота
    executor.start_polling(dp, skip_updates=True)
