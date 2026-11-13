"""SPANIX SHOP BOT — єдиний файл.

Запуск:  python bot.py
Потрібно: pip install "aiogram>=3.13,<4" "aiosqlite>=0.20" python-dotenv
Поруч із файлом створіть .env:
    BOT_TOKEN=токен_від_BotFather
    BOOTSTRAP_OWNER_ID=ваш_telegram_id
    DATABASE_PATH=data/database.sqlite3   (необов'язково)
"""
import asyncio, csv, html, json, logging, os, re
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import aiosqlite
from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv необов'язковий: тоді змінні беруться із середовища
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent
if load_dotenv:
    load_dotenv(BASE_DIR / ".env")

# ======================= CONFIG =======================
BOOTSTRAP_OWNER_ID = int(os.getenv("BOOTSTRAP_OWNER_ID", "0").strip() or 0)
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
_db_path = Path(os.getenv("DATABASE_PATH", "data/database.sqlite3").strip() or "data/database.sqlite3")
DATABASE_PATH = str(_db_path if _db_path.is_absolute() else BASE_DIR / _db_path)

# ======================= HELPERS =======================
def cents(value):
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def money(value):
    return f"{value/100:.2f} UAH"


# ======================= DATABASE =======================
SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users(
 id INTEGER PRIMARY KEY, username TEXT, created_at TEXT NOT NULL, blocked INTEGER NOT NULL DEFAULT 0,
 referred_by INTEGER, referral_balance_cents INTEGER NOT NULL DEFAULT 0, note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS admins(
 user_id INTEGER PRIMARY KEY, role TEXT NOT NULL, permissions TEXT NOT NULL DEFAULT '[]', added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS products(
 id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, title TEXT NOT NULL, description TEXT DEFAULT '',
 price_cents INTEGER NOT NULL, stock INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
 emoji_id TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders(
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id), product_id INTEGER,
 kind TEXT NOT NULL, title TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 1,
 unit_price_cents INTEGER NOT NULL, total_cents INTEGER NOT NULL, currency TEXT NOT NULL DEFAULT 'UAH',
 recipient TEXT DEFAULT '', status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 FOREIGN KEY(product_id) REFERENCES products(id)
);
CREATE TABLE IF NOT EXISTS payments(
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id),
 method TEXT NOT NULL, amount_cents INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 provider_ref TEXT UNIQUE, created_at TEXT NOT NULL, checked_by INTEGER
);
CREATE TABLE IF NOT EXISTS receipts(
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id),
 user_id INTEGER NOT NULL, username TEXT, file_id TEXT NOT NULL, local_path TEXT,
 amount_cents INTEGER NOT NULL, method TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 created_at TEXT NOT NULL, reviewed_by INTEGER
);
CREATE TABLE IF NOT EXISTS audit_logs(
 id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER NOT NULL, action TEXT NOT NULL,
 object_type TEXT, object_id TEXT, old_value TEXT, new_value TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS referrals(
 user_id INTEGER PRIMARY KEY REFERENCES users(id), referrer_id INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS referral_transactions(
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, order_id INTEGER NOT NULL,
 amount_cents INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(order_id)
);
CREATE TABLE IF NOT EXISTS withdrawal_requests(
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount_cents INTEGER NOT NULL,
 details TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nft_sales(
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, username TEXT, url TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', reason TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS custom_emojis(key TEXT PRIMARY KEY, emoji_id TEXT);
CREATE TABLE IF NOT EXISTS broadcasts(
 id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER NOT NULL, body TEXT NOT NULL,
 sent INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inventory(
 id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL REFERENCES products(id),
 stock_delta INTEGER NOT NULL, reason TEXT NOT NULL, actor_id INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payment_transactions(
 id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, tx_hash TEXT NOT NULL UNIQUE,
 order_id INTEGER NOT NULL REFERENCES orders(id), amount_cents INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_receipts_order ON receipts(order_id);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category,active);
"""

def now():
    return datetime.now(timezone.utc).isoformat()

class Database:
    def __init__(self, path):
        self.path = path
    async def connect(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)
        await self.db.commit()
        return self
    async def close(self):
        await self.db.close()
    async def execute(self, sql, args=()):
        cur = await self.db.execute(sql, args); await self.db.commit(); return cur
    async def one(self, sql, args=()):
        cur = await self.db.execute(sql, args); return await cur.fetchone()
    async def all(self, sql, args=()):
        cur = await self.db.execute(sql, args); return await cur.fetchall()
    async def setting(self, key, default=""):
        row = await self.one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default
    async def set_setting(self, key, value):
        await self.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key,str(value)))
    async def log(self, actor, action, typ=None, oid=None, old=None, new=None):
        await self.execute("INSERT INTO audit_logs(actor_id,action,object_type,object_id,old_value,new_value,created_at) VALUES(?,?,?,?,?,?,?)",
            (actor,action,typ,str(oid) if oid is not None else None,json.dumps(old,ensure_ascii=False) if old is not None else None,json.dumps(new,ensure_ascii=False) if new is not None else None,now()))
    async def create_order(self, user_id, kind, title, quantity, unit_price_cents, recipient="", product_id=None):
        total = int(quantity) * int(unit_price_cents)
        cur = await self.execute("""INSERT INTO orders(user_id,product_id,kind,title,quantity,unit_price_cents,total_cents,recipient,status,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (user_id,product_id,kind,title,int(quantity),int(unit_price_cents),total,recipient,"awaiting_payment",now(),now()))
        return cur.lastrowid


# ======================= UI HELPERS =======================
async def safe_edit(c, text, reply_markup=None):
    """edit_text, який не падає, коли текст не змінився."""
    try:
        await c.message.edit_text(text[:4000], reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


# ======================= KEYBOARDS =======================
PAY_METHODS = ("Monobank","А-Банк","PUMB","Privat24","Oschadbank","Alliance Bank","CryptoBot","TON")

MAIN = [
 ("Купить Stars","buy_stars"),("Купить Gram","buy_gram"),
 ("Продать Gram","sell_gram"),("Продать NFT","sell_nft"),
 ("Купить NFT","buy_nft"),("Tg Premium","premium"),
 ("Gold Standoff","gold"),("Telegram аккаунты","accounts"),
 ("Профиль","profile"),("Поддержка","support"),("Инструкция","guide")
]
def kb(rows):
    b=InlineKeyboardBuilder()
    for text, data in rows: b.button(text=text, callback_data=data)
    b.adjust(2)
    return b.as_markup()
def main_menu():
    return kb([(t,"menu:"+k) for t,k in MAIN])
def back():
    return kb([("Назад","home")])
def choices(items, prefix):
    return kb([(str(x),f"{prefix}:{x}") for x in items]+[("Назад","home")])
def pay_methods(order_id):
    return kb([(x,f"pay:{order_id}:{x}") for x in PAY_METHODS]+[("Отмена","home")])
def admin_menu():
    return kb([(x,"adm:"+k) for x,k in [
      ("Статистика","stats"),("Заказы","orders"),("Архив квитанций","receipts"),
      ("Курсы и цены","rates"),("Каталог","catalog"),("Платежи и API","payments"),
      ("Premium Emoji","emoji"),("Пользователи","users"),("Поддержка","support"),
      ("Журнал действий","logs"),("Резервная копия","backup"),("Экспорт","export"),
      ("Рассылка","broadcast"),("Настройки магазина","settings"),("Администраторы","admins")
    ]])


# ======================= SHOP HANDLERS =======================
log = logging.getLogger(__name__)
shop_router = Router()

CUSTOM = "Указать свое количество"
MAX_QTY = 1_000_000
PACKAGES = {
    "buy_stars": [50, 75, 100, 150, 250, 350, 500, 750, 1000, 1500, 2500, 5000],
    "buy_gram": [1, 5, 10, 15, 20, 25, 30, 50, 70, 100],
    "premium": [1, 3, 6, 12],
}
TITLES = {"buy_stars": "Telegram Stars", "buy_gram": "TON (Gram)", "premium": "Telegram Premium", "gold": "Gold Standoff 2"}


class Flow(StatesGroup):
    recipient = State(); custom_qty = State(); sell_qty = State(); nft_url = State(); receipt = State(); player_id = State()


class BlockedUserMiddleware(BaseMiddleware):
    """Заблоковані користувачі не можуть користуватися ботом (раніше перевірявся лише /start)."""
    async def __call__(self, handler, event, data):
        user, db = data.get("event_from_user"), data.get("db")
        if user and db:
            row = await db.one("SELECT blocked FROM users WHERE id=?", (user.id,))
            if row and row["blocked"]:
                if isinstance(event, CallbackQuery):
                    await event.answer("Доступ к магазину ограничен.", show_alert=True)
                elif isinstance(event, Message):
                    await event.answer("Доступ к магазину ограничен.")
                return
        return await handler(event, data)


shop_router.message.outer_middleware(BlockedUserMiddleware())
shop_router.callback_query.outer_middleware(BlockedUserMiddleware())


def bank_key(method):
    return "bank_" + method.replace(" ", "_")


async def ensure_user(db, u, ref=None):
    row = await db.one("SELECT id FROM users WHERE id=?", (u.id,))
    if row:
        await db.execute("UPDATE users SET username=? WHERE id=?", (u.username, u.id))
        return
    # реферер має існувати (FOREIGN KEY) і не може бути самим користувачем
    if ref is None or ref == u.id or not await db.one("SELECT id FROM users WHERE id=?", (ref,)):
        ref = None
    await db.execute("INSERT INTO users(id,username,created_at,referred_by) VALUES(?,?,?,?)", (u.id, u.username, now(), ref))
    if ref:
        await db.execute("INSERT OR IGNORE INTO referrals(user_id,referrer_id,created_at) VALUES(?,?,?)", (u.id, ref, now()))


@shop_router.message(CommandStart())
async def start(m: Message, db, state: FSMContext):
    await state.clear()
    parts = (m.text or "").split(maxsplit=1); ref = None
    if len(parts) > 1 and parts[1].isascii() and parts[1].isdigit() and len(parts[1]) <= 15:
        ref = int(parts[1])
    await ensure_user(db, m.from_user, ref)
    await m.answer("SPANIX SHOP\nВыберите нужный раздел.", reply_markup=main_menu())


@shop_router.callback_query(F.data == "home")
async def home(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(c, "SPANIX SHOP\nВыберите нужный раздел.", main_menu()); await c.answer()


async def show_catalog(c, db, category, header, empty):
    rows = await db.all("SELECT id,title,price_cents,stock FROM products WHERE category=? AND active=1 AND stock>0", (category,))
    if not rows:
        return await safe_edit(c, empty, back())
    b = InlineKeyboardBuilder()
    for p in rows:
        b.button(text=f"{p['title']} — {money(p['price_cents'])}", callback_data=f"item:{category}:{p['id']}")
    b.adjust(2)
    await safe_edit(c, header, b.as_markup())


@shop_router.callback_query(F.data.startswith("menu:"))
async def menu(c: CallbackQuery, state: FSMContext, db):
    await c.answer()
    await state.clear()  # скидаємо застарілий стан (напр. очікування квитанції)
    key = c.data.split(":", 1)[1]
    if key in ("buy_stars", "buy_gram", "premium", "gold", "buy_nft", "accounts"):
        await state.update_data(kind=key, recipient="")
        if key == "buy_nft":
            return await show_catalog(c, db, "nft", "Выберите NFT:", "Каталог NFT пока пуст.")
        if key == "accounts":
            return await show_catalog(c, db, "accounts", "Выберите страну и товар:", "Каталог товаров пока пуст.")
        await safe_edit(c, "Кому хотите приобрести?\nВыберите вариант:", choices(["Купить себе", "Купить другу"], "recipient"))
    elif key == "sell_gram":
        await state.set_state(Flow.sell_qty); await safe_edit(c, "Введите количество TON для продажи:", back())
    elif key == "sell_nft":
        await state.set_state(Flow.nft_url); await safe_edit(c, "Отправьте публичную ссылку на NFT:", back())
    elif key == "profile":
        row = await db.one("SELECT * FROM users WHERE id=?", (c.from_user.id,))
        if not row:
            await ensure_user(db, c.from_user)
            row = await db.one("SELECT * FROM users WHERE id=?", (c.from_user.id,))
        count = await db.one("SELECT COUNT(*) n FROM orders WHERE user_id=?", (c.from_user.id,))
        uname = html.escape(c.from_user.username or "не указан")
        await safe_edit(c, f"ID: {c.from_user.id}\nUsername: @{uname}\nЗаказов: {count['n']}\nРеферальный баланс: {money(row['referral_balance_cents'])}",
                        choices(["Реферальная система", "История покупок"], "profile"))
    elif key == "support":
        username = await db.setting("support_username", "")
        await safe_edit(c, "Поддержка: " + (("@" + html.escape(username.lstrip("@"))) if username else "не настроена"), back())
    elif key == "guide":
        await safe_edit(c, "Инструкция по использованию бота.\n\nВыберите товар, оформите заказ и выберите способ оплаты. При банковском переводе отправьте квитанцию. Для помощи откройте раздел Поддержка.", back())


@shop_router.callback_query(F.data.startswith("profile:"))
async def profile_section(c: CallbackQuery, db):
    """Раніше кнопки профілю не мали обробника."""
    await c.answer()
    choice = c.data.split(":", 1)[1]
    if choice == "Реферальная система":
        me = await c.bot.me()
        n = await db.one("SELECT COUNT(*) n FROM referrals WHERE referrer_id=?", (c.from_user.id,))
        text = f"Ваша реферальная ссылка:\nhttps://t.me/{me.username}?start={c.from_user.id}\nПриглашено: {n['n']}"
    else:
        rows = await db.all("SELECT id,title,total_cents,status FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (c.from_user.id,))
        text = "Последние заказы:\n" + "\n".join(f"#{r['id']} {html.escape(r['title'])} — {money(r['total_cents'])} ({r['status']})" for r in rows) if rows else "Заказов пока нет."
    await safe_edit(c, text, back())


@shop_router.callback_query(F.data.startswith("recipient:"))
async def recipient(c: CallbackQuery, state: FSMContext):
    data = await state.get_data(); kind = data.get("kind")
    if kind not in TITLES:
        return await c.answer("Сессия устарела. Откройте меню заново.", show_alert=True)
    choice = c.data.split(":", 1)[1]
    await state.update_data(recipient="self" if choice == "Купить себе" else "friend")
    who = "свой" if choice == "Купить себе" else "получателя"
    if kind in PACKAGES:
        await state.set_state(Flow.recipient)
        extra = " или TON-адрес кошелька" if kind == "buy_gram" else ""
        await safe_edit(c, f"Введите {who} Telegram username в формате @username{extra}:")
    elif kind == "gold":
        await state.set_state(Flow.player_id); await safe_edit(c, "Введите игровой ID Standoff 2:")
    await c.answer()


@shop_router.message(Flow.recipient)
async def recipient_text(m: Message, state: FSMContext, db):
    value = (m.text or "").strip()
    data = await state.get_data(); kind = data.get("kind")
    if kind not in PACKAGES:
        await state.clear(); return await m.answer("Сессия устарела. Откройте меню заново.", reply_markup=main_menu())
    is_user = re.fullmatch(r"@?[A-Za-z0-9_]{5,32}", value)
    is_addr = kind == "buy_gram" and re.fullmatch(r"[A-Za-z0-9_\-]{48}", value)
    if not (is_user or is_addr):
        return await m.answer("Неверный формат. Введите username в формате @username.")
    target = value if is_addr else value.lstrip("@")
    await state.update_data(target=target)
    items = PACKAGES[kind] + ([CUSTOM] if kind in ("buy_stars", "buy_gram") else [])
    await m.answer(f"Получатель: {html.escape(value)}\nВыберите пакет:", reply_markup=choices(items, "qty"))


@shop_router.message(Flow.player_id)
async def player_id(m: Message, state: FSMContext):
    """Раніше стан player_id не мав обробника — замовлення Gold не можна було оформити."""
    value = (m.text or "").strip()
    if not (value.isascii() and value.isdigit() and 4 <= len(value) <= 15):
        return await m.answer("Игровой ID — это число (4–15 цифр). Попробуйте ещё раз.")
    await state.update_data(target=value)
    await state.set_state(Flow.custom_qty)
    await m.answer("Введите количество голды:")


@shop_router.callback_query(F.data.startswith("qty:"))
async def quantity(c: CallbackQuery, state: FSMContext, db):
    value = c.data.split(":", 1)[1]
    kind = (await state.get_data()).get("kind")
    if kind not in PACKAGES:
        return await c.answer("Сессия устарела. Откройте меню заново.", show_alert=True)
    if value == CUSTOM and kind != "premium":
        await state.set_state(Flow.custom_qty); await c.answer()
        return await safe_edit(c, "Введите количество" + (" (минимум 50):" if kind == "buy_stars" else ":"))
    if not (value.isascii() and value.isdigit()) or int(value) not in PACKAGES[kind]:
        return await c.answer("Неверный пакет.", show_alert=True)
    await c.answer()
    await make_order(c.message, c.from_user, state, db, int(value))


async def make_order(message, user, state, db, qty):
    data = await state.get_data(); kind = data.get("kind", "")
    if kind not in TITLES:
        await state.clear(); return await message.answer("Сессия устарела. Откройте меню заново.", reply_markup=main_menu())
    key = {"buy_stars": "stars_rate", "buy_gram": "ton_buy_rate", "premium": f"premium_{qty}_rate", "gold": "gold_rate"}[kind]
    default = {"buy_stars": "1.0", "buy_gram": "1.0", "premium": "0", "gold": "0"}[kind]
    try: price = cents(await db.setting(key, default))
    except (ArithmeticError, ValueError): price = 0
    if price <= 0:  # раніше можна було створити замовлення на 0 грн
        await state.clear()
        return await message.answer("Цена для этого товара ещё не настроена. Обратитесь в поддержку.", reply_markup=back())
    if kind == "premium":  # курс — це ціна за весь пакет, а не за місяць
        order_qty, unit, title = 1, price, f"{TITLES[kind]} — {qty} мес."
    else:
        order_qty, unit, title = qty, price, f"{TITLES[kind]} x{qty}"
    await ensure_user(db, user)
    oid = await db.create_order(user.id, kind, title, order_qty, unit, data.get("target", ""))
    await state.clear()
    await message.answer(f"Заказ #{oid}\nТовар: {title}\nК оплате: {money(order_qty * unit)}\n\nВыберите способ оплаты:", reply_markup=pay_methods(oid))


@shop_router.message(Flow.custom_qty)
async def custom_qty(m: Message, state: FSMContext, db):
    text = (m.text or "").strip()
    if not (text.isascii() and text.isdigit()):  # isdigit() пропускає «²» і падає в int()
        return await m.answer("Введите целое число.")
    qty = int(text); kind = (await state.get_data()).get("kind")
    if kind not in TITLES or kind == "premium":
        await state.clear(); return await m.answer("Сессия устарела. Откройте меню заново.", reply_markup=main_menu())
    if qty < 1 or qty > MAX_QTY:
        return await m.answer(f"Допустимое количество: от 1 до {MAX_QTY}.")
    if kind == "buy_stars" and qty < 50:
        return await m.answer("Минимум — 50 Stars.")
    await make_order(m, m.from_user, state, db, qty)


@shop_router.message(Flow.sell_qty)
async def sell_qty(m: Message, db, state: FSMContext):
    try: q = Decimal((m.text or "").replace(",", ".").strip())
    except ArithmeticError: return await m.answer("Введите корректное количество.")
    if not q.is_finite() or q <= 0 or q > 100000:
        return await m.answer("Введите корректное количество.")
    try: rate = Decimal(await db.setting("ton_sell_rate", "0"))
    except ArithmeticError: rate = Decimal(0)
    if not rate.is_finite() or rate <= 0:
        await state.clear(); return await m.answer("Курс продажи пока не настроен. Обратитесь в поддержку.", reply_markup=back())
    await m.answer(f"Количество: {format(q.normalize(), 'f')} TON\nКурс: {format(rate.normalize(), 'f')} UAH\nК выплате: {(q * rate).quantize(Decimal('0.01'))} UAH\nДля оформления напишите в поддержку.", reply_markup=back())
    await state.clear()


@shop_router.message(Flow.nft_url)
async def nft_url(m: Message, db, state: FSMContext):
    url = (m.text or "").strip()
    if len(url) > 500 or not re.match(r"^https://(getgems\.io|fragment\.com)/\S*$", url):
        return await m.answer("Поддерживаются публичные ссылки Getgems или Fragment.")
    cur = await db.execute("INSERT INTO nft_sales(user_id,username,url,created_at) VALUES(?,?,?,?)", (m.from_user.id, m.from_user.username, url, now()))
    for a in await db.all("SELECT user_id FROM admins"):
        try: await m.bot.send_message(a["user_id"], f"Заявка на продажу NFT #{cur.lastrowid}\nID: {m.from_user.id}\nСсылка: {html.escape(url)}")
        except Exception as e: log.warning("Cannot notify admin %s: %s", a["user_id"], e)
    await m.answer("Заявка передана администрации."); await state.clear()


@shop_router.callback_query(F.data.startswith("item:"))
async def item(c: CallbackQuery, state: FSMContext, db):
    try: pid = int(c.data.split(":")[2])
    except (IndexError, ValueError): return await c.answer("Товар недоступен.", show_alert=True)
    p = await db.one("SELECT * FROM products WHERE id=? AND active=1 AND stock>0", (pid,))
    if not p: return await c.answer("Товар недоступен.", show_alert=True)
    await c.answer()
    await ensure_user(db, c.from_user)
    oid = await db.create_order(c.from_user.id, p["category"], p["title"], 1, p["price_cents"], product_id=p["id"])
    await safe_edit(c, f"Заказ #{oid}\n{html.escape(p['title'])}\nЦена: {money(p['price_cents'])}", pay_methods(oid))


@shop_router.callback_query(F.data.startswith("pay:"))
async def pay(c: CallbackQuery, state: FSMContext, db):
    try:
        _, oid, method = c.data.split(":", 2); oid = int(oid)
    except ValueError:
        return await c.answer("Кнопка устарела. Откройте меню заново.", show_alert=True)
    if method not in PAY_METHODS: return await c.answer("Неизвестный способ оплаты.", show_alert=True)
    # оплата прив'язана до конкретного замовлення, а не до «останнього» користувача
    row = await db.one("SELECT * FROM orders WHERE id=? AND user_id=? AND status='awaiting_payment'", (oid, c.from_user.id))
    if not row: return await c.answer("Активный заказ не найден.", show_alert=True)
    await c.answer()
    if method in ("CryptoBot", "TON"):
        return await safe_edit(c, f"Способ {method} пока не активирован владельцем. Выберите банковский перевод или поддержку.", back())
    bank = await db.setting(bank_key(method), "")
    if not bank: return await safe_edit(c, f"Реквизиты {html.escape(method)} не настроены. Обратитесь в поддержку.", back())
    await db.execute("UPDATE payments SET status='cancelled' WHERE order_id=? AND status='pending' AND method!=?", (oid, method))
    if not await db.one("SELECT id FROM payments WHERE order_id=? AND method=? AND status='pending'", (oid, method)):
        await db.execute("INSERT INTO payments(order_id,method,amount_cents,status,created_at) VALUES(?,?,?,?,?)", (oid, method, row["total_cents"], "pending", now()))
    await state.update_data(receipt_order=oid, receipt_method=method)
    await state.set_state(Flow.receipt)
    await safe_edit(c, f"Заказ #{oid}\nСумма: {money(row['total_cents'])}\n{html.escape(bank)}\n\nПосле оплаты отправьте фото или PDF квитанции.")


@shop_router.message(Flow.receipt)
async def receipt(m: Message, state: FSMContext, db):
    data = await state.get_data()
    if m.photo: fid, is_photo = m.photo[-1].file_id, True
    elif m.document: fid, is_photo = m.document.file_id, False
    else: return await m.answer("Отправьте фото или документ квитанции.")
    order = await db.one("SELECT * FROM orders WHERE id=? AND user_id=? AND status='awaiting_payment'", (data.get("receipt_order"), m.from_user.id))
    if not order:
        await state.clear(); return await m.answer("Заказ не найден или квитанция уже отправлена.", reply_markup=main_menu())
    cur = await db.execute("INSERT INTO receipts(order_id,user_id,username,file_id,amount_cents,method,created_at) VALUES(?,?,?,?,?,?,?)",
                           (order["id"], m.from_user.id, m.from_user.username, fid, order["total_cents"], data.get("receipt_method", ""), now()))
    await db.execute("UPDATE orders SET status='awaiting_review',updated_at=? WHERE id=?", (now(), order["id"]))
    b = InlineKeyboardBuilder()
    b.button(text="Подтвердить", callback_data=f"review:yes:{cur.lastrowid}"); b.button(text="Отклонить", callback_data=f"review:no:{cur.lastrowid}"); b.adjust(2)
    caption = f"Квитанция #{cur.lastrowid}, заказ #{order['id']}, пользователь {m.from_user.id}, сумма {money(order['total_cents'])}"
    for a in await db.all("SELECT user_id FROM admins"):
        try:  # фото-file_id не можна надсилати як документ — вибираємо метод за типом
            if is_photo: await m.bot.send_photo(a["user_id"], fid, caption=caption, reply_markup=b.as_markup())
            else: await m.bot.send_document(a["user_id"], fid, caption=caption, reply_markup=b.as_markup())
        except Exception as e: log.warning("Cannot notify admin %s: %s", a["user_id"], e)
    await m.answer("Квитанция получена и отправлена на ручную проверку.")
    await state.clear()


# ======================= ADMIN HANDLERS =======================
log = logging.getLogger(__name__)
admin_router = Router()
RATE_KEYS = {"stars_rate", "ton_buy_rate", "ton_sell_rate", "gold_rate", "premium_1_rate", "premium_3_rate", "premium_6_rate", "premium_12_rate"}
BANK_KEYS = {"bank_" + m.replace(" ", "_") for m in PAY_METHODS if m not in ("CryptoBot", "TON")}


class AdminFlow(StatesGroup):
    setting = State(); product = State(); bank = State()


async def is_admin(db, uid):
    return await db.one("SELECT user_id FROM admins WHERE user_id=?", (uid,)) is not None


def data_dir(db):
    return Path(db.path).parent


@admin_router.message(Command("admin"))
async def admin(m: Message, db, state: FSMContext):
    await state.clear()
    if not await is_admin(db, m.from_user.id): return await m.answer("Нет доступа.")
    await m.answer("Панель администратора", reply_markup=admin_menu())


@admin_router.callback_query(F.data.startswith("adm:"))
async def admin_section(c: CallbackQuery, db, state: FSMContext):
    if not await is_admin(db, c.from_user.id): return await c.answer("Нет доступа", show_alert=True)
    await c.answer()
    await state.clear()
    key = c.data.split(":")[1]
    if key == "stats":
        users = await db.one("SELECT COUNT(*) n FROM users"); orders = await db.one("SELECT COUNT(*) n FROM orders")
        done = await db.one("SELECT COUNT(*) n FROM orders WHERE status IN ('paid','completed')")  # 'completed' ніде не виставлявся
        await safe_edit(c, f"Пользователи: {users['n']}\nЗаказы: {orders['n']}\nОплачено: {done['n']}", admin_menu())
    elif key == "orders":
        rows = await db.all("SELECT id,user_id,title,total_cents,status FROM orders ORDER BY id DESC LIMIT 20")
        txt = "Последние заказы:\n" + "\n".join(f"#{r['id']} | {r['user_id']} | {html.escape(r['title'])} | {money(r['total_cents'])} | {r['status']}" for r in rows) if rows else "Заказов пока нет."
        await safe_edit(c, txt, admin_menu())
    elif key == "receipts":
        rows = await db.all("SELECT id,order_id,user_id,amount_cents,status FROM receipts ORDER BY id DESC LIMIT 20")
        txt = "Архив квитанций:\n" + "\n".join(f"#{r['id']} / заказ #{r['order_id']} / user {r['user_id']} / {money(r['amount_cents'])} / {r['status']}" for r in rows) if rows else "Квитанций пока нет."
        await safe_edit(c, txt, admin_menu())
    elif key == "rates":
        await safe_edit(c, "Отправьте ключ и значение через пробел.\nКлючи: stars_rate, ton_buy_rate, ton_sell_rate, gold_rate, premium_1_rate, premium_3_rate, premium_6_rate, premium_12_rate (цена в UAH; для premium — за весь пакет), support_username\nПример: stars_rate 1.25", admin_menu())
        await state.set_state(AdminFlow.setting)
    elif key == "payments":
        await safe_edit(c, "Отправьте реквизиты в формате:\nbank_Monobank Номер карты; Получатель; Комментарий\nКлючи: " + ", ".join(sorted(BANK_KEYS)) + "\nСекретные API-ключи в этой сборке не принимаются в открытом чате.", admin_menu())
        await state.set_state(AdminFlow.bank)
    elif key == "catalog":
        await safe_edit(c, "Добавление товара:\ncategory | title | description | price UAH | stock\nКатегории: nft, accounts, gold", admin_menu())
        await state.set_state(AdminFlow.product)
    elif key == "backup":
        d = data_dir(db) / "backups"; d.mkdir(parents=True, exist_ok=True)
        dest = d / f"backup_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.sqlite3"
        await db.execute("VACUUM INTO ?", (str(dest),))  # консистентна копія, на відміну від copy2 живого файлу
        await c.message.answer_document(FSInputFile(dest), caption="Резервная копия базы данных.")
    elif key == "logs":
        rows = await db.all("SELECT actor_id,action,object_type,object_id,created_at FROM audit_logs ORDER BY id DESC LIMIT 20")
        txt = "\n".join(f"{r['created_at']} {r['actor_id']} {html.escape(r['action'])} {r['object_type'] or ''}:{r['object_id'] or ''}" for r in rows) or "Журнал пуст."
        await safe_edit(c, txt, admin_menu())
    elif key == "export":
        rows = await db.all("SELECT id,user_id,title,quantity,total_cents,status,created_at FROM orders ORDER BY id")
        path = data_dir(db) / "orders_export.csv"; path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f); w.writerow(["id", "user_id", "title", "quantity", "total_cents", "status", "created_at"])
            for r in rows: w.writerow(list(r))
        await c.message.answer_document(FSInputFile(path))
    else:
        await safe_edit(c, "Раздел доступен владельцу после настройки.", admin_menu())


@admin_router.message(AdminFlow.setting)
async def save_setting(m: Message, db, state: FSMContext):
    if not await is_admin(db, m.from_user.id): return await state.clear()
    try: key, value = (m.text or "").split(maxsplit=1)
    except ValueError: return await m.answer("Формат: ключ значение")
    value = value.strip().replace(",", ".") if key in RATE_KEYS else value.strip()
    if key in RATE_KEYS:
        try:
            if cents(value) <= 0 or not Decimal(value).is_finite(): raise ValueError
        except (ArithmeticError, ValueError):  # раніше будь-який текст ламав оформлення замовлень
            return await m.answer("Значение должно быть положительным числом, например 1.25")
    elif key == "support_username":
        if not re.fullmatch(r"@?[A-Za-z0-9_]{5,32}", value): return await m.answer("Неверный username.")
    else:
        return await m.answer("Неизвестный параметр.")
    await db.set_setting(key, value); await db.log(m.from_user.id, "setting_changed", "settings", key, new=value)
    await state.clear(); await m.answer("Сохранено.")


@admin_router.message(AdminFlow.bank)
async def save_bank(m: Message, db, state: FSMContext):
    if not await is_admin(db, m.from_user.id): return await state.clear()
    try: key, value = (m.text or "").split(maxsplit=1)
    except ValueError: return await m.answer("Формат: bank_Название реквизиты")
    if key not in BANK_KEYS:  # «Alliance Bank» треба писати як bank_Alliance_Bank
        return await m.answer("Неизвестный ключ. Допустимые: " + ", ".join(sorted(BANK_KEYS)))
    await db.set_setting(key, value); await db.log(m.from_user.id, "bank_details_changed", "settings", key, new="[redacted]")
    await state.clear(); await m.answer("Реквизиты сохранены.")


@admin_router.message(AdminFlow.product)
async def add_product(m: Message, db, state: FSMContext):
    if not await is_admin(db, m.from_user.id): return await state.clear()
    try:
        cat, title, desc, price, stock = [x.strip() for x in (m.text or "").split("|", 4)]
        p = cents(price.replace(",", ".")); stock = int(stock)
        if not title or p < 0 or stock < 0 or cat not in ("nft", "accounts", "gold"): raise ValueError()
    except (ValueError, ArithmeticError):
        return await m.answer("Проверьте формат: category | title | description | price | stock")
    cur = await db.execute("INSERT INTO products(category,title,description,price_cents,stock,active,created_at) VALUES(?,?,?,?,?,1,?)", (cat, title, desc, p, stock, now()))
    await db.execute("INSERT INTO inventory(product_id,stock_delta,reason,actor_id,created_at) VALUES(?,?,?,?,?)", (cur.lastrowid, stock, "initial stock", m.from_user.id, now()))
    await db.log(m.from_user.id, "product_created", "product", cur.lastrowid, new=title)
    await state.clear(); await m.answer(f"Товар добавлен. ID: {cur.lastrowid}")


@admin_router.callback_query(F.data.startswith("review:"))
async def review(c: CallbackQuery, db):
    if not await is_admin(db, c.from_user.id): return await c.answer("Нет доступа", show_alert=True)
    try:
        _, decision, rid = c.data.split(":"); rid = int(rid)
    except ValueError:
        return await c.answer("Некорректные данные.", show_alert=True)
    if decision not in ("yes", "no"): return await c.answer("Некорректные данные.", show_alert=True)
    status = "approved" if decision == "yes" else "rejected"
    # атомарно: подвійний клік / два адміни не обробляють квитанцію двічі
    cur = await db.execute("UPDATE receipts SET status=?,reviewed_by=? WHERE id=? AND status='pending'", (status, c.from_user.id, rid))
    if cur.rowcount == 0: return await c.answer("Уже обработано.", show_alert=True)
    r = await db.one("SELECT * FROM receipts WHERE id=?", (rid,))
    order = await db.one("SELECT * FROM orders WHERE id=?", (r["order_id"],))
    await db.execute("UPDATE payments SET status=?,checked_by=? WHERE order_id=? AND status='pending'", (status, c.from_user.id, r["order_id"]))
    await db.execute("UPDATE orders SET status=?,updated_at=? WHERE id=?", ("paid" if decision == "yes" else "rejected", now(), r["order_id"]))
    note = ""
    if decision == "yes" and order and order["product_id"]:  # склад раніше ніколи не зменшувався
        q = order["quantity"]
        cur = await db.execute("UPDATE products SET stock=stock-? WHERE id=? AND stock>=?", (q, order["product_id"], q))
        if cur.rowcount: await db.execute("INSERT INTO inventory(product_id,stock_delta,reason,actor_id,created_at) VALUES(?,?,?,?,?)", (order["product_id"], -q, f"order #{order['id']}", c.from_user.id, now()))
        else: note = " Внимание: остаток товара недостаточен, проверьте вручную."
    await db.log(c.from_user.id, "receipt_" + status, "receipt", rid)
    try: await c.bot.send_message(r["user_id"], f"Оплата заказа #{r['order_id']} подтверждена." if decision == "yes" else f"Квитанция по заказу #{r['order_id']} отклонена. Обратитесь в поддержку.")
    except Exception as e: log.warning("Cannot notify user %s: %s", r["user_id"], e)
    try: await c.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    await c.answer("Готово." + note, show_alert=bool(note))

# ======================= MAIN =======================
async def main():
    (BASE_DIR / "data" / "logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[logging.FileHandler(BASE_DIR / "data" / "logs" / "bot.log", encoding="utf-8"), logging.StreamHandler()])
    db = await Database(DATABASE_PATH).connect()
    bot = None
    try:
        token = BOT_TOKEN or await db.setting("bot_token", "")
        if not token:
            logging.error("BOT_TOKEN missing. Set BOT_TOKEN in .env.")
            return
        bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        me = await bot.get_me()
        logging.info("Started as @%s", me.username)
        if BOOTSTRAP_OWNER_ID:
            await db.execute("INSERT OR IGNORE INTO admins(user_id,role,permissions,added_at) VALUES(?,?,?,datetime('now'))",
                             (BOOTSTRAP_OWNER_ID, "owner", '["*"]'))
        elif not await db.one("SELECT 1 FROM admins LIMIT 1"):
            logging.warning("No admins configured: set BOOTSTRAP_OWNER_ID in .env.")
        dp = Dispatcher(storage=MemoryStorage())
        dp.include_router(admin_router)
        dp.include_router(shop_router)
        await dp.start_polling(bot, db=db, allowed_updates=dp.resolve_used_update_types())
    finally:
        if bot:
            await bot.session.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
