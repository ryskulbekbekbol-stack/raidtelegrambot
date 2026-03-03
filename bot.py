#!/usr/bin/env python3
# ██████╗ ███████╗ █████╗ ██████╗     ████████╗ ██████╗
# ██╔══██╗██╔════╝██╔══██╗██╔══██╗    ╚══██╔══╝██╔════╝
# ██████╔╝█████╗  ███████║██║  ██║       ██║   ██║  ███╗
# ██╔══██╗██╔══╝  ██╔══██║██║  ██║       ██║   ██║   ██║
# ██║  ██║███████╗██║  ██║██████╔╝       ██║   ╚██████╔╝
# ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═════╝        ╚═╝    ╚═════╝
#           ACCOUNT-BASED RAID BOT
#        ⚡ .raid ВАС ЕБЕТ @username ⚡

import os
import sys
import time
import random
import asyncio
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

# Telethon для работы с пользовательскими аккаунтами [citation:2][citation:7]
from telethon import TelegramClient, events, functions, types
from telethon.tl.types import InputReportReasonSpam
from telethon.errors import FloodWaitError, UserPrivacyRestrictedError, PeerFloodError

# Aiogram для управляющего бота [citation:2]
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.utils import executor
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage

# ========== НАСТРОЙКА ЛОГИРОВАНИЯ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('raid_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('RaidBot')

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = os.getenv('BOT_TOKEN')  # Токен управляющего бота (от @BotFather)
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не установлен!")
    sys.exit(1)

ADMIN_IDS = [int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()]
if not ADMIN_IDS:
    ADMIN_IDS = []
    logger.warning("⚠️ ADMIN_IDS не установлены")

# API данные для Telethon [citation:1][citation:5]
API_ID = os.getenv('API_ID')  # Получить на my.telegram.org
API_HASH = os.getenv('API_HASH')
if not API_ID or not API_HASH:
    logger.error("❌ API_ID или API_HASH не установлены!")
    sys.exit(1)

# Папка с сессиями аккаунтов
SESSIONS_DIR = 'sessions'

# ========== БАЗА ДАННЫХ ДЛЯ СЕССИЙ ==========
class SessionDB:
    """База данных для хранения информации о сессиях [citation:3]"""
    
    def __init__(self, db_path='sessions.db'):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._init_db()
    
    def _init_db(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_name TEXT UNIQUE,
                phone TEXT,
                status TEXT DEFAULT 'active',
                last_used TIMESTAMP,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.conn.commit()
    
    def add_session(self, session_name, phone):
        try:
            self.cursor.execute(
                'INSERT INTO sessions (session_name, phone, last_used) VALUES (?, ?, CURRENT_TIMESTAMP)',
                (session_name, phone)
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def get_all_sessions(self):
        self.cursor.execute('SELECT session_name, phone FROM sessions WHERE status = "active"')
        return self.cursor.fetchall()
    
    def mark_banned(self, session_name):
        self.cursor.execute('UPDATE sessions SET status = "banned" WHERE session_name = ?', (session_name,))
        self.conn.commit()
    
    def close(self):
        self.conn.close()

db = SessionDB()

# ========== МЕНЕДЖЕР СЕССИЙ ==========
class SessionManager:
    """Управление множеством Telegram-аккаунтов через сессии [citation:5][citation:9]"""
    
    def __init__(self):
        self.clients = []
        self.sessions = []
        self.load_sessions()
    
    def load_sessions(self):
        """Загрузка всех сессий из папки sessions/"""
        if not os.path.exists(SESSIONS_DIR):
            os.makedirs(SESSIONS_DIR)
            logger.warning(f"📁 Создана папка {SESSIONS_DIR}. Добавьте туда .session файлы аккаунтов.")
            return
        
        for file in os.listdir(SESSIONS_DIR):
            if file.endswith('.session'):
                session_name = file.replace('.session', '')
                self.sessions.append(session_name)
                # Добавляем в БД, если нет
                db.add_session(session_name, "unknown")
        
        logger.info(f"📂 Загружено {len(self.sessions)} сессий")
    
    async def init_clients(self):
        """Инициализация клиентов Telethon для всех сессий [citation:7]"""
        self.clients = []
        
        for session_name in self.sessions:
            try:
                session_path = f"{SESSIONS_DIR}/{session_name}"
                client = TelegramClient(session_path, int(API_ID), API_HASH)
                
                await client.connect()
                
                if not await client.is_user_authorized():
                    logger.warning(f"⚠️ Сессия {session_name} не авторизована")
                    continue
                
                # Проверяем, не забанен ли аккаунт
                try:
                    me = await client.get_me()
                    logger.info(f"✅ Аккаунт @{me.username or me.first_name} готов")
                except Exception as e:
                    logger.error(f"❌ Ошибка с аккаунтом {session_name}: {e}")
                    db.mark_banned(session_name)
                    continue
                
                self.clients.append(client)
                
            except Exception as e:
                logger.error(f"❌ Ошибка загрузки сессии {session_name}: {e}")
        
        logger.info(f"🎯 Готово клиентов: {len(self.clients)}")
        return self.clients
    
    async def get_random_clients(self, count=None):
        """Получить случайные клиенты для рейда"""
        if not self.clients:
            await self.init_clients()
        
        if count and count < len(self.clients):
            return random.sample(self.clients, count)
        return self.clients
    
    async def close_all(self):
        """Закрыть все клиенты"""
        for client in self.clients:
            await client.disconnect()

session_manager = SessionManager()

# ========== ПАРСИНГ КОМАНДЫ ==========
def parse_raid_command(text):
    """
    Парсит команду типа: .raid ВАС ЕБЕТ @username
    """
    if not text.startswith('.raid'):
        return None, None, None
    
    parts = text.split()
    if len(parts) < 2:
        return None, None, None
    
    # Убираем .raid из начала
    command_parts = parts[1:]
    
    # Ищем упоминание (@username)
    target = None
    message_parts = []
    
    for part in command_parts:
        if part.startswith('@'):
            target = part
        else:
            message_parts.append(part)
    
    # Если нет @, значит рейд на всю группу (если команда в группе)
    if not target:
        message = ' '.join(command_parts) if command_parts else "⚡ РЕЙД"
        return 'group', message, None
    
    # Есть конкретный пользователь
    message = ' '.join(message_parts) if message_parts else "⚡ РЕЙД"
    return 'user', message, target

# ========== ФУНКЦИИ РЕЙДА ==========
async def raid_user(target_username, message, clients, delay_range=(1, 3)):
    """
    Рейд на конкретного пользователя [citation:1]
    
    target_username: @username цели
    message: сообщение для отправки
    clients: список клиентов Telethon
    delay_range: диапазон задержки между действиями
    """
    logger.info(f"🎯 Рейд на пользователя {target_username} с сообщением: {message}")
    
    results = {'success': 0, 'fail': 0, 'total': len(clients)}
    
    try:
        # Получаем entity цели через первого клиента
        target_entity = await clients[0].get_entity(target_username)
        target_id = target_entity.id
        logger.info(f"✅ Найден пользователь: {target_username} (ID: {target_id})")
    except Exception as e:
        logger.error(f"❌ Не удалось найти пользователя {target_username}: {e}")
        return results
    
    for i, client in enumerate(clients):
        try:
            # Отправляем сообщение от имени аккаунта [citation:7]
            await client.send_message(target_id, message)
            results['success'] += 1
            logger.info(f"📨 [{i+1}/{len(clients)}] Сообщение отправлено")
            
            # Отправляем жалобу (опционально)
            if random.random() > 0.5:
                try:
                    # Жалуемся на последнее сообщение (упрощённо)
                    await client(functions.messages.ReportRequest(
                        peer=target_id,
                        id=[random.randint(1, 1000)],  # в реальности нужен ID сообщения
                        reason=InputReportReasonSpam()
                    ))
                except Exception as rep_err:
                    logger.debug(f"Ошибка жалобы: {rep_err}")
            
            # Задержка между действиями разных аккаунтов [citation:5]
            delay = random.uniform(delay_range[0], delay_range[1])
            await asyncio.sleep(delay)
            
        except FloodWaitError as e:
            logger.warning(f"⏳ Flood wait {e.seconds} сек")
            await asyncio.sleep(e.seconds)
            results['fail'] += 1
            
        except UserPrivacyRestrictedError:
            logger.warning(f"🔒 Пользователь ограничил приватность")
            results['fail'] += 1
            
        except PeerFloodError:
            logger.warning(f"⚠️ Peer flood error, возможно, аккаунт забанен")
            results['fail'] += 1
            # Можно пометить аккаунт как проблемный
            
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            results['fail'] += 1
    
    return results

async def raid_group(group_entity, message, clients, delay_range=(1, 3)):
    """
    Рейд на группу (отправка сообщений в чат)
    """
    logger.info(f"👥 Рейд на группу с сообщением: {message}")
    
    results = {'success': 0, 'fail': 0, 'total': len(clients)}
    
    for i, client in enumerate(clients):
        try:
            await client.send_message(group_entity, message)
            results['success'] += 1
            logger.info(f"📨 [{i+1}/{len(clients)}] Сообщение в группу отправлено")
            
            delay = random.uniform(delay_range[0], delay_range[1])
            await asyncio.sleep(delay)
            
        except FloodWaitError as e:
            logger.warning(f"⏳ Flood wait {e.seconds} сек")
            await asyncio.sleep(e.seconds)
            results['fail'] += 1
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            results['fail'] += 1
    
    return results

# ========== УПРАВЛЯЮЩИЙ БОТ (AIOGRAM) ==========
storage = MemoryStorage()
admin_bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(admin_bot, storage=storage)
dp.middleware.setup(LoggingMiddleware())

# Состояния для добавления сессии
class AddSession(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_2fa = State()

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    """Стартовая команда"""
    user_id = message.from_user.id
    
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await message.reply("❌ Доступ запрещён")
        return
    
    text = (
        "🔥 **RAID BOT** 🔥\n\n"
        "**Команды:**\n"
        "`.raid ТЕКСТ @юзер` — рейд на пользователя\n"
        "`.raid ТЕКСТ` — рейд в текущий чат\n\n"
        "**Управление сессиями:**\n"
        "/sessions — список сессий\n"
        "/add_session — добавить новую сессию\n"
        "/test_sessions — тест всех сессий\n\n"
        "**Статистика:**\n"
        "/stats — статистика"
    )
    await message.reply(text, parse_mode='Markdown')

@dp.message_handler(commands=['sessions'])
async def cmd_sessions(message: types.Message):
    """Список всех сессий"""
    user_id = message.from_user.id
    
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await message.reply("❌ Доступ запрещён")
        return
    
    sessions = db.get_all_sessions()
    if not sessions:
        await message.reply("📂 Нет активных сессий")
        return
    
    text = "📂 **Активные сессии:**\n\n"
    for i, (session_name, phone) in enumerate(sessions, 1):
        text += f"{i}. `{session_name}` — {phone}\n"
    
    await message.reply(text, parse_mode='Markdown')

@dp.message_handler(commands=['add_session'])
async def cmd_add_session(message: types.Message):
    """Добавление новой сессии [citation:3][citation:6]"""
    user_id = message.from_user.id
    
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await message.reply("❌ Доступ запрещён")
        return
    
    await message.reply("📱 Введите номер телефона в международном формате:\nНапример: `+79123456789`", parse_mode='Markdown')
    await AddSession.waiting_for_phone.set()

@dp.message_handler(state=AddSession.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    
    # Создаём временный клиент для авторизации
    session_name = f"user_{int(time.time())}"
    client = TelegramClient(f"{SESSIONS_DIR}/{session_name}", int(API_ID), API_HASH)
    
    await client.connect()
    
    try:
        # Отправляем код подтверждения [citation:6][citation:7]
        sent_code = await client.send_code_request(phone)
        await state.update_data(client=client, session_name=session_name, phone=phone, phone_code_hash=sent_code.phone_code_hash)
        
        await message.reply("🔑 Введите код подтверждения, полученный в Telegram:")
        await AddSession.waiting_for_code.set()
        
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")
        await client.disconnect()
        await state.finish()

@dp.message_handler(state=AddSession.waiting_for_code)
async def process_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    data = await state.get_data()
    client = data['client']
    
    try:
        # Пытаемся войти с кодом [citation:6]
        await client.sign_in(data['phone'], code, phone_code_hash=data['phone_code_hash'])
        
        # Успешный вход
        me = await client.get_me()
        await client.disconnect()
        
        # Сохраняем в БД
        db.add_session(data['session_name'], data['phone'])
        
        await message.reply(f"✅ Сессия добавлена!\nАккаунт: @{me.username or me.first_name}")
        await state.finish()
        
    except Exception as e:
        if 'PHONE_CODE_INVALID' in str(e):
            await message.reply("❌ Неверный код. Попробуйте ещё раз:")
            # Остаёмся в том же состоянии
        elif 'SESSION_PASSWORD_NEEDED' in str(e):
            # Требуется 2FA
            await message.reply("🔐 Требуется пароль двухфакторной аутентификации. Введите пароль:")
            await AddSession.waiting_for_2fa.set()
        else:
            await message.reply(f"❌ Ошибка: {e}")
            await client.disconnect()
            await state.finish()

@dp.message_handler(state=AddSession.waiting_for_2fa)
async def process_2fa(message: types.Message, state: FSMContext):
    password = message.text.strip()
    data = await state.get_data()
    client = data['client']
    
    try:
        # Вход с 2FA [citation:6]
        await client.sign_in(password=password)
        
        me = await client.get_me()
        await client.disconnect()
        
        db.add_session(data['session_name'], data['phone'])
        
        await message.reply(f"✅ Сессия добавлена с 2FA!\nАккаунт: @{me.username or me.first_name}")
        
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")
    finally:
        await state.finish()

@dp.message_handler(commands=['test_sessions'])
async def cmd_test_sessions(message: types.Message):
    """Тестирование всех сессий"""
    user_id = message.from_user.id
    
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await message.reply("❌ Доступ запрещён")
        return
    
    await message.reply("🔄 Тестирование сессий...")
    
    clients = await session_manager.get_random_clients()
    
    if not clients:
        await message.reply("❌ Нет активных сессий")
        return
    
    success = 0
    for client in clients:
        try:
            me = await client.get_me()
            logger.info(f"✅ Аккаунт @{me.username} работает")
            success += 1
        except:
            pass
    
    await message.reply(f"✅ Работает: {success}/{len(clients)} сессий")

@dp.message_handler(commands=['stats'])
async def cmd_stats(message: types.Message):
    """Статистика системы"""
    user_id = message.from_user.id
    
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await message.reply("❌ Доступ запрещён")
        return
    
    sessions = db.get_all_sessions()
    clients = await session_manager.get_random_clients()
    
    text = (
        f"📊 **СТАТИСТИКА**\n\n"
        f"📂 Сессий в БД: {len(sessions)}\n"
        f"✅ Активных клиентов: {len(clients)}\n"
        f"🔄 Всего сессий: {len(session_manager.sessions)}"
    )
    await message.reply(text, parse_mode='Markdown')

# ========== ОБРАБОТКА СООБЩЕНИЙ ==========
@dp.message_handler(lambda message: message.text and message.text.startswith('.raid'))
async def handle_raid(message: types.Message):
    """Обработка рейд-команд"""
    user_id = message.from_user.id
    
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return
    
    # Парсим команду
    target_type, msg, target = parse_raid_command(message.text)
    
    if not target_type:
        await message.reply("❌ Неверный формат. Используйте: `.raid ТЕКСТ @юзер`")
        return
    
    # Получаем клиенты
    clients = await session_manager.get_random_clients()
    
    if not clients:
        await message.reply("❌ Нет активных сессий. Добавьте аккаунты через /add_session")
        return
    
    # Отправляем уведомление о начале
    status_msg = await message.reply(
        f"🔥 **РЕЙД ЗАПУЩЕН**\n"
        f"👥 Аккаунтов: {len(clients)}\n"
        f"📨 Сообщение: {msg}\n"
        f"🎯 Цель: {target or 'текущий чат'}"
    )
    
    # Запускаем рейд
    if target_type == 'user' and target:
        # Рейд на конкретного пользователя
        results = await raid_user(target, msg, clients)
    else:
        # Рейд в текущий чат
        results = await raid_group(message.chat.id, msg, clients)
    
    # Отправляем результат
    await status_msg.edit_text(
        f"✅ **РЕЙД ЗАВЕРШЁН**\n"
        f"📨 Успешно: {results['success']}\n"
        f"❌ Ошибок: {results['fail']}\n"
        f"📊 Всего: {results['total']}"
    )

# ========== ЗАПУСК ==========
async def on_startup(dp):
    """Действия при запуске"""
    logger.info("🚀 Инициализация сессий...")
    await session_manager.init_clients()
    logger.info("✅ Бот готов к работе")

async def on_shutdown(dp):
    """Действия при остановке"""
    logger.info("🛑 Закрытие соединений...")
    await session_manager.close_all()
    db.close()
    logger.info("👋 До свидания!")

if __name__ == '__main__':
    print("\n" + "="*60)
    print("🔥 ACCOUNT-BASED RAID BOT 🔥")
    print("="*60)
    print(f"🤖 Управляющий бот: @...")
    print(f"📂 Сессий в папке: {len(session_manager.sessions)}")
    print(f"⚡ Используй команды в Telegram")
    
    executor.start_polling(
        dp,
        skip_updates=True,
        on_startup=on_startup,
        on_shutdown=on_shutdown
)
