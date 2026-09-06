# bot.py
import asyncio
import logging
import sqlite3
import json
import io
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from aiogram import Bot, Dispatcher, F, types
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    BufferedInputFile,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ---------- Конфигурация ----------
BOT_TOKEN = "8625023834:AAH4tDi9UBHQe2Chp19tKvtyXcV719iNBRc"
ADMIN_ID = 6689292068
DB_PATH = "bot.db"

# ---------- Логирование ----------
logging.basicConfig(level=logging.INFO)

# ---------- База данных ----------
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cur = conn.cursor()
    
    # Таблица пользователей
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            is_banned INTEGER DEFAULT 0,
            ban_reason TEXT,
            banned_at TIMESTAMP,
            banned_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (banned_by) REFERENCES users(user_id)
        )
    """)
    
    # Таблица серверов
    cur.execute("""
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            ip TEXT NOT NULL UNIQUE,
            version TEXT NOT NULL,
            creator_user_id INTEGER NOT NULL,
            owner_user_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_new_until TIMESTAMP,
            website TEXT,
            is_deleted INTEGER DEFAULT 0,
            deleted_at TIMESTAMP,
            deleted_by INTEGER,
            FOREIGN KEY (creator_user_id) REFERENCES users(user_id),
            FOREIGN KEY (owner_user_id) REFERENCES users(user_id),
            FOREIGN KEY (deleted_by) REFERENCES users(user_id)
        )
    """)
    
    # Добавляем новые столбцы, если их нет
    cur.execute("PRAGMA table_info(servers)")
    server_columns = [col[1] for col in cur.fetchall()]
    if 'website' not in server_columns:
        cur.execute("ALTER TABLE servers ADD COLUMN website TEXT")
    if 'is_deleted' not in server_columns:
        cur.execute("ALTER TABLE servers ADD COLUMN is_deleted INTEGER DEFAULT 0")
    if 'deleted_at' not in server_columns:
        cur.execute("ALTER TABLE servers ADD COLUMN deleted_at TIMESTAMP")
    if 'deleted_by' not in server_columns:
        cur.execute("ALTER TABLE servers ADD COLUMN deleted_by INTEGER")
    
    # Таблица покупных баллов
    cur.execute("""
        CREATE TABLE IF NOT EXISTS purchased_boost (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            points INTEGER NOT NULL,
            duration_days INTEGER NOT NULL,
            cost_stars INTEGER NOT NULL,
            purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            active INTEGER DEFAULT 1,
            FOREIGN KEY (server_id) REFERENCES servers(id),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)
    
    # Таблица заработанных баллов
    cur.execute("""
        CREATE TABLE IF NOT EXISTS earned_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            points INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            active INTEGER DEFAULT 1,
            FOREIGN KEY (server_id) REFERENCES servers(id),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)
    
    # Журнал действий
    cur.execute("""
        CREATE TABLE IF NOT EXISTS actions_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (server_id) REFERENCES servers(id),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)
    
    # Избранное
    cur.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER NOT NULL,
            server_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, server_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (server_id) REFERENCES servers(id)
        )
    """)
    
    # Заявки на владение
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ownership_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP,
            processed_by INTEGER,
            FOREIGN KEY (server_id) REFERENCES servers(id),
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (processed_by) REFERENCES users(user_id)
        )
    """)
    
    # Таблица для полного аудита
    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)
    
    conn.commit()
    conn.close()

# Функция аудита
def add_audit_log(user_id: Optional[int], action: str, details: str = None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO audit_log (user_id, action, details) VALUES (?, ?, ?)",
                (user_id, action, details))
    conn.commit()
    conn.close()

# Функция получения имени пользователя
def get_user_info(user_id: int) -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_display_name(user_id: int) -> str:
    user = get_user_info(user_id)
    if user:
        if user['username']:
            return f"@{user['username']}"
        elif user['first_name']:
            return user['first_name']
        else:
            return str(user_id)
    return str(user_id)

# Вспомогательные функции БД
def add_user(user_id: int, username: str = None, first_name: str = None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
                (user_id, username, first_name))
    conn.commit()
    conn.close()
    add_audit_log(user_id, "user_registered", f"Пользователь {get_user_display_name(user_id)} зарегистрировался")

def is_user_banned(user_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
    result = cur.fetchone()
    conn.close()
    return result['is_banned'] == 1 if result else False

def ban_user(user_id: int, reason: str, banned_by: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users SET is_banned = 1, ban_reason = ?, banned_at = ?, banned_by = ?
        WHERE user_id = ?
    """, (reason, datetime.now(), banned_by, user_id))
    conn.commit()
    conn.close()
    add_audit_log(banned_by, "user_banned", f"Пользователь {get_user_display_name(user_id)} забанен. Причина: {reason}")

def unban_user(user_id: int, unbanned_by: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users SET is_banned = 0, ban_reason = NULL, banned_at = NULL, banned_by = NULL
        WHERE user_id = ?
    """, (user_id,))
    conn.commit()
    conn.close()
    add_audit_log(unbanned_by, "user_unbanned", f"Пользователь {get_user_display_name(user_id)} разбанен")

def add_server(name: str, ip: str, version: str, creator_user_id: int, is_new_until: datetime, website: str = None) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO servers (name, ip, version, creator_user_id, is_new_until, website) VALUES (?, ?, ?, ?, ?, ?)",
                (name, ip, version, creator_user_id, is_new_until, website))
    conn.commit()
    server_id = cur.lastrowid
    conn.close()
    add_audit_log(creator_user_id, "server_added", f"Сервер {name} ({ip}) добавлен пользователем {get_user_display_name(creator_user_id)}")
    return server_id

def get_server_by_ip(ip: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM servers WHERE ip = ? AND is_deleted = 0", (ip,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def get_server_by_id(server_id: int, include_deleted: bool = False) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    if include_deleted:
        cur.execute("SELECT * FROM servers WHERE id = ?", (server_id,))
    else:
        cur.execute("SELECT * FROM servers WHERE id = ? AND is_deleted = 0", (server_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def get_servers_by_creator(user_id: int, include_deleted: bool = False) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    if include_deleted:
        cur.execute("SELECT * FROM servers WHERE creator_user_id = ?", (user_id,))
    else:
        cur.execute("SELECT * FROM servers WHERE creator_user_id = ? AND is_deleted = 0", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_all_servers(include_deleted: bool = False) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    if include_deleted:
        cur.execute("SELECT * FROM servers")
    else:
        cur.execute("SELECT * FROM servers WHERE is_deleted = 0")
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def update_server_owner(server_id: int, owner_user_id: Optional[int], processed_by: int = None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE servers SET owner_user_id = ? WHERE id = ?", (owner_user_id, server_id))
    conn.commit()
    conn.close()
    
    if processed_by:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE ownership_requests SET status = 'approved', processed_at = ?, processed_by = ?
            WHERE server_id = ? AND status = 'pending'
        """, (datetime.now(), processed_by, server_id))
        conn.commit()
        conn.close()
    
    if owner_user_id:
        add_audit_log(processed_by, "owner_changed", f"Владелец сервера ID {server_id} изменён на {get_user_display_name(owner_user_id)}")
    else:
        add_audit_log(processed_by, "owner_removed", f"Владелец сервера ID {server_id} снят")

def delete_server(server_id: int, deleted_by: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE servers SET is_deleted = 1, deleted_at = ?, deleted_by = ?
        WHERE id = ?
    """, (datetime.now(), deleted_by, server_id))
    conn.commit()
    conn.close()
    
    server = get_server_by_id(server_id, include_deleted=True)
    if server:
        add_audit_log(deleted_by, "server_deleted", f"Сервер {server['name']} ({server['ip']}) удалён администратором {get_user_display_name(deleted_by)}")

def delete_servers_batch(server_ids: List[int], deleted_by: int):
    for server_id in server_ids:
        delete_server(server_id, deleted_by)

# Функции для работы с баллами
def add_purchased_boost(server_id: int, user_id: int, points: int, duration_days: int, cost_stars: int):
    expires_at = datetime.now() + timedelta(days=duration_days)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO purchased_boost (server_id, user_id, points, duration_days, cost_stars, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (server_id, user_id, points, duration_days, cost_stars, expires_at))
    conn.commit()
    conn.close()
    add_audit_log(user_id, "boost_purchased", f"Сервер ID {server_id} получил буст {points} баллов на {duration_days} дней от {get_user_display_name(user_id)}")

def add_earned_points(server_id: int, user_id: int, action_type: str, points: int):
    expires_at = datetime.now() + timedelta(days=30)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO earned_points (server_id, user_id, action_type, points, expires_at) VALUES (?, ?, ?, ?, ?)",
                (server_id, user_id, action_type, points, expires_at))
    conn.commit()
    conn.close()
    
    action_names = {
        'like': 'лайк',
        'copy': 'копирование IP',
        'save': 'сохранение',
        'site_visit': 'переход по ссылке'
    }
    action_name = action_names.get(action_type, action_type)
    add_audit_log(user_id, "points_earned", f"Сервер ID {server_id} получил {points} баллов за {action_name} от {get_user_display_name(user_id)}")

def get_active_purchased_points(server_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT SUM(points) FROM purchased_boost WHERE server_id = ? AND active = 1 AND expires_at > ?",
                (server_id, datetime.now()))
    result = cur.fetchone()[0]
    conn.close()
    return result or 0

def get_active_earned_points(server_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT SUM(points) FROM earned_points WHERE server_id = ? AND active = 1 AND expires_at > ?",
                (server_id, datetime.now()))
    result = cur.fetchone()[0]
    conn.close()
    return result or 0

def get_total_balance(server_id: int) -> int:
    return get_active_purchased_points(server_id) + get_active_earned_points(server_id)

# Действия пользователей
def log_action(server_id: int, user_id: int, action_type: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO actions_log (server_id, user_id, action_type) VALUES (?, ?, ?)",
                (server_id, user_id, action_type))
    conn.commit()
    conn.close()

def has_action_in_last_days(server_id: int, user_id: int, action_type: str, days: int = 30) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cutoff = datetime.now() - timedelta(days=days)
    cur.execute("SELECT COUNT(*) FROM actions_log WHERE server_id = ? AND user_id = ? AND action_type = ? AND created_at > ?",
                (server_id, user_id, action_type, cutoff))
    count = cur.fetchone()[0]
    conn.close()
    return count > 0

def has_saved(server_id: int, user_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM favorites WHERE server_id = ? AND user_id = ?", (server_id, user_id))
    count = cur.fetchone()[0]
    conn.close()
    return count > 0

def save_favorite(server_id: int, user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO favorites (server_id, user_id) VALUES (?, ?)", (server_id, user_id))
    conn.commit()
    conn.close()
    add_audit_log(user_id, "server_saved", f"Сервер ID {server_id} сохранён пользователем {get_user_display_name(user_id)}")

def get_likes_count_last_month(server_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cutoff = datetime.now() - timedelta(days=30)
    cur.execute("SELECT COUNT(DISTINCT user_id) FROM actions_log WHERE server_id = ? AND action_type = 'like' AND created_at > ?",
                (server_id, cutoff))
    count = cur.fetchone()[0]
    conn.close()
    return count

def get_saves_count_total(server_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM favorites WHERE server_id = ?", (server_id,))
    count = cur.fetchone()[0]
    conn.close()
    return count

def get_copies_count_last_month(server_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cutoff = datetime.now() - timedelta(days=30)
    cur.execute("SELECT COUNT(DISTINCT user_id) FROM actions_log WHERE server_id = ? AND action_type = 'copy' AND created_at > ?",
                (server_id, cutoff))
    count = cur.fetchone()[0]
    conn.close()
    return count

# Заявки на владение
def add_ownership_request(server_id: int, user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO ownership_requests (server_id, user_id) VALUES (?, ?)", (server_id, user_id))
    conn.commit()
    conn.close()
    add_audit_log(user_id, "ownership_requested", f"Пользователь {get_user_display_name(user_id)} подал заявку на владение сервером ID {server_id}")

def get_pending_ownership_requests() -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM ownership_requests WHERE status = 'pending'")
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# Функция получения полной истории сервера
def get_server_full_history(server_id: int) -> str:
    server = get_server_by_id(server_id, include_deleted=True)
    if not server:
        return "Сервер не найден"
    
    history_text = f"📜 ИСТОРИЯ СЕРВЕРА: {server['name']}\n"
    history_text += f"IP: {server['ip']}\n"
    history_text += f"Версия: {server['version']}\n"
    history_text += f"{'='*40}\n\n"
    
    creator = get_user_info(server['creator_user_id'])
    creator_name = get_user_display_name(server['creator_user_id'])
    history_text += f"👤 СОЗДАТЕЛЬ:\n  {creator_name}\n"
    if creator and creator['username']:
        history_text += f"  ID: {creator['user_id']}\n"
    history_text += f"  Дата: {server['created_at']}\n\n"
    
    if server['owner_user_id']:
        owner_name = get_user_display_name(server['owner_user_id'])
        history_text += f"👑 ВЛАДЕЛЕЦ:\n  {owner_name}\n  ID: {server['owner_user_id']}\n\n"
    else:
        history_text += f"👑 ВЛАДЕЛЕЦ: Не установлен\n\n"
    
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT al.*, u.username, u.first_name
        FROM actions_log al
        JOIN users u ON al.user_id = u.user_id
        WHERE al.server_id = ? AND al.action_type = 'like'
        ORDER BY al.created_at
    """, (server_id,))
    likes = cur.fetchall()
    if likes:
        history_text += f"❤️ ЛАЙКИ ({len(likes)}):\n"
        for like in likes:
            user_name = f"@{like['username']}" if like['username'] else like['first_name']
            history_text += f"  • {user_name} - {like['created_at']}\n"
        history_text += f"\n"
    
    cur.execute("""
        SELECT al.*, u.username, u.first_name
        FROM actions_log al
        JOIN users u ON al.user_id = u.user_id
        WHERE al.server_id = ? AND al.action_type = 'copy'
        ORDER BY al.created_at
    """, (server_id,))
    copies = cur.fetchall()
    if copies:
        history_text += f"📋 КОПИРОВАНИЯ IP ({len(copies)}):\n"
        for copy in copies:
            user_name = f"@{copy['username']}" if copy['username'] else copy['first_name']
            history_text += f"  • {user_name} - {copy['created_at']}\n"
        history_text += f"\n"
    
    cur.execute("""
        SELECT f.*, u.username, u.first_name
        FROM favorites f
        JOIN users u ON f.user_id = u.user_id
        WHERE f.server_id = ?
        ORDER BY f.created_at
    """, (server_id,))
    saves = cur.fetchall()
    if saves:
        history_text += f"⭐ СОХРАНЕНИЯ ({len(saves)}):\n"
        for save in saves:
            user_name = f"@{save['username']}" if save['username'] else save['first_name']
            history_text += f"  • {user_name} - {save['created_at']}\n"
        history_text += f"\n"
    
    cur.execute("""
        SELECT o.*, u.username, u.first_name
        FROM ownership_requests o
        JOIN users u ON o.user_id = u.user_id
        WHERE o.server_id = ?
        ORDER BY o.created_at
    """, (server_id,))
    ownership_requests = cur.fetchall()
    if ownership_requests:
        history_text += f"📝 ЗАЯВКИ НА ВЛАДЕНИЕ ({len(ownership_requests)}):\n"
        for req in ownership_requests:
            user_name = f"@{req['username']}" if req['username'] else req['first_name']
            status = req['status']
            history_text += f"  • {user_name} - {status} - {req['created_at']}\n"
        history_text += f"\n"
    
    cur.execute("""
        SELECT pb.*, u.username, u.first_name
        FROM purchased_boost pb
        JOIN users u ON pb.user_id = u.user_id
        WHERE pb.server_id = ?
        ORDER BY pb.purchased_at
    """, (server_id,))
    boosts = cur.fetchall()
    if boosts:
        history_text += f"🚀 БУСТЫ ({len(boosts)}):\n"
        for boost in boosts:
            user_name = f"@{boost['username']}" if boost['username'] else boost['first_name']
            history_text += f"  • {user_name}: {boost['points']} баллов на {boost['duration_days']} дней - {boost['purchased_at']}\n"
        history_text += f"\n"
    
    cur.execute("""
        SELECT ep.*, u.username, u.first_name
        FROM earned_points ep
        JOIN users u ON ep.user_id = u.user_id
        WHERE ep.server_id = ?
        ORDER BY ep.created_at
    """, (server_id,))
    earned = cur.fetchall()
    if earned:
        history_text += f"💰 ЗАРАБОТАННЫЕ БАЛЛЫ ({len(earned)}):\n"
        for point in earned:
            user_name = f"@{point['username']}" if point['username'] else point['first_name']
            action_names = {
                'like': 'лайк',
                'copy': 'копирование IP',
                'save': 'сохранение',
                'site_visit': 'переход по ссылке'
            }
            action_name = action_names.get(point['action_type'], point['action_type'])
            history_text += f"  • {user_name}: +{point['points']} за {action_name} - {point['created_at']}\n"
    
    conn.close()
    return history_text

# Функция получения аудита пользователя
def get_user_audit(user_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT al.*, u.username, u.first_name
        FROM audit_log al
        LEFT JOIN users u ON al.user_id = u.user_id
        WHERE al.user_id = ?
        ORDER BY al.created_at DESC
    """, (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# Функция проверки антиспама
def can_add_server(user_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    # Получаем время последнего добавления сервера пользователем
    cur.execute("SELECT MAX(created_at) FROM servers WHERE creator_user_id = ?", (user_id,))
    last_add = cur.fetchone()[0]
    conn.close()
    if last_add is None:
        return True
    # Проверяем, прошло ли 10 минут
    last_add_time = datetime.fromisoformat(last_add)
    return (datetime.now() - last_add_time) >= timedelta(minutes=10)

# Сортировка серверов для топа
def get_top_servers() -> List[Dict[str, Any]]:
    servers = get_all_servers()
    server_data = []
    for server in servers:
        balance = get_total_balance(server['id'])
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT MAX(last_change) FROM (
                SELECT purchased_at as last_change FROM purchased_boost WHERE server_id = ? AND active = 1
                UNION ALL
                SELECT created_at FROM earned_points WHERE server_id = ? AND active = 1
            )
        """, (server['id'], server['id']))
        last_change = cur.fetchone()[0]
        conn.close()
        if last_change is None:
            last_change = server['created_at']
        server_data.append({
            'server': server,
            'balance': balance,
            'last_change': last_change
        })
    server_data.sort(key=lambda x: (-x['balance'], x['last_change'], x['server']['created_at']))
    return [item['server'] for item in server_data]

def get_new_servers() -> List[Dict[str, Any]]:
    servers = get_all_servers()
    servers.sort(key=lambda x: x['created_at'], reverse=True)
    return servers

# Очистка просроченных баллов
def expire_points():
    now = datetime.now()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE purchased_boost SET active = 0 WHERE active = 1 AND expires_at <= ?", (now,))
    cur.execute("UPDATE earned_points SET active = 0 WHERE active = 1 AND expires_at <= ?", (now,))
    conn.commit()
    conn.close()

# ---------- Клавиатуры ----------
def main_menu_kb() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text="🏆 Топ"))
    builder.add(KeyboardButton(text="🆕 Новые серверы"))
    builder.add(KeyboardButton(text="🚀 Буст"))
    builder.add(KeyboardButton(text="🔍 Поиск"))
    builder.add(KeyboardButton(text="➕ Добавить сервер"))
    builder.adjust(2, 2, 1)
    return builder.as_markup(resize_keyboard=True)

def servers_list_kb(servers: list, page: int, total_pages: int, mode: str = "top") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, server in enumerate(servers):
        pos = (page-1)*5 + idx + 1
        balance = get_total_balance(server['id'])
        likes = get_likes_count_last_month(server['id'])
        # Формируем текст кнопки
        button_text = f"#{pos} {server['name']} ({server['ip']}) | ⭐{balance} | 👍{likes}"
        builder.row(InlineKeyboardButton(text=button_text, callback_data=f"details:{server['id']}"))
    # Пагинация
    pagination_buttons = []
    if page > 1:
        pagination_buttons.append(InlineKeyboardButton(text="← Назад", callback_data=f"page:{mode}:{page-1}"))
    pagination_buttons.append(InlineKeyboardButton(text=f"Страница {page} из {total_pages}", callback_data="ignore"))
    if page < total_pages:
        pagination_buttons.append(InlineKeyboardButton(text="Вперёд →", callback_data=f"page:{mode}:{page+1}"))
    builder.row(*pagination_buttons)
    return builder.as_markup()

def server_details_kb(server_id: int, owner_established: bool, current_user_owner: bool, website: str = None, is_admin: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❤️ Лайк", callback_data=f"like:{server_id}"))
    builder.row(InlineKeyboardButton(text="📋 Скопировать IP", callback_data=f"copy:{server_id}"))
    builder.row(InlineKeyboardButton(text="⭐ Сохранить", callback_data=f"save:{server_id}"))
    if website:
        builder.row(InlineKeyboardButton(text="🔗 Открыть сайт", callback_data=f"open_site:{server_id}"))
    if not owner_established:
        builder.row(InlineKeyboardButton(text="Я владелец", callback_data=f"claim_owner:{server_id}"))
    if is_admin:
        builder.row(InlineKeyboardButton(text="📜 Полная история", callback_data=f"full_history:{server_id}"))
        builder.row(InlineKeyboardButton(text="🗑 Удалить сервер", callback_data=f"admin_delete_server:{server_id}"))
    builder.row(InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_top"))
    return builder.as_markup()

def boost_select_server_kb(servers: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for server in servers:
        builder.row(InlineKeyboardButton(text=server['name'], callback_data=f"boost_select:{server['id']}"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="boost_cancel"))
    return builder.as_markup()

def boost_duration_kb(server_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="3 дня (0.5⭐/балл, мин. 2)", callback_data=f"boost_dur:{server_id}:3"))
    builder.row(InlineKeyboardButton(text="7 дней (1⭐/балл)", callback_data=f"boost_dur:{server_id}:7"))
    builder.row(InlineKeyboardButton(text="30 дней (3⭐/балл)", callback_data=f"boost_dur:{server_id}:30"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="boost_cancel"))
    return builder.as_markup()

def admin_kb() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text="🗑 Удалить серверы"))
    builder.add(KeyboardButton(text="📊 Статистика"))
    builder.add(KeyboardButton(text="📨 Рассылка"))
    builder.add(KeyboardButton(text="👑 Управление владельцами"))
    builder.add(KeyboardButton(text="🚫 Бан пользователя"))
    builder.add(KeyboardButton(text="✅ Разбан пользователя"))
    builder.add(KeyboardButton(text="📜 Аудит лог"))
    builder.add(KeyboardButton(text="💾 Экспорт БД"))
    builder.add(KeyboardButton(text="📥 Импорт БД"))
    builder.add(KeyboardButton(text="🏠 В меню"))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

# ---------- Состояния FSM ----------
class AddServerStates(StatesGroup):
    waiting_name = State()
    waiting_ip = State()
    waiting_version = State()
    waiting_website = State()
    confirm = State()

class BoostStates(StatesGroup):
    waiting_server = State()
    waiting_duration = State()
    waiting_points = State()

class SearchStates(StatesGroup):
    waiting_query = State()

class AdminStates(StatesGroup):
    waiting_broadcast = State()
    waiting_delete_servers = State()
    waiting_import_db = State()
    waiting_owner_select_server = State()
    waiting_owner_user = State()
    waiting_ban_user = State()
    waiting_ban_reason = State()
    waiting_unban_user = State()
    waiting_history_server = State()
    waiting_audit_user = State()

# ---------- Обработчики пользователя ----------
user_router = Router()

@user_router.message(F.text == "/start")
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    
    if is_user_banned(user_id):
        await message.answer("❌ Вы забанены в этом боте.")
        return
    
    add_user(user_id, message.from_user.username, message.from_user.first_name)
    
    await message.answer(
        "👋 Здравствуйте! Добро пожаловать в бот RushX.\n\n"
        "Используйте кнопки ниже для навигации:",
        reply_markup=main_menu_kb()
    )
    
    await show_top(message, page=1)

@user_router.message(F.text == "🏆 Топ")
async def show_top_cmd(message: types.Message, state: FSMContext):
    if is_user_banned(message.from_user.id):
        await message.answer("❌ Вы забанены.")
        return
    await state.clear()
    await show_top(message, page=1)

@user_router.message(F.text == "🆕 Новые серверы")
async def show_new_cmd(message: types.Message, state: FSMContext):
    if is_user_banned(message.from_user.id):
        await message.answer("❌ Вы забанены.")
        return
    await state.clear()
    await show_new_servers(message, page=1)

@user_router.message(F.text == "➕ Добавить сервер")
async def add_server_start(message: types.Message, state: FSMContext):
    if is_user_banned(message.from_user.id):
        await message.answer("❌ Вы забанены.")
        return
    await state.clear()
    await state.set_state(AddServerStates.waiting_name)
    await message.answer("Назовите название своего сервера!")

@user_router.message(F.text == "🔍 Поиск")
async def search_start(message: types.Message, state: FSMContext):
    if is_user_banned(message.from_user.id):
        await message.answer("❌ Вы забанены.")
        return
    await state.clear()
    await state.set_state(SearchStates.waiting_query)
    await message.answer("Какой сервер Вы хотите найти?")

@user_router.message(F.text == "🚀 Буст")
async def boost_start(message: types.Message, state: FSMContext):
    if is_user_banned(message.from_user.id):
        await message.answer("❌ Вы забанены.")
        return
    await state.clear()
    user_id = message.from_user.id
    servers = get_servers_by_creator(user_id)
    if not servers:
        await message.answer("У вас нет добавленных серверов. Сначала добавьте сервер.")
        return
    await state.set_state(BoostStates.waiting_server)
    await message.answer("Выберите сервер для буста:", reply_markup=boost_select_server_kb(servers))

async def show_top(message: types.Message, page: int):
    servers = get_top_servers()
    total_pages = max(1, (len(servers) + 4) // 5)  # 5 на страницу
    if page < 1 or page > total_pages:
        page = 1
    start = (page-1)*5
    end = start+5
    page_servers = servers[start:end]
    text = "🏆 Топ серверов:\n\n"
    await message.answer(text, reply_markup=servers_list_kb(page_servers, page, total_pages, mode="top"))

async def show_new_servers(message: types.Message, page: int):
    servers = get_new_servers()
    total_pages = max(1, (len(servers) + 4) // 5)
    if page < 1 or page > total_pages:
        page = 1
    start = (page-1)*5
    end = start+5
    page_servers = servers[start:end]
    text = "🆕 Топ новых серверов:\n\n"
    # Для новых серверов можно просто кнопки без доп. инфо или с минимальной
    # Используем ту же функцию, но передадим mode="new"
    await message.answer(text, reply_markup=servers_list_kb(page_servers, page, total_pages, mode="new"))

# Обработчики шагов добавления
@user_router.message(AddServerStates.waiting_name)
async def process_name(message: types.Message, state: FSMContext):
    if is_user_banned(message.from_user.id):
        await message.answer("❌ Вы забанены.")
        await state.clear()
        return
    
    name = message.text.strip()
    if len(name) > 32:
        await message.answer("Название слишком длинное (макс. 32 символа). Попробуйте ещё раз.")
        return
    forbidden = ["badword", "дурак", "спам", "рейд"]
    if any(word in name.lower() for word in forbidden):
        await message.answer("Название содержит запрещённые слова.")
        return
    await state.update_data(name=name)
    await state.set_state(AddServerStates.waiting_ip)
    await message.answer("Какой IP вашего сервера?")

@user_router.message(AddServerStates.waiting_ip)
async def process_ip(message: types.Message, state: FSMContext):
    if is_user_banned(message.from_user.id):
        await message.answer("❌ Вы забанены.")
        await state.clear()
        return
    
    ip = message.text.strip()
    if not ('.' in ip or ':' in ip):
        await message.answer("Некорректный IP или домен.")
        return
    existing = get_server_by_ip(ip)
    if existing:
        await message.answer(
            "Этот IP уже занят. Не волнуйтесь, если вы реальный владелец этого сервера, "
            "напишите нам в поддержку @PRMManager."
        )
        await state.clear()
        return
    await state.update_data(ip=ip)
    await state.set_state(AddServerStates.waiting_version)
    await message.answer("Какая версия вашего сервера?")

@user_router.message(AddServerStates.waiting_version)
async def process_version(message: types.Message, state: FSMContext):
    if is_user_banned(message.from_user.id):
        await message.answer("❌ Вы забанены.")
        await state.clear()
        return
    
    version = message.text.strip()
    if len(version) > 16:
        await message.answer("Версия слишком длинная (макс. 16 символов).")
        return
    await state.update_data(version=version)
    await state.set_state(AddServerStates.waiting_website)
    await message.answer("Введите ссылку на сайт или Discord сервера (или напишите 'нет' для пропуска):")

@user_router.message(AddServerStates.waiting_website)
async def process_website(message: types.Message, state: FSMContext):
    if is_user_banned(message.from_user.id):
        await message.answer("❌ Вы забанены.")
        await state.clear()
        return
    
    website_input = message.text.strip()
    if website_input.lower() in ['нет', 'нету', 'пропустить', '-']:
        website = None
    else:
        if not website_input.startswith(('http://', 'https://')):
            website = 'https://' + website_input
        else:
            website = website_input
    await state.update_data(website=website)
    data = await state.get_data()
    await state.set_state(AddServerStates.confirm)
    text = f"Проверьте данные:\nНазвание: {data['name']}\nIP: {data['ip']}\nВерсия: {data['version']}\n"
    if data.get('website'):
        text += f"Сайт: {data['website']}\n"
    text += "Подтвердить?"
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_add"),
        InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_add")
    )
    await message.answer(text, reply_markup=builder.as_markup())

@user_router.callback_query(F.data == "confirm_add", AddServerStates.confirm)
async def confirm_add(callback: types.CallbackQuery, state: FSMContext):
    if is_user_banned(callback.from_user.id):
        await callback.answer("❌ Вы забанены.", show_alert=True)
        await state.clear()
        return
    
    data = await state.get_data()
    user_id = callback.from_user.id
    
    # Проверка антиспама: можно добавлять не чаще раза в 10 минут
    if not can_add_server(user_id):
        await callback.answer("Слишком часто добавляете серверы. Подождите 10 минут.", show_alert=True)
        await state.clear()
        return
    
    user_servers = get_servers_by_creator(user_id)
    if len(user_servers) >= 5:
        await callback.message.answer("Вы достигли лимита добавления серверов (5).")
        await state.clear()
        return
    is_new_until = datetime.now()
    website = data.get('website')
    server_id = add_server(data['name'], data['ip'], data['version'], user_id, is_new_until, website)
    await state.clear()
    await callback.message.answer(f"✅ Сервер {data['name']} успешно добавлен!\nОн появится в топе новых серверов и в общем списке.")
    await show_top(callback.message, page=1)
    await callback.answer()

@user_router.callback_query(F.data == "cancel_add", AddServerStates.confirm)
async def cancel_add(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("❌ Добавление отменено.")
    await callback.answer()

# Пагинация
@user_router.callback_query(F.data.startswith("page:"))
async def handle_pagination(callback: types.CallbackQuery, state: FSMContext):
    if is_user_banned(callback.from_user.id):
        await callback.answer("❌ Вы забанены.", show_alert=True)
        return
    
    parts = callback.data.split(":")
    mode = parts[1] от
    page = int(parts[2])
    if mode == "top":
        await show_top(callback.message, page)
    elif mode == "new":
        await show_new_servers(callback.message, page)
    await callback.answer()

# Детали сервера
@user_router.callback_query(F.data.startswith("details:"))
async def server_details(callback: types.CallbackQuery, state: FSMContext):
    if is_user_banned(callback.from_user.id):
        await callback.answer("❌ Вы забанены.", show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    server = get_server_by_id(server_id)
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    pos = get_top_servers().index(server) + 1 if server in get_top_servers() else 0
    balance_purchased = get_active_purchased_points(server_id)
    balance_earned = get_active_earned_points(server_id)
    likes = get_likes_count_last_month(server_id)
    saves = get_saves_count_total(server_id)
    copies = get_copies_count_last_month(server_id)
    owner = server['owner_user_id']
    owner_text = get_user_display_name(owner) if owner else "не установлен"
    website = server.get('website')
    creator_name = get_user_display_name(server['creator_user_id'])
    
    text = f"📊 Информация о сервере:\n\n"
    text += f"Название: {server['name']}\n"
    text += f"IP: {server['ip']}\n"
    text += f"Версия: {server['version']}\n"
    if website:
        text += f"Сайт: {website}\n"
    text += f"Создатель: {creator_name}\n"
    text += f"Текущая позиция в топе: #{pos}\n"
    text += f"Общий баланс баллов: {balance_purchased + balance_earned}\n"
    text += f"  - покупные: {balance_purchased}\n"
    text += f"  - заработанные: {balance_earned}\n"
    text += f"Лайков за месяц: {likes}\n"
    text += f"Сохранений всего: {saves}\n"
    text += f"Копирований IP за месяц: {copies}\n"
    text += f"Владелец: {owner_text}\n"
    if owner and owner != callback.from_user.id:
        text += "Вы владелец? Напишите @PRMManager\n"
    elif not owner:
        text += "Вы владелец? Нажмите кнопку ниже для заявки.\n"
    
    current_user_owner = (owner == callback.from_user.id)
    is_admin_user = is_admin(callback.from_user.id)
    await callback.message.answer(text, reply_markup=server_details_kb(server_id, owner is not None, current_user_owner, website, is_admin_user))
    await callback.answer()

# Действия лайк, копирование, сохранение
@user_router.callback_query(F.data.startswith("like:"))
async def like_server(callback: types.CallbackQuery):
    if is_user_banned(callback.from_user.id):
        await callback.answer("❌ Вы забанены.", show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    if has_action_in_last_days(server_id, user_id, "like", 30):
        await callback.answer("Вы уже лайкали этот сервер за последние 30 дней.", show_alert=True)
        return
    log_action(server_id, user_id, "like")
    add_earned_points(server_id, user_id, "like", 5)
    await callback.answer("❤️ +5 баллов за лайк!", show_alert=True)

@user_router.callback_query(F.data.startswith("copy:"))
async def copy_ip(callback: types.CallbackQuery):
    if is_user_banned(callback.from_user.id):
        await callback.answer("❌ Вы забанены.", show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    server = get_server_by_id(server_id)
    if not has_action_in_last_days(server_id, user_id, "copy", 30):
        log_action(server_id, user_id, "copy")
        add_earned_points(server_id, user_id, "copy", 2)
        await callback.answer(f"📋 IP скопирован: {server['ip']}. +2 балла.", show_alert=True)
    else:
        await callback.answer(f"📋 IP скопирован: {server['ip']} (баллы не начислены, лимит)", show_alert=True)

@user_router.callback_query(F.data.startswith("save:"))
async def save_server(callback: types.CallbackQuery):
    if is_user_banned(callback.from_user.id):
        await callback.answer("❌ Вы забанены.", show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    if has_saved(server_id, user_id):
        await callback.answer("Вы уже сохраняли этот сервер.", show_alert=True)
        return
    save_favorite(server_id, user_id)
    add_earned_points(server_id, user_id, "save", 1)
    await callback.answer("⭐ Сервер сохранён! +1 балл.", show_alert=True)

@user_router.callback_query(F.data.startswith("open_site:"))
async def open_site(callback: types.CallbackQuery):
    if is_user_banned(callback.from_user.id):
        await callback.answer("❌ Вы забанены.", show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    server = get_server_by_id(server_id)
    if not server or not server.get('website'):
        await callback.answer("Ссылка не указана.", show_alert=True)
        return
    if has_action_in_last_days(server_id, user_id, "site_visit", 30):
        await callback.answer("Вы уже переходили по ссылке этого сервера за последние 30 дней.", show_alert=True)
        await callback.answer(url=server['website'])
        return
    log_action(server_id, user_id, "site_visit")
    add_earned_points(server_id, user_id, "site_visit", 3)
    await callback.answer(url=server['website'])
    await callback.message.answer(f"🔗 Переход по ссылке: +3 балла!")

@user_router.callback_query(F.data.startswith("claim_owner:"))
async def claim_owner(callback: types.CallbackQuery):
    if is_user_banned(callback.from_user.id):
        await callback.answer("❌ Вы забанены.", show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    add_ownership_request(server_id, user_id)
    await callback.answer("✅ Заявка отправлена! Для подтверждения напишите @PRMManager.", show_alert=True)

@user_router.callback_query(F.data == "back_to_top")
async def back_to_top(callback: types.CallbackQuery):
    await show_top(callback.message, page=1)
    await callback.answer()

# Поиск
@user_router.message(SearchStates.waiting_query)
async def process_search(message: types.Message, state: FSMContext):
    if is_user_banned(message.from_user.id):
        await message.answer("❌ Вы забанены.")
        await state.clear()
        return
    
    query = message.text.strip()
    servers = search_servers(query)
    await state.clear()
    if not servers:
        await message.answer("Сервер не найден.")
        return
    text = f"🔍 Результаты поиска по '{query}':\n\n"
    # Показываем результаты тоже кнопками, используя servers_list_kb с mode="search"
    await message.answer(text, reply_markup=servers_list_kb(servers, 1, max(1, (len(servers)+4)//5), mode="search"))

# Буст
@user_router.callback_query(F.data.startswith("boost_select:"), BoostStates.waiting_server)
async def boost_server_selected(callback: types.CallbackQuery, state: FSMContext):
    if is_user_banned(callback.from_user.id):
        await callback.answer("❌ Вы забанены.", show_alert=True)
        await state.clear()
        return
    
    server_id = int(callback.data.split(":")[1])
    server = get_server_by_id(server_id)
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    await state.update_data(server_id=server_id)
    await state.set_state(BoostStates.waiting_duration)
    await callback.message.answer("Выберите срок действия баллов:", reply_markup=boost_duration_kb(server_id))
    await callback.answer()

@user_router.callback_query(F.data.startswith("boost_dur:"), BoostStates.waiting_duration)
async def boost_duration_selected(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    server_id = int(parts[1])
    duration = int(parts[2])
    await state.update_data(duration=duration)
    await state.set_state(BoostStates.waiting_points)
    await callback.message.answer("Введите количество баллов (целое число).")
    await callback.answer()

@user_router.callback_query(F.data == "boost_cancel", BoostStates.waiting_server)
async def boost_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("❌ Буст отменён.")
    await callback.answer()

@user_router.message(BoostStates.waiting_points)
async def boost_points_input(message: types.Message, state: FSMContext):
    if is_user_banned(message.from_user.id):
        await message.answer("❌ Вы забанены.")
        await state.clear()
        return
    
    try:
        points = int(message.text)
    except ValueError:
        await message.answer("Введите целое число.")
        return
    data = await state.get_data()
    server_id = data['server_id']
    duration = data['duration']
    if duration == 3:
        if points < 2 or points % 2 != 0:
            await message.answer("Для 3 дней минимальная покупка 2 балла, количество должно быть чётным.")
            return
        price_per_point = 0.5
    elif duration == 7:
        if points < 1:
            await message.answer("Минимум 1 балл.")
            return
        price_per_point = 1
    elif duration == 30:
        if points < 1:
            await message.answer("Минимум 1 балл.")
            return
        price_per_point = 3
    else:
        await message.answer("Неверный срок.")
        return
    total_stars = int(points * price_per_point)
    await state.update_data(points=points, total_stars=total_stars)
    prices = [LabeledPrice(label=f"Буст {points} баллов на {duration} дней", amount=total_stars)]
    await message.answer_invoice(
        title="Покупка баллов",
        description=f"Сервер ID {server_id}, {points} баллов на {duration} дней",
        payload=f"boost:{server_id}:{duration}:{points}:{total_stars}",
        provider_token="",
        currency="XTR",
        prices=prices,
    )
    await state.clear()

# ---------- Обработчики платежей ----------
payments_router = Router()

@payments_router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: types.PreCheckoutQuery):
    if is_user_banned(pre_checkout_query.from_user.id):
        await pre_checkout_query.answer(ok=False, error_message="Вы забанены.")
        return
    await pre_checkout_query.answer(ok=True)

@payments_router.message(F.successful_payment)
async def successful_payment(message: types.Message):
    if is_user_banned(message.from_user.id):
        await message.answer("❌ Вы забанены.")
        return
    
    payload = message.successful_payment.invoice_payload
    parts = payload.split(":")
    if parts[0] == "boost":
        server_id = int(parts[1])
        duration = int(parts[2])
        points = int(parts[3])
        total_stars = int(parts[4])
        user_id = message.from_user.id
        add_purchased_boost(server_id, user_id, points, duration, total_stars)
        await message.answer(f"✅ Оплата прошла успешно! Сервер получил {points} баллов на {duration} дней.")
        server = get_server_by_id(server_id)
        if server:
            notify_user_id = server['owner_user_id'] or server['creator_user_id']
            if notify_user_id and notify_user_id != user_id:
                try:
                    await message.bot.send_message(notify_user_id, f"Ваш сервер {server['name']} получил буст: {points} баллов на {duration} дней.")
                except:
                    pass
    else:
        await message.answer("Неизвестный платёж.")

# ---------- Обработчики администратора ----------
admin_router = Router()

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

@admin_router.message(F.text == "/admin")
async def admin_cmd(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    await state.clear()
    await message.answer("👑 Админ-панель:", reply_markup=admin_kb())

@admin_router.message(F.text == "🗑 Удалить серверы")
async def admin_delete_servers(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    
    servers = get_all_servers()
    if not servers:
        await message.answer("Нет серверов.")
        return
    
    text = "Выберите серверы для удаления (можно несколько через запятую):\n\n"
    for idx, server in enumerate(servers, 1):
        creator_name = get_user_display_name(server['creator_user_id'])
        text += f"{idx}. {server['name']} ({server['ip']}) - Создатель: {creator_name} - ID: {server['id']}\n"
    
    text += "\nВведите номера через запятую (например: 1,3,5) или ID серверов (например: 10,15,20)"
    await state.set_state(AdminStates.waiting_delete_servers)
    await message.answer(text)

@admin_router.message(AdminStates.waiting_delete_servers)
async def admin_delete_servers_choose(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        await state.clear()
        return
    
    input_text = message.text.strip()
    servers = get_all_servers()
    
    try:
        parts = [p.strip() for p in input_text.split(',')]
        ids_to_delete = []
        
        for part in parts:
            if part.isdigit():
                num = int(part)
                if 1 <= num <= len(servers):
                    ids_to_delete.append(servers[num-1]['id'])
                else:
                    ids_to_delete.append(num)
        
        if not ids_to_delete:
            await message.answer("Не удалось распознать номера серверов.")
            await state.clear()
            return
        
        deleted_count = 0
        for server_id in ids_to_delete:
            if get_server_by_id(server_id):
                delete_server(server_id, message.from_user.id)
                deleted_count += 1
        
        await message.answer(f"✅ Удалено серверов: {deleted_count}")
        await state.clear()
        await show_top(message, page=1)
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
        await state.clear()

@admin_router.message(F.text == "🚫 Бан пользователя")
async def admin_ban_user(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    await state.set_state(AdminStates.waiting_ban_user)
    await message.answer("Введите user_id пользователя для бана:")

@admin_router.message(AdminStates.waiting_ban_user)
async def admin_ban_user_id(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text)
    except:
        await message.answer("Введите число.")
        return
    
    await state.update_data(ban_user_id=user_id)
    await state.set_state(AdminStates.waiting_ban_reason)
    await message.answer("Введите причину бана:")

@admin_router.message(AdminStates.waiting_ban_reason)
async def admin_ban_user_reason(message: types.Message, state: FSMContext):
    reason = message.text
    data = await state.get_data()
    user_id = data['ban_user_id']
    
    ban_user(user_id, reason, message.from_user.id)
    
    user_servers = get_servers_by_creator(user_id)
    for server in user_servers:
        delete_server(server['id'], message.from_user.id)
    
    user_name = get_user_display_name(user_id)
    await message.answer(f"✅ Пользователь {user_name} забанен.\nПричина: {reason}\nУдалено серверов: {len(user_servers)}")
    await state.clear()

@admin_router.message(F.text == "✅ Разбан пользователя")
async def admin_unban_user(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    await state.set_state(AdminStates.waiting_unban_user)
    await message.answer("Введите user_id пользователя для разбана:")

@admin_router.message(AdminStates.waiting_unban_user)
async def admin_unban_user_id(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text)
    except:
        await message.answer("Введите число.")
        return
    
    unban_user(user_id, message.from_user.id)
    user_name = get_user_display_name(user_id)
    await message.answer(f"✅ Пользователь {user_name} разбанен.")
    await state.clear()

@admin_router.message(F.text == "📜 Аудит лог")
async def admin_audit_log(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    await state.set_state(AdminStates.waiting_history_server)
    await message.answer("Введите ID сервера для просмотра полной истории:")

@admin_router.message(AdminStates.waiting_history_server)
async def admin_history_server(message: types.Message, state: FSMContext):
    try:
        server_id = int(message.text)
    except:
        await message.answer("Введите число.")
        return
    
    history = get_server_full_history(server_id)
    
    if len(history) > 4000:
        parts = [history[i:i+4000] for i in range(0, len(history), 4000)]
        for part in parts:
            await message.answer(part)
    else:
        await message.answer(history)
    
    await state.clear()

@admin_router.message(F.text == "📊 Статистика")
async def admin_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE is_banned = 0")
    active_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
    banned_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM servers WHERE is_deleted = 0")
    active_servers = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM servers WHERE is_deleted = 1")
    deleted_servers = cur.fetchone()[0]
    cur.execute("SELECT SUM(points) FROM purchased_boost WHERE active=1")
    purchased_points = cur.fetchone()[0] or 0
    cur.execute("SELECT SUM(points) FROM earned_points WHERE active=1")
    earned_points = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM audit_log")
    audit_count = cur.fetchone()[0]
    conn.close()
    
    text = f"📊 Статистика:\n\n"
    text += f"Активных пользователей: {active_users}\n"
    text += f"Забаненных пользователей: {banned_users}\n"
    text += f"Активных серверов: {active_servers}\n"
    text += f"Удалённых серверов: {deleted_servers}\n"
    text += f"Активных покупных баллов: {purchased_points}\n"
    text += f"Активных заработанных баллов: {earned_points}\n"
    text += f"Записей в аудите: {audit_count}"
    
    await message.answer(text)

@admin_router.message(F.text == "📨 Рассылка")
async def admin_broadcast_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    await state.set_state(AdminStates.waiting_broadcast)
    await message.answer("Введите текст рассылки:")

@admin_router.message(AdminStates.waiting_broadcast)
async def admin_broadcast_send(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        await state.clear()
        return
    
    text = message.text
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE is_banned = 0")
    users = cur.fetchall()
    conn.close()
    count = 0
    for user in users:
        try:
            await message.bot.send_message(user[0], text)
            count += 1
        except:
            pass
    await message.answer(f"✅ Рассылка отправлена {count} пользователям.")
    await state.clear()

@admin_router.message(F.text == "👑 Управление владельцами")
async def admin_manage_owners(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    
    servers = get_all_servers()
    if not servers:
        await message.answer("Нет серверов.")
        return
    
    text = "Выберите сервер для управления владельцем (номер):\n"
    for idx, server in enumerate(servers, 1):
        creator_name = get_user_display_name(server['creator_user_id'])
        text += f"{idx}. {server['name']} (owner: {get_user_display_name(server['owner_user_id']) if server['owner_user_id'] else 'нет'}) - Создатель: {creator_name}\n"
    
    await state.set_state(AdminStates.waiting_owner_select_server)
    await message.answer(text)

@admin_router.message(AdminStates.waiting_owner_select_server)
async def admin_owner_select_server(message: types.Message, state: FSMContext):
    try:
        idx = int(message.text) - 1
    except:
        await message.answer("Введите число.")
        return
    
    servers = get_all_servers()
    if idx < 0 or idx >= len(servers):
        await message.answer("Неверный номер.")
        return
    
    server = servers[idx]
    await state.update_data(server_id=server['id'])
    await state.set_state(AdminStates.waiting_owner_user)
    await message.answer("Введите user_id нового владельца (или 0 для снятия):")

@admin_router.message(AdminStates.waiting_owner_user)
async def admin_owner_set_user(message: types.Message, state: FSMContext):
    try:
        owner_id = int(message.text)
    except:
        await message.answer("Введите число.")
        return
    
    data = await state.get_data()
    server_id = data['server_id']
    
    if owner_id == 0:
        update_server_owner(server_id, None, message.from_user.id)
    else:
        update_server_owner(server_id, owner_id, message.from_user.id)
    
    await message.answer("✅ Владелец обновлён.")
    await state.clear()

@admin_router.message(F.text == "💾 Экспорт БД")
async def admin_export_db(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    
    data = {}
    conn = get_connection()
    cur = conn.cursor()
    tables = ["users", "servers", "purchased_boost", "earned_points", "actions_log", "favorites", "ownership_requests", "audit_log"]
    for table in tables:
        cur.execute(f"SELECT * FROM {table}")
        rows = cur.fetchall()
        data[table] = [dict(row) for row in rows]
    conn.close()
    
    json_str = json.dumps(data, ensure_ascii=False, default=str)
    file = io.BytesIO(json_str.encode('utf-8'))
    file.name = "database.json"
    await message.answer_document(BufferedInputFile(file.getvalue(), filename="database.json"))

@admin_router.message(F.text == "📥 Импорт БД")
async def admin_import_db_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    await state.set_state(AdminStates.waiting_import_db)
    await message.answer("Отправьте JSON файл базы данных.")

@admin_router.message(AdminStates.waiting_import_db, F.document)
async def admin_import_db_file(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        await state.clear()
        return
    
    document = message.document
    file = await message.bot.get_file(document.file_id)
    file_path = file.file_path
    downloaded = await message.bot.download_file(file_path)
    json_str = downloaded.read().decode('utf-8')
    data = json.loads(json_str)
    
    conn = get_connection()
    cur = conn.cursor()
    for table in ["users", "servers", "purchased_boost", "earned_points", "actions_log", "favorites", "ownership_requests", "audit_log"]:
        cur.execute(f"DELETE FROM {table}")
    conn.commit()
    
    for table, rows in data.items():
        for row in rows:
            columns = ', '.join(row.keys())
            placeholders = ', '.join(['?'] * len(row))
            sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
            cur.execute(sql, tuple(row.values()))
    conn.commit()
    conn.close()
    
    await message.answer("✅ База данных импортирована.")
    await state.clear()

@admin_router.message(F.text == "🏠 В меню")
async def back_to_user_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Вы в главном меню", reply_markup=main_menu_kb())

# Обработчик полной истории сервера
@user_router.callback_query(F.data.startswith("full_history:"))
async def full_history_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    history = get_server_full_history(server_id)
    
    if len(history) > 4000:
        parts = [history[i:i+4000] for i in range(0, len(history), 4000)]
        for part in parts:
            await callback.message.answer(part)
    else:
        await callback.message.answer(history)
    
    await callback.answer()

# Обработчик удаления сервера из карточки
@user_router.callback_query(F.data.startswith("admin_delete_server:"))
async def admin_delete_server_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    delete_server(server_id, callback.from_user.id)
    await callback.answer("✅ Сервер удалён.", show_alert=True)
    await show_top(callback.message, page=1)

# ---------- Планировщик ----------
async def check_expired_points(bot: Bot):
    before = {s['id']: get_total_balance(s['id']) for s in get_all_servers()}
    expire_points()
    after_servers = get_all_servers()
    after = {s['id']: get_total_balance(s['id']) for s in after_servers}
    changed = [sid for sid in before if before[sid] != after.get(sid, 0)]
    if changed:
        top_after = get_top_servers()
        positions = {s['id']: idx+1 for idx, s in enumerate(top_after)}
        for sid in changed:
            server = next((s for s in after_servers if s['id'] == sid), None)
            if server:
                user_id = server['owner_user_id'] or server['creator_user_id']
                if user_id:
                    new_pos = positions.get(sid, 0)
                    try:
                        await bot.send_message(user_id, f"⚠️ Баллы сервера {server['name']} изменились (сгорание или другое). Новая позиция: #{new_pos}")
                    except:
                        pass

async def start_scheduler(bot: Bot):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_expired_points, 'interval', minutes=5, args=[bot])
    scheduler.start()

# ---------- Основная функция ----------
async def main():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())

    init_db()

    dp.include_router(admin_router)
    dp.include_router(payments_router)
    dp.include_router(user_router)

    await start_scheduler(bot)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
