#!/usr/bin/env python3
# RAID BOT — MAX SPEED EDITION (NO DELAYS)

import os
import sys
import time
import random
import asyncio
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

# Telethon для работы с аккаунтами
from telethon import TelegramClient, functions, types
from telethon.tl.types import InputReportReasonSpam
from telethon.errors import FloodWaitError, UserPrivacyRestrictedError, PeerFloodError

# Aiogram для управляющего бота
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor

# ========== НАСТРОЙКА ==========
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('Raid')

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    print("❌ BOT_TOKEN не установлен")
    sys.exit(1)

ADMIN_IDS = [int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()]

API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
if not API_ID or not API_HASH:
    print("❌ API_ID или API_HASH не установлены")
    sys.exit(1)

SESSIONS_DIR = 'sessions'
os.makedirs(SESSIONS_DIR, exist_ok=True)

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect('sessions.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS sessions (session_name TEXT PRIMARY KEY, phone TEXT, status TEXT DEFAULT 'active')''')
conn.commit()

# ========== МЕНЕДЖЕР СЕССИЙ ==========
class SessionManager:
    def __init__(self):
        self.clients = []
        self.load_sessions()
    
    def load_sessions(self):
        self.sessions = [f.replace('.session', '') for f in os.listdir(SESSIONS_DIR) if f.endswith('.session')]
        logger.info(f"📂 Загружено сессий: {len(self.sessions)}")
    
    async def init_clients(self):
        self.clients = []
        for name in self.sessions:
            try:
                client = TelegramClient(f"{SESSIONS_DIR}/{name}", int(API_ID), API_HASH)
                await client.connect()
                if not await client.is_user_authorized():
                    continue
                self.clients.append(client)
                logger.info(f"✅ Аккаунт {name} готов")
            except Exception as e:
                logger.error(f"❌ Ошибка {name}: {e}")
        return self.clients
    
    async def close_all(self):
        for c in self.clients:
            await c.disconnect()

sm = SessionManager()

# ========== СОСТОЯНИЯ ДЛЯ ДОБАВЛЕНИЯ ==========
class AddSession(StatesGroup):
    phone = State()
    code = State()
    password = State()

# ========== УПРАВЛЯЮЩИЙ БОТ ==========
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

def is_admin(user_id):
    return not ADMIN_IDS or user_id in ADMIN_IDS

# ========== КОМАНДЫ ==========
@dp.message_handler(commands=['start'])
async def cmd_start(m: types.Message):
    if not is_admin(m.from_user.id):
        return await m.reply("❌ Доступ запрещён")
    await m.reply(
        "🔥 **RAID BOT — MAX SPEED** 🔥\n\n"
        "`.raid ТЕКСТ @user` — мгновенный рейд (без задержек)\n"
        "`/add_session` — добавить аккаунт\n"
        "`/sessions` — список аккаунтов\n"
        "`/stats` — статистика\n\n"
        "⚠️ ВНИМАНИЕ: Без задержек аккаунты могут быстрее получить бан!"
    )

@dp.message_handler(commands=['sessions'])
async def cmd_sessions(m: types.Message):
    if not is_admin(m.from_user.id):
        return
    c.execute('SELECT session_name, phone FROM sessions')
    rows = c.fetchall()
    if not rows:
        return await m.reply("📂 Нет сессий")
    text = "📂 **Сессии:**\n" + "\n".join([f"• {r[0]} — {r[1]}" for r in rows])
    await m.reply(text, parse_mode='Markdown')

@dp.message_handler(commands=['stats'])
async def cmd_stats(m: types.Message):
    if not is_admin(m.from_user.id):
        return
    c.execute('SELECT COUNT(*) FROM sessions')
    total = c.fetchone()[0]
    await m.reply(f"📊 **Статистика**\n\n📂 Сессий в БД: {total}\n✅ Активных клиентов: {len(sm.clients)}")

@dp.message_handler(commands=['add_session'])
async def cmd_add_session(m: types.Message):
    if not is_admin(m.from_user.id):
        return
    await m.reply("📱 Введите номер в формате +79123456789")
    await AddSession.phone.set()

@dp.message_handler(state=AddSession.phone)
async def process_phone(m: types.Message, state: FSMContext):
    phone = m.text.strip()
    name = f"user_{int(time.time())}"
    client = TelegramClient(f"{SESSIONS_DIR}/{name}", int(API_ID), API_HASH)
    await client.connect()
    await state.update_data(client=client, name=name, phone=phone)
    await client.send_code_request(phone)
    await m.reply("🔑 Введите код из Telegram")
    await AddSession.code.set()

@dp.message_handler(state=AddSession.code)
async def process_code(m: types.Message, state: FSMContext):
    data = await state.get_data()
    client = data['client']
    try:
        await client.sign_in(data['phone'], m.text.strip())
        me = await client.get_me()
        c.execute('INSERT INTO sessions (session_name, phone) VALUES (?, ?)', (data['name'], data['phone']))
        conn.commit()
        await m.reply(f"✅ Аккаунт @{me.username or me.first_name} добавлен")
        await client.disconnect()
        await state.finish()
    except Exception as e:
        if 'SESSION_PASSWORD_NEEDED' in str(e):
            await m.reply("🔐 Введите пароль 2FA")
            await AddSession.password.set()
        else:
            await m.reply(f"❌ Ошибка: {e}")
            await client.disconnect()
            await state.finish()

@dp.message_handler(state=AddSession.password)
async def process_password(m: types.Message, state: FSMContext):
    data = await state.get_data()
    client = data['client']
    try:
        await client.sign_in(password=m.text.strip())
        me = await client.get_me()
        c.execute('INSERT INTO sessions (session_name, phone) VALUES (?, ?)', (data['name'], data['phone']))
        conn.commit()
        await m.reply(f"✅ Аккаунт @{me.username or me.first_name} добавлен (2FA)")
        await client.disconnect()
    except Exception as e:
        await m.reply(f"❌ Ошибка: {e}")
    finally:
        await state.finish()

# ========== РЕЙД КОМАНДА (БЕЗ ЗАДЕРЖЕК) ==========
@dp.message_handler(lambda m: m.text and m.text.startswith('.raid'))
async def handle_raid(m: types.Message):
    if not is_admin(m.from_user.id):
        return
    
    parts = m.text.split()
    if len(parts) < 2:
        return await m.reply("❌ Формат: `.raid ТЕКСТ @user`")
    
    # Парсим сообщение и цель
    msg = ' '.join(parts[1:])
    target = None
    for part in parts:
        if part.startswith('@'):
            target = part
            msg = msg.replace(part, '').strip()
            break
    
    if not target:
        return await m.reply("❌ Укажи @username")
    
    # Загружаем клиенты
    clients = await sm.init_clients()
    if not clients:
        return await m.reply("❌ Нет активных аккаунтов")
    
    status = await m.reply(f"🔥 Рейд на {target} ({len(clients)} акк.) — БЕЗ ЗАДЕРЖЕК")
    
    # Получаем цель
    try:
        entity = await clients[0].get_entity(target)
        target_id = entity.id
    except Exception as e:
        return await status.edit_text(f"❌ Не найден {target}")
    
    # ОТПРАВЛЯЕМ ВСЁ ОДНОВРЕМЕННО (без задержек)
    success = 0
    tasks = []
    
    for client in clients:
        tasks.append(client.send_message(target_id, msg))
    
    # Запускаем все задачи параллельно
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Считаем успешные
    for r in results:
        if not isinstance(r, Exception):
            success += 1
    
    await status.edit_text(
        f"✅ **РЕЙД ЗАВЕРШЁН**\n"
        f"📨 Успешно: {success}/{len(clients)}\n"
        f"⚡ Режим: без задержек\n"
        f"⏱ Время: мгновенно"
    )

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    print("\n" + "="*50)
    print("🔥 RAID BOT — MAX SPEED 🔥")
    print("="*50)
    print(f"🤖 Бот: @{bot.username}")
    print(f"📂 Сессий: {len(sm.sessions)}")
    print(f"⚡ Режим: БЕЗ ЗАДЕРЖЕК")
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(sm.init_clients())
    
    executor.start_polling(dp, skip_updates=True)
