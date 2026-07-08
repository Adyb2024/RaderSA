#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════════
📡 Telegram Lead Radar Bot - GitHub Actions Edition (Persistent Cache)
═══════════════════════════════════════════════════════════════════════════
"""

import os
import re
import json
import logging
import asyncio
import sqlite3
import threading
import traceback
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from functools import wraps
from typing import Dict, Optional, List, Tuple, Set
from concurrent.futures import ThreadPoolExecutor

from telegram import (
    Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
)
from telegram.ext import (
    Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters,
    ConversationHandler, CallbackContext
)
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError,
    PhoneCodeExpiredError, FloodWaitError
)

# ═══════════════════════════════════════════════════════════════════════════
# 🔧 إعدادات السجل (Logging)
# ═══════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# 📦 متغيرات البيئة
# ═══════════════════════════════════════════════════════════════════════════
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
API_ID = int(os.environ.get("TELEGRAM_API_ID", 0))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
RUN_DURATION = int(os.environ.get("RUN_DURATION", 3000))  # 50 دقيقة افتراضيًا
DATA_DIR = Path("./data")
DB_PATH = DATA_DIR / "users.db"
USER_BLOCKS_DIR = DATA_DIR / "user_blocks"
USER_KEYWORDS_DIR = DATA_DIR / "user_keywords"

DATA_DIR.mkdir(exist_ok=True)
USER_BLOCKS_DIR.mkdir(exist_ok=True)
USER_KEYWORDS_DIR.mkdir(exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════════
# 🗄️ قاعدة البيانات
# ═══════════════════════════════════════════════════════════════════════════
def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            session_string TEXT,
            phone_number TEXT,
            alert_target TEXT DEFAULT 'private',
            alert_target_id TEXT,
            monitoring_active INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_groups (
            user_id INTEGER,
            group_id INTEGER,
            group_title TEXT,
            PRIMARY KEY (user_id, group_id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def db_get_user(user_id: int):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def db_save_session(user_id: int, session_string: str, phone_number: str = ""):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO users (user_id, session_string, phone_number)
        VALUES (?, ?, ?)
    ''', (user_id, session_string, phone_number))
    conn.commit()
    conn.close()

def db_update_alert_settings(user_id: int, alert_target: str, alert_target_id: Optional[str] = None):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    if alert_target_id:
        c.execute('''
            UPDATE users SET alert_target=?, alert_target_id=? WHERE user_id=?
        ''', (alert_target, alert_target_id, user_id))
    else:
        c.execute('UPDATE users SET alert_target=? WHERE user_id=?', (alert_target, user_id))
    conn.commit()
    conn.close()

def db_set_monitoring_status(user_id: int, status: bool):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("UPDATE users SET monitoring_active=? WHERE user_id=?", (1 if status else 0, user_id))
    conn.commit()
    conn.close()

def db_get_user_groups(user_id: int) -> List[Tuple[int, str]]:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("SELECT group_id, group_title FROM user_groups WHERE user_id=?", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def db_save_user_groups(user_id: int, groups: List[Tuple[int, str]]):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("DELETE FROM user_groups WHERE user_id=?", (user_id,))
    for gid, gtitle in groups:
        c.execute("INSERT INTO user_groups (user_id, group_id, group_title) VALUES (?, ?, ?)",
                  (user_id, gid, gtitle))
    conn.commit()
    conn.close()

# ═══════════════════════════════════════════════════════════════════════════
# 🔑 الكلمات المفتاحية
# ═══════════════════════════════════════════════════════════════════════════
DEFAULT_KEYWORDS = {
    "categories": {
        "web": {
            "label": "🌐 تطوير مواقع",
            "keywords": ["مبرمج", "موقع", "متجر", "وردبريس", "تصميم"]
        },
        "app": {
            "label": "📱 تطبيقات جوال",
            "keywords": ["تطبيق", "اندرويد", "ايفون", "flutter", "ios"]
        },
        "marketing": {
            "label": "📈 تسويق",
            "keywords": ["تسويق", "اعلان", "سوشال", "ميديا"]
        },
        "student_services": {
            "label": "🎓 خدمات طلابية",
            "keywords": [
                "أبي أحد", "أبغى أحد", "محتاج شخص", "مين يعرف", "مطلوب", "فزعتكم", "تكفون", "بمقابل", "أحد يساعدني", "مساعدة", "تعرفون أحد", "أبي فزعة", "مين بطل", "تعرفون أحد ثقة", "أحد قد جرب", "بمقابل مادي", "شخص يخلص لي", "أبي أحد فاهم", "مين يعرف للدكتور",
                "بحث", "البحث", "بحوث", "البحوث", "خطة بحث", "مراجعة أدبية", "توثيق", "APA", "تدقيق لغوي", "واجب", "الواجب", "واجبات", "الواجبات", "تكليف", "التكليف", "تكاليف", "التكاليف", "اساينمنت", "الاساينمنت", "assignment", "كويز", "الكويز", "كويزات", "كوز", "لاب", "اللاب", "لابات", "شيت", "الشيت", "تقرير", "التقرير", "تقارير", "التقارير", "تلخيص", "ملخص", "ملخصات", "تفريغ", "تجميعات", "حلول", "نوتس", "سلايدات", "السلايدات", "بروجكت", "البروجكت", "ريبورت",
                "برمجة", "البرمجة", "مبرمج", "بايثون", "python", "جافا", "java", "سي بلس بلس", "C++", "C#", "كود", "الكود", "سورس كود", "مشروع تخرج", "قاعدة بيانات", "داتابيز", "database", "ويب", "تطبيق", "تطبيقات", "أندرويد", "iOS", "شبكات", "أمن سيبراني", "ذكاء اصطناعي", "خوارزميات", "هياكل بيانات",
                "بوربوينت", "البوربوينت", "powerpoint", "عرض", "العرض", "عروض", "العروض", "برزنتيشن", "البرزنتيشن", "presentation", "فوتوشوب", "تصميم", "التصميم", "لوقو", "شعار", "مونتاج", "وورد", "word", "تحويل PDF", "كتابة تقارير",
                "بلاك بورد", "blackboard", "رايات", "تدارس", "ميد", "الميد", "ميدات", "الميدات", "فاينل", "الفاينل", "فاينلات", "الفاينلات", "اختبار نهائي", "دكتور", "الدكتور", "دكتورة", "الدكتورة", "محاضر", "مادة", "المادة", "مقرر", "ساعات", "معدل", "قريد",
                "يحل", "تحل", "يسوي", "تسوي", "يضبط", "تضبط", "يخلص", "تخلص", "يشرح", "تشرح", "يفهم", "تفهم", "يكتب", "تكتب", "يبرمج", "تبرمج", "يصمم", "تصمم",
                "أبي حل مشروع", "مين يسوي بحوث", "أحد يفهم في البرمجة", "مساعده في تكليف", "يسوي عروض بوربوينت", "يحل واجبات برمجية", "مشروع تخرج حاسب", "حل مشروع تخرج", "أحد يضبط لي البحث", "مين يحل الكويز", "حل كويز", "حل واجب", "حل ميد", "عمل بحوث", "كتابة تقرير", "شرح مواد", "برمجة بايثون", "تفريغ محاضرات"
            ]
        }
    },
    "negative_keywords": ["وظيفة", "مجانا", "بدون مقابل", "مطلوب موظف"]
}

def load_user_keywords(user_id: int) -> dict:
    file = USER_KEYWORDS_DIR / f"{user_id}.json"
    if file.exists():
        try:
            with open(file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return DEFAULT_KEYWORDS.copy()

def save_user_keywords(user_id: int, data: dict):
    file = USER_KEYWORDS_DIR / f"{user_id}.json"
    with open(file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def check_negative(text: str, negative_keywords: List[str]) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in negative_keywords)

def check_positive_category(text: str, keywords_data: dict) -> Tuple[Optional[str], Optional[str]]:
    text_lower = text.lower()
    for cat_key, cat_info in keywords_data.get("categories", {}).items():
        for kw in cat_info.get("keywords", []):
            if kw.lower() in text_lower:
                return cat_key, cat_info.get("label", cat_key)
    return None, None

# ═══════════════════════════════════════════════════════════════════════════
# 🚫 قوائم الحظر
# ═══════════════════════════════════════════════════════════════════════════
def get_user_block_dir(user_id: int) -> Path:
    d = USER_BLOCKS_DIR / str(user_id)
    d.mkdir(exist_ok=True)
    return d

def load_user_blocklist(user_id: int, name: str) -> Set[str]:
    file = get_user_block_dir(user_id) / f"{name}.json"
    if file.exists():
        with open(file, 'r') as f:
            return set(json.load(f))
    return set()

def save_user_blocklist(user_id: int, name: str, data: Set[str]):
    file = get_user_block_dir(user_id) / f"{name}.json"
    with open(file, 'w') as f:
        json.dump(list(data), f)

def load_user_seen_ids(user_id: int) -> Set[str]:
    file = get_user_block_dir(user_id) / "seen_ids.txt"
    if file.exists():
        with open(file, 'r') as f:
            return set(line.strip() for line in f)
    return set()

def save_user_seen_id(user_id: int, unique_id: str):
    file = get_user_block_dir(user_id) / "seen_ids.txt"
    with open(file, 'a') as f:
        f.write(f"{unique_id}\n")

def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'[^\w\s\u0600-\u06FF]', '', text)
    return text

def is_message_blocked(user_id: int, unique_id: str) -> bool:
    return unique_id in load_user_blocklist(user_id, "blocked_messages")

def is_user_blocked(user_id: int, sender_id) -> bool:
    return str(sender_id) in load_user_blocklist(user_id, "blocked_users")

def is_text_blocked(user_id: int, text: str) -> bool:
    return normalize_text(text) in load_user_blocklist(user_id, "blocked_texts")

# ═══════════════════════════════════════════════════════════════════════════
# 🧵 مدير جلسات Telethon المركزي (مستقر جداً مع إعادة اتصال تلقائية)
# ═══════════════════════════════════════════════════════════════════════════
class SessionManager:
    def __init__(self):
        self.clients: Dict[int, TelegramClient] = {}
        self.tasks: Dict[int, asyncio.Task] = {}
        self.loop = asyncio.new_event_loop()
        self.bot: Optional[Bot] = None
        self.executor = ThreadPoolExecutor(max_workers=20)
        self.user_sessions: Dict[int, str] = {}

    def set_bot(self, bot: Bot):
        self.bot = bot

    def start_background_loop(self):
        def run_loop():
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()
        t = threading.Thread(target=run_loop, daemon=True)
        t.start()

    def run_coro(self, coro, timeout=30):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout=timeout)
        except Exception as e:
            logger.error(f"Error in run_coro: {e}")
            raise

    async def _validate_session(self, session_string: str):
        client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
        try:
            await asyncio.wait_for(client.connect(), timeout=15.0)
            if await client.is_user_authorized():
                me = await client.get_me()
                phone = me.phone if me.phone else ""
                await client.disconnect()
                return True, phone
            else:
                await client.disconnect()
                return False, "الجلسة غير مصرح بها (قد تكون منتهية)"
        except asyncio.TimeoutError:
            return False, "انتهت مهلة الاتصال"
        except Exception as e:
            return False, str(e)

    def validate_session(self, session_string: str):
        return self.run_coro(self._validate_session(session_string))

    async def _fetch_groups(self, session_str):
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        try:
            await asyncio.wait_for(client.connect(), timeout=20.0)
            if not await client.is_user_authorized():
                await client.disconnect()
                raise Exception("الجلسة غير صالحة أو منتهية الصلاحية")
            dialogs = await client.get_dialogs()
            groups = []
            for d in dialogs:
                if d.is_group or d.is_channel:
                    groups.append((d.id, d.title))
            await client.disconnect()
            logger.info(f"Fetched {len(groups)} groups/channels")
            return groups
        except asyncio.TimeoutError:
            raise Exception("انتهت مهلة الاتصال. الشبكة بطيئة أو الجلسة غير صالحة.")
        except Exception as e:
            logger.error(f"Error fetching groups: {e}")
            raise e

    def fetch_groups(self, session_str):
        return self.run_coro(self._fetch_groups(session_str), timeout=25)

    def start_user_monitoring(self, user_id: int) -> bool:
        user = db_get_user(user_id)
        if not user or not user[1]:
            return False
        session_str = user[1]
        self.user_sessions[user_id] = session_str
        groups = db_get_user_groups(user_id)
        if not groups:
            return False
        future = asyncio.run_coroutine_threadsafe(
            self._monitor_user_with_restart(user_id, session_str, groups),
            self.loop
        )
        try:
            future.result(timeout=15)
            db_set_monitoring_status(user_id, True)
            return True
        except Exception as e:
            logger.error(f"Failed to start monitoring for {user_id}: {e}")
            return False

    async def _monitor_user_with_restart(self, user_id: int, session_str: str, groups: List[Tuple[int, str]]):
        while True:
            user = db_get_user(user_id)
            if not user or user[4] == 0:
                logger.info(f"Monitoring stopped by user {user_id}")
                break
            try:
                await self._monitor_user(user_id, session_str, groups)
            except Exception as e:
                logger.error(f"Monitor crashed for user {user_id}: {e}\n{traceback.format_exc()}")
                logger.info(f"Restarting monitor for user {user_id} in 5 seconds...")
                await asyncio.sleep(5)
                user = db_get_user(user_id)
                if user and user[1]:
                    session_str = user[1]
                groups = db_get_user_groups(user_id)

    async def _monitor_user(self, user_id: int, session_str: str, groups: List[Tuple[int, str]]):
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        try:
            await client.start()
            self.clients[user_id] = client
            logger.info(f"✅ Client started for user {user_id}")
        except Exception as e:
            logger.error(f"Client start failed for {user_id}: {e}")
            raise

        group_ids = [g[0] for g in groups]

        @client.on(events.NewMessage(chats=group_ids))
        async def handler(event):
            try:
                asyncio.create_task(self._process_message_safe(user_id, event.message))
            except Exception as e:
                logger.error(f"Error in handler for user {user_id}: {e}")

        logger.info(f"👂 Listening for user {user_id} on {len(groups)} groups")
        try:
            await client.run_until_disconnected()
        except Exception as e:
            logger.error(f"Client disconnected for user {user_id}: {e}")
            raise
        finally:
            if user_id in self.clients:
                del self.clients[user_id]

    async def _process_message_safe(self, user_id: int, message):
        try:
            await self._process_message(user_id, message)
        except Exception as e:
            logger.error(f"Error processing message for user {user_id}: {e}\n{traceback.format_exc()}")

    async def _process_message(self, user_id: int, message):
        if not message.text:
            return
        user = db_get_user(user_id)
        if not user:
            return
        keywords_data = load_user_keywords(user_id)
        if check_negative(message.text, keywords_data.get("negative_keywords", [])):
            return
        cat_key, cat_label = check_positive_category(message.text, keywords_data)
        if not cat_label:
            return

        unique_id = f"tg_{message.chat_id}_{message.id}"
        seen = load_user_seen_ids(user_id)
        if unique_id in seen:
            return
        save_user_seen_id(user_id, unique_id)

        if is_message_blocked(user_id, unique_id):
            return
        if message.sender_id and is_user_blocked(user_id, message.sender_id):
            return
        if is_text_blocked(user_id, message.text):
            return

        chat_id = message.chat_id
        if str(chat_id).startswith('-100'):
            link = f"https://t.me/c/{str(chat_id)[4:]}/{message.id}"
        else:
            link = f"https://t.me/{message.chat.username}/{message.id}" if message.chat.username else ""

        title = message.text[:100].replace('\n', ' ')
        alert_text = (
            f"🎯 *تنبيه جديد*\n"
            f"🏷️ التصنيف: {cat_label}\n"
            f"📌 النص: {title}\n"
            f"🔗 [رابط الرسالة]({link})"
        )

        keyboard = [
            [InlineKeyboardButton("🚫 حظر المعرف", callback_data=f"block_msg_{user_id}_{unique_id}")],
            [InlineKeyboardButton("🙅‍♂️ حظر المستخدم", callback_data=f"block_user_{user_id}_{message.sender_id}")],
            [InlineKeyboardButton("📝 حظر هذا النص", callback_data=f"block_text_{user_id}_{normalize_text(message.text)[:20]}")]
        ]

        alert_target = user[3]
        alert_target_id = user[4]
        target_chat_id = user_id if alert_target == "private" else alert_target_id
        if not target_chat_id:
            return

        try:
            await asyncio.to_thread(
                self.bot.send_message,
                chat_id=target_chat_id,
                text=alert_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard),
                disable_web_page_preview=True
            )
            logger.info(f"✅ Alert sent to {target_chat_id} for user {user_id}")
        except Exception as e:
            if "FloodWait" in str(e):
                wait = int(re.search(r'\d+', str(e)).group()) if re.search(r'\d+', str(e)) else 10
                logger.warning(f"FloodWait {wait}s for user {user_id}, sleeping...")
                await asyncio.sleep(wait + 1)
                try:
                    await asyncio.to_thread(
                        self.bot.send_message,
                        chat_id=target_chat_id,
                        text=alert_text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        disable_web_page_preview=True
                    )
                except Exception as e2:
                    logger.error(f"Retry failed: {e2}")
            else:
                logger.error(f"Failed to send alert to {target_chat_id}: {e}")

    def stop_user_monitoring(self, user_id: int):
        if user_id in self.tasks:
            future = asyncio.run_coroutine_threadsafe(
                self._stop_monitor(user_id),
                self.loop
            )
            try:
                future.result(timeout=5)
            except:
                pass
            db_set_monitoring_status(user_id, False)
        if user_id in self.user_sessions:
            del self.user_sessions[user_id]

    async def _stop_monitor(self, user_id: int):
        if user_id in self.clients:
            await self.clients[user_id].disconnect()
            del self.clients[user_id]
        if user_id in self.tasks:
            self.tasks[user_id].cancel()
            del self.tasks[user_id]

    def is_user_monitoring(self, user_id: int) -> bool:
        return user_id in self.clients and self.clients[user_id].is_connected()

session_manager = SessionManager()

# ═══════════════════════════════════════════════════════════════════════════
# 🛡️ ديكور التحقق من تسجيل الدخول
# ═══════════════════════════════════════════════════════════════════════════
def require_login(func):
    @wraps(func)
    def wrapper(update: Update, context: CallbackContext, *args, **kwargs):
        user_id = update.effective_user.id
        user = db_get_user(user_id)
        if not user or not user[1]:
            keyboard = [[InlineKeyboardButton("🔐 تسجيل الدخول الآن", callback_data="menu_login")]]
            text = "⚠️ *يجب عليك تسجيل الدخول أولاً.*"
            if update.callback_query:
                update.callback_query.answer("يجب تسجيل الدخول أولاً", show_alert=True)
                update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        return func(update, context, *args, **kwargs)
    return wrapper

# ═══════════════════════════════════════════════════════════════════════════
# 📋 القائمة الرئيسية
# ═══════════════════════════════════════════════════════════════════════════
def start(update: Update, context: CallbackContext):
    user = update.effective_user
    text = (
        f"👋 *مرحباً {user.first_name}!*\n\n"
        f"أنا بوت رادار تيليجرام، أساعدك في مراقبة المجموعات والتقاط الفرص.\n\n"
        f"📌 *القائمة الرئيسية:*"
    )
    keyboard = [
        [InlineKeyboardButton("🔐 تسجيل الدخول", callback_data="menu_login")],
        [InlineKeyboardButton("🔑 الكلمات المفتاحية", callback_data="menu_keywords")],
        [InlineKeyboardButton("👥 اختيار المجموعات", callback_data="menu_groups")],
        [InlineKeyboardButton("⚙️ الإعدادات", callback_data="menu_settings")],
        [InlineKeyboardButton("🚀 بدء المراقبة", callback_data="menu_start_monitor")],
        [InlineKeyboardButton("📊 حالة الحساب", callback_data="menu_status")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    else:
        update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

def main_menu_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data == "menu_login":
        context.user_data.clear()
        query.edit_message_text(
            "🔑 *تسجيل الدخول بكود الجلسة*\n\n"
            "الرجاء إرسال كود الجلسة (StringSession) الخاص بحسابك.\n\n"
            "لإلغاء العملية أرسل /cancel",
            parse_mode=ParseMode.MARKDOWN
        )
        return SESSION_CODE
    elif data == "menu_keywords":
        if not db_get_user(user_id) or not db_get_user(user_id)[1]:
            query.answer("يجب تسجيل الدخول أولاً", show_alert=True)
            return
        keywords_menu(update, context)
    elif data == "menu_groups":
        if not db_get_user(user_id) or not db_get_user(user_id)[1]:
            query.answer("يجب تسجيل الدخول أولاً", show_alert=True)
            return
        select_groups(update, context)
    elif data == "menu_settings":
        if not db_get_user(user_id) or not db_get_user(user_id)[1]:
            query.answer("يجب تسجيل الدخول أولاً", show_alert=True)
            return
        settings(update, context)
    elif data == "menu_start_monitor":
        if not db_get_user(user_id) or not db_get_user(user_id)[1]:
            query.answer("يجب تسجيل الدخول أولاً", show_alert=True)
            return
        start_monitoring(update, context)
    elif data == "menu_status":
        if not db_get_user(user_id) or not db_get_user(user_id)[1]:
            query.answer("يجب تسجيل الدخول أولاً", show_alert=True)
            return
        status(update, context)

# ═══════════════════════════════════════════════════════════════════════════
# 🔐 محادثة تسجيل الدخول بكود الجلسة
# ═══════════════════════════════════════════════════════════════════════════
SESSION_CODE = range(1)

def login_start(update: Update, context: CallbackContext):
    context.user_data.clear()
    update.message.reply_text(
        "🔑 *تسجيل الدخول بكود الجلسة*\n\n"
        "الرجاء إرسال كود الجلسة (StringSession) الخاص بحسابك.\n\n"
        "لإلغاء العملية أرسل /cancel",
        parse_mode=ParseMode.MARKDOWN
    )
    return SESSION_CODE

def session_received(update: Update, context: CallbackContext):
    session_string = update.message.text.strip()
    msg = update.message.reply_text("⏳ جارٍ التحقق من كود الجلسة...")

    try:
        is_valid, result = session_manager.validate_session(session_string)
    except Exception as e:
        is_valid, result = False, str(e)

    msg.delete()

    if is_valid:
        user_id = update.effective_user.id
        phone = result if isinstance(result, str) else ""
        db_save_session(user_id, session_string, phone)
        update.message.reply_text("✅ تم تسجيل الدخول بنجاح! كود الجلسة صالح.")
        start(update, context)
        return ConversationHandler.END
    else:
        error_msg = result if result else "كود الجلسة غير صالح أو منتهي الصلاحية"
        update.message.reply_text(f"❌ فشل تسجيل الدخول:\n`{error_msg}`\n\nحاول مرة أخرى أو أرسل /cancel للخروج.", parse_mode=ParseMode.MARKDOWN)
        return SESSION_CODE

def cancel_login(update: Update, context: CallbackContext):
    context.user_data.clear()
    update.message.reply_text("❌ تم إلغاء تسجيل الدخول.")
    start(update, context)
    return ConversationHandler.END

login_conv = ConversationHandler(
    entry_points=[
        CommandHandler('login', login_start),
        CallbackQueryHandler(main_menu_callback, pattern='^menu_login$')
    ],
    states={
        SESSION_CODE: [MessageHandler(Filters.text & ~Filters.command, session_received)],
    },
    fallbacks=[CommandHandler('cancel', cancel_login)],
    allow_reentry=True
)

# ═══════════════════════════════════════════════════════════════════════════
# 🔖 إدارة الكلمات المفتاحية
# ═══════════════════════════════════════════════════════════════════════════
@require_login
def keywords_menu(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    data = load_user_keywords(user_id)
    cats = data.get("categories", {})
    neg = data.get("negative_keywords", [])
    text = "*📚 إدارة الكلمات المفتاحية*\n\n"
    text += "*التصنيفات:*\n"
    for cat_key, cat_info in cats.items():
        text += f"• {cat_info['label']}: {', '.join(cat_info['keywords'][:3])}{'...' if len(cat_info['keywords'])>3 else ''}\n"
    text += f"\n*🚫 الكلمات السلبية:* {', '.join(neg[:5])}{'...' if len(neg)>5 else ''}\n"
    keyboard = [
        [InlineKeyboardButton("➕ إضافة تصنيف", callback_data="kw_add_cat")],
        [InlineKeyboardButton("✏️ تعديل تصنيف", callback_data="kw_edit_cat")],
        [InlineKeyboardButton("🗑️ حذف تصنيف", callback_data="kw_del_cat")],
        [InlineKeyboardButton("🔧 الكلمات السلبية", callback_data="kw_neg_menu")],
        [InlineKeyboardButton("🔄 استعادة الافتراضي", callback_data="kw_reset")],
    ]
    if update.callback_query:
        update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

def keywords_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data
    user_id = update.effective_user.id
    if data == "kw_add_cat":
        context.user_data['kw_state'] = 'awaiting_cat_name'
        query.edit_message_text("📝 أرسل اسم التصنيف الجديد (مثال: تصميم):")
    elif data == "kw_edit_cat":
        kw_data = load_user_keywords(user_id)
        cats = kw_data.get("categories", {})
        if not cats:
            query.edit_message_text("لا يوجد تصنيفات لتعديلها.")
            return
        keyboard = []
        for cid, cinfo in cats.items():
            keyboard.append([InlineKeyboardButton(cinfo['label'], callback_data=f"kw_editcat_{cid}")])
        query.edit_message_text("اختر التصنيف المراد تعديله:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("kw_editcat_"):
        cat_id = data.split("_")[2]
        context.user_data['kw_edit_cat'] = cat_id
        query.edit_message_text("📝 أرسل الكلمات المفتاحية الجديدة مفصولة بفواصل (مثال: كلمة1, كلمة2):")
        context.user_data['kw_state'] = 'awaiting_cat_keywords'
    elif data == "kw_del_cat":
        kw_data = load_user_keywords(user_id)
        cats = kw_data.get("categories", {})
        if not cats:
            query.edit_message_text("لا يوجد تصنيفات.")
            return
        keyboard = []
        for cid, cinfo in cats.items():
            keyboard.append([InlineKeyboardButton(f"❌ {cinfo['label']}", callback_data=f"kw_delcat_{cid}")])
        query.edit_message_text("اختر التصنيف المراد حذفه:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("kw_delcat_"):
        cat_id = data.split("_")[2]
        kw_data = load_user_keywords(user_id)
        if cat_id in kw_data.get("categories", {}):
            del kw_data["categories"][cat_id]
            save_user_keywords(user_id, kw_data)
        query.edit_message_text("✅ تم حذف التصنيف.")
    elif data == "kw_neg_menu":
        kw_data = load_user_keywords(user_id)
        neg = kw_data.get("negative_keywords", [])
        text = f"*🚫 الكلمات السلبية الحالية:*\n{', '.join(neg) if neg else 'لا يوجد'}\n\n"
        text += "أرسل الكلمات الجديدة مفصولة بفواصل لتحديث القائمة:"
        context.user_data['kw_state'] = 'awaiting_neg_keywords'
        query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
    elif data == "kw_reset":
        save_user_keywords(user_id, DEFAULT_KEYWORDS.copy())
        query.edit_message_text("✅ تم استعادة الكلمات الافتراضية.")

def keywords_text_handler(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    state = context.user_data.get('kw_state')
    text = update.message.text.strip()
    if state == 'awaiting_cat_name':
        cat_name = text
        cat_id = f"cat_{int(datetime.now().timestamp())}"
        kw_data = load_user_keywords(user_id)
        kw_data["categories"][cat_id] = {"label": cat_name, "keywords": []}
        save_user_keywords(user_id, kw_data)
        context.user_data['kw_edit_cat'] = cat_id
        context.user_data['kw_state'] = 'awaiting_cat_keywords'
        update.message.reply_text("✅ تم إضافة التصنيف. الآن أرسل الكلمات المفتاحية له مفصولة بفواصل:")
    elif state == 'awaiting_cat_keywords':
        cat_id = context.user_data.get('kw_edit_cat')
        if not cat_id:
            update.message.reply_text("⚠️ انتهت الجلسة. ابدأ من جديد من /keywords")
            return
        keywords_list = [kw.strip() for kw in text.split(',') if kw.strip()]
        kw_data = load_user_keywords(user_id)
        if cat_id in kw_data.get("categories", {}):
            kw_data["categories"][cat_id]["keywords"] = keywords_list
            save_user_keywords(user_id, kw_data)
        update.message.reply_text("✅ تم تحديث الكلمات المفتاحية للتصنيف.")
        context.user_data.pop('kw_state', None)
        context.user_data.pop('kw_edit_cat', None)
    elif state == 'awaiting_neg_keywords':
        keywords_list = [kw.strip() for kw in text.split(',') if kw.strip()]
        kw_data = load_user_keywords(user_id)
        kw_data["negative_keywords"] = keywords_list
        save_user_keywords(user_id, kw_data)
        update.message.reply_text("✅ تم تحديث الكلمات السلبية.")
        context.user_data.pop('kw_state', None)

# ═══════════════════════════════════════════════════════════════════════════
# 👥 اختيار المجموعات (مع Pagination)
# ═══════════════════════════════════════════════════════════════════════════
@require_login
def select_groups(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    user = db_get_user(user_id)
    is_callback = update.callback_query is not None

    if is_callback:
        query = update.callback_query
        query.answer()
        query.edit_message_text("⏳ جارٍ جلب قائمة المجموعات...")
    else:
        msg = update.message.reply_text("⏳ جارٍ جلب قائمة المجموعات...")
        context.user_data['groups_temp_msg'] = msg

    session_str = user[1]
    try:
        groups = session_manager.fetch_groups(session_str)
    except Exception as e:
        error_text = str(e)
        logger.error(f"Fetch groups error for user {user_id}: {error_text}")
        if is_callback:
            query.edit_message_text(
                f"❌ فشل جلب المجموعات:\n`{error_text}`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 إعادة المحاولة", callback_data="grp_refresh")]])
            )
        else:
            context.user_data.get('groups_temp_msg', update.message).reply_text(
                f"❌ فشل جلب المجموعات:\n`{error_text}`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 إعادة المحاولة", callback_data="grp_refresh")]])
            )
        return

    if not groups:
        text = "❌ لم يتم العثور على أي مجموعات أو قنوات."
        if is_callback:
            query.edit_message_text(text)
        else:
            context.user_data.get('groups_temp_msg', update.message).edit_text(text) if 'groups_temp_msg' in context.user_data else update.message.reply_text(text)
        return

    groups.sort(key=lambda x: x[1].lower())
    context.user_data['groups_list'] = groups
    selected = {g[0] for g in db_get_user_groups(user_id)}
    context.user_data['temp_selected'] = selected.copy()

    page = 0
    per_page = 8
    total_pages = (len(groups) + per_page - 1) // per_page
    context.user_data['groups_page'] = page

    start_idx = page * per_page
    end_idx = min(start_idx + per_page, len(groups))
    page_groups = groups[start_idx:end_idx]

    keyboard = []
    for gid, gtitle in page_groups:
        prefix = "✅" if gid in selected else "⬜️"
        keyboard.append([InlineKeyboardButton(f"{prefix} {gtitle[:30]}", callback_data=f"tog_{gid}")])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ السابق", callback_data="grp_prev"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("التالي ▶️", callback_data="grp_next"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("💾 حفظ الاختيارات", callback_data="save_groups")])
    keyboard.append([InlineKeyboardButton("🔄 تحديث القائمة", callback_data="grp_refresh")])

    text = f"📌 *اختر المجموعات للمراقبة:* (صفحة {page+1}/{total_pages} - إجمالي {len(groups)} مجموعة)"

    if is_callback:
        query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        if 'groups_temp_msg' in context.user_data:
            context.user_data['groups_temp_msg'].edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

def groups_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data
    user_id = update.effective_user.id

    groups_list = context.user_data.get('groups_list', [])
    selected = context.user_data.get('temp_selected', set())
    page = context.user_data.get('groups_page', 0)
    per_page = 8
    total_pages = (len(groups_list) + per_page - 1) // per_page if groups_list else 0

    if data.startswith("tog_"):
        gid = int(data.split("_")[1])
        if gid in selected:
            selected.remove(gid)
        else:
            selected.add(gid)
        context.user_data['temp_selected'] = selected

    elif data == "grp_next":
        if page < total_pages - 1:
            page += 1
            context.user_data['groups_page'] = page

    elif data == "grp_prev":
        if page > 0:
            page -= 1
            context.user_data['groups_page'] = page

    elif data == "grp_refresh":
        user = db_get_user(user_id)
        if not user or not user[1]:
            query.edit_message_text("❌ الجلسة غير صالحة. الرجاء إعادة تسجيل الدخول.")
            return
        query.edit_message_text("⏳ جارٍ تحديث قائمة المجموعات...")
        session_str = user[1]
        try:
            groups = session_manager.fetch_groups(session_str)
        except Exception as e:
            query.edit_message_text(
                f"❌ فشل جلب المجموعات:\n`{e}`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 إعادة المحاولة", callback_data="grp_refresh")]])
            )
            return
        if not groups:
            query.edit_message_text("❌ لم يتم العثور على أي مجموعات أو قنوات.")
            return
        groups.sort(key=lambda x: x[1].lower())
        context.user_data['groups_list'] = groups
        selected = {g[0] for g in db_get_user_groups(user_id)}
        context.user_data['temp_selected'] = selected.copy()
        page = 0
        context.user_data['groups_page'] = page
        total_pages = (len(groups) + per_page - 1) // per_page

    elif data == "save_groups":
        to_save = [(gid, title) for gid, title in groups_list if gid in selected]
        db_save_user_groups(user_id, to_save)
        query.edit_message_text(f"✅ تم حفظ {len(to_save)} مجموعة.\nيمكنك بدء المراقبة بـ /start_monitoring")
        return

    if not groups_list:
        return

    start_idx = page * per_page
    end_idx = min(start_idx + per_page, len(groups_list))
    page_groups = groups_list[start_idx:end_idx]

    keyboard = []
    for gid, gtitle in page_groups:
        prefix = "✅" if gid in selected else "⬜️"
        keyboard.append([InlineKeyboardButton(f"{prefix} {gtitle[:30]}", callback_data=f"tog_{gid}")])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ السابق", callback_data="grp_prev"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("التالي ▶️", callback_data="grp_next"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("💾 حفظ الاختيارات", callback_data="save_groups")])
    keyboard.append([InlineKeyboardButton("🔄 تحديث القائمة", callback_data="grp_refresh")])

    text = f"📌 *اختر المجموعات للمراقبة:* (صفحة {page+1}/{total_pages} - إجمالي {len(groups_list)} مجموعة)"

    query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

# ═══════════════════════════════════════════════════════════════════════════
# ⚙️ الإعدادات
# ═══════════════════════════════════════════════════════════════════════════
@require_login
def settings(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    user = db_get_user(user_id)
    alert_target = user[3]
    alert_id = user[4] or "غير محدد"
    text = (
        f"⚙️ *الإعدادات الحالية*\n\n"
        f"• وجهة التنبيهات: {'📱 حساب خاص' if alert_target=='private' else '📢 قناة/مجموعة'}\n"
        f"• المعرف: `{alert_id}`\n\n"
        "اختر وجهة التنبيهات:"
    )
    keyboard = [
        [InlineKeyboardButton("📱 حسابي الخاص", callback_data="set_alert_private")],
        [InlineKeyboardButton("📢 قناة / مجموعة", callback_data="set_alert_channel")],
    ]
    if update.callback_query:
        update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

def settings_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data
    user_id = update.effective_user.id
    if data == "set_alert_private":
        db_update_alert_settings(user_id, "private")
        query.edit_message_text("✅ تم تعيين التنبيهات إلى حسابك الخاص.")
    elif data == "set_alert_channel":
        query.edit_message_text(
            "📛 أرسل معرف القناة أو المجموعة التي تريد استلام التنبيهات فيها.\n"
            "يمكنك إرسال @username أو المعرف الرقمي (مثل -100xxxxxx)."
        )
        context.user_data['awaiting_channel_id'] = True

def settings_text_handler(update: Update, context: CallbackContext):
    if context.user_data.get('awaiting_channel_id'):
        channel_id = update.message.text.strip()
        user_id = update.effective_user.id
        db_update_alert_settings(user_id, "channel", channel_id)
        update.message.reply_text(f"✅ تم حفظ وجهة التنبيهات: {channel_id}")
        context.user_data.pop('awaiting_channel_id', None)

# ═══════════════════════════════════════════════════════════════════════════
# 🚀 بدء / إيقاف المراقبة
# ═══════════════════════════════════════════════════════════════════════════
@require_login
def start_monitoring(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    groups = db_get_user_groups(user_id)
    if not groups:
        text = "لم تختر أي مجموعة. استخدم /select_groups"
        if update.callback_query:
            update.callback_query.edit_message_text(text)
        else:
            update.message.reply_text(text)
        return
    if session_manager.is_user_monitoring(user_id):
        text = "⚠️ المراقبة قيد التشغيل بالفعل."
        if update.callback_query:
            update.callback_query.edit_message_text(text)
        else:
            update.message.reply_text(text)
        return
    success = session_manager.start_user_monitoring(user_id)
    text = "✅ بدأت مراقبة المجموعات. سيتم إرسال التنبيهات حسب إعداداتك." if success else "❌ فشل بدء المراقبة. تأكد من صحة الجلسة."
    if update.callback_query:
        update.callback_query.edit_message_text(text)
    else:
        update.message.reply_text(text)

def stop_monitoring(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    session_manager.stop_user_monitoring(user_id)
    update.message.reply_text("⏹️ تم إيقاف المراقبة.")

@require_login
def status(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    user = db_get_user(user_id)
    monitoring = "🟢 يعمل" if session_manager.is_user_monitoring(user_id) else "🔴 متوقف"
    groups = db_get_user_groups(user_id)
    text = (
        f"📊 *حالة الحساب*\n\n"
        f"• المراقبة: {monitoring}\n"
        f"• المجموعات المختارة: {len(groups)}\n"
        f"• وجهة التنبيهات: {user[3]}"
    )
    if update.callback_query:
        update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
    else:
        update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

def ping(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if session_manager.is_user_monitoring(user_id):
        update.message.reply_text("🟢 البوت يعمل والمراقبة نشطة.")
    else:
        update.message.reply_text("🟡 البوت يعمل لكن المراقبة متوقفة. ابدأها بـ /start_monitoring")

# ═══════════════════════════════════════════════════════════════════════════
# 🚫 أزرار الحظر
# ═══════════════════════════════════════════════════════════════════════════
def block_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data
    parts = data.split('_')
    if len(parts) < 3:
        return
    action = parts[1]
    user_id = int(parts[2])
    if update.effective_user.id != user_id:
        query.answer("هذا الإجراء خاص بك فقط.", show_alert=True)
        return
    if action == "msg":
        unique_id = parts[3]
        blocked = load_user_blocklist(user_id, "blocked_messages")
        blocked.add(unique_id)
        save_user_blocklist(user_id, "blocked_messages", blocked)
        query.edit_message_text("🚫 تم حظر هذه الرسالة ولن تظهر مرة أخرى.")
    elif action == "user":
        sender_id = parts[3]
        blocked = load_user_blocklist(user_id, "blocked_users")
        blocked.add(sender_id)
        save_user_blocklist(user_id, "blocked_users", blocked)
        query.edit_message_text("🙅‍♂️ تم حظر هذا المستخدم.")
    elif action == "text":
        text_fragment = parts[3]
        original_text = query.message.text or ""
        normalized = normalize_text(original_text)
        blocked = load_user_blocklist(user_id, "blocked_texts")
        blocked.add(normalized)
        save_user_blocklist(user_id, "blocked_texts", blocked)
        query.edit_message_text("📝 تم حظر هذا النص.")

# ═══════════════════════════════════════════════════════════════════════════
# ⏹️ إيقاف ذاتي بعد مدة محددة لحفظ البيانات قبل timeout
# ═══════════════════════════════════════════════════════════════════════════
updater_instance = None

def schedule_shutdown(duration_seconds):
    def stop():
        logger.info("Self-timer: Stopping bot to save cache...")
        if updater_instance:
            updater_instance.stop()
        for uid in list(session_manager.clients.keys()):
            session_manager.stop_user_monitoring(uid)
        logger.info("All monitors stopped. Exiting.")
        sys.exit(0)

    timer = threading.Timer(duration_seconds, stop)
    timer.daemon = True
    timer.start()
    logger.info(f"Self-timer set: bot will stop after {duration_seconds} seconds")

def graceful_shutdown(signum, frame):
    logger.info("Received signal to stop. Shutting down gracefully...")
    if updater_instance:
        updater_instance.stop()
    for uid in list(session_manager.clients.keys()):
        session_manager.stop_user_monitoring(uid)
    sys.exit(0)

signal.signal(signal.SIGTERM, graceful_shutdown)
signal.signal(signal.SIGINT, graceful_shutdown)

# ═══════════════════════════════════════════════════════════════════════════
# 🎬 الدالة الرئيسية
# ═══════════════════════════════════════════════════════════════════════════
def main():
    global updater_instance
    session_manager.start_background_loop()

    updater = Updater(BOT_TOKEN, use_context=True)
    updater_instance = updater
    dp = updater.dispatcher
    bot = updater.bot
    session_manager.set_bot(bot)

    dp.add_handler(login_conv)
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(CallbackQueryHandler(main_menu_callback, pattern='^menu_'))
    dp.add_handler(CommandHandler("keywords", keywords_menu))
    dp.add_handler(CallbackQueryHandler(keywords_callback, pattern='^kw_'))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, keywords_text_handler), group=1)
    dp.add_handler(CommandHandler("select_groups", select_groups))
    dp.add_handler(CallbackQueryHandler(groups_callback, pattern='^(tog_|grp_|save_groups)'))
    dp.add_handler(CommandHandler("settings", settings))
    dp.add_handler(CallbackQueryHandler(settings_callback, pattern='^set_alert_'))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, settings_text_handler), group=2)
    dp.add_handler(CommandHandler("start_monitoring", start_monitoring))
    dp.add_handler(CommandHandler("stop_monitoring", stop_monitoring))
    dp.add_handler(CommandHandler("status", status))
    dp.add_handler(CallbackQueryHandler(block_callback, pattern='^block_'))

    schedule_shutdown(RUN_DURATION)

    updater.start_polling()
    logger.info("🤖 Bot started!")
    updater.idle()

if __name__ == "__main__":
    main()
