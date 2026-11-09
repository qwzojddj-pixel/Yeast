from __future__ import annotations

"""SPANIX STARS Telegram bot — single-file build.

Install dependencies from requirements.txt, set BOT_TOKEN and OWNER_IDS
(environment variables or a .env file in the same directory), then run:
    python spanix_stars_bot.py
"""


import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_ids: frozenset[int]
    db_path: str
    timezone: str
    log_level: str


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN не заданий. Заповніть .env за прикладом .env.example")

    raw_ids = os.getenv("OWNER_IDS", "")
    ids: set[int] = set()
    for item in raw_ids.split(","):
        item = item.strip()
        if item:
            try:
                ids.add(int(item))
            except ValueError as exc:
                raise RuntimeError(f"Некоректний OWNER_IDS: {item!r}") from exc
    if not ids:
        raise RuntimeError("Додайте хоча б один Telegram ID у OWNER_IDS")

    db_path = os.getenv("DB_PATH", "data/spanix.sqlite3").strip()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return Config(
        bot_token=token,
        owner_ids=frozenset(ids),
        db_path=db_path,
        timezone=os.getenv("TIMEZONE", "Europe/Kyiv").strip(),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )


# ----------------------------- layout helpers -----------------------------


from typing import Iterable, TypeVar

T = TypeVar("T")


def split_alternating(items: Iterable[T]) -> list[list[T]]:
    """Group items into rows with repeating widths 2, 1, 2, 1, ..."""
    values = list(items)
    rows: list[list[T]] = []
    cursor = 0
    width = 2
    while cursor < len(values):
        rows.append(values[cursor:cursor + width])
        cursor += width
        width = 1 if width == 2 else 2
    return rows


# ----------------------------- keyboard helpers ---------------------------


from dataclasses import dataclass
from typing import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup



@dataclass
class Button:
    text: str
    callback: str | None = None
    url: str | None = None
    style: str = ""
    icon_custom_emoji_id: str = ""


def alternating_rows(buttons: Iterable[InlineKeyboardButton]) -> list[list[InlineKeyboardButton]]:
    """Розкладка 2 кнопки, 1 кнопка, 2, 1 ... Повторюється до кінця списку."""
    return split_alternating(buttons)


def make_keyboard(items: Iterable[Button], alternating: bool = True) -> InlineKeyboardMarkup:
    buttons: list[InlineKeyboardButton] = []
    for item in items:
        kwargs = {"text": item.text}
        if item.url:
            kwargs["url"] = item.url
        else:
            kwargs["callback_data"] = item.callback or "nav:home"
        if item.style in {"primary", "success", "danger"}:
            kwargs["style"] = item.style
        if item.icon_custom_emoji_id:
            kwargs["icon_custom_emoji_id"] = item.icon_custom_emoji_id
        buttons.append(InlineKeyboardButton(**kwargs))
    rows = alternating_rows(buttons) if alternating else [[b] for b in buttons]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# -------------------------------- FSM states ------------------------------

from aiogram.fsm.state import State, StatesGroup


class Purchase(StatesGroup):
    stars_recipient = State()
    stars_quantity = State()
    ton_amount = State()
    ton_wallet = State()
    premium_recipient = State()
    game_fields = State()
    receipt = State()
    wallet = State()
    withdraw_amount = State()
    withdraw_details = State()
    review_comment = State()
    review_photo = State()


class AdminFlow(StatesGroup):
    edit_setting = State()
    edit_text = State()
    edit_button = State()
    bank_name = State()
    bank_details = State()
    shared_card = State()
    product_name = State()
    product_description = State()
    product_price = State()
    product_stock = State()
    product_fields = State()
    banner_photo = State()
    broadcast_text = State()
    user_lookup = State()
    add_admin = State()
    reject_reason = State()
    review_number = State()
    set_review_counter = State()


# -------------------------------- database ---------------------------------


import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import aiosqlite


SCHEMA_VERSION = 1

DEFAULT_SETTINGS: dict[str, str] = {
    "brand": "SPANIX STARS",
    "support_url": "",
    "reviews_channel": "",
    "news_channel": "",
    "offer_url": "",
    "policy_url": "",
    "site_url": "",
    "price_star": "0.95",
    "min_stars": "50",
    "max_stars": "25000",
    "ton_price": "0",
    "min_ton": "0.1",
    "max_ton": "100",
    "premium_3": "0",
    "premium_6": "0",
    "premium_12": "0",
    "referral_percent": "5",
    "referral_min_withdraw": "100",
    "referral_enabled": "1",
    "payment_timeout_minutes": "30",
    "review_next_number": "1",
    "review_auto_increment": "1",
    "review_comment_required": "0",
    "review_photo_required": "0",
    "review_mask_names": "0",
    "review_template": "Відгук №{review_number}\nКлієнт: {client_name}\nКоментар: {comment}\nОцінка: {rating_stars}\nПридбано: {product_line}\nДата: {date}\n{brand} — ваш надійний партнер!",
    "maintenance": "0",
    "backup_enabled": "0",
    "code_repeat_seconds": "120",
    "loyalty_enabled": "1",
    "loyalty_silver_orders": "5",
    "loyalty_silver_discount": "2",
    "loyalty_gold_orders": "15",
    "loyalty_gold_discount": "5",
    "promo_banner": "",
    "text:menu.welcome": "Ласкаво просимо до <b>{brand}</b>!\nТут ви можете придбати Telegram Stars, TON та цифрові товари за гривні.\nПриємних покупок!",
    "text:payment.instruction": "Після переказу натисніть <b>«Я оплатив(ла)»</b> та надішліть чек або скриншот. Замовлення буде перевірене адміністратором.",
    "text:review.ask": "Дякуємо за покупку! Оцініть, будь ласка, замовлення.",
}

DEFAULT_BANKS = [
    "monobank", "ПриватБанк", "ПУМБ", "Банк Альянс", "Ощадбанк",
    "Райффайзен", "Sense Bank", "А-Банк", "Укрсиббанк", "OTP Bank",
    "Універсал Банк",
]

DEFAULT_BUTTONS: dict[str, tuple[str, str, str, int]] = {
    "main.stars": ("Купити Stars", "⭐", "primary", 1),
    "main.ton": ("Купити TON", "💎", "primary", 1),
    "main.nft": ("Продаж NFT", "🎁", "primary", 1),
    "main.premium": ("Premium", "👑", "primary", 1),
    "main.games": ("Донат у ігри", "🎮", "primary", 1),
    "main.accounts": ("ТГ акаунти", "📱", "", 1),
    "main.reviews": ("Відгуки", "⭐", "", 1),
    "main.support": ("Підтримка", "💬", "", 1),
    "main.profile": ("Профіль", "👤", "", 1),
}


class Database:
    def __init__(self, config: Config):
        self.path = config.db_path

    async def connect(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(self.path, timeout=30)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=30000")
        return db

    async def init(self, owner_ids: frozenset[int]) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with await self.connect() as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS users(
                    user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
                    referred_by INTEGER, ton_wallet TEXT DEFAULT '',
                    total_spent_kop INTEGER DEFAULT 0, orders_count INTEGER DEFAULT 0,
                    ref_balance_kop INTEGER DEFAULT 0, ref_earned_kop INTEGER DEFAULT 0,
                    banned INTEGER DEFAULT 0, blocked INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL, last_seen TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS admins(
                    user_id INTEGER PRIMARY KEY, role TEXT NOT NULL DEFAULT 'moderator',
                    permissions TEXT NOT NULL DEFAULT 'orders,reviews', added_by INTEGER, added_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS banks(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                    card_number TEXT DEFAULT '', cardholder TEXT DEFAULT '', comment TEXT DEFAULT '',
                    enabled INTEGER DEFAULT 1, sort_order INTEGER DEFAULT 0, limit_kop INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS buttons(
                    key TEXT PRIMARY KEY, label TEXT NOT NULL, emoji TEXT DEFAULT '', style TEXT DEFAULT '',
                    visible INTEGER DEFAULT 1, custom_emoji_id TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS products(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL,
                    name TEXT NOT NULL, description TEXT DEFAULT '', price_kop INTEGER NOT NULL,
                    stock INTEGER DEFAULT -1, fields TEXT DEFAULT '[]', photo_file_id TEXT DEFAULT '',
                    active INTEGER DEFAULT 1, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS orders(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                    type TEXT NOT NULL, payload TEXT NOT NULL, amount_kop INTEGER NOT NULL,
                    discount_kop INTEGER DEFAULT 0, bank_id INTEGER, status TEXT NOT NULL,
                    receipt_file_id TEXT DEFAULT '', receipt_hash TEXT DEFAULT '',
                    admin_id INTEGER, admin_note TEXT DEFAULT '',
                    created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    paid_at TEXT DEFAULT '', done_at TEXT DEFAULT '',
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, id DESC);
                CREATE TABLE IF NOT EXISTS reviews(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL UNIQUE,
                    user_id INTEGER NOT NULL, rating INTEGER NOT NULL, comment TEXT DEFAULT '',
                    photo_file_id TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
                    review_number INTEGER DEFAULT 0, published_message_id INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS referral_ledger(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER NOT NULL,
                    buyer_id INTEGER NOT NULL, order_id INTEGER NOT NULL UNIQUE,
                    amount_kop INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS withdrawals(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                    amount_kop INTEGER NOT NULL, details TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
                    processed_by INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS audit_log(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER NOT NULL,
                    action TEXT NOT NULL, details TEXT DEFAULT '', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS receipt_hashes(
                    hash TEXT PRIMARY KEY, order_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            now = datetime.now().isoformat(timespec="seconds")
            for key, value in DEFAULT_SETTINGS.items():
                await db.execute(
                    "INSERT OR IGNORE INTO settings(key,value,updated_at) VALUES(?,?,?)",
                    (key, value, now),
                )
            for index, name in enumerate(DEFAULT_BANKS):
                await db.execute(
                    "INSERT OR IGNORE INTO banks(name,sort_order) VALUES(?,?)", (name, index)
                )
            for key, (label, emoji, style, visible) in DEFAULT_BUTTONS.items():
                await db.execute(
                    "INSERT OR IGNORE INTO buttons(key,label,emoji,style,visible) VALUES(?,?,?,?,?)",
                    (key, label, emoji, style, visible),
                )
            for owner_id in owner_ids:
                await db.execute(
                    "INSERT OR IGNORE INTO admins(user_id,role,permissions,added_at) VALUES(?,?,?,?)",
                    (owner_id, "owner", "*", now),
                )
            await db.execute(
                "INSERT INTO meta(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            await db.commit()

    async def get_setting(self, key: str, default: str = "") -> str:
        async with await self.connect() as db:
            cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
            row = await cur.fetchone()
            return str(row["value"]) if row else default

    async def set_setting(self, key: str, value: str) -> None:
        async with await self.connect() as db:
            await db.execute(
                "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (key, value, datetime.now().isoformat(timespec="seconds")),
            )
            await db.commit()

    async def audit(self, admin_id: int, action: str, details: str = "") -> None:
        async with await self.connect() as db:
            await db.execute(
                "INSERT INTO audit_log(admin_id,action,details,created_at) VALUES(?,?,?,?)",
                (admin_id, action, details[:1000], datetime.now().isoformat(timespec="seconds")),
            )
            await db.commit()

    async def register_user(self, user_id: int, username: str | None, first_name: str,
                            referrer_id: int | None = None) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        async with await self.connect() as db:
            cur = await db.execute("SELECT referred_by FROM users WHERE user_id=?", (user_id,))
            existing = await cur.fetchone()
            if existing is None:
                if referrer_id == user_id:
                    referrer_id = None
                if referrer_id:
                    cur = await db.execute("SELECT user_id FROM users WHERE user_id=?", (referrer_id,))
                    if await cur.fetchone() is None:
                        referrer_id = None
                await db.execute(
                    "INSERT INTO users(user_id,username,first_name,referred_by,created_at,last_seen) VALUES(?,?,?,?,?,?)",
                    (user_id, username, first_name, referrer_id, now, now),
                )
            else:
                await db.execute(
                    "UPDATE users SET username=?,first_name=?,last_seen=? WHERE user_id=?",
                    (username, first_name, now, user_id),
                )
            await db.commit()

    async def get_user(self, user_id: int) -> aiosqlite.Row | None:
        async with await self.connect() as db:
            cur = await db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
            return await cur.fetchone()

    async def is_admin(self, user_id: int, permission: str = "") -> bool:
        async with await self.connect() as db:
            cur = await db.execute("SELECT role,permissions FROM admins WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            if not row:
                return False
            if row["role"] == "owner" or row["permissions"] == "*":
                return True
            if not permission:
                return True
            return permission in (row["permissions"] or "").split(",")

    async def create_order(self, user_id: int, order_type: str, payload: dict[str, Any],
                           amount_kop: int, timeout_minutes: int) -> int:
        from datetime import timedelta
        now = datetime.now()
        expires = now + timedelta(minutes=timeout_minutes)
        async with await self.connect() as db:
            cur = await db.execute(
                "INSERT INTO orders(user_id,type,payload,amount_kop,status,created_at,expires_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (user_id, order_type, json.dumps(payload, ensure_ascii=False), amount_kop,
                 "awaiting_payment", now.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds")),
            )
            order_id = int(cur.lastrowid)
            await db.commit()
            return order_id


# -------------------------------- bot logic --------------------------------



import asyncio
import csv
import hashlib
import html
import io
import json
import logging
import os
import re
import shutil
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from string import Formatter
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)


CONFIG = load_config()
logging.basicConfig(
    level=getattr(logging, CONFIG.log_level, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("spanix_stars")
DB = Database(CONFIG)
router = Router()
BOT_USERNAME = ""


# ----------------------------- common utilities -----------------------------


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def decimal_value(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def money_to_kop(value: Any) -> int:
    amount = decimal_value(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(amount * 100)


def kop_to_money(value: int | None) -> Decimal:
    return (Decimal(int(value or 0)) / Decimal(100)).quantize(Decimal("0.01"))


def fmt_money(value: int | Decimal) -> str:
    amount = kop_to_money(value) if isinstance(value, int) else value.quantize(Decimal("0.01"))
    raw = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
    return f"{raw} грн"


def escape(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def valid_url(value: str) -> bool:
    return value.startswith(("https://", "http://", "tg://"))


def valid_username(value: str) -> bool:
    return bool(re.fullmatch(r"@?[A-Za-z0-9_]{5,32}", value.strip()))


def valid_ton_address(value: str) -> bool:
    value = value.strip()
    # Friendly address (EQ/UQ + base64url) або raw workchain:hex.
    return bool(
        re.fullmatch(r"(?:EQ|UQ)[A-Za-z0-9_-]{46,48}", value)
        or re.fullmatch(r"-?\d:[0-9a-fA-F]{64}", value)
    )


def parse_usernames(raw: str) -> str:
    value = raw.strip()
    if value and not value.startswith("@"):
        value = "@" + value
    return value


def base_kb(*items: Button) -> InlineKeyboardMarkup:
    return make_keyboard(items)


async def setting(key: str, default: str = "") -> str:
    return await DB.get_setting(key, default)


async def price_text(key: str) -> str:
    return fmt_money(money_to_kop(await setting(key, "0")))


async def render_text(key: str, fallback: str, **values: Any) -> str:
    template = await setting(f"text:{key}", fallback)
    safe_values = {k: escape(v) for k, v in values.items()}
    safe_values.setdefault("brand", escape(await setting("brand", "SPANIX STARS")))
    try:
        return template.format_map(_SafeFormat(safe_values))
    except (KeyError, ValueError):
        return fallback.format_map(_SafeFormat(safe_values))


class _SafeFormat(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


async def admin_ids() -> list[int]:
    async with await DB.connect() as db:
        cur = await db.execute("SELECT user_id FROM admins")
        rows = await cur.fetchall()
    return list({int(row["user_id"]) for row in rows} | set(CONFIG.owner_ids))


async def has_admin(user_id: int, permission: str = "") -> bool:
    return await DB.is_admin(user_id, permission)


async def require_admin(callback: CallbackQuery, permission: str = "") -> bool:
    if not await has_admin(callback.from_user.id, permission):
        await callback.answer("Недостатньо прав для цієї дії.", show_alert=True)
        return False
    return True


async def notify_admins(bot: Bot, text: str, reply_markup: InlineKeyboardMarkup | None = None,
                        photo_file_id: str = "") -> None:
    for admin_id in await admin_ids():
        try:
            if photo_file_id:
                await bot.send_photo(admin_id, photo_file_id, caption=text, reply_markup=reply_markup)
            else:
                await bot.send_message(admin_id, text, reply_markup=reply_markup)
        except (TelegramForbiddenError, TelegramBadRequest):
            continue
        except Exception:
            log.exception("Не вдалося надіслати сповіщення адміну %s", admin_id)


async def ui_button(key: str, fallback: str, callback: str | None = None,
                    url: str | None = None) -> Button | None:
    async with await DB.connect() as db:
        cur = await db.execute("SELECT * FROM buttons WHERE key=?", (key,))
        row = await cur.fetchone()
    if row and not int(row["visible"]):
        return None
    label = row["label"] if row else fallback
    emoji = row["emoji"] if row else ""
    style = row["style"] if row else ""
    custom = row["custom_emoji_id"] if row else ""
    text = f"{emoji} {label}".strip()
    return Button(text=text, callback=callback, url=url, style=style, icon_custom_emoji_id=custom)


async def home_keyboard() -> InlineKeyboardMarkup:
    reviews_url = await setting("reviews_channel", "")
    if reviews_url.startswith("@"):
        reviews_url = "https://t.me/" + reviews_url[1:]
    support_url = await setting("support_url", "")
    items: list[Button] = []
    defaults = [
        ("main.stars", "Купити Stars", "buy:stars", None),
        ("main.ton", "Купити TON", "buy:ton", None),
        ("main.nft", "Продаж NFT", "catalog:nft", None),
        ("main.premium", "Premium", "buy:premium", None),
        ("main.games", "Донат у ігри", "catalog:game", None),
        ("main.accounts", "ТГ акаунти", "nav:accounts", None),
        ("main.reviews", "Відгуки", "nav:reviews", reviews_url if valid_url(reviews_url) else None),
        ("main.support", "Підтримка", "nav:support", support_url if valid_url(support_url) else None),
        ("main.profile", "Профіль", "nav:profile", None),
    ]
    for key, label, callback, url in defaults:
        item = await ui_button(key, label, callback=callback, url=url)
        if item:
            items.append(item)
    return make_keyboard(items)


async def edit_or_answer(message: Message, text: str,
                         markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        if message.photo:
            await message.edit_caption(caption=text, reply_markup=markup)
        else:
            await message.edit_text(text, reply_markup=markup)
    except (TelegramBadRequest, AttributeError):
        await message.answer(text, reply_markup=markup)


async def show_home(message: Message) -> None:
    brand = await setting("brand", "SPANIX STARS")
    fallback = (
        "Ласкаво просимо до <b>{brand}</b>!\n"
        "Тут ви можете придбати Telegram Stars, TON та цифрові товари за гривні.\n"
        "Приємних покупок!"
    )
    text = await render_text("menu.welcome", fallback, brand=brand)
    banner = await setting("banner:home", "")
    if banner:
        await message.answer_photo(banner, caption=text, reply_markup=await home_keyboard())
    else:
        await message.answer(text, reply_markup=await home_keyboard())


async def show_home_callback(callback: CallbackQuery) -> None:
    brand = await setting("brand", "SPANIX STARS")
    fallback = (
        "Ласкаво просимо до <b>{brand}</b>!\n"
        "Тут ви можете придбати Telegram Stars, TON та цифрові товари за гривні.\n"
        "Приємних покупок!"
    )
    text = await render_text("menu.welcome", fallback, brand=brand)
    banner = await setting("banner:home", "")
    if banner and not callback.message.photo:
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass
        await callback.message.answer_photo(banner, caption=text, reply_markup=await home_keyboard())
    else:
        await edit_or_answer(callback.message, text, await home_keyboard())
    await callback.answer()


async def pay_keyboard(order_id: int) -> InlineKeyboardMarkup:
    async with await DB.connect() as db:
        cur = await db.execute(
            "SELECT * FROM banks WHERE enabled=1 AND card_number!='' ORDER BY sort_order,id"
        )
        banks = await cur.fetchall()
    items = [Button(f"🏦 {row['name']}", callback=f"pay:bank:{order_id}:{row['id']}") for row in banks]
    items.append(Button("Скасувати", callback=f"order:cancel:{order_id}", style="danger"))
    return make_keyboard(items)


async def make_order_summary(bot: Bot, user_id: int, order_id: int) -> None:
    async with await DB.connect() as db:
        cur = await db.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, user_id))
        order = await cur.fetchone()
    if not order:
        return
    payload = json.loads(order["payload"])
    lines = [f"<b>Замовлення №{order_id}</b>", f"Товар: {escape(payload.get('title', order['type']))}"]
    for key, label in (("recipient", "Отримувач"), ("quantity", "Кількість"), ("wallet", "TON-гаманець"),
                       ("duration", "Тривалість"), ("details", "Дані")):
        if payload.get(key):
            lines.append(f"{label}: {escape(payload[key])}")
    lines.append(f"До сплати: <b>{fmt_money(order['amount_kop'])}</b>")
    lines.append("\nОберіть банк для оплати:")
    kb = await pay_keyboard(order_id)
    if len(kb.inline_keyboard) == 1 and kb.inline_keyboard[0][0].callback_data == f"order:cancel:{order_id}":
        lines.append("\nПоки що немає банків із заповненими реквізитами. Зверніться до підтримки або повідомте власника бота.")
    await bot.send_message(user_id, "\n".join(lines), reply_markup=kb)


async def create_order_and_show(bot: Bot, user_id: int, order_type: str,
                                payload: dict[str, Any], amount_kop: int) -> int:
    timeout = int(await setting("payment_timeout_minutes", "30"))
    if amount_kop <= 0:
        await bot.send_message(user_id, "Ціна цього товару ще не налаштована. Напишіть у підтримку.")
        return 0
    order_id = await DB.create_order(user_id, order_type, payload, amount_kop, timeout)
    await make_order_summary(bot, user_id, order_id)
    user = await DB.get_user(user_id)
    username = "@" + user["username"] if user and user["username"] else "без username"
    notice = (f"<b>Нове замовлення №{order_id}</b>\nКлієнт: {escape(username)} · <code>{user_id}</code>\n"
              f"Товар: {escape(payload.get('title', order_type))}\nСума: <b>{fmt_money(amount_kop)}</b>\nСтатус: очікує оплату")
    await notify_admins(bot, notice, base_kb(Button("Відкрити замовлення", callback=f"adm:order:view:{order_id}")))
    return order_id


async def get_order(order_id: int) -> aiosqlite.Row | None:
    async with await DB.connect() as db:
        cur = await db.execute("SELECT * FROM orders WHERE id=?", (order_id,))
        return await cur.fetchone()


# -------------------------------- user screens -------------------------------

@router.message(CommandStart())
async def start_handler(message: Message, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    args = (message.text or "").split(maxsplit=1)
    referrer = None
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referrer = int(args[1][4:])
        except ValueError:
            referrer = None
    await DB.register_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name or "",
        referrer,
    )
    user = await DB.get_user(message.from_user.id)
    if user and int(user["banned"]):
        await message.answer("Доступ до магазину тимчасово обмежено. Зверніться до підтримки.")
        return
    if await setting("maintenance", "0") == "1" and message.from_user.id not in CONFIG.owner_ids:
        await message.answer("Магазин тимчасово на технічному обслуговуванні. Спробуйте пізніше.")
        return
    await show_home(message)


@router.message(Command("admin"))
async def admin_command(message: Message, state: FSMContext) -> None:
    if not await has_admin(message.from_user.id):
        await message.answer("У вас немає доступу до адмін-панелі.")
        return
    await state.clear()
    await message.answer("<b>SPANIX STARS · Адмін-панель</b>\nОберіть розділ:", reply_markup=admin_keyboard())


def admin_keyboard() -> InlineKeyboardMarkup:
    names = [
        Button("📦 Замовлення", callback="adm:orders"),
        Button("💳 Оплати й банки", callback="adm:banks"),
        Button("💰 Ціни", callback="adm:prices"),
        Button("🗂 Каталоги", callback="adm:catalog"),
        Button("📝 Тексти", callback="adm:texts"),
        Button("🖼 Банери", callback="adm:banners"),
        Button("🎛 Кнопки", callback="adm:buttons"),
        Button("👁 Живе редагування меню", callback="adm:live"),
        Button("⭐ Відгуки", callback="adm:reviews"),
        Button("👥 Реферали", callback="adm:referrals"),
        Button("📣 Розсилка", callback="adm:broadcast"),
        Button("🔎 Користувачі", callback="adm:users"),
        Button("📊 Статистика / CSV", callback="adm:stats"),
        Button("⚙️ Налаштування", callback="adm:settings"),
        Button("🛡 Адміни", callback="adm:admins"),
        Button("🧰 Система / бекап", callback="adm:system"),
        Button("🏠 Головне меню", callback="nav:home"),
    ]
    return make_keyboard(names)


@router.callback_query(F.data == "nav:home")
async def nav_home(callback: CallbackQuery) -> None:
    await show_home_callback(callback)


@router.callback_query(F.data == "nav:profile")
async def nav_profile(callback: CallbackQuery, bot: Bot) -> None:
    user = await DB.get_user(callback.from_user.id)
    async with await DB.connect() as db:
        cur = await db.execute("SELECT COUNT(*) AS n FROM orders WHERE user_id=? AND status='done'", (callback.from_user.id,))
        count = int((await cur.fetchone())["n"])
    total = fmt_money(int(user["total_spent_kop"])) if user else "0,00 грн"
    ref_balance = fmt_money(int(user["ref_balance_kop"])) if user else "0,00 грн"
    text = (
        "<b>👤 Профіль</b>\n"
        f"ID: <code>{callback.from_user.id}</code>\n"
        f"Сума покупок: {total}\nКількість покупок: {count}\n"
        f"Реферальний баланс: {ref_balance}"
    )
    kb = make_keyboard([
        Button("👥 Реферальна система", callback="nav:referrals"),
        Button("💸 Вивести кошти", callback="ref:withdraw"),
        Button("💎 TON-гаманець", callback="profile:wallet"),
        Button("🧾 Чеки", callback="nav:checks"),
        Button("📄 Інструкція", callback="nav:instruction"),
        Button("🏠 Назад", callback="nav:home"),
    ])
    await edit_or_answer(callback.message, text, kb)
    await callback.answer()


@router.callback_query(F.data == "nav:checks")
async def nav_checks(callback: CallbackQuery) -> None:
    async with await DB.connect() as db:
        cur = await db.execute(
            "SELECT id,type,amount_kop,status,created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",
            (callback.from_user.id,),
        )
        rows = await cur.fetchall()
    if not rows:
        text = "Поки що у вас немає замовлень."
    else:
        statuses = {
            "awaiting_payment": "Очікує оплату", "paid_review": "Перевірка чека", "paid": "Оплачено",
            "in_progress": "У роботі", "done": "Виконано", "cancelled": "Скасовано",
            "rejected": "Відхилено", "refunded": "Повернення",
        }
        lines = ["<b>🧾 Останні замовлення</b>"]
        for row in rows:
            lines.append(
                f"№{row['id']} · {escape(row['type'])} · {fmt_money(row['amount_kop'])}\n"
                f"Статус: {statuses.get(row['status'], row['status'])} · {escape(row['created_at'][:16])}"
            )
        text = "\n\n".join(lines)
    await edit_or_answer(callback.message, text, base_kb(Button("🏠 Назад", callback="nav:profile")))
    await callback.answer()


@router.callback_query(F.data == "nav:instruction")
async def nav_instruction(callback: CallbackQuery) -> None:
    offer = await setting("offer_url", "")
    policy = await setting("policy_url", "")
    items = [Button("🏠 Назад", callback="nav:profile")]
    if valid_url(offer):
        items.insert(0, Button("Оферта", url=offer))
    if valid_url(policy):
        items.insert(0, Button("Політика", url=policy))
    text = (
        "<b>📄 Інструкція</b>\n"
        "1. Оберіть потрібний товар у головному меню.\n"
        "2. Вкажіть отримувача або необхідні дані.\n"
        "3. Оберіть банк, сплатіть точну суму та надішліть чек.\n"
        "4. Після перевірки адміністратор виконає замовлення.\n"
        "5. Статус можна переглянути у розділі «Чеки».\n"
        "6. Питання щодо замовлення — через «Підтримка»."
    )
    await edit_or_answer(callback.message, text, make_keyboard(items))
    await callback.answer()


@router.callback_query(F.data == "nav:accounts")
async def nav_accounts(callback: CallbackQuery) -> None:
    # Безпечна інформаційна сторінка: бот не збирає та не пересилає OTP-коди,
    # паролі, QR-коди входу чи інші секрети авторизації Telegram.
    support_url = await setting("support_url", "")
    items = []
    if valid_url(support_url):
        items.append(Button("Звернутися до підтримки", url=support_url))
    items.append(Button("Назад", callback="nav:home"))
    text = (
        "<b>📱 Telegram акаунти</b>\n\n"
        "Допомога з питаннями щодо Telegram акаунтів. Для безпеки ми не просимо "
        "номер телефону для входу, коди підтвердження, паролі чи QR-коди авторизації "
        "та не передаємо їх між користувачами.\n\n"
        "Нікому не надсилайте код входу — навіть якщо людина стверджує, що це потрібно "
        "для передачі акаунта. Скористайтеся офіційними налаштуваннями Telegram."
    )
    await edit_or_answer(callback.message, text, make_keyboard(items))
    await callback.answer()


@router.callback_query(F.data == "nav:reviews")
async def nav_reviews(callback: CallbackQuery) -> None:
    url = await setting("reviews_channel", "")
    if url.startswith("@"):
        url = "https://t.me/" + url[1:]
    if valid_url(url):
        await callback.message.answer("Канал відгуків: " + escape(url),
                                      reply_markup=base_kb(Button("Відкрити канал", url=url), Button("Назад", callback="nav:home")))
    else:
        await callback.answer("Канал відгуків ще не налаштований.", show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data == "nav:support")
async def nav_support(callback: CallbackQuery) -> None:
    url = await setting("support_url", "")
    if valid_url(url):
        await callback.message.answer("Зверніться до підтримки:", reply_markup=base_kb(Button("Відкрити підтримку", url=url)))
    else:
        await callback.answer("Посилання підтримки ще не налаштоване.", show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data == "buy:stars")
async def buy_stars(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    kb = make_keyboard([
        Button("Купити собі", callback="stars:self"),
        Button("Купити другу", callback="stars:friend"),
        Button("Назад", callback="nav:home"),
    ])
    await edit_or_answer(callback.message, "<b>⭐ Купити Stars</b>\nОберіть отримувача:", kb)
    await callback.answer()


async def ask_stars_quantity(callback: CallbackQuery, state: FSMContext, recipient: str) -> None:
    await state.update_data(recipient=recipient)
    minimum = await setting("min_stars", "50")
    maximum = await setting("max_stars", "25000")
    packages = [50, 100, 150, 250, 500, 1000, 2500]
    buttons = [Button(f"{p} ⭐", callback=f"stars:qty:{p}") for p in packages if int(minimum) <= p <= int(maximum)]
    buttons.extend([Button("Ввести кількість", callback="stars:custom"), Button("Назад", callback="buy:stars")])
    await state.set_state(Purchase.stars_quantity)
    await edit_or_answer(callback.message,
                         f"Отримувач: <b>{escape(recipient)}</b>\nОберіть пакет або введіть число від {minimum} до {maximum}:",
                         make_keyboard(buttons))


@router.callback_query(F.data == "stars:self")
async def stars_self(callback: CallbackQuery, state: FSMContext) -> None:
    username = callback.from_user.username
    if username:
        await ask_stars_quantity(callback, state, "@" + username)
    else:
        await state.set_state(Purchase.stars_recipient)
        await state.update_data(recipient_self=True)
        await edit_or_answer(callback.message, "У вашому Telegram немає username. Надішліть username отримувача у форматі @name:",
                             base_kb(Button("Назад", callback="buy:stars")))
        await callback.answer()


@router.callback_query(F.data == "stars:friend")
async def stars_friend(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Purchase.stars_recipient)
    await state.update_data(recipient_self=False)
    await edit_or_answer(callback.message, "Надішліть username отримувача у форматі @name:",
                         base_kb(Button("Назад", callback="buy:stars")))
    await callback.answer()


@router.message(Purchase.stars_recipient)
async def stars_recipient_input(message: Message, state: FSMContext) -> None:
    value = parse_usernames(message.text or "")
    if not valid_username(value.lstrip("@")):
        await message.answer("Некоректний username. Спробуйте ще раз, наприклад @username.")
        return
    await ask_stars_quantity_from_message(message, state, value)


async def ask_stars_quantity_from_message(message: Message, state: FSMContext, recipient: str) -> None:
    await state.update_data(recipient=recipient)
    await state.set_state(Purchase.stars_quantity)
    minimum = await setting("min_stars", "50")
    maximum = await setting("max_stars", "25000")
    buttons = [Button(f"{p} ⭐", callback=f"stars:qty:{p}") for p in [50, 100, 150, 250, 500, 1000, 2500]
              if int(minimum) <= p <= int(maximum)]
    buttons += [Button("Ввести кількість", callback="stars:custom"), Button("Назад", callback="nav:home")]
    await message.answer(f"Отримувач: <b>{escape(recipient)}</b>\nОберіть пакет або введіть число від {minimum} до {maximum}:",
                         reply_markup=make_keyboard(buttons))


@router.callback_query(F.data == "stars:custom")
async def stars_custom(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Purchase.stars_quantity)
    await edit_or_answer(callback.message, "Введіть цілу кількість Stars:", base_kb(Button("Назад", callback="buy:stars")))
    await callback.answer()


async def create_stars_order(bot: Bot, user_id: int, state: FSMContext, qty: int) -> None:
    data = await state.get_data()
    minimum = int(await setting("min_stars", "50"))
    maximum = int(await setting("max_stars", "25000"))
    if qty < minimum or qty > maximum:
        await bot.send_message(user_id, f"Кількість має бути від {minimum} до {maximum} ⭐.")
        return
    price = (decimal_value(await setting("price_star", "0.95")) * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    payload = {"title": f"Telegram Stars · {qty} ⭐", "recipient": data.get("recipient", ""), "quantity": qty}
    await state.clear()
    await create_order_and_show(bot, user_id, "stars", payload, money_to_kop(price))


@router.callback_query(F.data.startswith("stars:qty:"))
async def stars_package(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    try:
        qty = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("Некоректний пакет.", show_alert=True)
        return
    await callback.answer()
    await create_stars_order(bot, callback.from_user.id, state, qty)


@router.message(Purchase.stars_quantity)
async def stars_quantity_input(message: Message, bot: Bot, state: FSMContext) -> None:
    try:
        qty = int((message.text or "").strip())
    except ValueError:
        await message.answer("Введіть ціле число, наприклад 100.")
        return
    await create_stars_order(bot, message.from_user.id, state, qty)


@router.callback_query(F.data == "buy:ton")
async def buy_ton(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(Purchase.ton_amount)
    await edit_or_answer(callback.message,
                         f"<b>💎 Купити TON</b>\nВведіть кількість TON від {await setting('min_ton','0.1')} до {await setting('max_ton','100')}:",
                         base_kb(Button("Назад", callback="nav:home")))
    await callback.answer()


@router.message(Purchase.ton_amount)
async def ton_amount_input(message: Message, state: FSMContext) -> None:
    amount = decimal_value(message.text, "-1")
    minimum = decimal_value(await setting("min_ton", "0.1"))
    maximum = decimal_value(await setting("max_ton", "100"))
    if amount < minimum or amount > maximum:
        await message.answer(f"Кількість має бути від {minimum} до {maximum} TON.")
        return
    await state.update_data(ton_amount=str(amount))
    await state.set_state(Purchase.ton_wallet)
    user = await DB.get_user(message.from_user.id)
    wallet_text = f"\nЗбережений гаманець: <code>{escape(user['ton_wallet'])}</code>" if user and user["ton_wallet"] else ""
    await message.answer("Надішліть TON-гаманець отримувача." + wallet_text)


@router.message(Purchase.ton_wallet)
async def ton_wallet_input(message: Message, bot: Bot, state: FSMContext) -> None:
    wallet = (message.text or "").strip()
    if not valid_ton_address(wallet):
        await message.answer("Не вдалося розпізнати формат TON-адреси. Перевірте EQ/UQ або raw-формат.")
        return
    data = await state.get_data()
    ton_amount = decimal_value(data.get("ton_amount", "0"))
    rate = decimal_value(await setting("ton_price", "150"))
    amount_kop = money_to_kop(ton_amount * rate)
    await state.clear()
    await create_order_and_show(bot, message.from_user.id, "ton",
                                {"title": f"TON · {ton_amount}", "quantity": str(ton_amount), "wallet": wallet},
                                amount_kop)


@router.callback_query(F.data == "buy:premium")
async def buy_premium(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_answer(callback.message, "<b>👑 Telegram Premium</b>\nДля кого оформлюємо?",
                         make_keyboard([Button("Для себе", callback="premium:self"),
                                        Button("Для друга", callback="premium:friend"),
                                        Button("Назад", callback="nav:home")]))
    await callback.answer()


async def premium_months(callback: CallbackQuery, state: FSMContext, recipient: str) -> None:
    await state.update_data(recipient=recipient)
    items = [Button(f"{m} місяців", callback=f"premium:months:{m}") for m in (3, 6, 12)]
    items.append(Button("Назад", callback="buy:premium"))
    await edit_or_answer(callback.message, f"Отримувач: <b>{escape(recipient)}</b>\nОберіть тривалість:", make_keyboard(items))


@router.callback_query(F.data == "premium:self")
async def premium_self(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user.username:
        await state.set_state(Purchase.premium_recipient)
        await state.update_data(recipient_self=True)
        await edit_or_answer(callback.message, "У вас немає username. Надішліть username отримувача:")
        await callback.answer()
        return
    await premium_months(callback, state, "@" + callback.from_user.username)
    await callback.answer()


@router.callback_query(F.data == "premium:friend")
async def premium_friend(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Purchase.premium_recipient)
    await state.update_data(recipient_self=False)
    await edit_or_answer(callback.message, "Надішліть username отримувача у форматі @name:")
    await callback.answer()


@router.message(Purchase.premium_recipient)
async def premium_recipient_input(message: Message, state: FSMContext) -> None:
    recipient = parse_usernames(message.text or "")
    if not valid_username(recipient.lstrip("@")):
        await message.answer("Некоректний username. Спробуйте ще раз.")
        return
    await state.update_data(recipient=recipient)
    await state.set_state(None)
    items = [Button(f"{m} місяців", callback=f"premium:months:{m}") for m in (3, 6, 12)]
    await message.answer(f"Отримувач: <b>{escape(recipient)}</b>\nОберіть тривалість:", reply_markup=make_keyboard(items))


@router.callback_query(F.data.startswith("premium:months:"))
async def premium_create_order(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    try:
        months = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("Некоректна тривалість.", show_alert=True)
        return
    if months not in (3, 6, 12):
        await callback.answer("Некоректна тривалість.", show_alert=True)
        return
    data = await state.get_data()
    recipient = data.get("recipient", "")
    price = money_to_kop(await setting(f"premium_{months}", "0"))
    await state.clear()
    await callback.answer()
    await create_order_and_show(bot, callback.from_user.id, "premium",
                                {"title": f"Telegram Premium · {months} міс.", "recipient": recipient, "duration": f"{months} місяців"},
                                price)


@router.callback_query(F.data.startswith("catalog:"))
async def catalog_open(callback: CallbackQuery) -> None:
    category = callback.data.split(":", 1)[1]
    if category not in ("nft", "game"):
        await callback.answer("Невідомий каталог.", show_alert=True)
        return
    async with await DB.connect() as db:
        cur = await db.execute("SELECT * FROM products WHERE category=? AND active=1 ORDER BY id DESC", (category,))
        products = await cur.fetchall()
    title = "<b>🎁 Продаж NFT</b>" if category == "nft" else "<b>🎮 Донат у ігри</b>"
    if not products:
        await edit_or_answer(callback.message, title + "\nКаталог поки порожній. Завітайте пізніше.",
                             base_kb(Button("Назад", callback="nav:home")))
    else:
        items = [Button(f"{p['name']} · {fmt_money(p['price_kop'])}", callback=f"product:{p['id']}") for p in products]
        items.append(Button("Назад", callback="nav:home"))
        await edit_or_answer(callback.message, title + "\nОберіть товар:", make_keyboard(items))
    await callback.answer()


@router.callback_query(F.data.startswith("product:"))
async def product_open(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        product_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("Некоректний товар.", show_alert=True)
        return
    async with await DB.connect() as db:
        cur = await db.execute("SELECT * FROM products WHERE id=? AND active=1", (product_id,))
        product = await cur.fetchone()
    if not product:
        await callback.answer("Товар більше недоступний.", show_alert=True)
        return
    if int(product["stock"]) == 0:
        await callback.answer("Немає в наявності.", show_alert=True)
        return
    text = (f"<b>{escape(product['name'])}</b>\n{escape(product['description'])}\n"
            f"Ціна: <b>{fmt_money(product['price_kop'])}</b>\n"
            f"Наявність: {'необмежена' if int(product['stock']) < 0 else int(product['stock'])}")
    await state.update_data(product_id=product_id)
    if product["category"] == "game":
        fields = json.loads(product["fields"] or "[]")
        await state.set_state(Purchase.game_fields)
        fields_text = ", ".join(fields) if fields else "Додаткові дані не потрібні"
        text += f"\n\nВведіть дані для поповнення ({escape(fields_text)})."
        kb = base_kb(Button("Назад", callback=f"catalog:{product['category']}"))
    else:
        await state.set_state(Purchase.game_fields)
        text += "\n\nВведіть username отримувача або напишіть <code>self</code>, якщо товар для вас."
        kb = base_kb(Button("Назад", callback=f"catalog:{product['category']}"))
    await edit_or_answer(callback.message, text, kb)
    await callback.answer()


@router.message(Purchase.game_fields)
async def product_fields_input(message: Message, bot: Bot, state: FSMContext) -> None:
    data = await state.get_data()
    product_id = int(data.get("product_id", 0))
    async with await DB.connect() as db:
        cur = await db.execute("SELECT * FROM products WHERE id=? AND active=1", (product_id,))
        product = await cur.fetchone()
    if not product:
        await state.clear()
        await message.answer("Товар більше недоступний.")
        return
    value = (message.text or "").strip()
    if not value:
        await message.answer("Надішліть дані текстом.")
        return
    if product["category"] == "nft":
        recipient = "Для себе" if value.lower() == "self" else parse_usernames(value)
        if recipient != "Для себе" and not valid_username(recipient.lstrip("@")):
            await message.answer("Введіть username у форматі @name або self.")
            return
        details = recipient
    else:
        fields = json.loads(product["fields"] or "[]")
        if fields and len([x for x in value.split(",") if x.strip()]) < len(fields):
            await message.answer("Заповніть усі поля: " + ", ".join(fields))
            return
        details = value
    await state.clear()
    await create_order_and_show(bot, message.from_user.id, product["category"],
                                {"title": product["name"], "details": details, "product_id": product_id},
                                int(product["price_kop"]))


# --------------------------------- referrals ---------------------------------

@router.callback_query(F.data == "nav:referrals")
async def referrals_screen(callback: CallbackQuery, bot: Bot) -> None:
    user = await DB.get_user(callback.from_user.id)
    ref_count = 0
    async with await DB.connect() as db:
        cur = await db.execute("SELECT COUNT(*) AS n FROM users WHERE referred_by=?", (callback.from_user.id,))
        ref_count = int((await cur.fetchone())["n"])
    me = await bot.get_me()
    percent = await setting("referral_percent", "5")
    balance = int(user["ref_balance_kop"]) if user else 0
    earned = int(user["ref_earned_kop"]) if user else 0
    link = f"https://t.me/{me.username}?start=ref_{callback.from_user.id}"
    text = (
        "<b>👥 Реферальна система</b>\n"
        f"Запрошуйте друзів і отримуйте {escape(percent)}% від їхніх успішних покупок Stars та TON.\n\n"
        f"Ваше посилання:\n<code>{escape(link)}</code>\n\n"
        f"Запрошено: {ref_count}\nЗароблено: {fmt_money(earned)}\nБаланс: {fmt_money(balance)}"
    )
    await edit_or_answer(callback.message, text,
                         make_keyboard([Button("💸 Вивести кошти", callback="ref:withdraw"), Button("Назад", callback="nav:profile")]))
    await callback.answer()


@router.callback_query(F.data == "ref:withdraw")
async def referral_withdraw(callback: CallbackQuery, state: FSMContext) -> None:
    user = await DB.get_user(callback.from_user.id)
    min_amount = decimal_value(await setting("referral_min_withdraw", "100"))
    if not user or int(user["ref_balance_kop"]) < money_to_kop(min_amount):
        await callback.answer(f"Мінімальна сума виведення — {min_amount} грн.", show_alert=True)
        return
    await state.set_state(Purchase.withdraw_amount)
    await edit_or_answer(callback.message, f"Введіть суму для виведення (мінімум {min_amount} грн).")
    await callback.answer()


@router.message(Purchase.withdraw_amount)
async def withdraw_amount_input(message: Message, state: FSMContext) -> None:
    amount_kop = money_to_kop(message.text or "0")
    user = await DB.get_user(message.from_user.id)
    minimum = money_to_kop(await setting("referral_min_withdraw", "100"))
    if amount_kop < minimum or not user or amount_kop > int(user["ref_balance_kop"]):
        await message.answer("Сума менша за мінімум або перевищує доступний баланс. Введіть іншу суму.")
        return
    await state.update_data(withdraw_amount_kop=amount_kop)
    await state.set_state(Purchase.withdraw_details)
    await message.answer("Надішліть реквізити для виплати (картка або TON-гаманець).")


@router.message(Purchase.withdraw_details)
async def withdraw_details_input(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    amount_kop = int(data.get("withdraw_amount_kop", 0))
    user = await DB.get_user(message.from_user.id)
    if not user or amount_kop > int(user["ref_balance_kop"]):
        await state.clear()
        await message.answer("Баланс змінився. Спробуйте ще раз.")
        return
    async with await DB.connect() as db:
        cur = await db.execute(
            "INSERT INTO withdrawals(user_id,amount_kop,details,created_at) VALUES(?,?,?,?)",
            (message.from_user.id, amount_kop, (message.text or "")[:500], now_iso()),
        )
        wid = cur.lastrowid
        await db.commit()
    await state.clear()
    await message.answer("Заявку на виведення надіслано адміністратору.")
    await notify_admins(bot, f"<b>Заявка на виведення №{wid}</b>\nКористувач: <code>{message.from_user.id}</code>\nСума: {fmt_money(amount_kop)}\nРеквізити: {escape(message.text)}",
                        make_keyboard([Button("Підтвердити", callback=f"adm:withdraw:ok:{wid}"),
                                       Button("Відхилити", callback=f"adm:withdraw:no:{wid}")]))


# ----------------------------- wallet and account ----------------------------

@router.callback_query(F.data == "profile:wallet")
async def wallet_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Purchase.wallet)
    await edit_or_answer(callback.message, "Надішліть свою TON-адресу або слово <code>delete</code>, щоб видалити збережену адресу.")
    await callback.answer()


@router.message(Purchase.wallet)
async def wallet_save(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if value.lower() == "delete":
        value = ""
    elif not valid_ton_address(value):
        await message.answer("Некоректна TON-адреса. Спробуйте ще раз.")
        return
    async with await DB.connect() as db:
        await db.execute("UPDATE users SET ton_wallet=? WHERE user_id=?", (value, message.from_user.id))
        await db.commit()
    await state.clear()
    await message.answer("TON-гаманець оновлено.", reply_markup=base_kb(Button("До профілю", callback="nav:profile")))


# --------------------------------- payments ----------------------------------

@router.callback_query(F.data.startswith("pay:bank:"))
async def payment_bank(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Некоректні дані.", show_alert=True)
        return
    order_id, bank_id = int(parts[2]), int(parts[3])
    order = await get_order(order_id)
    if not order or int(order["user_id"]) != callback.from_user.id or order["status"] != "awaiting_payment":
        await callback.answer("Замовлення недоступне.", show_alert=True)
        return
    async with await DB.connect() as db:
        cur = await db.execute("SELECT * FROM banks WHERE id=? AND enabled=1", (bank_id,))
        bank = await cur.fetchone()
    if not bank or not bank["card_number"]:
        await callback.answer("Ці реквізити недоступні.", show_alert=True)
        return
    async with await DB.connect() as db:
        await db.execute("UPDATE orders SET bank_id=? WHERE id=?", (bank_id, order_id))
        await db.commit()
    timeout = int(await setting("payment_timeout_minutes", "30"))
    text = (
        f"<b>Оплата замовлення №{order_id}</b>\n"
        f"Банк: <b>{escape(bank['name'])}</b>\n"
        f"Картка: <code>{escape(bank['card_number'])}</code>\n"
        f"Отримувач: <b>{escape(bank['cardholder'])}</b>\n"
        f"Сума: <b>{fmt_money(order['amount_kop'])}</b>\n"
        f"Коментар: <code>SPANIX-{order_id}</code>\n"
        f"Час на оплату: {timeout} хв.\n\n"
        f"{await render_text('payment.instruction', 'Після оплати натисніть «Я оплатив(ла)» та надішліть чек.') }"
    )
    await edit_or_answer(callback.message, text,
                         make_keyboard([Button("✅ Я оплатив(ла)", callback=f"pay:paid:{order_id}", style="success"),
                                        Button("Змінити банк", callback=f"pay:change:{order_id}"),
                                        Button("Скасувати", callback=f"order:cancel:{order_id}", style="danger")]))
    await callback.answer()


@router.callback_query(F.data.startswith("pay:change:"))
async def payment_change(callback: CallbackQuery) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    order = await get_order(order_id)
    if not order or int(order["user_id"]) != callback.from_user.id or order["status"] != "awaiting_payment":
        await callback.answer("Замовлення недоступне.", show_alert=True)
        return
    await edit_or_answer(callback.message, "Оберіть інший банк:", await pay_keyboard(order_id))
    await callback.answer()


@router.callback_query(F.data.startswith("pay:paid:"))
async def payment_paid(callback: CallbackQuery, state: FSMContext) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    order = await get_order(order_id)
    if not order or int(order["user_id"]) != callback.from_user.id or order["status"] != "awaiting_payment":
        await callback.answer("Замовлення недоступне або вже обробляється.", show_alert=True)
        return
    await state.set_state(Purchase.receipt)
    await state.update_data(order_id=order_id)
    await callback.message.answer("Надішліть фото або файл квитанції. Переконайтеся, що видно суму й час переказу.")
    await callback.answer()


@router.message(Purchase.receipt)
async def payment_receipt(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document:
        file_id = message.document.file_id
    else:
        await message.answer("Потрібно надіслати фото або файл квитанції.")
        return
    data = await state.get_data()
    order_id = int(data.get("order_id", 0))
    order = await get_order(order_id)
    if not order or order["status"] != "awaiting_payment":
        await state.clear()
        await message.answer("Це замовлення вже не очікує оплату.")
        return
    receipt_hash = hashlib.sha256(file_id.encode()).hexdigest()
    async with await DB.connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT order_id FROM receipt_hashes WHERE hash=?", (receipt_hash,))
        duplicate = await cur.fetchone()
        if duplicate:
            await db.rollback()
            await message.answer(f"Цей чек уже прикріплено до замовлення №{duplicate['order_id']}. Надішліть інший файл або зверніться до підтримки.")
            return
        cur = await db.execute(
            "UPDATE orders SET status='paid_review',receipt_file_id=?,receipt_hash=? WHERE id=? AND status='awaiting_payment'",
            (file_id, receipt_hash, order_id),
        )
        if cur.rowcount == 0:
            await db.rollback()
            await state.clear()
            await message.answer("Замовлення вже обробляється. Повторно надсилати чек не потрібно.")
            return
        await db.execute("INSERT INTO receipt_hashes(hash,order_id,user_id,created_at) VALUES(?,?,?,?)",
                         (receipt_hash, order_id, message.from_user.id, now_iso()))
        await db.commit()
    await state.clear()
    await message.answer(f"Чек для замовлення №{order_id} передано на перевірку. Очікуйте повідомлення.")
    payload = json.loads(order["payload"])
    admin_text = (
        f"<b>Нова оплата · замовлення №{order_id}</b>\n"
        f"Клієнт: @{escape(message.from_user.username or 'без username')} (<code>{message.from_user.id}</code>)\n"
        f"Товар: {escape(payload.get('title', order['type']))}\n"
        f"Сума: <b>{fmt_money(order['amount_kop'])}</b>\n"
        f"Перевірте чек і звірте платіж у банківському застосунку."
    )
    kb = make_keyboard([
        Button("✅ Підтвердити оплату", callback=f"adm:order:approve:{order_id}", style="success"),
        Button("❌ Відхилити", callback=f"adm:order:reject:{order_id}", style="danger"),
    ])
    if message.photo:
        await notify_admins(bot, admin_text, kb, file_id)
    else:
        for admin_id in await admin_ids():
            try:
                await bot.send_document(admin_id, file_id, caption=admin_text, reply_markup=kb)
            except (TelegramForbiddenError, TelegramBadRequest):
                continue
            except Exception:
                log.exception("Не вдалося надіслати чек адміну %s", admin_id)


@router.callback_query(F.data.startswith("order:cancel:"))
async def order_cancel(callback: CallbackQuery) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    order = await get_order(order_id)
    if not order or int(order["user_id"]) != callback.from_user.id or order["status"] != "awaiting_payment":
        await callback.answer("Замовлення вже не можна скасувати.", show_alert=True)
        return
    async with await DB.connect() as db:
        await db.execute("UPDATE orders SET status='cancelled' WHERE id=?", (order_id,))
        await db.commit()
    await edit_or_answer(callback.message, f"Замовлення №{order_id} скасовано.", base_kb(Button("Головне меню", callback="nav:home")))
    await callback.answer()


# -------------------------------- review flow --------------------------------

async def start_review_flow(bot: Bot, user_id: int, order_id: int) -> None:
    text = await setting("text:review.ask", "Дякуємо за покупку! Оцініть, будь ласка, замовлення.")
    await bot.send_message(user_id, text, reply_markup=make_keyboard([
        Button("1 ⭐", callback=f"review:rate:{order_id}:1"),
        Button("2 ⭐", callback=f"review:rate:{order_id}:2"),
        Button("3 ⭐", callback=f"review:rate:{order_id}:3"),
        Button("4 ⭐", callback=f"review:rate:{order_id}:4"),
        Button("5 ⭐", callback=f"review:rate:{order_id}:5"),
    ]))


@router.callback_query(F.data.startswith("review:rate:"))
async def review_rate(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, order_part, rating_part = callback.data.split(":")
    order_id, rating = int(order_part), int(rating_part)
    order = await get_order(order_id)
    if not order or int(order["user_id"]) != callback.from_user.id or order["status"] != "done":
        await callback.answer("Відгук для цього замовлення недоступний.", show_alert=True)
        return
    await state.set_state(Purchase.review_comment)
    await state.update_data(review_order_id=order_id, review_rating=rating)
    await callback.message.answer("Напишіть короткий коментар до покупки. Можна натиснути «Пропустити».",
                                  reply_markup=base_kb(Button("Пропустити", callback=f"review:skipcomment:{order_id}")))
    await callback.answer()


@router.callback_query(F.data.startswith("review:skipcomment:"))
async def review_skip_comment(callback: CallbackQuery, state: FSMContext) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    if await setting("review_comment_required", "0") == "1":
        await callback.answer("Коментар обов'язковий.", show_alert=True)
        return
    await state.update_data(review_order_id=order_id, review_comment="")
    await proceed_review_photo(callback.message, state)
    await callback.answer()


@router.message(Purchase.review_comment)
async def review_comment_input(message: Message, state: FSMContext) -> None:
    comment = (message.text or "").strip()[:1500]
    if not comment and await setting("review_comment_required", "0") == "1":
        await message.answer("Будь ласка, додайте коментар.")
        return
    await state.update_data(review_comment=comment)
    await proceed_review_photo(message, state)


async def proceed_review_photo(message: Message, state: FSMContext) -> None:
    if await setting("review_photo_required", "0") == "1":
        await state.set_state(Purchase.review_photo)
        await message.answer("Надішліть скриншот покупки.")
    else:
        await state.set_state(Purchase.review_photo)
        await message.answer("Можете надіслати скриншот покупки або пропустити.",
                             reply_markup=base_kb(Button("Пропустити", callback="review:skipphoto")))


@router.callback_query(F.data == "review:skipphoto")
async def review_skip_photo(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if await setting("review_photo_required", "0") == "1":
        await callback.answer("Скриншот обов'язковий.", show_alert=True)
        return
    await save_review(callback.from_user.id, state, bot, "")
    await callback.answer("Відгук надіслано на модерацію.", show_alert=True)


@router.message(Purchase.review_photo)
async def review_photo_input(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.photo:
        await message.answer("Надішліть зображення або натисніть «Пропустити», якщо це дозволено.")
        return
    await save_review(message.from_user.id, state, bot, message.photo[-1].file_id)
    await message.answer("Дякуємо! Відгук надіслано на модерацію.")


async def save_review(user_id: int, state: FSMContext, bot: Bot, photo_id: str) -> None:
    data = await state.get_data()
    order_id = int(data.get("review_order_id", 0))
    rating = int(data.get("review_rating", 5))
    comment = str(data.get("review_comment", ""))
    async with await DB.connect() as db:
        cur = await db.execute("SELECT id FROM reviews WHERE order_id=?", (order_id,))
        if await cur.fetchone():
            await state.clear()
            return
        cur = await db.execute(
            "INSERT INTO reviews(order_id,user_id,rating,comment,photo_file_id,status,created_at) VALUES(?,?,?,?,?,'pending',?)",
            (order_id, user_id, rating, comment, photo_id, now_iso()),
        )
        review_id = int(cur.lastrowid)
        await db.commit()
    await state.clear()
    order = await get_order(order_id)
    payload = json.loads(order["payload"]) if order else {}
    review_text = (
        f"<b>Новий відгук №{review_id}</b>\nКористувач: <code>{user_id}</code>\n"
        f"Замовлення: №{order_id} · {escape(payload.get('title', ''))}\n"
        f"Оцінка: {'⭐' * rating}\nКоментар: {escape(comment or 'Без коментаря')}"
    )
    kb = make_keyboard([
        Button("✅ Опублікувати", callback=f"adm:review:approve:{review_id}", style="success"),
        Button("❌ Відхилити", callback=f"adm:review:reject:{review_id}", style="danger"),
        Button("🔢 Номер", callback=f"adm:review:number:{review_id}"),
    ])
    await notify_admins(bot, review_text, kb, photo_id)


# --------------------------------- admin UI ----------------------------------

@router.callback_query(F.data.startswith("adm:"))
async def admin_callback_router(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = callback.data.split(":")
    action = data[1] if len(data) > 1 else ""
    if action in {"orders", "order"}:
        required = "orders"
    elif action in {"reviews", "review"}:
        required = "reviews"
    elif action == "back":
        required = ""
    else:
        required = "owner"
    if not await require_admin(callback, required):
        return

    if action == "back":
        await edit_or_answer(callback.message, "<b>SPANIX STARS · Адмін-панель</b>\nОберіть розділ:", admin_keyboard())
        await callback.answer()
        return

    if action == "live":
        live_items = []
        for key, label in [
            ("main.stars", "Купити Stars"), ("main.ton", "Купити TON"),
            ("main.nft", "Продаж NFT"), ("main.premium", "Premium"),
            ("main.games", "Донат у ігри"), ("main.accounts", "ТГ акаунти"),
            ("main.reviews", "Відгуки"), ("main.support", "Підтримка"),
            ("main.profile", "Профіль"),
        ]:
            item = await ui_button(key, label, callback=f"adm:button:{key}")
            if item:
                live_items.append(item)
        live_items.append(Button("Готово", callback="adm:back"))
        await edit_or_answer(callback.message, "<b>Живе редагування головного меню</b>\nНатисніть кнопку, яку потрібно змінити. Користувацькі дії тут вимкнені.", make_keyboard(live_items))
        await callback.answer()
        return

    if action == "orders":
        if len(data) == 2:
            await edit_or_answer(callback.message, "<b>📦 Замовлення</b>\nОберіть статус:", make_keyboard([
                Button("Очікують оплату", callback="adm:orders:awaiting_payment"),
                Button("Чеки на перевірку", callback="adm:orders:paid_review"),
                Button("Оплачені", callback="adm:orders:paid"),
                Button("У роботі", callback="adm:orders:in_progress"),
                Button("Виконані", callback="adm:orders:done"),
                Button("Скасовані / відхилені", callback="adm:orders:cancelled"),
                Button("Назад", callback="adm:back"),
            ]))
            await callback.answer()
            return
        status = data[2]
        async with await DB.connect() as db:
            cur = await db.execute("SELECT * FROM orders WHERE status=? ORDER BY id DESC LIMIT 15", (status,))
            rows = await cur.fetchall()
        if not rows:
            text = "У цьому статусі замовлень немає."
            kb = base_kb(Button("До адмінки", callback="adm:back"))
        else:
            lines = [f"<b>Замовлення · {escape(status)}</b>"]
            items = []
            for row in rows:
                payload = json.loads(row["payload"])
                lines.append(f"№{row['id']} · {escape(payload.get('title', row['type']))} · {fmt_money(row['amount_kop'])} · ID {row['user_id']}")
                items.append(Button(f"№{row['id']} · {payload.get('title', row['type'])[:24]}", callback=f"adm:order:view:{row['id']}"))
            items.append(Button("Назад", callback="adm:back"))
            text, kb = "\n".join(lines), make_keyboard(items)
        await edit_or_answer(callback.message, text, kb)
        await callback.answer()
        return

    if action == "order" and len(data) >= 4 and data[2] == "view":
        order_id = int(data[3])
        order = await get_order(order_id)
        if not order:
            await callback.answer("Замовлення не знайдено.", show_alert=True)
            return
        payload = json.loads(order["payload"])
        text = (f"<b>Замовлення №{order_id}</b>\nКористувач: <code>{order['user_id']}</code>\n"
                f"Тип: {escape(order['type'])}\nТовар: {escape(payload.get('title',''))}\n"
                f"Сума: {fmt_money(order['amount_kop'])}\nСтатус: {escape(order['status'])}\n"
                f"Дані: {escape(json.dumps(payload, ensure_ascii=False))}\nСтворено: {escape(order['created_at'])}")
        items = []
        if order["status"] == "paid_review":
            items += [Button("Підтвердити оплату", callback=f"adm:order:approve:{order_id}", style="success"),
                      Button("Відхилити", callback=f"adm:order:reject:{order_id}", style="danger")]
        if order["status"] in ("paid", "in_progress"):
            items += [Button("Взяти в роботу", callback=f"adm:order:work:{order_id}"),
                      Button("Виконано", callback=f"adm:order:done:{order_id}", style="success")]
        items.append(Button("Назад", callback="adm:orders"))
        await edit_or_answer(callback.message, text, make_keyboard(items))
        await callback.answer()
        return

    if action == "order" and len(data) >= 4:
        order_action, order_id = data[2], int(data[3])
        order = await get_order(order_id)
        if not order:
            await callback.answer("Замовлення не знайдено.", show_alert=True)
            return
        if order_action == "approve":
            if order["status"] != "paid_review":
                await callback.answer("Статус уже змінено.", show_alert=True)
                return
            async with await DB.connect() as db:
                cur = await db.execute("UPDATE orders SET status='paid',paid_at=?,admin_id=? WHERE id=? AND status='paid_review'",
                                       (now_iso(), callback.from_user.id, order_id))
                await db.commit()
            if cur.rowcount == 0:
                await callback.answer("Замовлення вже обробляється.", show_alert=True)
                return
            await DB.audit(callback.from_user.id, "order.approve", f"order={order_id}")
            try:
                await bot.send_message(order["user_id"], f"Оплату за замовлення №{order_id} підтверджено. Замовлення передано в роботу.")
            except TelegramForbiddenError:
                pass
            await callback.message.answer(f"Оплату №{order_id} підтверджено.",
                                          reply_markup=base_kb(Button("Взяти в роботу", callback=f"adm:order:work:{order_id}"),
                                                               Button("Виконано", callback=f"adm:order:done:{order_id}")))
            await callback.answer("Оплату підтверджено.")
            return
        if order_action == "reject":
            await state.set_state(AdminFlow.reject_reason)
            await state.update_data(reject_order_id=order_id)
            await callback.message.answer(f"Введіть причину відхилення оплати для замовлення №{order_id}:")
            await callback.answer()
            return
        if order_action == "work":
            if order["status"] not in ("paid", "in_progress"):
                await callback.answer("Замовлення ще не оплачене.", show_alert=True)
                return
            async with await DB.connect() as db:
                await db.execute("UPDATE orders SET status='in_progress',admin_id=? WHERE id=?", (callback.from_user.id, order_id))
                await db.commit()
            await DB.audit(callback.from_user.id, "order.take", f"order={order_id}")
            try:
                await bot.send_message(order["user_id"], f"Замовлення №{order_id} взято в роботу.")
            except TelegramForbiddenError:
                pass
            await callback.answer("Взято в роботу.")
            return
        if order_action == "done":
            if order["status"] not in ("paid", "in_progress"):
                await callback.answer("Спочатку підтвердіть оплату.", show_alert=True)
                return
            await complete_order(order_id, callback.from_user.id, bot)
            await callback.answer("Замовлення позначено виконаним.")
            return

    if action == "banks":
        async with await DB.connect() as db:
            cur = await db.execute("SELECT * FROM banks ORDER BY sort_order,id")
            rows = await cur.fetchall()
        items = [Button(f"{'🟢' if row['enabled'] else '⚪'} {row['name']}", callback=f"adm:bank:{row['id']}") for row in rows]
        items += [Button("➕ Додати банк", callback="adm:bankadd"),
                  Button("📌 Один реквізит на всі картки", callback="adm:bankall"),
                  Button("Назад", callback="adm:back")]
        await edit_or_answer(callback.message, "<b>💳 Оплати й реквізити</b>\nОберіть банк:", make_keyboard(items))
        await callback.answer()
        return

    if action == "bank" and len(data) == 3:
        bank_id = int(data[2])
        async with await DB.connect() as db:
            cur = await db.execute("SELECT * FROM banks WHERE id=?", (bank_id,))
            row = await cur.fetchone()
        if not row:
            await callback.answer("Банк не знайдено.", show_alert=True)
            return
        card = row["card_number"] or "не задано"
        masked = card if card == "не задано" else "•••• " + card[-4:]
        text = (f"<b>{escape(row['name'])}</b>\nРеквізити: {escape(masked)}\n"
                f"Отримувач: {escape(row['cardholder'] or 'не задано')}\nКоментар: {escape(row['comment'] or '—')}\n"
                f"Статус: {'увімкнено' if row['enabled'] else 'вимкнено'}")
        kb = make_keyboard([Button("Змінити реквізити", callback=f"adm:bankedit:{bank_id}"),
                            Button("Увімкнути / вимкнути", callback=f"adm:banktoggle:{bank_id}"),
                            Button("Перейменувати", callback=f"adm:bankrename:{bank_id}"),
                            Button("Назад", callback="adm:banks")])
        await edit_or_answer(callback.message, text, kb)
        await callback.answer()
        return

    if action == "bankedit":
        bank_id = int(data[2])
        await state.set_state(AdminFlow.bank_details)
        await state.update_data(bank_id=bank_id)
        await callback.message.answer("Надішліть одним повідомленням: номер картки; ПІБ отримувача; коментар. Наприклад: 4444...; Іван Іванов; Оплата замовлення")
        await callback.answer()
        return

    if action == "banktoggle":
        bank_id = int(data[2])
        async with await DB.connect() as db:
            await db.execute("UPDATE banks SET enabled=CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (bank_id,))
            await db.commit()
        await DB.audit(callback.from_user.id, "bank.toggle", f"bank={bank_id}")
        await callback.answer("Статус банку змінено.")
        await callback.message.answer("Готово. Поверніться до списку банків.", reply_markup=base_kb(Button("Банки", callback="adm:banks")))
        return

    if action == "bankrename":
        await state.set_state(AdminFlow.bank_name)
        await state.update_data(bank_id=int(data[2]))
        await callback.message.answer("Надішліть нову назву банку:")
        await callback.answer()
        return

    if action == "bankadd":
        await state.set_state(AdminFlow.bank_name)
        await state.update_data(bank_id=0)
        await callback.message.answer("Введіть назву нового банку:")
        await callback.answer()
        return

    if action == "bankall":
        await state.set_state(AdminFlow.shared_card)
        await callback.message.answer("Надішліть реквізити в форматі: номер картки; ПІБ отримувача; коментар. Вони будуть застосовані до всіх банків після вашого підтвердження.")
        await callback.answer()
        return

    if action == "bankallconfirm":
        data_state = await state.get_data()
        card = data_state.get("shared_card", "")
        if not card:
            await callback.answer("Спочатку введіть реквізити.", show_alert=True)
            return
        async with await DB.connect() as db:
            cur = await db.execute("SELECT COUNT(*) AS n FROM banks")
            count = int((await cur.fetchone())["n"])
            await db.execute("UPDATE banks SET card_number=?,cardholder=?,comment=?",
                             (card, data_state.get("shared_holder", ""), data_state.get("shared_comment", "")))
            await db.commit()
        await DB.audit(callback.from_user.id, "bank.apply_all", f"banks={count};card=****{card[-4:]}")
        await state.clear()
        await callback.message.answer(f"Реквізити застосовано до {count} банків. За потреби кожен банк можна відредагувати окремо.")
        await callback.answer("Застосовано.")
        return

    if action == "bankallcancel":
        await state.clear()
        await callback.message.answer("Масову зміну реквізитів скасовано.")
        await callback.answer()
        return

    if action == "prices":
        price_keys = [
            ("price_star", "Ціна за 1 ⭐"), ("min_stars", "Мін. Stars"), ("max_stars", "Макс. Stars"),
            ("ton_price", "Курс TON у грн"), ("min_ton", "Мін. TON"), ("max_ton", "Макс. TON"),
            ("premium_3", "Premium 3 міс."), ("premium_6", "Premium 6 міс."),
            ("premium_12", "Premium 12 міс."), ("referral_percent", "Реферальний %"),
        ]
        items = [Button(label, callback=f"adm:set:{key}") for key, label in price_keys]
        items.append(Button("Назад", callback="adm:back"))
        await edit_or_answer(callback.message, "<b>💰 Ціни</b>\nУсі суми задаються у гривнях. Оберіть параметр:", make_keyboard(items))
        await callback.answer()
        return

    if action == "set":
        key = data[2]
        await state.set_state(AdminFlow.edit_setting)
        await state.update_data(setting_key=key)
        current = await setting(key, "не задано")
        await callback.message.answer(f"Поточне значення <code>{escape(key)}</code>: <b>{escape(current)}</b>\nНадішліть нове значення:")
        await callback.answer()
        return

    if action == "banners":
        current = await setting("banner:home", "")
        await edit_or_answer(callback.message,
                             "<b>🖼 Фото й банери</b>\nГоловне меню: " + ("банер додано" if current else "без банера"),
                             make_keyboard([Button("Змінити банер головного меню", callback="adm:banner:home"),
                                            Button("Видалити банер", callback="adm:bannerdelete:home"),
                                            Button("Назад", callback="adm:back")]))
        await callback.answer()
        return

    if action == "banner" and len(data) == 3:
        await state.set_state(AdminFlow.banner_photo)
        await state.update_data(banner_key=data[2])
        await callback.message.answer("Надішліть фото банера. Щоб прибрати банер, натисніть «Видалити» в попередньому меню.")
        await callback.answer()
        return

    if action == "bannerdelete" and len(data) == 3:
        await DB.set_setting(f"banner:{data[2]}", "")
        await DB.audit(callback.from_user.id, "banner.delete", data[2])
        await callback.answer("Банер видалено.")
        await callback.message.answer("Банер видалено.", reply_markup=base_kb(Button("До банерів", callback="adm:banners")))
        return

    if action == "settings":
        keys = [
            ("brand", "Назва бренду"), ("support_url", "Посилання підтримки"),
            ("reviews_channel", "Канал відгуків"), ("news_channel", "Канал новин"),
            ("offer_url", "Оферта"), ("policy_url", "Політика"),
            ("payment_timeout_minutes", "Таймер оплати, хв"), ("maintenance", "Техобслуговування 0/1"),
            ("backup_enabled", "Автобекап 0/1"), ("referral_enabled", "Реферали 0/1"),
            ("referral_min_withdraw", "Мін. виведення, грн"),
            ("review_comment_required", "Коментар у відгуку обов'язковий 0/1"),
            ("review_photo_required", "Фото у відгуку обов'язкове 0/1"),
            ("review_mask_names", "Маскувати імена у відгуках 0/1"),
        ]
        items = [Button(label, callback=f"adm:set:{key}") for key, label in keys]
        items.append(Button("Назад", callback="adm:back"))
        await edit_or_answer(callback.message, "<b>⚙️ Налаштування магазину</b>", make_keyboard(items))
        await callback.answer()
        return

    if action == "catalog":
        await edit_or_answer(callback.message, "<b>🗂 Каталоги</b>\nОберіть каталог:", make_keyboard([
            Button("Продаж NFT", callback="adm:products:nft"),
            Button("Донат у ігри", callback="adm:products:game"),
            Button("Додати NFT", callback="adm:productadd:nft"),
            Button("Додати ігровий пакет", callback="adm:productadd:game"),
            Button("Назад", callback="adm:back"),
        ]))
        await callback.answer()
        return

    if action == "products":
        category = data[2]
        async with await DB.connect() as db:
            cur = await db.execute("SELECT * FROM products WHERE category=? ORDER BY id DESC LIMIT 30", (category,))
            rows = await cur.fetchall()
        if not rows:
            text = "У цьому каталозі поки немає товарів."
        else:
            text = "<b>Товари каталогу</b>\n" + "\n".join(
                f"#{r['id']} · {escape(r['name'])} · {fmt_money(r['price_kop'])} · {'активний' if r['active'] else 'прихований'}" for r in rows
            )
        items = [Button(f"Приховати #{r['id']}", callback=f"adm:producthide:{r['id']}") for r in rows if r["active"]]
        items += [Button("Додати", callback=f"adm:productadd:{category}"), Button("Назад", callback="adm:catalog")]
        await edit_or_answer(callback.message, text, make_keyboard(items))
        await callback.answer()
        return

    if action == "productadd":
        category = data[2]
        await state.set_state(AdminFlow.product_name)
        await state.update_data(product_category=category)
        await callback.message.answer("Введіть назву товару:")
        await callback.answer()
        return

    if action == "producthide":
        product_id = int(data[2])
        async with await DB.connect() as db:
            await db.execute("UPDATE products SET active=0 WHERE id=?", (product_id,))
            await db.commit()
        await DB.audit(callback.from_user.id, "product.hide", f"product={product_id}")
        await callback.answer("Товар приховано.")
        return

    if action == "texts":
        keys = ["menu.welcome", "payment.instruction", "review.ask", "review_template"]
        items = [Button(key, callback=f"adm:text:{key}") for key in keys]
        items.append(Button("Назад", callback="adm:back"))
        await edit_or_answer(callback.message, "<b>📝 Редактор текстів</b>\nНадішліть новий текст після вибору ключа. Підтримується HTML.", make_keyboard(items))
        await callback.answer()
        return

    if action == "text":
        key = ":".join(data[2:])
        await state.set_state(AdminFlow.edit_text)
        await state.update_data(text_key=key)
        current = await setting("review_template" if key == "review_template" else f"text:{key}", "")
        await callback.message.answer(f"Поточний текст <code>{escape(key)}</code>:\n{current}\n\nНадішліть новий текст:")
        await callback.answer()
        return

    if action == "buttons":
        async with await DB.connect() as db:
            cur = await db.execute("SELECT * FROM buttons ORDER BY key")
            rows = await cur.fetchall()
        items = [Button(f"{r['emoji']} {r['label']}".strip(), callback=f"adm:button:{r['key']}") for r in rows]
        items.append(Button("Назад", callback="adm:back"))
        await edit_or_answer(callback.message, "<b>🎛 Редактор кнопок</b>\nОберіть кнопку. Можна змінити назву, емодзі, колір або видимість.", make_keyboard(items))
        await callback.answer()
        return

    if action == "button":
        key = ":".join(data[2:])
        async with await DB.connect() as db:
            cur = await db.execute("SELECT * FROM buttons WHERE key=?", (key,))
            row = await cur.fetchone()
        if not row:
            await callback.answer("Кнопку не знайдено.", show_alert=True)
            return
        text = f"<b>Кнопка {escape(key)}</b>\nТекст: {escape(row['label'])}\nЕмодзі: {escape(row['emoji'])}\nКолір: {escape(row['style'] or 'стандартний')}\nВидима: {'так' if row['visible'] else 'ні'}"
        await edit_or_answer(callback.message, text, make_keyboard([
            Button("Змінити текст", callback=f"adm:buttonedit:{key}"),
            Button("Змінити емодзі", callback=f"adm:buttonemoji:{key}"),
            Button("Колір", callback=f"adm:buttonstyle:{key}"),
            Button("Показати / сховати", callback=f"adm:buttonvisible:{key}"),
            Button("Назад", callback="adm:buttons"),
        ]))
        await callback.answer()
        return

    if action in {"buttonedit", "buttonemoji"}:
        key = ":".join(data[2:])
        await state.set_state(AdminFlow.edit_button)
        await state.update_data(button_key=key, button_field="label" if action == "buttonedit" else "emoji")
        await callback.message.answer("Надішліть новий текст кнопки:" if action == "buttonedit" else "Надішліть емодзі (або слово none):")
        await callback.answer()
        return

    if action == "buttonstyle":
        key = ":".join(data[2:])
        await edit_or_answer(callback.message, "Оберіть стиль:", make_keyboard([
            Button("Стандартний", callback=f"adm:buttonsetstyle:{key}:"),
            Button("Синій", callback=f"adm:buttonsetstyle:{key}:primary", style="primary"),
            Button("Зелений", callback=f"adm:buttonsetstyle:{key}:success", style="success"),
            Button("Червоний", callback=f"adm:buttonsetstyle:{key}:danger", style="danger"),
        ]))
        await callback.answer()
        return

    if action == "buttonsetstyle":
        key = data[2]
        style = data[3] if len(data) > 3 else ""
        async with await DB.connect() as db:
            await db.execute("UPDATE buttons SET style=? WHERE key=?", (style, key))
            await db.commit()
        await DB.audit(callback.from_user.id, "button.style", f"{key}={style}")
        await callback.answer("Стиль збережено.")
        return

    if action == "buttonvisible":
        key = ":".join(data[2:])
        async with await DB.connect() as db:
            await db.execute("UPDATE buttons SET visible=CASE visible WHEN 1 THEN 0 ELSE 1 END WHERE key=?", (key,))
            await db.commit()
        await DB.audit(callback.from_user.id, "button.visible", key)
        await callback.answer("Видимість змінено.")
        return

    if action == "reviews":
        async with await DB.connect() as db:
            cur = await db.execute("SELECT * FROM reviews WHERE status='pending' ORDER BY id LIMIT 15")
            rows = await cur.fetchall()
        counter = await setting("review_next_number", "1")
        auto = await setting("review_auto_increment", "1")
        items = [Button(f"Відгук #{r['id']} · {r['rating']}⭐", callback=f"adm:review:view:{r['id']}") for r in rows]
        items += [Button(f"Наступний номер: {counter}", callback="adm:reviewcounter"),
                  Button(f"Автоінкремент: {'так' if auto=='1' else 'ні'}", callback="adm:reviewautoinc"),
                  Button("Назад", callback="adm:back")]
        await edit_or_answer(callback.message, "<b>⭐ Модерація відгуків</b>\nОберіть відгук або налаштуйте лічильник:", make_keyboard(items))
        await callback.answer()
        return

    if action == "review" and len(data) >= 4 and data[2] == "view":
        rid = int(data[3])
        async with await DB.connect() as db:
            cur = await db.execute("SELECT * FROM reviews WHERE id=?", (rid,))
            row = await cur.fetchone()
        if not row:
            await callback.answer("Відгук не знайдено.", show_alert=True)
            return
        text = f"<b>Відгук #{rid}</b>\nКористувач: <code>{row['user_id']}</code>\nЗамовлення: №{row['order_id']}\nОцінка: {'⭐'*row['rating']}\nКоментар: {escape(row['comment'])}"
        kb = make_keyboard([Button("Опублікувати", callback=f"adm:review:approve:{rid}", style="success"),
                            Button("Відхилити", callback=f"adm:review:reject:{rid}", style="danger"),
                            Button("Змінити номер", callback=f"adm:review:number:{rid}"),
                            Button("Назад", callback="adm:reviews")])
        if row["photo_file_id"]:
            await callback.message.answer_photo(row["photo_file_id"], caption=text, reply_markup=kb)
        else:
            await edit_or_answer(callback.message, text, kb)
        await callback.answer()
        return

    if action == "review" and len(data) >= 4:
        sub, rid = data[2], int(data[3])
        if sub == "approve":
            await publish_review(rid, bot, callback.from_user.id)
            await callback.answer("Спроба публікації виконана.")
            return
        if sub == "reject":
            async with await DB.connect() as db:
                cur = await db.execute("SELECT user_id FROM reviews WHERE id=?", (rid,))
                row = await cur.fetchone()
                await db.execute("UPDATE reviews SET status='rejected' WHERE id=?", (rid,))
                await db.commit()
            if row:
                try:
                    await bot.send_message(row["user_id"], "Дякуємо за відгук. На жаль, його не вдалося опублікувати.")
                except TelegramForbiddenError:
                    pass
            await DB.audit(callback.from_user.id, "review.reject", f"review={rid}")
            await callback.answer("Відгук відхилено.")
            return
        if sub == "number":
            await state.set_state(AdminFlow.review_number)
            await state.update_data(review_id=rid)
            await callback.message.answer(f"Введіть номер, який слід присвоїти відгуку #{rid}:")
            await callback.answer()
            return

    if action == "reviewcounter":
        await state.set_state(AdminFlow.set_review_counter)
        await callback.message.answer("Введіть наступний номер відгуку (ціле число):")
        await callback.answer()
        return

    if action == "reviewautoinc":
        new_value = "0" if await setting("review_auto_increment", "1") == "1" else "1"
        await DB.set_setting("review_auto_increment", new_value)
        await callback.answer("Перемикач змінено.")
        return

    if action == "referrals":
        text = (f"<b>👥 Реферальна система</b>\nВідсоток: {await setting('referral_percent','5')}%\n"
                f"Мінімальне виведення: {await setting('referral_min_withdraw','100')} грн\n"
                f"Активна: {'так' if await setting('referral_enabled','1')=='1' else 'ні'}")
        await edit_or_answer(callback.message, text, make_keyboard([
            Button("Змінити відсоток", callback="adm:set:referral_percent"),
            Button("Мін. виведення", callback="adm:set:referral_min_withdraw"),
            Button("Увімкнути / вимкнути", callback="adm:set:referral_enabled"),
            Button("Заявки на виведення", callback="adm:withdrawals"),
            Button("Назад", callback="adm:back"),
        ]))
        await callback.answer()
        return

    if action == "withdrawals":
        async with await DB.connect() as db:
            cur = await db.execute("SELECT * FROM withdrawals WHERE status='pending' ORDER BY id DESC LIMIT 20")
            rows = await cur.fetchall()
        items = [Button(f"Заявка #{r['id']} · {fmt_money(r['amount_kop'])}", callback=f"adm:withdraw:view:{r['id']}") for r in rows]
        items.append(Button("Назад", callback="adm:referrals"))
        await edit_or_answer(callback.message, "Заявки на виведення:", make_keyboard(items))
        await callback.answer()
        return

    if action == "withdraw" and len(data) >= 4 and data[2] == "view":
        wid = int(data[3])
        async with await DB.connect() as db:
            cur = await db.execute("SELECT * FROM withdrawals WHERE id=?", (wid,))
            row = await cur.fetchone()
        if not row:
            await callback.answer("Заявку не знайдено.", show_alert=True)
            return
        await edit_or_answer(callback.message,
                             f"Заявка #{wid}\nКористувач: {row['user_id']}\nСума: {fmt_money(row['amount_kop'])}\nРеквізити: {escape(row['details'])}",
                             make_keyboard([Button("Підтвердити", callback=f"adm:withdraw:ok:{wid}"),
                                            Button("Відхилити", callback=f"adm:withdraw:no:{wid}"),
                                            Button("Назад", callback="adm:withdrawals")]))
        await callback.answer()
        return

    if action == "withdraw" and len(data) >= 4:
        decision, wid = data[2], int(data[3])
        async with await DB.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT * FROM withdrawals WHERE id=? AND status='pending'", (wid,))
            row = await cur.fetchone()
            if not row:
                await db.rollback()
                await callback.answer("Заявка вже оброблена.", show_alert=True)
                return
            if decision == "ok":
                cur = await db.execute("SELECT ref_balance_kop FROM users WHERE user_id=?", (row["user_id"],))
                user_row = await cur.fetchone()
                if not user_row or int(user_row["ref_balance_kop"]) < int(row["amount_kop"]):
                    await db.rollback()
                    await callback.answer("Недостатньо балансу для виплати.", show_alert=True)
                    return
                await db.execute("UPDATE users SET ref_balance_kop=ref_balance_kop-? WHERE user_id=?", (row["amount_kop"], row["user_id"]))
                status = "approved"
            else:
                status = "rejected"
            await db.execute("UPDATE withdrawals SET status=?,processed_by=? WHERE id=?", (status, callback.from_user.id, wid))
            await db.commit()
        try:
            await bot.send_message(row["user_id"], f"Заявку на виведення №{wid} {'підтверджено' if decision=='ok' else 'відхилено'}.")
        except TelegramForbiddenError:
            pass
        await DB.audit(callback.from_user.id, "withdraw.process", f"withdraw={wid};{status}")
        await callback.answer("Заявку оброблено.")
        return

    if action == "broadcast":
        if len(data) == 3:
            sub = data[2]
            state_data = await state.get_data()
            broadcast_text = state_data.get("broadcast_text", "")
            if not broadcast_text:
                await callback.answer("Спочатку введіть текст.", show_alert=True)
                return
            if sub == "test":
                await callback.message.answer(broadcast_text)
                await callback.answer("Тест надіслано.")
                return
            if sub == "send":
                async with await DB.connect() as db:
                    cur = await db.execute("SELECT user_id FROM users WHERE banned=0")
                    recipients = [int(r["user_id"]) for r in await cur.fetchall()]
                sent = failed = blocked = 0
                status_msg = await callback.message.answer(f"Розсилка розпочата: 0/{len(recipients)}")
                for index, uid in enumerate(recipients, 1):
                    try:
                        await bot.send_message(uid, broadcast_text)
                        sent += 1
                    except TelegramForbiddenError:
                        blocked += 1
                        async with await DB.connect() as db:
                            await db.execute("UPDATE users SET blocked=1 WHERE user_id=?", (uid,))
                            await db.commit()
                    except TelegramRetryAfter as exc:
                        await asyncio.sleep(exc.retry_after)
                        try:
                            await bot.send_message(uid, broadcast_text)
                            sent += 1
                        except Exception:
                            failed += 1
                    except Exception:
                        failed += 1
                    await asyncio.sleep(0.05)
                    if index % 25 == 0:
                        try:
                            await status_msg.edit_text(f"Розсилка: {index}/{len(recipients)}\nДоставлено: {sent} · Помилки: {failed} · Заблокували: {blocked}")
                        except TelegramBadRequest:
                            pass
                await status_msg.edit_text(f"<b>Розсилку завершено</b>\nДоставлено: {sent}\nПомилки: {failed}\nЗаблокували: {blocked}")
                await DB.audit(callback.from_user.id, "broadcast.complete", f"sent={sent};failed={failed};blocked={blocked}")
                await state.clear()
                await callback.answer("Готово.")
                return
        await state.set_state(AdminFlow.broadcast_text)
        await callback.message.answer("Надішліть текст розсилки (HTML дозволено). Розсилка піде всім користувачам, які запускали бота.")
        await callback.answer()
        return

    if action == "user" and len(data) >= 4 and data[2] == "toggle":
        user_id = int(data[3])
        async with await DB.connect() as db:
            await db.execute("UPDATE users SET banned=CASE banned WHEN 1 THEN 0 ELSE 1 END WHERE user_id=?", (user_id,))
            await db.commit()
        await DB.audit(callback.from_user.id, "user.toggle_ban", str(user_id))
        await callback.answer("Статус блокування змінено.")
        return

    if action == "users":
        await state.set_state(AdminFlow.user_lookup)
        await callback.message.answer("Надішліть Telegram ID або @username користувача:")
        await callback.answer()
        return

    if action == "admins":
        if callback.from_user.id not in CONFIG.owner_ids:
            await callback.answer("Керувати ролями може лише власник.", show_alert=True)
            return
        async with await DB.connect() as db:
            cur = await db.execute("SELECT user_id,role,permissions FROM admins ORDER BY user_id")
            rows = await cur.fetchall()
        text = "<b>🛡 Адміністратори</b>\n" + "\n".join(
            f"{r['user_id']} · {r['role']} · {r['permissions']}" for r in rows
        )
        await edit_or_answer(callback.message, text, make_keyboard([
            Button("Додати модератора", callback="adm:adminadd"), Button("Назад", callback="adm:back")
        ]))
        await callback.answer()
        return

    if action == "adminadd":
        if callback.from_user.id not in CONFIG.owner_ids:
            await callback.answer("Лише власник може додавати адміністраторів.", show_alert=True)
            return
        await state.set_state(AdminFlow.add_admin)
        await callback.message.answer("Надішліть Telegram ID нового модератора. Йому будуть доступні замовлення та відгуки.")
        await callback.answer()
        return

    if action == "stats":
        async with await DB.connect() as db:
            cur = await db.execute("SELECT COUNT(*) AS n,COALESCE(SUM(amount_kop),0) AS total FROM orders WHERE status='done'")
            all_row = await cur.fetchone()
            cur = await db.execute("SELECT COUNT(*) AS n FROM users")
            users_count = int((await cur.fetchone())["n"])
            cur = await db.execute("SELECT type,COUNT(*) AS n FROM orders GROUP BY type")
            type_rows = await cur.fetchall()
        text = ("<b>📊 Статистика</b>\n"
                f"Виконаних замовлень: {all_row['n']}\nВиручка: {fmt_money(all_row['total'])}\n"
                f"Користувачів: {users_count}\n\n" + "\n".join(f"{r['type']}: {r['n']}" for r in type_rows))
        await edit_or_answer(callback.message, text, make_keyboard([
            Button("Експорт замовлень CSV", callback="adm:export:orders"),
            Button("Експорт користувачів CSV", callback="adm:export:users"),
            Button("Назад", callback="adm:back"),
        ]))
        await callback.answer()
        return

    if action == "export":
        export_type = data[2]
        await export_csv(callback, export_type)
        return

    if action == "system":
        await edit_or_answer(callback.message, "<b>🧰 Система</b>\nРезервна копія бази даних, журнал дій адміністратора.", make_keyboard([
            Button("📥 Скачати бекап БД", callback="adm:backup"),
            Button("📋 Останні дії адмінів", callback="adm:audit"),
            Button("Назад", callback="adm:back"),
        ]))
        await callback.answer()
        return

    if action == "backup":
        await send_backup(callback, bot)
        return

    if action == "audit":
        async with await DB.connect() as db:
            cur = await db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 15")
            rows = await cur.fetchall()
        text = "<b>Останні дії</b>\n" + ("\n".join(f"{r['created_at']} · {r['admin_id']} · {escape(r['action'])} · {escape(r['details'])}" for r in rows) or "Записів поки немає.")
        await edit_or_answer(callback.message, text, base_kb(Button("Назад", callback="adm:system")))
        await callback.answer()
        return

    await callback.answer("Команда не знайдена.", show_alert=True)


# ---------------------------- admin state handlers ---------------------------

@router.message(AdminFlow.edit_setting)
async def admin_setting_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("setting_key", "")
    value = (message.text or "").strip()
    if not value:
        await message.answer("Значення не може бути порожнім.")
        return
    if key.endswith("url") and value and not valid_url(value):
        await message.answer("Посилання має починатися з https:// або http://")
        return
    if key in {"reviews_channel", "news_channel"} and value and not (valid_url(value) or value.startswith("@") or value.startswith("-100")):
        await message.answer("Введіть @username каналу, числовий ID або https://t.me/посилання")
        return
    if key in {"maintenance", "backup_enabled", "referral_enabled", "review_comment_required", "review_photo_required", "review_mask_names"} and value not in {"0", "1"}:
        await message.answer("Введіть 1 (увімкнути) або 0 (вимкнути).")
        return
    if key in {"price_star", "ton_price", "premium_3", "premium_6", "premium_12", "referral_percent", "referral_min_withdraw", "min_ton", "max_ton"}:
        number = decimal_value(value, "-1")
        if number < 0:
            await message.answer("Введіть коректне невід’ємне число.")
            return
        value = str(number)
    if key in {"min_stars", "max_stars", "payment_timeout_minutes"}:
        try:
            number = int(value)
            if number <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Потрібне додатне ціле число.")
            return
    await DB.set_setting(key, value)
    await DB.audit(message.from_user.id, "setting.update", f"{key}={value[:80]}")
    await state.clear()
    await message.answer(f"Збережено: <code>{escape(key)}</code> = <b>{escape(value)}</b>", reply_markup=base_kb(Button("Адмін-панель", callback="adm:back")))


@router.message(AdminFlow.edit_text)
async def admin_text_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("text_key", "")
    text = message.html_text or message.text or ""
    if not text.strip():
        await message.answer("Текст не може бути порожнім.")
        return
    allowed = {"brand", "user_id", "username", "balance", "order_id", "amount", "stars", "price", "bank", "card", "min", "max", "ref_percent", "ref_count", "ref_earned", "ref_balance", "product_line", "date", "client_name", "comment", "rating_stars", "review_number"}
    try:
        for _, field, _, _ in Formatter().parse(text):
            if field and field.split(".")[0].split("[")[0] not in allowed:
                raise ValueError(f"Невідомий плейсхолдер {{{field}}}")
    except ValueError as exc:
        await message.answer(f"Помилка шаблону: {escape(exc)}. Збереження не виконано.")
        return
    # Перевіряємо баланс HTML-тегів, щоб випадковий незакритий тег не ламав повідомлення.
    stack: list[str] = []
    for match in re.finditer(r"</?([a-zA-Z0-9-]+)(?:\s+[^>]*)?>", text):
        token = match.group(0)
        tag = match.group(1).lower()
        if tag in {"br", "hr", "img"} or token.endswith("/>"):
            continue
        if token.startswith("</"):
            if not stack or stack[-1] != tag:
                await message.answer("HTML-теги не збалансовані. Перевірте відкривальні й закривальні теги.")
                return
            stack.pop()
        else:
            stack.append(tag)
    if stack:
        await message.answer("Не закриті HTML-теги: " + ", ".join(stack))
        return
    await DB.set_setting("review_template" if key == "review_template" else f"text:{key}", text)
    await DB.audit(message.from_user.id, "text.update", key)
    await state.clear()
    await message.answer("Текст збережено. Перевірте прев’ю:\n\n" + text,
                         reply_markup=base_kb(Button("До редактора текстів", callback="adm:texts")))


@router.message(AdminFlow.edit_button)
async def admin_button_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key, field = data.get("button_key"), data.get("button_field")
    value = (message.text or "").strip()
    if field == "emoji":
        custom_entity = next((e for e in (message.entities or []) if e.type == "custom_emoji"), None)
        if custom_entity:
            async with await DB.connect() as db:
                await db.execute("UPDATE buttons SET emoji='',custom_emoji_id=? WHERE key=?", (custom_entity.custom_emoji_id, key))
                await db.commit()
            value = "<premium emoji>"
        else:
            if not value:
                await message.answer("Надішліть звичайне emoji або преміум-emoji окремим повідомленням.")
                return
            if value.lower() == "none":
                value = ""
            async with await DB.connect() as db:
                await db.execute("UPDATE buttons SET emoji=?,custom_emoji_id='' WHERE key=?", (value, key))
                await db.commit()
    else:
        if not value:
            await message.answer("Значення не може бути порожнім.")
            return
        async with await DB.connect() as db:
            await db.execute("UPDATE buttons SET label=? WHERE key=?", (value, key))
            await db.commit()
    await DB.audit(message.from_user.id, "button.update", f"{key}.{field}")
    await state.clear()
    await message.answer("Кнопку оновлено. Зміни застосовуються без перезапуску.",
                         reply_markup=base_kb(Button("До редактора кнопок", callback="adm:buttons")))


@router.message(AdminFlow.bank_details)
async def admin_bank_details_input(message: Message, state: FSMContext) -> None:
    parts = [p.strip() for p in (message.text or "").split(";", 2)]
    if len(parts) < 2:
        await message.answer("Формат: номер картки; ПІБ; коментар")
        return
    card = re.sub(r"\s+", "", parts[0])
    holder = parts[1]
    comment = parts[2] if len(parts) > 2 else ""
    if not re.fullmatch(r"\d{16}", card):
        await message.answer("Номер картки має містити 16 цифр. Збереження не виконано.")
        return
    data = await state.get_data()
    bank_id = int(data.get("bank_id", 0))
    async with await DB.connect() as db:
        await db.execute("UPDATE banks SET card_number=?,cardholder=?,comment=? WHERE id=?", (card, holder, comment, bank_id))
        await db.commit()
    await DB.audit(message.from_user.id, "bank.requisites", f"bank={bank_id}; card=****{card[-4:]}")
    await state.clear()
    await message.answer("Реквізити банку збережено.", reply_markup=base_kb(Button("До банків", callback="adm:banks")))


@router.message(AdminFlow.shared_card)
async def admin_shared_card_input(message: Message, state: FSMContext) -> None:
    parts = [p.strip() for p in (message.text or "").split(";", 2)]
    if len(parts) < 2:
        await message.answer("Формат: номер картки; ПІБ; коментар")
        return
    card = re.sub(r"\s+", "", parts[0])
    if not re.fullmatch(r"\d{16}", card):
        await message.answer("Номер картки має містити 16 цифр.")
        return
    holder = parts[1]
    comment = parts[2] if len(parts) > 2 else ""
    await state.update_data(shared_card=card, shared_holder=holder, shared_comment=comment)
    await message.answer("Підтвердьте застосування цих реквізитів до всіх банків:", reply_markup=make_keyboard([
        Button("Так, застосувати", callback="adm:bankallconfirm", style="success"),
        Button("Скасувати", callback="adm:bankallcancel", style="danger"),
    ]))


@router.message(AdminFlow.bank_name)
async def admin_bank_name_input(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Назва не може бути порожньою.")
        return
    data = await state.get_data()
    bank_id = int(data.get("bank_id", 0))
    async with await DB.connect() as db:
        if bank_id:
            await db.execute("UPDATE banks SET name=? WHERE id=?", (name, bank_id))
        else:
            cur = await db.execute("SELECT COALESCE(MAX(sort_order),0)+1 AS n FROM banks")
            order = int((await cur.fetchone())["n"])
            await db.execute("INSERT INTO banks(name,sort_order) VALUES(?,?)", (name, order))
        await db.commit()
    await DB.audit(message.from_user.id, "bank.name", name)
    await state.clear()
    await message.answer("Банк збережено. Додайте реквізити перед використанням.", reply_markup=base_kb(Button("До банків", callback="adm:banks")))


@router.message(AdminFlow.product_name)
async def product_name_input(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Вкажіть назву.")
        return
    await state.update_data(product_name=name)
    await state.set_state(AdminFlow.product_description)
    await message.answer("Введіть опис товару:")


@router.message(AdminFlow.product_description)
async def product_description_input(message: Message, state: FSMContext) -> None:
    await state.update_data(product_description=(message.text or "")[:1000])
    await state.set_state(AdminFlow.product_price)
    await message.answer("Введіть ціну у гривнях, наприклад 129.50:")


@router.message(AdminFlow.product_price)
async def product_price_input(message: Message, state: FSMContext) -> None:
    price = decimal_value(message.text, "-1")
    if price <= 0:
        await message.answer("Ціна має бути більшою за нуль. Введіть число у гривнях.")
        return
    await state.update_data(product_price_kop=money_to_kop(price))
    await state.set_state(AdminFlow.product_stock)
    await message.answer("Введіть кількість на складі. -1 означає необмежену наявність:")


@router.message(AdminFlow.product_stock)
async def product_stock_input(message: Message, state: FSMContext) -> None:
    try:
        stock = int((message.text or "").strip())
        if stock < -1:
            raise ValueError
    except ValueError:
        await message.answer("Введіть -1 або невід’ємне ціле число.")
        return
    await state.update_data(product_stock=stock)
    data = await state.get_data()
    if data.get("product_category") == "game":
        await state.set_state(AdminFlow.product_fields)
        await message.answer("Введіть назви полів через кому, наприклад UID, сервер. Якщо поля не потрібні — напишіть none.")
    else:
        await save_product(message, state, [])


@router.message(AdminFlow.product_fields)
async def product_fields_input_admin(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    fields = [] if value.lower() == "none" else [v.strip() for v in value.split(",") if v.strip()]
    await save_product(message, state, fields)


async def save_product(message: Message, state: FSMContext, fields: list[str]) -> None:
    data = await state.get_data()
    async with await DB.connect() as db:
        await db.execute(
            "INSERT INTO products(category,name,description,price_kop,stock,fields,created_at) VALUES(?,?,?,?,?,?,?)",
            (data["product_category"], data["product_name"], data.get("product_description", ""),
             data["product_price_kop"], data["product_stock"], json.dumps(fields, ensure_ascii=False), now_iso()),
        )
        await db.commit()
    await DB.audit(message.from_user.id, "product.create", data["product_name"])
    await state.clear()
    await message.answer("Товар додано до каталогу.", reply_markup=base_kb(Button("До каталогів", callback="adm:catalog")))


@router.message(AdminFlow.banner_photo)
async def admin_banner_photo(message: Message, state: FSMContext) -> None:
    if not message.photo:
        await message.answer("Надішліть саме фото.")
        return
    data = await state.get_data()
    key = data.get("banner_key", "home")
    file_id = message.photo[-1].file_id
    await DB.set_setting(f"banner:{key}", file_id)
    await DB.audit(message.from_user.id, "banner.update", key)
    await state.clear()
    await message.answer("Банер збережено. Він відображатиметься в головному меню без перезапуску.",
                         reply_markup=base_kb(Button("До банерів", callback="adm:banners")))


@router.message(AdminFlow.reject_reason)
async def admin_reject_reason(message: Message, state: FSMContext, bot: Bot) -> None:
    reason = (message.text or "").strip()[:500]
    data = await state.get_data()
    order_id = int(data.get("reject_order_id", 0))
    order = await get_order(order_id)
    if not order or order["status"] != "paid_review":
        await state.clear()
        await message.answer("Замовлення вже оброблене.")
        return
    async with await DB.connect() as db:
        cur = await db.execute("UPDATE orders SET status='rejected',admin_id=?,admin_note=? WHERE id=? AND status='paid_review'",
                               (message.from_user.id, reason, order_id))
        await db.commit()
    if cur.rowcount == 0:
        await state.clear()
        await message.answer("Замовлення вже оброблено іншим адміністратором.")
        return
    await DB.audit(message.from_user.id, "order.reject", f"order={order_id}")
    try:
        await bot.send_message(order["user_id"], f"Оплату за замовлення №{order_id} не підтверджено. Причина: {escape(reason)}")
    except TelegramForbiddenError:
        pass
    await state.clear()
    await message.answer("Оплату відхилено.")


@router.message(AdminFlow.review_number)
async def review_number_input(message: Message, state: FSMContext) -> None:
    try:
        number = int((message.text or "").strip())
    except ValueError:
        await message.answer("Введіть ціле число.")
        return
    data = await state.get_data()
    rid = int(data.get("review_id", 0))
    async with await DB.connect() as db:
        await db.execute("UPDATE reviews SET review_number=? WHERE id=?", (number, rid))
        await db.commit()
    await DB.audit(message.from_user.id, "review.number", f"review={rid};number={number}")
    await state.clear()
    await message.answer(f"Для відгуку #{rid} встановлено номер {number}.")


@router.message(AdminFlow.set_review_counter)
async def review_counter_input(message: Message, state: FSMContext) -> None:
    try:
        number = int((message.text or "").strip())
    except ValueError:
        await message.answer("Введіть ціле число.")
        return
    await DB.set_setting("review_next_number", str(number))
    await DB.audit(message.from_user.id, "review.counter", str(number))
    await state.clear()
    await message.answer(f"Наступний номер відгуку: {number}.")


@router.message(AdminFlow.broadcast_text)
async def broadcast_text_input(message: Message, state: FSMContext, bot: Bot) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Текст розсилки не може бути порожнім.")
        return
    await state.update_data(broadcast_text=text)
    await message.answer("Попередній перегляд:\n\n" + text,
                         reply_markup=make_keyboard([Button("Надіслати тест собі", callback="adm:broadcast:test"),
                                                     Button("Запустити всім", callback="adm:broadcast:send", style="success"),
                                                     Button("Скасувати", callback="adm:back", style="danger")]))


@router.message(AdminFlow.user_lookup)
async def admin_user_lookup(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()
    async with await DB.connect() as db:
        if query.isdigit():
            cur = await db.execute("SELECT * FROM users WHERE user_id=?", (int(query),))
        else:
            cur = await db.execute("SELECT * FROM users WHERE username=?", (query.lstrip("@"),))
        user = await cur.fetchone()
    if not user:
        await message.answer("Користувача не знайдено.")
        await state.clear()
        return
    async with await DB.connect() as db:
        cur = await db.execute("SELECT COUNT(*) AS n FROM orders WHERE user_id=?", (user["user_id"],))
        count = int((await cur.fetchone())["n"])
    text = (f"<b>Користувач</b>\nID: <code>{user['user_id']}</code>\nUsername: @{escape(user['username'] or '')}\n"
            f"Покупок: {count}\nВитрачено: {fmt_money(user['total_spent_kop'])}\n"
            f"Реферальний баланс: {fmt_money(user['ref_balance_kop'])}\nБан: {'так' if user['banned'] else 'ні'}")
    await state.clear()
    await message.answer(text, reply_markup=make_keyboard([
        Button("Заблокувати / розблокувати", callback=f"adm:user:toggle:{user['user_id']}"),
        Button("Назад", callback="adm:back"),
    ]))


@router.message(AdminFlow.add_admin)
async def admin_add_moderator(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in CONFIG.owner_ids:
        await state.clear()
        return
    try:
        user_id = int((message.text or "").strip())
    except ValueError:
        await message.answer("Telegram ID має бути числом.")
        return
    async with await DB.connect() as db:
        await db.execute("INSERT INTO admins(user_id,role,permissions,added_by,added_at) VALUES(?,?,?,?,?) "
                         "ON CONFLICT(user_id) DO UPDATE SET role='moderator',permissions='orders,reviews',added_by=excluded.added_by",
                         (user_id, "moderator", "orders,reviews", message.from_user.id, now_iso()))
        await db.commit()
    await DB.audit(message.from_user.id, "admin.add", str(user_id))
    await state.clear()
    await message.answer(f"Модератора {user_id} додано.")


# ------------------------------ completion/referral --------------------------

async def complete_order(order_id: int, admin_id: int, bot: Bot) -> None:
    order = await get_order(order_id)
    if not order:
        return
    payload = json.loads(order["payload"])
    now = now_iso()
    async with await DB.connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "UPDATE orders SET status='done',done_at=?,admin_id=? WHERE id=? AND status IN ('paid','in_progress')",
            (now, admin_id, order_id),
        )
        if cur.rowcount == 0:
            await db.rollback()
            return
        await db.execute("UPDATE users SET total_spent_kop=total_spent_kop+?,orders_count=orders_count+1 WHERE user_id=?",
                         (order["amount_kop"], order["user_id"]))
        product_id = payload.get("product_id")
        if product_id:
            await db.execute("UPDATE products SET stock=CASE WHEN stock>0 THEN stock-1 ELSE stock END WHERE id=?", (product_id,))
        cur = await db.execute("SELECT referred_by FROM users WHERE user_id=?", (order["user_id"],))
        user = await cur.fetchone()
        if user and user["referred_by"] and order["type"] in ("stars", "ton") and await setting("referral_enabled", "1") == "1":
            percent = decimal_value(await setting("referral_percent", "5"))
            reward = int((Decimal(order["amount_kop"]) * percent / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            if reward > 0:
                cur = await db.execute("SELECT id FROM referral_ledger WHERE order_id=?", (order_id,))
                if not await cur.fetchone():
                    await db.execute("INSERT INTO referral_ledger(referrer_id,buyer_id,order_id,amount_kop,created_at) VALUES(?,?,?,?,?)",
                                     (user["referred_by"], order["user_id"], order_id, reward, now))
                    await db.execute("UPDATE users SET ref_balance_kop=ref_balance_kop+?,ref_earned_kop=ref_earned_kop+? WHERE user_id=?",
                                     (reward, reward, user["referred_by"]))
        await db.commit()
    try:
        await bot.send_message(order["user_id"], f"✅ Замовлення №{order_id} виконано. Дякуємо за покупку!")
        await start_review_flow(bot, int(order["user_id"]), order_id)
    except TelegramForbiddenError:
        pass
    await DB.audit(admin_id, "order.done", f"order={order_id}")


async def publish_review(review_id: int, bot: Bot, admin_id: int) -> None:
    async with await DB.connect() as db:
        cur = await db.execute("SELECT * FROM reviews WHERE id=?", (review_id,))
        review = await cur.fetchone()
    if not review:
        return
    if review["status"] == "published":
        await notify_admins(bot, f"Відгук #{review_id} уже опублікований.")
        return
    channel = await setting("reviews_channel", "")
    if not channel:
        await notify_admins(bot, f"Не задано канал відгуків. Відгук #{review_id} залишився в черзі.")
        return
    channel = channel.strip()
    if channel.startswith("https://t.me/"):
        channel_name = channel.rstrip("/").split("/")[-1]
        if channel_name and not channel_name.startswith("+"):
            channel = "@" + channel_name
    number = int(review["review_number"] or 0)
    if number == 0:
        number = int(await setting("review_next_number", "1"))
    order = await get_order(int(review["order_id"]))
    payload = json.loads(order["payload"]) if order else {}
    user = await DB.get_user(int(review["user_id"]))
    hide_name = await setting("review_mask_names", "0") == "1"
    client_name = "Прихований клієнт" if hide_name else ("@" + user["username"] if user and user["username"] else f"ID {review['user_id']}")
    date_text = datetime.now().strftime("%d.%m.%Y")
    template = await setting("review_template", "Відгук №{review_number}\nКлієнт: {client_name}\nКоментар: {comment}\nОцінка: {rating_stars}\nПридбано: {product_line}\nДата: {date}\n{brand} — ваш надійний партнер!")
    vals = {
        "review_number": number, "client_name": client_name, "comment": review["comment"] or "Без коментаря",
        "rating_stars": "⭐" * int(review["rating"]), "product_line": payload.get("title", order["type"] if order else ""),
        "date": date_text, "brand": await setting("brand", "SPANIX STARS"),
    }
    try:
        post_text = template.format_map(_SafeFormat({k: escape(v) for k, v in vals.items()}))
        if review["photo_file_id"]:
            sent = await bot.send_photo(channel, review["photo_file_id"], caption=post_text)
        else:
            sent = await bot.send_message(channel, post_text)
        async with await DB.connect() as db:
            await db.execute("UPDATE reviews SET status='published',review_number=?,published_message_id=? WHERE id=?",
                             (number, sent.message_id, review_id))
            await db.commit()
        if await setting("review_auto_increment", "1") == "1":
            await DB.set_setting("review_next_number", str(number + 1))
        try:
            await bot.send_message(review["user_id"], "Ваш відгук опубліковано. Дякуємо!")
        except TelegramForbiddenError:
            pass
        await DB.audit(admin_id, "review.publish", f"review={review_id};number={number}")
    except Exception as exc:
        log.exception("Помилка публікації відгуку %s", review_id)
        await notify_admins(bot, f"Не вдалося опублікувати відгук #{review_id}: {escape(exc)}. Перевірте, чи бот є адміністратором каналу.",
                            make_keyboard([Button("Спробувати ще раз", callback=f"adm:review:approve:{review_id}")]))


# -------------------------------- exports/backup -----------------------------

async def export_csv(callback: CallbackQuery, export_type: str) -> None:
    output = io.StringIO()
    writer = csv.writer(output)
    async with await DB.connect() as db:
        if export_type == "orders":
            writer.writerow(["id", "user_id", "type", "amount_kop", "status", "created_at", "paid_at", "done_at", "payload"])
            cur = await db.execute("SELECT * FROM orders ORDER BY id DESC")
            rows = await cur.fetchall()
            for row in rows:
                writer.writerow([row[k] for k in ["id", "user_id", "type", "amount_kop", "status", "created_at", "paid_at", "done_at", "payload"]])
            filename = "spanix_orders.csv"
        else:
            writer.writerow(["user_id", "username", "first_name", "total_spent_kop", "orders_count", "ref_balance_kop", "created_at"])
            cur = await db.execute("SELECT * FROM users ORDER BY user_id")
            rows = await cur.fetchall()
            for row in rows:
                writer.writerow([row[k] for k in ["user_id", "username", "first_name", "total_spent_kop", "orders_count", "ref_balance_kop", "created_at"]])
            filename = "spanix_users.csv"
    data = output.getvalue().encode("utf-8-sig")
    await callback.message.answer_document(BufferedInputFile(data, filename=filename), caption="Експорт завершено.")
    await callback.answer()


async def send_backup(callback: CallbackQuery, bot: Bot) -> None:
    src = Path(CONFIG.db_path)
    dest = src.with_name("spanix_backup.sqlite3")
    try:
        # sqlite backup API враховує WAL і дає узгоджену копію.
        source = sqlite3.connect(str(src))
        target = sqlite3.connect(str(dest))
        source.backup(target)
        target.close()
        source.close()
        await callback.message.answer_document(BufferedInputFile(dest.read_bytes(), filename="spanix_backup.sqlite3"),
                                               caption="Резервна копія бази даних.")
    except Exception:
        log.exception("Не вдалося створити бекап")
        await callback.message.answer("Не вдалося створити бекап. Перевірте права доступу до папки data.")
    finally:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
    await callback.answer()


# ------------------------------ background jobs ------------------------------

async def expire_orders_loop(bot: Bot) -> None:
    while True:
        try:
            now = datetime.now().isoformat(timespec="seconds")
            async with await DB.connect() as db:
                cur = await db.execute("SELECT id,user_id FROM orders WHERE status='awaiting_payment' AND expires_at<=?", (now,))
                candidates = await cur.fetchall()
                expired = []
                for row in candidates:
                    changed = await db.execute("UPDATE orders SET status='cancelled' WHERE id=? AND status='awaiting_payment'", (row["id"],))
                    if changed.rowcount:
                        expired.append(row)
                await db.commit()
            for row in expired:
                try:
                    await bot.send_message(row["user_id"], f"Час оплати замовлення №{row['id']} минув. Його автоматично скасовано.")
                except TelegramForbiddenError:
                    pass
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Помилка фонового скасування замовлень")
            await asyncio.sleep(30)


async def daily_backup_loop(bot: Bot) -> None:
    last_day = ""
    while True:
        try:
            if await setting("backup_enabled", "0") == "1":
                today = datetime.now().date().isoformat()
                if datetime.now().hour >= 3 and last_day != today:
                    src = Path(CONFIG.db_path)
                    dest = src.with_name("spanix_daily_backup.sqlite3")
                    source = sqlite3.connect(str(src))
                    target = sqlite3.connect(str(dest))
                    source.backup(target)
                    target.close()
                    source.close()
                    data = dest.read_bytes()
                    for owner in CONFIG.owner_ids:
                        try:
                            await bot.send_document(owner, BufferedInputFile(data, filename="spanix_daily_backup.sqlite3"),
                                                    caption="Щоденна резервна копія SPANIX STARS")
                        except TelegramForbiddenError:
                            pass
                    dest.unlink(missing_ok=True)
                    last_day = today
            await asyncio.sleep(300)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Помилка щоденного бекапу")
            await asyncio.sleep(300)


# --------------------------------- fallbacks ---------------------------------

@router.message(F.text)
async def text_fallback(message: Message) -> None:
    user = await DB.get_user(message.from_user.id)
    if user and int(user["banned"]):
        return
    await message.answer("Оберіть потрібний розділ кнопками нижче або введіть /start для головного меню.",
                         reply_markup=await home_keyboard())


@router.errors()
async def global_error_handler(event, bot: Bot) -> bool:
    log.exception("Необроблена помилка", exc_info=event.exception)
    try:
        await notify_admins(bot, f"⚠️ Помилка бота: <code>{escape(type(event.exception).__name__)}</code>\n{escape(str(event.exception)[:500])}")
    except Exception:
        log.exception("Не вдалося повідомити власника про помилку")
    return True


class ThrottleMiddleware(BaseMiddleware):
    """Невеликий антифлуд: не більше однієї дії від користувача за 0,5 секунди."""
    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.last_seen: dict[int, float] = {}

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user:
            now = asyncio.get_running_loop().time()
            previous = self.last_seen.get(user.id, 0.0)
            if now - previous < self.interval:
                if isinstance(event, CallbackQuery):
                    try:
                        await event.answer("Зачекайте мить перед наступною дією.")
                    except TelegramBadRequest:
                        pass
                return None
            self.last_seen[user.id] = now
            if len(self.last_seen) > 10000:
                self.last_seen = {user.id: now}
        return await handler(event, data)


async def main() -> None:
    await DB.init(CONFIG.owner_ids)
    bot = Bot(CONFIG.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    global BOT_USERNAME
    BOT_USERNAME = me.username or ""
    dp = Dispatcher()
    throttle = ThrottleMiddleware(0.5)
    dp.message.middleware(throttle)
    dp.callback_query.middleware(throttle)
    dp.include_router(router)
    expiry_task = asyncio.create_task(expire_orders_loop(bot), name="expire-orders")
    backup_task = asyncio.create_task(daily_backup_loop(bot), name="daily-backup")
    log.info("Бот @%s запущений. Адміни: %s", BOT_USERNAME, sorted(CONFIG.owner_ids))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        expiry_task.cancel()
        backup_task.cancel()
        await asyncio.gather(expiry_task, backup_task, return_exceptions=True)
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Бота зупинено")
