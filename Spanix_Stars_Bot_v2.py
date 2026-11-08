import asyncio
import base64
import csv
import contextvars
import difflib
import hashlib
import html
import json
import math
import os
import re
import secrets
import sqlite3
from types import SimpleNamespace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote

import aiohttp
from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ContentType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    CallbackQuery,
    ErrorEvent,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().with_name(".env"))

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CRYPTO_API_URL = os.getenv("CRYPTO_API_URL", "https://pay.crypt.bot/api").rstrip("/")
TONCENTER_API_URL = os.getenv(
    "TONCENTER_API_URL",
    "https://toncenter.com/api/v2",
).rstrip("/")
USDT_UAH_RATE = os.getenv("USDT_UAH_RATE", "41")
_configured_admin_ids = {
    int(x) for x in re.split(r"[\s,;]+", os.getenv("ADMIN_IDS", "")) if x.isdigit()
}
_owner_value = (
    os.getenv("OWNER_ID", "").strip()
    or os.getenv("PRIMARY_ADMIN_ID", "").strip()
    or os.getenv("ADMIN_ID", "").strip()
)
if _owner_value.isdigit():
    PRIMARY_ADMIN_ID = int(_owner_value)
elif len(_configured_admin_ids) == 1:
    # Backward compatibility for installations that configured exactly one
    # administrator before OWNER_ID was introduced.
    PRIMARY_ADMIN_ID = next(iter(_configured_admin_ids))
else:
    PRIMARY_ADMIN_ID = 0
OWNER_IDS = {PRIMARY_ADMIN_ID} if PRIMARY_ADMIN_ID else set()
ADMIN_IDS = set(_configured_admin_ids) | OWNER_IDS
for _admin_env_name in ("ADMIN_ID", "OWNER_ID"):
    _admin_env_value = os.getenv(_admin_env_name, "").strip()
    if _admin_env_value.isdigit():
        _admin_id = int(_admin_env_value)
        ADMIN_IDS.add(_admin_id)
        if _admin_env_name == "OWNER_ID":
            OWNER_IDS.add(_admin_id)
BOT_NAME = "Spanix Stars"
PROJECT_DIR = Path(__file__).resolve().parent
DB_PATH = os.getenv("DB_PATH", str(PROJECT_DIR / "bot.db")).strip()
if DB_PATH != ":memory:":
    db_file = Path(DB_PATH).expanduser()
    if not db_file.is_absolute():
        db_file = PROJECT_DIR / db_file
    db_file.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH = str(db_file.resolve())
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@support")
REVIEWS_URL = os.getenv("REVIEWS_URL", "https://t.me/")

BANK_DETAILS = {
    "private": os.getenv("PRIVATE_DETAILS", "Реквізити Privat24 не налаштовані."),
    "mono": os.getenv("MONO_DETAILS", "Реквізити Mono не налаштовані."),
    "pumb": os.getenv("PUMB_DETAILS", "Реквізити PUMB не налаштовані."),
    "alliance": os.getenv("ALLIANCE_DETAILS", "Реквізити Альянс не налаштовані."),
    "abank": os.getenv("ABANK_DETAILS", "Реквізити А-Банк не налаштовані."),
}

# One source of truth for the banks shown to customers and administrators.
# The same keys are used by payment_method, settings and receipt validation.
BANK_OPTIONS = (
    ("Privat24", "private", "ПриватБанк"),
    ("Mono", "mono", "Monobank"),
    ("PUMB", "pumb", "ПУМБ"),
    ("Альянс", "alliance", "Альянс Банк"),
    ("А-Банк", "abank", "А-Банк"),
)

BANK_DISPLAY_NAMES = {code: display for _, code, display in BANK_OPTIONS}

MAIN_EMOJI = {
    "buy_stars": "5848021027782661221",
    "withdraw_stars": "5920281855378068765",
    "buy_gram": "5265151230790884988",
    "premium": "5848021027782661221",
    "sell_stars": "5301176488756788169",
    "profile": "5920108570627544286",
    "calculator": "6035084557378654059",
    "reviews": "5816567393136154162",
    "support": "5300801237464135848",
}

PREMIUM_EMOJI_ENABLED = os.getenv("PREMIUM_EMOJI_ENABLED", "1").strip() == "1"
CUSTOM_EMOJI_ID_MAX = (1 << 64) - 1

WELCOME_EMOJI = {
    "stars": "5920281855378068765",
    "ton": "5264760470371328402",
    "fire": "5303061717406727152",
}

EMOJI_POOL = [
    "5848021027782661221", "5474311027095000780", "5920281855378068765",
    "5271934564699226262", "5206607081334906820", "5296588050640420683",
    "5296437159849376813", "5326056199215406977", "5429515712198637494",
    "5296727834646036322", "5265151230790884988", "5264760470371328402",
    "5301246586918024418", "6039402906476613311", "5301276827782755360",
    "5474174000458389011", "5303061717406727152", "5440660757194744323",
    "5443038326535759644", "5305522282695768654", "5440539497383087970",
    "5235579174072112613", "5416041192905265756", "5262622667579603340",
    "5296562641613897196", "5296727834646036322", "5848021027782661221",
    "6035084557378654059", "5816567393136154162", "5300801237464135848",
]

# Every customer-facing screen has its own configurable banner. Keeping this
# list separate from MENU_LABELS makes it possible to add a screen without
# forgetting its setting in the admin panel.
UI_BANNER_KEYS = (
    "language",
    "welcome",
    "stars",
    "ton",
    "premium",
    "withdraw",
    "sell",
    "profile",
    "referrals",
    "calculator",
    "reviews",
    "support",
)

UI_BANNER_TITLES = {
    "language": "Вибір мови",
    "welcome": "Стартове меню",
    "stars": "Купівля Stars",
    "ton": "Купівля TON",
    "premium": "Telegram Premium",
    "withdraw": "Виведення Stars",
    "sell": "Продаж Stars",
    "profile": "Профіль",
    "referrals": "Реферальна програма",
    "calculator": "Калькулятор",
    "reviews": "Відгуки",
    "support": "Підтримка",
}


def pe(emoji_id: str, fallback: str = "⭐") -> str:
    if not PREMIUM_EMOJI_ENABLED:
        return fallback
    value = str(emoji_id or "").strip()
    if not value.isdigit():
        return fallback
    try:
        if not 0 < int(value) <= CUSTOM_EMOJI_ID_MAX:
            return fallback
    except (TypeError, ValueError):
        return fallback
    return f'<tg-emoji emoji-id="{value}">{fallback}</tg-emoji>'


def esc(value) -> str:
    return html.escape(str(value or ""))


def message_html(message: Message) -> str:
    """Return Telegram's HTML representation for text or media captions."""
    attribute = "html_text" if message.text is not None else "html_caption"
    try:
        rendered = getattr(message, attribute, None)
    except (AttributeError, TypeError):
        rendered = None
    if rendered is not None:
        return str(rendered)
    raw_text = message.text if message.text is not None else message.caption
    return html.escape(raw_text or "")


def strip_custom_emoji_tags(text: str | None) -> str | None:
    if text is None:
        return None
    return re.sub(
        r"<tg-emoji\b[^>]*>(.*?)</tg-emoji>",
        r"\1",
        str(text),
        flags=re.DOTALL | re.IGNORECASE,
    )


def strip_custom_emoji_button_icons(markup):
    """Remove Premium emoji icons from buttons while keeping their labels."""
    if markup is None:
        return None
    if hasattr(markup, "inline_keyboard"):
        rows = [
            [
                button.model_copy(update={"icon_custom_emoji_id": None})
                for button in row
            ]
            for row in markup.inline_keyboard
        ]
        return markup.model_copy(update={"inline_keyboard": rows})
    if hasattr(markup, "keyboard"):
        rows = [
            [
                button.model_copy(update={"icon_custom_emoji_id": None})
                for button in row
            ]
            for row in markup.keyboard
        ]
        return markup.model_copy(update={"keyboard": rows})
    return markup


def has_custom_emoji_button_icons(markup) -> bool:
    if markup is None:
        return False
    rows = (
        markup.inline_keyboard
        if hasattr(markup, "inline_keyboard")
        else markup.keyboard
        if hasattr(markup, "keyboard")
        else ()
    )
    return any(
        bool(button.icon_custom_emoji_id)
        for row in rows
        for button in row
    )


def should_retry_without_custom_emoji(error, text="", caption="", reply_markup=None):
    """Use a formatting-preserving fallback for any Telegram custom emoji error."""
    if not isinstance(error, TelegramBadRequest):
        return False
    return (
        "<tg-emoji" in f"{text or ''}{caption or ''}".casefold()
        or has_custom_emoji_button_icons(reply_markup)
    )


def normalize_reviews_url(raw_value: str):
    value = raw_value.strip()
    if value.startswith("@"):
        username = value[1:].strip()
        return f"https://t.me/{username}" if username else None
    if value.startswith(("t.me/", "telegram.me/")):
        return f"https://{value}"
    if value.startswith(("https://t.me/", "http://t.me/", "https://telegram.me/", "http://telegram.me/")):
        return value
    return None


def _compact_spaces(value):
    return re.sub(r"\s+", " ", str(value or "")).strip(" \t:;-")


def _label_value(lines, labels, start=0, end=None):
    """Find a value on the same line as a label or on the following line."""
    end = len(lines) if end is None else end
    aliases = sorted((label.lower() for label in labels), key=len, reverse=True)
    for index in range(start, end):
        line = _compact_spaces(lines[index])
        lowered = line.lower()
        for label in aliases:
            if not lowered.startswith(label):
                continue
            remainder = line[len(label):].lstrip(" \t:;-")
            if remainder:
                return remainder
            for next_index in range(index + 1, min(index + 3, end)):
                value = _compact_spaces(lines[next_index])
                if value:
                    return value
    return ""


def _section_bounds(lines, section_labels, next_section_labels):
    start = next((i for i, line in enumerate(lines) if any(
        label in line.lower() for label in section_labels
    )), None)
    if start is None:
        return 0, len(lines)
    end = next(
        (i for i in range(start + 1, len(lines))
         if any(label in lines[i].lower() for label in next_section_labels)),
        len(lines),
    )
    return start, end


def _number(value):
    if not value:
        return ""
    match = re.search(r"(?<!\d)(\d[\d\s]*(?:[.,]\d{1,2})?)(?!\d)", value)
    if not match:
        return ""
    return match.group(1).replace(" ", "").replace(",", ".")


def _text_value(value):
    """Remove OCR punctuation around a text field without destroying its value."""
    return _compact_spaces(value).strip(" .,:;|-")


def _identifier(value):
    """Keep receipt identifiers readable while removing common OCR separators."""
    return _text_value(value).replace("№", "").strip()


def _find_value_by_patterns(text, patterns):
    """Return the first value captured by one of several label patterns."""
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            value = _text_value(match.group(1))
            if value:
                return value
    return ""


def _bank_method_match(expected_method, detected_bank):
    """Classify a receipt bank against the bank selected before payment."""
    expected_aliases = {
        "private": ("privat", "приват", "приват24"),
        "mono": ("mono", "монобанк", "monobank"),
        "pumb": ("pumb", "пумб"),
        "alliance": ("alliance", "альянс"),
        "abank": ("a-bank", "a bank", "абанк", "а-банк"),
    }.get(expected_method, ())
    if not expected_method:
        return ""
    if not detected_bank:
        return "Банк не визначено"
    detected_key = _bank_key(detected_bank)
    if any(_bank_key(alias) in detected_key or detected_key in _bank_key(alias)
           for alias in expected_aliases):
        return "Збігається"
    return "Не збігається"


def _amount_match(receipt_amount, order_amount):
    """Compare a parsed receipt amount to the amount of the current order."""
    if not receipt_amount or order_amount in (None, ""):
        return ""
    try:
        receipt_value = round(float(str(receipt_amount).replace(",", ".")), 2)
        order_value = round(float(order_amount), 2)
    except (TypeError, ValueError):
        return "Не вдалося перевірити"
    return "Збігається" if receipt_value == order_value else "Не збігається"


def _is_payment_system(value):
    return bool(re.fullmatch(
        r"(visa|mastercard|maestro|amex|мир|віза|master card)",
        value.strip(),
        re.IGNORECASE,
    ))


def _is_money_line(value):
    return bool(re.fullmatch(r"\d{1,6}(?:[.,]\d{1,2})?", value.replace(" ", "")))


RECEIPT_BANK_MARKERS = (
    # Ukrainian banks and the names commonly printed on their receipts.
    # Keep aliases compact and include the names used by mobile banking apps:
    # OCR often drops spaces, hyphens, quotes, or the "АТ" prefix.
    ("monobank", "Monobank"),
    ("mono", "Monobank"),
    ("mono bank", "Monobank"),
    ("приватбанк", "ПриватБанк"),
    ("privatbank", "PrivatBank"),
    ("приват банк", "ПриватБанк"),
    ("приват24", "Privat24"),
    ("privat24", "Privat24"),
    ("пумб", "ПУМБ"),
    ("pumb", "PUMB"),
    ("pumb bank", "PUMB"),
    ("а-банк", "А-Банк"),
    ("а банк", "А-Банк"),
    ("a-bank", "A-Bank"),
    ("a bank", "A-Bank"),
    ("абанк", "А-Банк"),
    ("альянс", "Альянс Банк"),
    ("alliance bank", "Alliance Bank"),
    ("ощадбанк", "Ощадбанк"),
    ("oschadbank", "Oschadbank"),
    ("райффайзен", "Райффайзен Банк"),
    ("raiffeisen", "Raiffeisen Bank"),
    ("райф", "Райффайзен Банк"),
    ("сенс банк", "Sense Bank"),
    ("sense bank", "Sense Bank"),
    ("сенсбанк", "Sense Bank"),
    ("sensebank", "Sense Bank"),
    ("альфа-банк", "Альфа-Банк"),
    ("alfabank", "Alfa Bank"),
    ("укрсиббанк", "УкрСиббанк"),
    ("ukrsibbank", "Ukrsibbank"),
    ("укрсиб", "УкрСиббанк"),
    ("укргазбанк", "Укргазбанк"),
    ("ukrgasbank", "Ukrgasbank"),
    ("укрексімбанк", "Укрексімбанк"),
    ("укрэксимбанк", "Укрэксимбанк"),
    ("ukreximbank", "Ukreximbank"),
    ("otp bank", "OTP Bank"),
    ("otp банк", "OTP Bank"),
    ("отп банк", "OTP Bank"),
    ("креді агріколь", "Credit Agricole"),
    ("креди агриколь", "Credit Agricole"),
    ("credit agricole", "Credit Agricole"),
    ("універсал банк", "Універсал Банк"),
    ("universal bank", "Universal Bank"),
    ("універсалбанк", "Універсал Банк"),
    ("universalbank", "Universal Bank"),
    ("таскомбанк", "ТАСКОМБАНК"),
    ("taskombank", "Tascombank"),
    ("кредобанк", "Кредобанк"),
    ("kredobank", "KredoBank"),
    ("банк кредит дніпро", "Банк Кредит Дніпро"),
    ("credit dnipro", "Credit Dnipro"),
    ("про kredit", "ProCredit Bank"),
    ("procredit", "ProCredit Bank"),
    ("ідея банк", "Ідея Банк"),
    ("idea bank", "Idea Bank"),
    ("піреус", "Піреус Банк"),
    ("piraeus", "Piraeus Bank"),
    ("банк восток", "Банк Восток"),
    ("bank vostok", "Bank Vostok"),
    ("банк львів", "Банк Львів"),
    ("bank lviv", "Bank Lviv"),
    ("мтб банк", "МТБ БАНК"),
    ("mtb bank", "MTB Bank"),
    ("izibank", "izibank"),
    ("izi банк", "izibank"),
    ("ізібанк", "izibank"),
    ("іsіbank", "izibank"),
    ("бісбанк", "BISBANK"),
    ("bisbank", "BISBANK"),
    ("банк альянс", "Альянс Банк"),
    ("банк інвестицій та заощаджень", "Банк інвестицій та заощаджень"),
    ("банк інвестицій", "Банк інвестицій та заощаджень"),
    ("бізбанк", "БІЗБАНК"),
    ("глобус банк", "Глобус Банк"),
    ("globus bank", "Globus Bank"),
    ("глобусбанк", "Глобус Банк"),
    ("необанк", "NEOBANK"),
    ("neobank", "NEOBANK"),
    ("юнекс банк", "Юнекс Банк"),
    ("unex bank", "Unex Bank"),
    ("юникс банк", "Юнекс Банк"),
    ("правекс банк", "Правекс Банк"),
    ("pravex bank", "Pravex Bank"),
    ("індустріалбанк", "Індустріалбанк"),
    ("industrialbank", "Industrialbank"),
    ("кристалбанк", "Кристалбанк"),
    ("crystalbank", "CrystalBank"),
    ("комінбанк", "COMINBANK"),
    ("cominbank", "COMINBANK"),
    ("мегабанк", "МЕГАБАНК"),
    ("megabank", "MEGABANK"),
    ("банк південний", "Банк Південний"),
    ("bank pivdennyi", "Bank Pivdennyi"),
    ("південний", "Банк Південний"),
    ("мiжнародний інвестиційний банк", "МІБ"),
    ("міжнародний інвестиційний банк", "МІБ"),
    ("mib", "MIB"),
    ("сredit agricole", "Credit Agricole"),
    ("сredit agricole", "Credit Agricole"),
    ("кредитвест банк", "Кредитвест Банк"),
    ("creditwest bank", "Creditwest Bank"),
    ("банк 3/4", "Банк 3/4"),
    ("банк три чверті", "Банк 3/4"),
    ("сhіпабанк", "Чінабaнк"),
    ("чинaбанк", "Чінабaнк"),
    ("china construction bank", "China Construction Bank"),
    ("сітібанк", "Citibank"),
    ("citibank", "Citibank"),
    ("деutsche bank", "Deutsche Bank"),
    ("deutsche bank", "Deutsche Bank"),
    ("юсіb", "USB"),
    ("укркапітал", "Український капітал"),
    ("український капітал", "Український капітал"),
    ("ukrcapital", "Ukrainian Capital Bank"),
)


def _bank_key(value):
    """Normalize a bank name for OCR-tolerant matching."""
    value = str(value or "").lower()
    # OCR may mix visually similar Latin/Cyrillic characters in one word.
    value = value.translate(str.maketrans({
        "а": "a", "е": "e", "і": "i", "ї": "i", "є": "e",
        "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    }))
    return re.sub(r"[^a-z0-9а-яіїєґ]", "", value)


def _bank_alias_matches(alias, normalized_text, lines):
    alias_key = _bank_key(alias)
    if not alias_key:
        return False
    if alias_key in normalized_text:
        return True

    # A short OCR typo (for example "monobark") should not turn a receipt
    # into an unknown bank. Only use fuzzy matching for longer aliases and
    # compare individual OCR words to avoid matching a whole unrelated line.
    if len(alias_key) < 7:
        return False
    for line in lines:
        for word in re.findall(r"[a-zа-яіїєґ0-9]{5,}", line.lower()):
            word_key = _bank_key(word)
            if abs(len(word_key) - len(alias_key)) > 2:
                continue
            if difflib.SequenceMatcher(None, alias_key, word_key).ratio() >= 0.84:
                return True
    return False


def _detect_receipt_bank(lines, raw_text):
    """Return a known bank name or a bank name read directly by OCR."""
    normalized_text = _bank_key(raw_text)
    for marker, bank_name in RECEIPT_BANK_MARKERS:
        if _bank_alias_matches(marker, normalized_text, lines):
            return bank_name

    # Keep working with a bank not listed above. Do not mistake field labels
    # such as "Банк одержувача" for the bank itself.
    ignored = (
        "банк одержувача", "банк получателя", "банк відправника",
        "банк отправителя", "код банку", "bank code", "recipient bank",
        "sender bank",
    )
    for line in lines:
        lowered = line.lower()
        if ("банк" in lowered or "bank" in lowered) and not any(
            label in lowered for label in ignored
        ):
            return line
    return ""


def _parse_columnar_receipt(lines, result):
    """Handle bank receipts where OCR reads the left column before the right."""
    bank_code_index = next(
        (
            i for i, line in enumerate(lines)
            if re.fullmatch(r"\d{6}", line.replace(" ", ""))
        ),
        None,
    )
    if bank_code_index is None or bank_code_index < 2:
        return

    sender_bank = lines[bank_code_index - 1]
    sender_name = lines[bank_code_index - 2]
    if "банк" not in sender_bank.lower() and "bank" not in sender_bank.lower():
        return

    result["sender_name"] = sender_name
    result["sender_bank"] = sender_bank
    result["sender_bank_code"] = lines[bank_code_index].replace(" ", "")

    sender_payment_index = next(
        (i for i in range(bank_code_index + 1, min(bank_code_index + 5, len(lines)))
         if _is_payment_system(lines[i])),
        None,
    )
    if sender_payment_index is None:
        return
    result["sender_payment_system"] = lines[sender_payment_index]

    receiver_bank_index = next(
        (
            i for i in range(sender_payment_index + 2, len(lines))
            if ("банк" in lines[i].lower() or "bank" in lines[i].lower())
            and "код" not in lines[i].lower()
        ),
        None,
    )
    if receiver_bank_index is None or receiver_bank_index < sender_payment_index + 2:
        return
    receiver_name_index = receiver_bank_index - 1
    result["receiver_name"] = lines[receiver_name_index]
    result["receiver_bank"] = lines[receiver_bank_index]

    receiver_payment_index = next(
        (i for i in range(receiver_bank_index + 1, min(receiver_bank_index + 4, len(lines)))
         if _is_payment_system(lines[i])),
        None,
    )
    if receiver_payment_index is None:
        return
    result["receiver_payment_system"] = lines[receiver_payment_index]

    sender_instrument_lines = lines[sender_payment_index + 1:receiver_name_index]
    if sender_instrument_lines:
        result["sender_instrument"] = " ".join(sender_instrument_lines)

    receiver_instrument_index = receiver_payment_index + 1
    if receiver_instrument_index < len(lines):
        result["receiver_instrument"] = lines[receiver_instrument_index]

    amount_index = next(
        (
            i for i in range(receiver_instrument_index + 1, len(lines))
            if re.fullmatch(r"\d{1,6}[.,]\d{1,2}", lines[i].replace(" ", ""))
        ),
        None,
    )
    if amount_index is None:
        return
    result["amount"] = lines[amount_index].replace(" ", "").replace(",", ".")

    fee_index = next(
        (i for i in range(amount_index + 1, min(amount_index + 4, len(lines)))
         if _is_money_line(lines[i])),
        None,
    )
    if fee_index is not None:
        result["fee"] = lines[fee_index].replace(" ", "").replace(",", ".")
        if result["fee"] in {"0", "00", "000"}:
            result["fee"] = "0.00"

    date_index = next(
        (
            i for i in range(amount_index + 1, len(lines))
            if re.fullmatch(
                r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\s+\d{1,2}:\d{2}(?::\d{2})?",
                lines[i],
            )
        ),
        None,
    )
    auth_index = next(
        (
            i for i in range((fee_index or amount_index) + 1, len(lines))
            if re.fullmatch(r"\d{6}", lines[i].replace(" ", ""))
        ),
        None,
    )
    if auth_index is not None:
        result["authorization_code"] = lines[auth_index].replace(" ", "")
    if date_index is not None:
        result["operation_datetime"] = lines[date_index]
        if auth_index is not None and auth_index < date_index:
            purpose_lines = [
                line for line in lines[auth_index + 1:date_index]
                if line and "сум" not in line.lower() and len(line) > 2
            ]
            if purpose_lines:
                result["payment_purpose"] = " ".join(purpose_lines)
        if date_index + 1 < len(lines):
            result["device_id"] = lines[date_index + 1].rstrip(":")


def parse_receipt_text(raw_text):
    """Extract common receipt fields while keeping the original OCR text."""
    text = raw_text or ""
    lines = [_compact_spaces(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    lower_text = text.lower()
    result = {
        "ocr_status": "Розпізнано" if lines else "Потрібна перевірка",
        "receipt_number": "",
        "bank": "",
        "transaction_id": "",
        "operation_id": "",
        "rrn": "",
        "status": "",
        "operation_datetime": "",
        "currency": "",
        "sender_name": "",
        "sender_bank": "",
        "sender_bank_code": "",
        "sender_account": "",
        "sender_card": "",
        "sender_payment_system": "",
        "sender_instrument": "",
        "receiver_name": "",
        "receiver_bank": "",
        "receiver_bank_code": "",
        "receiver_account": "",
        "receiver_card": "",
        "receiver_payment_system": "",
        "receiver_instrument": "",
        "amount": "",
        "fee": "",
        "total_amount": "",
        "authorization_code": "",
        "payment_purpose": "",
        "comment": "",
        "merchant": "",
        "device_id": "",
    }

    receipt_line = _label_value(lines, (
        "квитанція", "квитанция", "receipt number", "receipt no", "receipt №",
    ))
    if receipt_line:
        number_match = re.search(
            r"(?:№|no\.?)\s*([A-Za-zА-Яа-яІіЇїЄє0-9][A-Za-zА-Яа-яІіЇїЄє0-9/_-]*)",
            receipt_line,
            re.IGNORECASE,
        )
        result["receipt_number"] = number_match.group(1) if number_match else receipt_line

    result["bank"] = _detect_receipt_bank(lines, lower_text)

    # Bank apps use different names for the same identifiers. Parse these
    # before sender/receiver sections so compact receipts are covered too.
    result["transaction_id"] = _identifier(_label_value(lines, (
        "ідентифікатор транзакції", "идентификатор транзакции",
        "transaction id", "transaction number", "номер транзакції",
        "номер транзакции", "номер операції", "номер операции",
    )))
    result["operation_id"] = _identifier(_label_value(lines, (
        "id операції", "id операции", "operation id", "операція id",
        "операция id", "reference", "референс", "референс платежу",
    )))
    result["rrn"] = _identifier(_label_value(lines, (
        "rrn", "retrieval reference number", "код трансакції",
        "код транзакції", "код транзакции",
    )))
    result["status"] = _text_value(_label_value(lines, (
        "статус платежу", "статус платежа", "payment status", "status",
    )))
    result["currency"] = _text_value(_label_value(lines, (
        "валюта", "currency", "валюта операції", "валюта платежу",
    )))

    result["operation_datetime"] = _label_value(lines, (
        "дата та час операції", "дата і час операції", "дата операції",
        "дата платежу", "дата и время операции", "transaction date",
        "дата переказу", "дата перевода", "created at",
    ))
    if not result["operation_datetime"]:
        date_match = re.search(
            r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\s+\d{1,2}:\d{2}(?::\d{2})?)\b"
            r"|\b(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\b",
            text,
        )
        if date_match:
            result["operation_datetime"] = date_match.group(1) or date_match.group(2)

    sender_start, sender_end = _section_bounds(
        lines,
        ("відправник", "отправитель", "sender", "payer"),
        ("одержувач", "получатель", "receiver", "recipient", "деталі транзакції",
         "детали операции", "transaction details"),
    )
    receiver_start, receiver_end = _section_bounds(
        lines,
        ("одержувач", "получатель", "receiver", "recipient"),
        ("деталі транзакції", "детали операции", "transaction details"),
    )
    sender = (
        ("sender_name", ("ім'я", "ім’я", "имя", "name")),
        ("sender_bank", ("банк відправника", "банк отправителя", "sender bank", "bank")),
        ("sender_bank_code", ("код банку", "bank code")),
        ("sender_account", (
            "рахунок відправника", "счет отправителя", "sender account",
            "рахунок платника", "счет плательщика", "payer account",
            "iban відправника", "iban отправителя",
        )),
        ("sender_card", (
            "картка відправника", "карта отправителя", "sender card",
            "картка платника", "карта плательщика", "payer card",
        )),
        ("sender_payment_system", ("платіжна система", "платежная система", "payment system")),
        ("sender_instrument", ("платіжний інструмент", "платежный инструмент", "payment instrument")),
    )
    for key, labels in sender:
        result[key] = _label_value(lines, labels, sender_start, sender_end)

    receiver = (
        ("receiver_name", ("ім'я", "ім’я", "имя", "name")),
        ("receiver_bank", ("банк одержувача", "банк получателя", "recipient bank", "bank")),
        ("receiver_bank_code", ("код банку одержувача", "код банка получателя", "recipient bank code")),
        ("receiver_account", (
            "рахунок одержувача", "счет получателя", "receiver account",
            "рахунок отримувача", "счет получателя", "recipient account",
            "iban одержувача", "iban получателя",
        )),
        ("receiver_card", (
            "картка одержувача", "карта получателя", "receiver card",
            "картка отримувача", "карта получателя", "recipient card",
        )),
        ("receiver_payment_system", ("платіжна система", "платежная система", "payment system")),
        ("receiver_instrument", ("платіжний інструмент", "платежный инструмент", "payment instrument")),
    )
    for key, labels in receiver:
        result[key] = _label_value(lines, labels, receiver_start, receiver_end)

    # Mobile receipts frequently put the party name directly after the label
    # instead of creating a separate "sender" section.
    if not result["sender_name"]:
        result["sender_name"] = _label_value(lines, (
            "відправник", "отправитель", "sender", "payer",
            "платник", "плательщик",
        ))
    if not result["receiver_name"]:
        result["receiver_name"] = _label_value(lines, (
            "одержувач", "получатель", "receiver", "recipient",
            "отримувач", "получатель платежа", "одержувач платежу",
        ))

    result["amount"] = _number(_label_value(lines, (
        "сума (грн)", "сума платежу", "сумма (грн)", "сумма платежа",
        "сума", "сумма", "amount", "payment amount",
    )))
    result["fee"] = _number(_label_value(lines, (
        "комісія (грн)", "комиссия (грн)", "комісія", "комиссия", "fee",
    )))
    result["total_amount"] = _number(_label_value(lines, (
        "сума з комісією", "сумма с комиссией", "total amount", "total",
    )))
    result["authorization_code"] = _label_value(lines, (
        "код авторизації", "код авторизации", "authorization code", "auth code",
    ))
    result["payment_purpose"] = _label_value(lines, (
        "призначення платежу", "назначение платежа", "payment purpose", "description",
        "призначення переказу", "назначение перевода", "деталі платежу",
        "детали платежа",
    ))
    result["comment"] = _label_value(lines, (
        "коментар", "комментарий", "comment", "примітка", "примечание",
    ))
    result["merchant"] = _label_value(lines, (
        "торговець", "торговец", "merchant", "одержувач платежу",
        "получатель платежа",
    ))
    result["device_id"] = _label_value(lines, (
        "ідентифікатор платіжного пристрою", "идентификатор платежного устройства",
        "device id", "terminal id",
    ))

    # Compact receipt variants often have no visible sender/receiver section.
    compact_fields = {
        "sender_account": ("рахунок платника", "счет плательщика", "payer account"),
        "receiver_account": ("рахунок отримувача", "счет получателя", "recipient account"),
        "sender_card": ("картка платника", "карта плательщика", "payer card"),
        "receiver_card": ("картка отримувача", "карта получателя", "recipient card"),
        "authorization_code": ("код авторизації", "код авторизации", "authorization code", "auth code"),
    }
    for key, labels in compact_fields.items():
        if not result[key]:
            result[key] = _label_value(lines, labels)

    # Last-resort patterns for labelled identifiers and IBANs.
    if not result["transaction_id"]:
        result["transaction_id"] = _find_value_by_patterns(lower_text, (
            r"(?:номер\s+(?:операції|операции|транзакції|транзакции)|"
            r"transaction\s+(?:id|number))\s*[:№#-]?\s*([a-zа-яіїєґ0-9/_-]{4,})",
        ))
    if not result["rrn"]:
        result["rrn"] = _find_value_by_patterns(lower_text, (
            r"\brrn\s*[:#-]?\s*([a-z0-9-]{6,})",
        ))
    iban_match = re.search(r"\b([A-Z]{2}\d{2}[A-Z0-9]{11,30})\b", text, re.IGNORECASE)
    if iban_match and not result["receiver_account"]:
        result["receiver_account"] = iban_match.group(1).upper()
    if not result["currency"]:
        currency_match = re.search(
            r"(?:сума|сумма|amount|total)\s*[:(]?[^\n]{0,40}?\b(UAH|грн|₴|USD|EUR)\b",
            text,
            re.IGNORECASE,
        )
        if currency_match:
            result["currency"] = currency_match.group(1).upper()

    # Some banks print all labels first and all values below them.
    _parse_columnar_receipt(lines, result)

    # Some banks print fields in a compact line without a visible label.
    if not result["sender_bank_code"]:
        result["sender_bank_code"] = _label_value(lines, ("код банку", "bank code"))
    if not result["total_amount"] and result["amount"] and result["fee"] in {"0", "0.00"}:
        result["total_amount"] = result["amount"]

    found_values = sum(bool(value) for key, value in result.items() if key != "ocr_status")
    if not found_values:
        result["ocr_status"] = "Потрібна перевірка"
    return result


def ocr_receipt_file(path):
    """OCR an image/PDF if optional OCR packages are installed."""
    try:
        from PIL import Image, ImageEnhance, ImageOps
        import pytesseract
    except ImportError:
        return {}, "", "Для OCR потрібні пакети Pillow та pytesseract."

    suffix = path.suffix.lower()
    images = []
    try:
        if suffix == ".pdf":
            try:
                import fitz
            except ImportError:
                return {}, "", "Для OCR PDF потрібен пакет PyMuPDF."
            pdf = fitz.open(path)
            for page in pdf:
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                images.append(Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples))
        else:
            images.append(Image.open(path))
    except Exception as exc:
        return {}, "", f"Не вдалося відкрити файл квитанції: {exc}"

    requested_languages = [
        item.strip()
        for item in os.getenv("TESSERACT_LANG", "ukr+rus+eng").split("+")
        if item.strip()
    ]
    try:
        installed_languages = set(pytesseract.get_languages(config=""))
    except Exception:
        installed_languages = set()
    available_languages = [
        language for language in requested_languages
        if not installed_languages or language in installed_languages
    ]
    language = "+".join(available_languages) or "eng"
    pages = []
    for image in images:
        try:
            # Receipts use small grey text and bank logos. A second,
            # high-contrast pass with a different page segmentation mode
            # recovers bank names that a single OCR pass commonly misses.
            prepared = ImageOps.autocontrast(ImageOps.grayscale(image))
            prepared = ImageEnhance.Contrast(prepared).enhance(1.6)
            if max(prepared.size) < 1800:
                scale = 1800 / max(prepared.size)
                prepared = prepared.resize(
                    (int(prepared.width * scale), int(prepared.height * scale))
                )
            candidates = [
                pytesseract.image_to_string(image, lang=language, config="--psm 6"),
                pytesseract.image_to_string(prepared, lang=language, config="--psm 11"),
            ]
            # Preserve all useful OCR lines; parse_receipt_text deduplicates
            # fields while the original text remains available to the admin.
            pages.append("\n".join(text for text in candidates if text.strip()))
        except Exception:
            # Try each installed language before giving up. This keeps Cyrillic
            # receipts working when only one of ukr/rus is installed.
            try:
                page_text = ""
                for fallback_language in ("ukr", "rus", "eng"):
                    if installed_languages and fallback_language not in installed_languages:
                        continue
                    try:
                        page_text = pytesseract.image_to_string(
                            image,
                            lang=fallback_language,
                            config="--psm 6",
                        )
                        if page_text.strip():
                            break
                    except Exception:
                        continue
                if page_text:
                    pages.append(page_text)
                else:
                    raise RuntimeError("не знайдено доступну мову Tesseract")
            except Exception as exc:
                return {}, "", f"Tesseract не налаштований: {exc}"
    text = "\n".join(page for page in pages if page).strip()
    data = parse_receipt_text(text)
    return data, text, ""


def receipt_summary(data, error=""):
    if error:
        return f"⚠️ Не вдалося зберегти файл: {esc(error)}"
    if not data:
        return "🧾 Квитанція доступна для ручної перевірки."
    fields = [
        ("№ квитанції", data.get("receipt_number")),
        ("Банк", data.get("bank")),
        ("Перевірка банку", data.get("bank_match")),
        ("Сума", data.get("amount")),
        ("Перевірка суми", data.get("amount_match")),
        ("Відправник", data.get("sender_name")),
        ("Одержувач", data.get("receiver_name")),
        ("Дата операції", data.get("operation_datetime")),
    ]
    visible = "\n".join(f"• {label}: <b>{esc(value)}</b>" for label, value in fields if value)
    return f"🧾 <b>Потрібна ручна перевірка</b>\n{visible or '• Перевірте квитанцію вручну'}"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row

db.executescript(
    """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    language TEXT DEFAULT '',
    balance_stars INTEGER DEFAULT 0,
    bought_stars INTEGER DEFAULT 0,
    bought_ton REAL DEFAULT 0,
    premium_months INTEGER DEFAULT 0,
    spent_uah REAL DEFAULT 0,
    invited INTEGER DEFAULT 0,
    referrer_id INTEGER,
    referral_earnings_stars INTEGER DEFAULT 0,
    referral_balance_stars INTEGER DEFAULT 0,
    status TEXT DEFAULT 'Новачок',
    registered_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS payment_settings (
    name TEXT PRIMARY KEY,
    encrypted_value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by INTEGER
);

CREATE TABLE IF NOT EXISTS payment_methods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    method_type TEXT NOT NULL DEFAULT 'other',
    encrypted_details TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS required_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    invite_url TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(chat_id)
);

CREATE TABLE IF NOT EXISTS moderators (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    added_by INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS support_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    source_message_id INTEGER,
    status TEXT NOT NULL DEFAULT 'open',
    claimed_by INTEGER,
    claimed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    order_type TEXT NOT NULL,
    quantity TEXT NOT NULL,
    amount REAL NOT NULL,
    payment_method TEXT,
    receipt_file_id TEXT,
    receipt_type TEXT,
    status TEXT DEFAULT 'waiting_payment',
    created_at TEXT NOT NULL,
    admin_note TEXT,
    ton_wallet TEXT,
    referral_rewarded INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS referral_withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount_stars INTEGER NOT NULL,
    ton_wallet TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    admin_id INTEGER,
    admin_note TEXT,
    created_at TEXT NOT NULL,
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS receipt_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    receipt_file_id TEXT NOT NULL,
    receipt_type TEXT,
    receipt_file_name TEXT,
    receipt_ocr_text TEXT,
    receipt_data_json TEXT,
    receipt_parsed_at TEXT,
    receipt_error TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payment_invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    invoice_id TEXT NOT NULL,
    payment_url TEXT,
    currency TEXT,
    amount REAL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    UNIQUE(provider, invoice_id)
);

CREATE TABLE IF NOT EXISTS customer_reviews (
    order_id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    screenshot_file_id TEXT,
    screenshot_type TEXT,
    rating INTEGER CHECK (rating BETWEEN 1 AND 5),
    comment TEXT,
    comment_html TEXT,
    review_no INTEGER,
    reward_stars INTEGER NOT NULL DEFAULT 0,
    reward_paid INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'waiting_rating',
    channel_message_id INTEGER,
    publish_error TEXT,
    created_at TEXT NOT NULL,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS balance_adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    admin_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    amount INTEGER NOT NULL,
    balance_before INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ui_messages (
    user_id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    screen_key TEXT
);

CREATE TABLE IF NOT EXISTS raffles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_stars INTEGER NOT NULL,
    pool_stars INTEGER NOT NULL DEFAULT 0,
    rules TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    winner_id INTEGER,
    prize_stars INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS raffle_entries (
    raffle_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    joined_at TEXT NOT NULL,
    PRIMARY KEY (raffle_id, user_id)
);

CREATE TABLE IF NOT EXISTS raffle_contributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raffle_id INTEGER NOT NULL,
    order_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    purchased_stars INTEGER NOT NULL,
    contributed_stars INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (raffle_id, order_id)
);

CREATE TABLE IF NOT EXISTS raffle_consents (
    raffle_id INTEGER NOT NULL,
    order_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('accepted','declined')),
    created_at TEXT NOT NULL,
    PRIMARY KEY (raffle_id, order_id)
);

CREATE TABLE IF NOT EXISTS game_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    emoji TEXT NOT NULL DEFAULT '🎮',
    delivery_method TEXT NOT NULL CHECK(delivery_method IN ('account','identifier')),
    unit_label TEXT NOT NULL DEFAULT 'Кількість',
    unit_emoji TEXT NOT NULL DEFAULT '📦',
    unit_price REAL NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS game_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL,
    product_name TEXT NOT NULL,
    emoji TEXT NOT NULL DEFAULT '📦',
    quantity TEXT NOT NULL,
    price REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(game_id) REFERENCES game_catalog(id)
);

CREATE TABLE IF NOT EXISTS shop_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL CHECK(category IN ('nft','digital')),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    emoji TEXT NOT NULL DEFAULT '🎁',
    price REAL NOT NULL CHECK(price > 0),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shop_products_category_active
    ON shop_products(category,active,id);

CREATE TABLE IF NOT EXISTS virtual_number_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    emoji TEXT NOT NULL DEFAULT '📱',
    price REAL NOT NULL CHECK(price > 0),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_virtual_number_active ON virtual_number_catalog(active,id);
""")

def ensure_column(table: str, column: str, definition: str):
    cols = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        db.commit()


ensure_column("orders", "ton_wallet", "TEXT")
ensure_column("orders", "receipt_ocr_text", "TEXT")
ensure_column("orders", "receipt_data_json", "TEXT")
ensure_column("orders", "receipt_parsed_at", "TEXT")
ensure_column("orders", "receipt_error", "TEXT")
ensure_column("ui_messages", "screen_key", "TEXT")
ensure_column("orders", "invoice_id", "TEXT")
ensure_column("orders", "payment_url", "TEXT")
ensure_column("orders", "payment_currency", "TEXT")
ensure_column("orders", "payment_amount", "REAL")
ensure_column("orders", "withdrawal_source", "TEXT NOT NULL DEFAULT 'main'")
ensure_column("orders", "withdrawal_reserved", "INTEGER NOT NULL DEFAULT 0")
ensure_column("users", "language", "TEXT")
ensure_column("users", "referrer_id", "INTEGER")
ensure_column("users", "premium_months", "INTEGER NOT NULL DEFAULT 0")
ensure_column("users", "referral_earnings_stars", "INTEGER NOT NULL DEFAULT 0")
ensure_column("users", "referral_balance_stars", "INTEGER NOT NULL DEFAULT 0")
ensure_column("orders", "claimed_by", "INTEGER")
ensure_column("orders", "claimed_at", "TEXT")
ensure_column("orders", "referral_rewarded", "INTEGER NOT NULL DEFAULT 0")
ensure_column("orders", "admin_note_html", "TEXT")
ensure_column("customer_reviews", "comment_html", "TEXT")
ensure_column("customer_reviews", "review_no", "INTEGER")
ensure_column("customer_reviews", "reward_stars", "INTEGER NOT NULL DEFAULT 0")
db.execute(
    """INSERT OR IGNORE INTO payment_invoices(
           order_id,provider,invoice_id,payment_url,currency,amount,status,created_at
       )
       SELECT id,payment_method,invoice_id,payment_url,payment_currency,
              payment_amount,'pending',created_at
       FROM orders
       WHERE payment_method IN ('tonkeeper','cryptobot')
         AND invoice_id IS NOT NULL AND invoice_id!=''"""
)
ensure_column("customer_reviews", "reward_paid", "INTEGER NOT NULL DEFAULT 0")
# Review numbering is administrator-controlled and may intentionally repeat.
# Drop the old unique index on existing installations before creating a
# non-unique lookup index.
db.execute("DROP INDEX IF EXISTS idx_customer_reviews_review_no")
db.execute(
    """CREATE INDEX IF NOT EXISTS idx_customer_reviews_review_no
       ON customer_reviews(review_no) WHERE review_no IS NOT NULL"""
)
db.execute(
    """UPDATE customer_reviews
       SET status='waiting_rating'
       WHERE status='waiting_screenshot'"""
)
db.execute(
    """UPDATE customer_reviews
       SET status='skipped'
       WHERE status IN ('waiting_rating','waiting_comment')
         AND order_id IN (
             SELECT id FROM orders
             WHERE order_type NOT IN (
                  'buy_ton','withdraw_stars','telegram_premium'
             )
         )"""
)
db.execute(
    """UPDATE customer_reviews
       SET status='waiting_rating'
       WHERE status='ready_to_publish' AND rating IS NULL"""
)
db.execute(
    """UPDATE customer_reviews
       SET status='waiting_comment'
       WHERE status='ready_to_publish'
         AND rating IS NOT NULL
         AND NULLIF(TRIM(COALESCE(comment,'')),'') IS NULL"""
)
db.execute(
    """UPDATE customer_reviews
       SET status='pending_moderation'
       WHERE status='ready_to_publish'
         AND rating IS NOT NULL
         AND NULLIF(TRIM(COALESCE(comment,'')),'') IS NOT NULL"""
)
db.execute(
    """UPDATE customer_reviews
       SET status='publish_failed',
           publish_error=COALESCE(
               NULLIF(TRIM(COALESCE(publish_error,'')),''),
               'Публікація перервалася під час роботи бота. Перевірте канал перед повторною спробою, щоб уникнути дубля.'
           )
       WHERE status='publishing'"""
)
db.commit()

# Preserve receipts saved by an earlier version before the archive table
# existed. This migration is idempotent for each order/file pair.
db.execute(
    """INSERT INTO receipt_archive(
           order_id,user_id,receipt_file_id,receipt_type,receipt_ocr_text,
           receipt_data_json,receipt_parsed_at,receipt_error,created_at
       )
       SELECT orders.id,orders.user_id,orders.receipt_file_id,orders.receipt_type,
              orders.receipt_ocr_text,orders.receipt_data_json,
              orders.receipt_parsed_at,orders.receipt_error,
              COALESCE(orders.receipt_parsed_at, orders.created_at)
       FROM orders
       WHERE orders.receipt_file_id IS NOT NULL
         AND NOT EXISTS (
             SELECT 1
             FROM receipt_archive
             WHERE receipt_archive.order_id=orders.id
               AND receipt_archive.receipt_file_id=orders.receipt_file_id
         )"""
)
db.commit()


MODERATOR_IDS = set()


def refresh_staff_ids():
    """Load persistent moderators without granting them full admin access."""
    MODERATOR_IDS.clear()
    MODERATOR_IDS.update(
        int(row["user_id"])
        for row in db.execute("SELECT user_id FROM moderators").fetchall()
    )


refresh_staff_ids()


def is_owner(user_id):
    return int(user_id) in OWNER_IDS


def is_admin(user_id):
    return int(user_id) in ADMIN_IDS


def is_staff(user_id):
    return is_admin(user_id) or int(user_id) in MODERATOR_IDS


def staff_ids():
    return ADMIN_IDS | MODERATOR_IDS


DEFAULTS = {
    "stars_rate": "0.73",
    "ton_rate": "66.71",
    "ton_payment_rate": os.getenv("TON_PAYMENT_UAH_RATE", "66.71"),
    "usdt_rate": USDT_UAH_RATE,
    "premium_price_3": "0",
    "premium_price_6": "0",
    "premium_price_12": "0",
    "min_withdraw": "50",
    "referral_percent": "10",
    "support": SUPPORT_USERNAME,
    "reviews_url": REVIEWS_URL,
    "review_next_number": "1",
    # This is a reporting checkpoint, not a data deletion flag. Resetting
    # statistics keeps users, orders and receipts intact.
    "stats_reset_at": "",
    # This is an archive checkpoint, not a data deletion flag. Resetting the
    # receipts screen keeps the source records available in the database.
    "receipts_reset_at": "",
    # Empty values mean that the bundled asset is used. Custom Telegram
    # file_ids are stored here and therefore survive restarts and redeploys.
    "banner_language": "",
    "banner_welcome": "",
}
for banner_key in UI_BANNER_KEYS:
    DEFAULTS.setdefault(f"banner_{banner_key}", "")
for method, details in BANK_DETAILS.items():
    DEFAULTS[f"bank_{method}"] = details
for key, value in DEFAULTS.items():
    db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))
db.commit()


def setting(key):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else ""


def set_setting(key, value):
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, str(value)))
    db.commit()


def premium_price(months):
    try:
        value = float(setting(f"premium_price_{int(months)}"))
        return value if math.isfinite(value) and value > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def premium_months_uk(months):
    months = int(months)
    return f"{months} місяці" if months == 3 else f"{months} місяців"


def premium_duration_label(months, language="uk"):
    months = int(months)
    return {
        "uk": {3: "3 місяці", 6: "6 місяців", 12: "12 місяців"},
        "ru": {3: "3 месяца", 6: "6 месяцев", 12: "12 месяцев"},
        "en": {3: "3 months", 6: "6 months", 12: "12 months"},
    }[language][months]


def order_type_title(order):
    order_type = order["order_type"]
    if order_type == "telegram_premium":
        return "Telegram Premium"
    if order_type in {"nft", "nft_purchase"}:
        return "NFT"
    if order_type == "digital_product":
        return "Товар"
    if order_type == "virtual_number":
        return "Віртуальний номер"
    return order_type


def order_quantity_title(order):
    if order["order_type"] == "telegram_premium":
        return premium_months_uk(order["quantity"])
    return str(order["quantity"])


def delete_settings_like(pattern):
    db.execute("DELETE FROM settings WHERE key LIKE ?", (pattern,))
    db.commit()


def menu_label(key, language):
    """Return an admin override, falling back to the built-in translation."""
    override = setting(f"menu_label:{key}:{language}")
    return override or MENU_LABELS[key].get(language, MENU_LABELS[key]["uk"])


def menu_emoji(key):
    return setting(f"menu_emoji:{key}") or MENU_DEFAULT_EMOJI[key]


def menu_style(key):
    value = setting(f"menu_style:{key}")
    return (
        value
        if value in {"primary", "success", "danger", "transparent"}
        else MENU_DEFAULT_STYLES[key]
    )


def custom_emoji_id_from_message(message):
    """Extract the first Telegram Premium Emoji entity from a message."""
    return next(
        (
            entity.custom_emoji_id
            for entity in (message.entities or [])
            if entity.type == "custom_emoji" and entity.custom_emoji_id
        ),
        None,
    )


def required_channels():
    return db.execute(
        "SELECT * FROM required_channels WHERE active=1 ORDER BY id"
    ).fetchall()


def normalize_channel_url(value):
    value = (value or "").strip()
    if value.startswith(("t.me/", "telegram.me/")):
        return f"https://{value}"
    if value.startswith((
        "https://t.me/", "http://t.me/",
        "https://telegram.me/", "http://telegram.me/",
    )):
        return value
    return None


def public_chat_link_username(value):
    """Extract a public Telegram username from a link.

    Telegram's Bot API cannot resolve a private invite link to a chat ID.
    Public channel/group links are resolvable through get_chat and are the
    only safe format when the admin should enter just one link.
    """
    normalized = normalize_channel_url(value)
    if not normalized:
        return None
    match = re.fullmatch(
        r"https?://(?:t\.me|telegram\.me)/([A-Za-z0-9_]{5,32})/?",
        normalized,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


async def resolve_required_chat_link(value):
    """Resolve and validate a public channel/group link for subscriptions."""
    username = public_chat_link_username(value)
    if not username:
        raise ValueError(
            "Надішліть публічне посилання виду https://t.me/username. "
            "Приватні invite-посилання Telegram Bot API не може розпізнати."
        )

    chat = await bot.get_chat(f"@{username}")
    if chat.type not in {"channel", "supergroup", "group"}:
        raise ValueError("Посилання має вести на канал або групу Telegram.")

    bot_user = await bot.get_me()
    bot_member = await bot.get_chat_member(chat.id, bot_user.id)
    if bot_member.status not in {"creator", "administrator"}:
        raise ValueError(
            "Спочатку додайте бота адміністратором цього каналу або групи."
        )

    title = chat.title or f"@{username}"
    invite_url = f"https://t.me/{chat.username or username}"
    return str(chat.id), title[:100], invite_url


async def user_has_required_subscriptions(user_id):
    """Return False on an API error so a private channel cannot be bypassed."""
    for channel in required_channels():
        try:
            member = await bot.get_chat_member(channel["chat_id"], user_id)
            if member.status not in {"creator", "administrator", "member"} and not (
                member.status == "restricted" and getattr(member, "is_member", False)
            ):
                return False
        except Exception as exc:
            print(
                f"Unable to check required channel {channel['chat_id']} for {user_id}: {exc}",
                flush=True,
            )
            return False
    return True


def required_subscription_text():
    channels = required_channels()
    return (
        "🔒 <b>Підписка потрібна</b>\n\n"
        "Щоб користуватися ботом, підпишіться на всі канали та групи нижче, "
        "а потім натисніть «Перевірити підписку».\n\n"
        f"📢 Обов’язкових чатів: <b>{len(channels)}</b>"
    )


def required_subscription_kb():
    builder = InlineKeyboardBuilder()
    for channel in required_channels():
        builder.row(kb_button(
            f"📢 {channel['title']}",
            style="primary",
            url=channel["invite_url"],
        ))
    builder.row(kb_button(
        "✅ Перевірити підписку",
        emoji_id=EMOJI_POOL[27],
        style="success",
        callback_data="required:check",
    ))
    return builder.as_markup()


def delete_setting(key):
    db.execute("DELETE FROM settings WHERE key=?", (key,))
    db.commit()


def payment_settings_fernet():
    """Return the encryption key used for payment credentials.

    SESSION_SECRET is preferred when the host provides it.  The bot already
    requires BOT_TOKEN to start, so it is a safe-enough deployment fallback
    for installations where the owner does not want to configure a second
    secret.  This keeps payment setup inside the admin panel: the owner only
    enters the CryptoBot token or TON wallet address.
    """
    secret = os.getenv("SESSION_SECRET", "").strip() or BOT_TOKEN.strip()
    if not secret:
        return None
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def payment_setting(name):
    row = db.execute(
        "SELECT encrypted_value FROM payment_settings WHERE name=?",
        (name,),
    ).fetchone()
    if row:
        fernet = payment_settings_fernet()
        if fernet:
            try:
                return fernet.decrypt(row["encrypted_value"].encode("utf-8")).decode("utf-8")
            except InvalidToken:
                print(f"Unable to decrypt payment setting {name}.", flush=True)
    return os.getenv(name, "").strip()


def save_payment_setting(name, value, updated_by):
    fernet = payment_settings_fernet()
    if not fernet:
        raise RuntimeError(
            "Не вдалося підготувати шифрування. Перевірте, що BOT_TOKEN налаштований."
        )
    encrypted_value = fernet.encrypt(value.strip().encode("utf-8")).decode("utf-8")
    db.execute(
        """
        INSERT INTO payment_settings(name, encrypted_value, updated_at, updated_by)
        VALUES(?,?,?,?)
        ON CONFLICT(name) DO UPDATE SET
            encrypted_value=excluded.encrypted_value,
            updated_at=excluded.updated_at,
            updated_by=excluded.updated_by
        """,
        (name, encrypted_value, now(), updated_by),
    )
    db.commit()


def payment_setting_configured(name):
    return bool(payment_setting(name))


def payment_method_fernet():
    """Use the same optional-secret policy for custom payment details."""
    return payment_settings_fernet()


def payment_method_details(method_id):
    row = db.execute(
        "SELECT encrypted_details FROM payment_methods WHERE id=? AND active=1",
        (method_id,),
    ).fetchone()
    if not row:
        return ""
    fernet = payment_method_fernet()
    if not fernet:
        return ""
    try:
        return fernet.decrypt(row["encrypted_details"].encode("utf-8")).decode("utf-8")
    except InvalidToken:
        print(f"Unable to decrypt custom payment method {method_id}.", flush=True)
        return ""


def save_payment_method(name, method_type, details, method_id=None):
    fernet = payment_method_fernet()
    if not fernet:
        raise RuntimeError("Не вдалося підготувати локальне шифрування платежів.")
    encrypted_details = fernet.encrypt(details.strip().encode("utf-8")).decode("utf-8")
    timestamp = now()
    if method_id:
        db.execute(
            """UPDATE payment_methods
               SET name=?,method_type=?,encrypted_details=?,updated_at=?
               WHERE id=?""",
            (name.strip(), method_type, encrypted_details, timestamp, method_id),
        )
    else:
        db.execute(
            """INSERT INTO payment_methods(
                   name,method_type,encrypted_details,active,created_at,updated_at
               ) VALUES(?,?,?,1,?,?)""",
            (name.strip(), method_type, encrypted_details, timestamp, timestamp),
        )
    db.commit()


def ensure_user(user, referrer_id=None):
    valid_referrer_id = referrer_id
    if (
        not valid_referrer_id
        or valid_referrer_id == user.id
        or not db.execute(
            "SELECT 1 FROM users WHERE id=?",
            (valid_referrer_id,),
        ).fetchone()
    ):
        valid_referrer_id = None

    row = db.execute("SELECT id FROM users WHERE id=?", (user.id,)).fetchone()
    if not row:
        db.execute(
            """INSERT INTO users(
                   id,username,first_name,referrer_id,registered_at
               ) VALUES(?,?,?,?,?)""",
            (
                user.id,
                user.username,
                user.first_name,
                valid_referrer_id,
                now(),
            ),
        )
        if valid_referrer_id:
            db.execute(
                "UPDATE users SET invited=invited+1 WHERE id=?",
                (valid_referrer_id,),
            )
    else:
        db.execute(
            "UPDATE users SET username=?, first_name=? WHERE id=?",
            (user.username, user.first_name, user.id),
        )
        if valid_referrer_id:
            cursor = db.execute(
                """UPDATE users SET referrer_id=?
                   WHERE id=? AND referrer_id IS NULL""",
                (valid_referrer_id, user.id),
            )
            if cursor.rowcount == 1:
                db.execute(
                    "UPDATE users SET invited=invited+1 WHERE id=?",
                    (valid_referrer_id,),
                )
    db.commit()


def referrer_id_from_start(text):
    """Read /start ref_<user_id> without trusting arbitrary arguments."""
    parts = (text or "").split(maxsplit=1)
    if len(parts) != 2:
        return None
    match = re.fullmatch(r"(?:ref|referral)_(\d+)", parts[1].strip(), re.IGNORECASE)
    if not match:
        return None
    candidate = int(match.group(1))
    return candidate if db.execute(
        "SELECT 1 FROM users WHERE id=?", (candidate,)
    ).fetchone() else None


LANGUAGE_NAMES = {
    "uk": "Українська",
    "ru": "Русский",
    "en": "English",
}

MENU_LABELS = {
    "buy_stars": {"uk": "Купити Stars", "ru": "Купить Stars", "en": "Buy Stars"},
    "buy_ton": {"uk": "Купити TON", "ru": "Купить TON", "en": "Buy TON"},
    "premium": {"uk": "Telegram Premium", "ru": "Telegram Premium", "en": "Telegram Premium"},
    "withdraw": {"uk": "Вивести Stars", "ru": "Вывести Stars", "en": "Withdraw Stars"},
    "sell": {"uk": "Продати Stars", "ru": "Продать Stars", "en": "Sell Stars"},
    "profile": {"uk": "Профіль", "ru": "Профиль", "en": "Profile"},
    "referrals": {"uk": "Реферали", "ru": "Рефералы", "en": "Referrals"},
    "calculator": {"uk": "Калькулятор", "ru": "Калькулятор", "en": "Calculator"},
    "reviews": {"uk": "Відгуки", "ru": "Отзывы", "en": "Reviews"},
    "support": {"uk": "Підтримка", "ru": "Поддержка", "en": "Support"},
    "games": {"uk": "Віртуальні номери", "ru": "Виртуальные номера", "en": "Virtual numbers"},
    "nft": {"uk": "NFT", "ru": "NFT", "en": "NFT"},
    "digital": {"uk": "Товари", "ru": "Товары", "en": "Products"},
    "language": {"uk": "🌐 Мова", "ru": "🌐 Язык", "en": "🌐 Language"},
}

MENU_BUTTON_TITLES = {
    "buy_stars": "Купити Stars",
    "buy_ton": "Купити TON",
    "premium": "Telegram Premium",
    "withdraw": "Вивести Stars",
    "sell": "Продати Stars",
    "profile": "Профіль",
    "calculator": "Калькулятор",
    "reviews": "Відгуки",
    "support": "Підтримка",
    "games": "Віртуальні номери",
    "nft": "NFT",
    "digital": "Товари",
}

MENU_DEFAULT_STYLES = {
    "buy_stars": "danger",
    "buy_ton": "danger",
    "premium": "primary",
    "withdraw": "success",
    "sell": "success",
    "profile": "success",
    "referrals": "success",
    "calculator": "primary",
    "reviews": "primary",
    "support": "primary",
    "games": "success",
    "nft": "primary",
    "digital": "primary",
    "language": "primary",
}

MENU_DEFAULT_EMOJI = {
    "buy_stars": MAIN_EMOJI["buy_stars"],
    "buy_ton": MAIN_EMOJI["buy_gram"],
    "premium": MAIN_EMOJI["premium"],
    "withdraw": MAIN_EMOJI["withdraw_stars"],
    "sell": MAIN_EMOJI["sell_stars"],
    "profile": MAIN_EMOJI["profile"],
    "referrals": EMOJI_POOL[16],
    "calculator": MAIN_EMOJI["calculator"],
    "reviews": MAIN_EMOJI["reviews"],
    "support": MAIN_EMOJI["support"],
    "games": EMOJI_POOL[16],
    "nft": EMOJI_POOL[0],
    "digital": EMOJI_POOL[3],
    "language": EMOJI_POOL[27],
}

def _tr(ru, en):
    return {"ru": ru, "en": en}


# Most of the bot was written before the language selector was added and
# contains Ukrainian copy in handlers. Keep the business logic unchanged and
# translate outgoing text and keyboard labels in one place instead.
LOCALIZED_TEXT = {
    "Головне меню": _tr("Главное меню", "Main menu"),
    "Оберіть потрібний розділ нижче 👇": _tr(
        "Выберите нужный раздел ниже 👇", "Choose a section below 👇"
    ),
    "Оберіть мову інтерфейсу": _tr(
        "Выберите язык интерфейса", "Choose interface language"
    ),
    "Вкажіть кількість Stars": _tr("Укажите количество Stars", "Enter the Stars amount"),
    "від": _tr("от", "from"),
    "Введіть коректну кількість Stars": _tr(
        "Введите корректное количество Stars", "Enter a valid Stars amount"
    ),
    "До оплати": _tr("К оплате", "Amount to pay"),
    "Кількість": _tr("Количество", "Quantity"),
    "Оберіть спосіб оплати": _tr("Выберите способ оплаты", "Choose a payment method"),
    "Оберіть банк для оплати": _tr(
        "Выберите банк для оплаты", "Choose a bank for payment"
    ),
    "Власна кількість Stars": _tr(
        "Своё количество Stars", "Custom Stars amount"
    ),
    "Оберіть кількість Stars": _tr(
        "Выберите количество Stars", "Choose Stars amount"
    ),
    "Актуальний курс": _tr("Актуальный курс", "Current rate"),
    "грн / 1 Star": _tr("грн / 1 Star", "UAH / 1 Star"),
    "Назад": _tr("Назад", "Back"),
    "Скасувати": _tr("Отменить", "Cancel"),
    "Скасувати замовлення": _tr("Отменить заказ", "Cancel order"),
    "Українська картка": _tr("Украинская карта", "Ukrainian card"),
    "Я оплатив — надіслати квитанцію": _tr(
        "Я оплатил — отправить квитанцию", "I paid — send receipt"
    ),
    "Перевірити оплату": _tr("Проверить оплату", "Check payment"),
    "Оплатити рахунок": _tr("Оплатить счёт", "Pay invoice"),
    "Перейти в Tonkeeper": _tr("Перейти в Tonkeeper", "Open Tonkeeper"),
    "Замовлення не знайдено": _tr("Заказ не найден", "Order not found"),
    "Оплата через Tonkeeper": _tr("Оплата через Tonkeeper", "Payment via Tonkeeper"),
    "Оплата через CryptoBot": _tr("Оплата через CryptoBot", "Payment via CryptoBot"),
    "Рахунок CryptoBot": _tr("Счёт CryptoBot", "CryptoBot invoice"),
    "Оберіть валюту рахунку": _tr(
        "Выберите валюту счёта", "Choose invoice currency"
    ),
    "Відкрийте рахунок, оплатіть його та натисніть": _tr(
        "Откройте счёт, оплатите его и нажмите",
        "Open the invoice, pay it, then tap",
    ),
    "Перейдіть у Tonkeeper, виконайте оплату та натисніть": _tr(
        "Перейдите в Tonkeeper, выполните оплату и нажмите",
        "Open Tonkeeper, complete the payment, then tap",
    ),
    "Платіж уже знайдений і переданий адміністратору": _tr(
        "Платёж уже найден и передан администратору",
        "Payment was found and sent to the administrator",
    ),
    "Спочатку створіть рахунок через Tonkeeper": _tr(
        "Сначала создайте счёт через Tonkeeper",
        "Create an invoice through Tonkeeper first",
    ),
    "Спочатку створіть рахунок CryptoBot": _tr(
        "Сначала создайте счёт CryptoBot",
        "Create a CryptoBot invoice first",
    ),
    "Платіж поки не знайдений": _tr(
        "Платёж пока не найден", "Payment has not been found yet"
    ),
    "Рахунок ще не оплачений": _tr(
        "Счёт ещё не оплачен", "The invoice has not been paid yet"
    ),
    "Платіж знайдений": _tr("Платёж найден", "Payment found"),
    "передано адміністратору на підтвердження": _tr(
        "передан администратору на подтверждение",
        "sent to the administrator for confirmation",
    ),
    "Реквізити": _tr("Реквизиты", "Payment details"),
    "Після оплати надішліть квитанцію": _tr(
        "После оплаты отправьте квитанцию", "Send the receipt after payment"
    ),
    "Дію скасовано": _tr("Действие отменено", "Action cancelled"),
    "Курс TON": _tr("Курс TON", "TON rate"),
    "Вкажіть кількість TON для покупки": _tr(
        "Укажите количество TON для покупки", "Enter the amount of TON to buy"
    ),
    "Мінімальна кількість": _tr("Минимальное количество", "Minimum amount"),
    "Введіть коректну кількість TON": _tr(
        "Введите корректное количество TON", "Enter a valid TON amount"
    ),
    "Недостатньо коштів": _tr("Недостаточно средств", "Insufficient funds"),
    "Ваш баланс": _tr("Ваш баланс", "Your balance"),
    "Мінімум для виводу": _tr("Минимум для вывода", "Minimum withdrawal"),
    "Вкажіть кількість Stars для виводу": _tr(
        "Укажите количество Stars для вывода",
        "Enter the amount of Stars to withdraw",
    ),
    "Введіть ціле число": _tr("Введите целое число", "Enter a whole number"),
    "Мінімальна сума для виводу": _tr(
        "Минимальная сумма для вывода", "Minimum withdrawal amount"
    ),
    "Запит на вивід": _tr("Запрос на вывод", "Withdrawal request"),
    "створено": _tr("создан", "created"),
    "Очікуйте обробки заявки адміністратором": _tr(
        "Ожидайте обработки заявки администратором",
        "Wait for the administrator to process the request",
    ),
    "Вкажіть кількість Stars, яку бажаєте продати": _tr(
        "Укажите количество Stars, которое хотите продать",
        "Enter the amount of Stars you want to sell",
    ),
    "Вкажіть коректну кількість Stars": _tr(
        "Укажите корректное количество Stars",
        "Enter a valid Stars amount",
    ),
    "Запит на продаж": _tr("Запрос на продажу", "Sale request"),
    "прийнято": _tr("принят", "accepted"),
    "Для отримання реквізитів зверніться до підтримки": _tr(
        "Для получения реквизитов обратитесь в поддержку",
        "Contact support to receive payment details",
    ),
    "Ціна": _tr("Цена", "Price"),
    "Ваш профіль": _tr("Ваш профиль", "Your profile"),
    "Статус": _tr("Статус", "Status"),
    "Реєстрація": _tr("Регистрация", "Registration"),
    "Баланс": _tr("Баланс", "Balance"),
    "Доступно": _tr("Доступно", "Available"),
    "Покупки": _tr("Покупки", "Purchases"),
    "Придбано": _tr("Куплено", "Purchased"),
    "Витрачено": _tr("Потрачено", "Spent"),
    "Реферальна програма": _tr("Реферальная программа", "Referral program"),
    "Запрошено людей": _tr("Приглашено людей", "People invited"),
    "Зароблено": _tr("Заработано", "Earned"),
    "Ваше посилання": _tr("Ваша ссылка", "Your link"),
    "Посилання тимчасово недоступне": _tr(
        "Ссылка временно недоступна", "Link temporarily unavailable"
    ),
    "Вкажіть кількість Stars для розрахунку": _tr(
        "Укажите количество Stars для расчёта",
        "Enter the amount of Stars to calculate",
    ),
    "Розрахунок": _tr("Расчёт", "Calculation"),
    "Вартість": _tr("Стоимость", "Cost"),
    "Переглянути відгуки": _tr("Посмотреть отзывы", "View reviews"),
    "Відгуки наших клієнтів": _tr(
        "Отзывы наших клиентов", "Reviews from our customers"
    ),
    "Тут ви можете переглянути відгуки наших клієнтів 👇": _tr(
        "Здесь вы можете посмотреть отзывы наших клиентов 👇",
        "View reviews from our customers here 👇",
    ),
    "Опишіть ваше питання або проблему": _tr(
        "Опишите ваш вопрос или проблему", "Describe your question or problem"
    ),
    "За потреби можете додати фото або відео": _tr(
        "При необходимости добавьте фото или видео",
        "You can add a photo or video if needed",
    ),
    "Надішліть текст, фото або відео": _tr(
        "Отправьте текст, фото или видео", "Send text, a photo, or a video"
    ),
    "Звернення передано в підтримку": _tr(
        "Обращение передано в поддержку", "Your request was sent to support"
    ),
    "Немає доступу": _tr("Нет доступа", "Access denied"),
    "Немає доступу.": _tr("Нет доступа.", "Access denied."),
    "Невідома мова": _tr("Неизвестный язык", "Unknown language"),
    "Спочатку підпишіться на всі канали": _tr(
        "Сначала подпишитесь на все каналы",
        "Subscribe to all required channels first",
    ),
    "Підпишіться на всі канали та групи нижче": _tr(
        "Подпишитесь на все каналы и группы ниже",
        "Subscribe to all channels and groups below",
    ),
    "а потім натисніть «Перевірити підписку»": _tr(
        "а затем нажмите «Проверить подписку»",
        "then tap «Check subscription»",
    ),
    "Обов’язкових чатів": _tr("Обязательных чатов", "Required chats"),
    "Мова": _tr("Язык", "Language"),
    "Квитанцію отримано": _tr("Квитанция получена", "Receipt received"),
    "передано на перевірку": _tr("передана на проверку", "sent for review"),
    "Надішліть фото або документ квитанції": _tr(
        "Отправьте фото или документ квитанции",
        "Send a receipt photo or document",
    ),
    "Надішліть саме фото або документ квитанції": _tr(
        "Отправьте именно фото или документ квитанции",
        "Send a receipt photo or document",
    ),
    "Потрібна ручна перевірка": _tr(
        "Нужна ручная проверка", "Manual review required"
    ),
    "Перевірте квитанцію вручну": _tr(
        "Проверьте квитанцию вручную", "Review the receipt manually"
    ),
    "Нове замовлення": _tr("Новый заказ", "New order"),
    "без_username": _tr("без_username", "no_username"),
    "Підтверджено": _tr("Подтверждено", "Confirmed"),
    "Замовлення": _tr("Заказ", "Order"),
    "Оплата": _tr("Оплата", "Payment"),
    "Надішліть": _tr("Отправьте", "Send"),
    "Введіть": _tr("Введите", "Enter"),
    "коректне": _tr("корректное", "valid"),
    "додатне число": _tr("положительное число", "positive number"),
    "Оновлено": _tr("Обновлено", "Updated"),
    "Збережено": _tr("Сохранено", "Saved"),
    "Видалено": _tr("Удалено", "Deleted"),
    "Оновити": _tr("Обновить", "Refresh"),
    "Скинути": _tr("Сбросить", "Reset"),
    "Так, внести 3% з цього поповнення": _tr(
        "Да, внести 3% от этого пополнения", "Yes, contribute 3% of this top-up"
    ),
    "Відмовитись": _tr("Отказаться", "Decline"),
}

# Additional copy used by less frequently visited customer and admin flows.
# Keeping these phrases here also translates text built with f-strings, where
# only the stable Ukrainian part is known ahead of time.
LOCALIZED_TEXT.update({
    "№ замовлення": _tr("№ заказа", "Order no."),
    "Дата замовлення": _tr("Дата заказа", "Order date"),
    "ID користувача": _tr("ID пользователя", "User ID"),
    "Тип замовлення": _tr("Тип заказа", "Order type"),
    "Сума замовлення, грн": _tr("Сумма заказа, грн", "Order amount, UAH"),
    "Спосіб оплати": _tr("Способ оплаты", "Payment method"),
    "Статус замовлення": _tr("Статус заказа", "Order status"),
    "Статус OCR": _tr("Статус OCR", "OCR status"),
    "№ квитанції": _tr("№ квитанции", "Receipt no."),
    "Очікуваний банк": _tr("Ожидаемый банк", "Expected bank"),
    "Перевірка банку": _tr("Проверка банка", "Bank check"),
    "ID транзакції": _tr("ID транзакции", "Transaction ID"),
    "ID операції": _tr("ID операции", "Operation ID"),
    "Статус платежу": _tr("Статус платежа", "Payment status"),
    "Дата/час операції": _tr("Дата/время операции", "Operation date/time"),
    "Валюта": _tr("Валюта", "Currency"),
    "Відправник": _tr("Отправитель", "Sender"),
    "Банк відправника": _tr("Банк отправителя", "Sender bank"),
    "Код банку відправника": _tr(
        "Код банка отправителя", "Sender bank code"
    ),
    "Рахунок відправника": _tr("Счёт отправителя", "Sender account"),
    "Картка відправника": _tr("Карта отправителя", "Sender card"),
    "Платіжна система відправника": _tr(
        "Платёжная система отправителя", "Sender payment system"
    ),
    "Платіжний інструмент відправника": _tr(
        "Платёжный инструмент отправителя", "Sender payment instrument"
    ),
    "Одержувач": _tr("Получатель", "Recipient"),
    "Банк одержувача": _tr("Банк получателя", "Recipient bank"),
    "Код банку одержувача": _tr(
        "Код банка получателя", "Recipient bank code"
    ),
    "Рахунок одержувача": _tr("Счёт получателя", "Recipient account"),
    "Картка одержувача": _tr("Карта получателя", "Recipient card"),
    "Платіжна система одержувача": _tr(
        "Платёжная система получателя", "Recipient payment system"
    ),
    "Платіжний інструмент одержувача": _tr(
        "Платёжный инструмент получателя", "Recipient payment instrument"
    ),
    "Сума з квитанції, грн": _tr(
        "Сумма из квитанции, грн", "Amount from receipt, UAH"
    ),
    "Перевірка суми": _tr("Проверка суммы", "Amount check"),
    "Комісія, грн": _tr("Комиссия, грн", "Fee, UAH"),
    "Сума з комісією, грн": _tr(
        "Сумма с комиссией, грн", "Amount with fee, UAH"
    ),
    "Код авторизації": _tr("Код авторизации", "Authorization code"),
    "Призначення платежу": _tr(
        "Назначение платежа", "Payment purpose"
    ),
    "Торговець": _tr("Торговец", "Merchant"),
    "Ідентифікатор пристрою": _tr(
        "Идентификатор устройства", "Device identifier"
    ),
    "Тип файлу квитанції": _tr("Тип файла квитанции", "Receipt file type"),
    "Ім'я файлу квитанції": _tr("Имя файла квитанции", "Receipt filename"),
    "ID файлу квитанції": _tr("ID файла квитанции", "Receipt file ID"),
    "Дата розбору OCR": _tr("Дата разбора OCR", "OCR parsed at"),
    "Помилка OCR": _tr("Ошибка OCR", "OCR error"),
    "Повний OCR текст": _tr("Полный OCR-текст", "Full OCR text"),
    "Банк не визначено": _tr("Банк не определён", "Bank not identified"),
    "Збігається": _tr("Совпадает", "Matches"),
    "Потрібна перевірка": _tr("Нужна проверка", "Review required"),
    "Не вдалося перевірити": _tr("Не удалось проверить", "Could not verify"),
    "Сума платежу повинна бути більшою за нуль": _tr(
        "Сумма платежа должна быть больше нуля",
        "Payment amount must be greater than zero",
    ),
    "Невідома мова.": _tr("Неизвестный язык.", "Unknown language."),
    "✅ Підписку підтверджено!": _tr(
        "✅ Подписка подтверждена!", "✅ Subscription confirmed!"
    ),
    "❌ Підпишіться на всі канали та спробуйте ще раз.": _tr(
        "❌ Подпишитесь на все каналы и попробуйте ещё раз.",
        "❌ Subscribe to all channels and try again.",
    ),
    "❌ Надішліть саме фото або документ квитанції.": _tr(
        "❌ Отправьте именно фото или документ квитанции.",
        "❌ Send a receipt photo or document.",
    ),
    "❌ Дію скасовано. Оберіть потрібний розділ нижче 👇": _tr(
        "❌ Действие отменено. Выберите нужный раздел ниже 👇",
        "❌ Action cancelled. Choose a section below 👇",
    ),
    "Канали ще не додані.": _tr(
        "Каналы ещё не добавлены.", "No channels have been added yet."
    ),
    "Модератори ще не додані.": _tr(
        "Модераторы ещё не добавлены.", "No moderators have been added yet."
    ),
    "Реквізити не налаштовані.": _tr(
        "Реквизиты не настроены.", "Payment details are not configured."
    ),
    "Користувачів поки немає.": _tr(
        "Пользователей пока нет.", "There are no users yet."
    ),
    "Поки немає користувачів.": _tr(
        "Пока нет пользователей.", "There are no users yet."
    ),
    "Без причини": _tr("Без причины", "No reason provided"),
    "Надішліть публічне посилання виду https://t.me/username. Приватні invite-посилання Telegram Bot API не може розпізнати.": _tr(
        "Отправьте публичную ссылку вида https://t.me/username. Приватные invite-ссылки Telegram Bot API распознать не может.",
        "Send a public link in the form https://t.me/username. The Telegram Bot API cannot resolve private invite links.",
    ),
    "Посилання має вести на канал або групу Telegram.": _tr(
        "Ссылка должна вести на канал или группу Telegram.",
        "The link must point to a Telegram channel or group.",
    ),
    "Спочатку додайте бота адміністратором цього каналу або групи.": _tr(
        "Сначала добавьте бота администратором этого канала или группы.",
        "First add the bot as an administrator of this channel or group.",
    ),
    "✅ Перевірити підписку": _tr(
        "✅ Проверить подписку", "✅ Check subscription"
    ),
    "🎁 <b>Розіграші</b>": _tr(
        "🎁 <b>Розыгрыши</b>", "🎁 <b>Giveaways</b>"
    ),
    "Створити розіграш": _tr("Создать розыгрыш", "Create giveaway"),
    "Провести розіграш": _tr("Провести розыгрыш", "Run giveaway"),
    "Оновити дані": _tr("Обновить данные", "Refresh data"),
    "Завершити": _tr("Завершить", "Finish"),
    "Відкрити головне меню": _tr("Открыть главное меню", "Open main menu"),
    "Відкрити адмін-панель": _tr("Открыть админ-панель", "Open admin panel"),
    "Купити Stars": _tr("Купить Stars", "Buy Stars"),
    "Купити TON": _tr("Купить TON", "Buy TON"),
    "Вивести Stars": _tr("Вывести Stars", "Withdraw Stars"),
    "Продати Stars": _tr("Продать Stars", "Sell Stars"),
    "Профіль": _tr("Профиль", "Profile"),
    "Відгуки": _tr("Отзывы", "Reviews"),
    "Підтримка": _tr("Поддержка", "Support"),
    "Віртуальні номери": _tr("Виртуальные номера", "Virtual numbers"),
    "Товари": _tr("Товары", "Products"),
    "Кому бажаєте придбати Stars?": _tr("Кому хотите купить Stars?", "Who would you like to buy Stars for?"),
    "Змінити отримувача": _tr("Изменить получателя", "Change recipient"),
    "До меню": _tr("В меню", "Main menu"),
    "Поточний курс Stars": _tr("Текущий курс Stars", "Current Stars rate"),
    "Курс Stars": _tr("Курс Stars", "Stars rate"),
    "Курс TON для оплати": _tr(
        "Курс TON для оплаты", "TON payment rate"
    ),
    "Курс USDT": _tr("Курс USDT", "USDT rate"),
    "Розіграш": _tr("Розыгрыш", "Giveaway"),
    "Баланс Stars": _tr("Баланс Stars", "Stars balance"),
    "Реквізити карток": _tr("Реквизиты карт", "Card payment details"),
    "Платіжні сервіси": _tr("Платёжные сервисы", "Payment services"),
    "Розсилка": _tr("Рассылка", "Broadcast"),
    "Канал відгуків": _tr("Канал отзывов", "Reviews channel"),
    "Підтвердження оплат": _tr(
        "Подтверждение оплат", "Payment confirmations"
    ),
    "Користувачі": _tr("Пользователи", "Users"),
    "Статистика": _tr("Статистика", "Statistics"),
    "Квитанції": _tr("Квитанции", "Receipts"),
    "Обов’язкові підписки": _tr(
        "Обязательные подписки", "Required subscriptions"
    ),
    "Модератори": _tr("Модераторы", "Moderators"),
    "➕ Додати канал": _tr("➕ Добавить канал", "➕ Add channel"),
    "➕ Додати модератора": _tr(
        "➕ Добавить модератора", "➕ Add moderator"
    ),
    "🧾 Квитанції": _tr("🧾 Квитанции", "🧾 Receipts"),
    "🆘 Підтримка": _tr("🆘 Поддержка", "🆘 Support"),
    "Налаштувати CryptoBot": _tr(
        "Настроить CryptoBot", "Configure CryptoBot"
    ),
    "Налаштувати TON Center API": _tr(
        "Настроить TON Center API", "Configure TON Center API"
    ),
    "Налаштувати TON-гаманець": _tr(
        "Настроить TON-кошелёк", "Configure TON wallet"
    ),
    "Скинути статистику": _tr("Сбросить статистику", "Reset statistics"),
    "Так, скинути архів": _tr(
        "Да, сбросить архив", "Yes, reset archive"
    ),
    "Змінити ціну": _tr("Изменить цену", "Change price"),
    "✏️ Змінити дані": _tr("✏️ Изменить данные", "✏️ Edit details"),
    "⏯ Увімкнути / вимкнути": _tr(
        "⏯ Включить / выключить", "⏯ Enable / disable"
    ),
    "🗑 Видалити": _tr("🗑 Удалить", "🗑 Delete"),
    "Відповісти": _tr("Ответить", "Reply"),
    "Взяти квитанцію": _tr("Взять квитанцию", "Take receipt"),
    "Підтвердити": _tr("Подтвердить", "Confirm"),
    "Відхилити": _tr("Отклонить", "Reject"),
    "Взяти звернення": _tr("Взять обращение", "Take request"),
    "Звернення взято в роботу.": _tr(
        "Обращение взято в работу.", "Request taken for processing."
    ),
    "Цю заявку вже оброблено.": _tr(
        "Эта заявка уже обработана.", "This request has already been processed."
    ),
    "Цю заявку вже взяв інший модератор.": _tr(
        "Эту заявку уже взял другой модератор.",
        "Another moderator already took this request.",
    ),
    "Квитанцію взято в роботу.": _tr(
        "Квитанция взята в работу.", "Receipt taken for processing."
    ),
    "Спочатку натисніть «Взяти квитанцію».": _tr(
        "Сначала нажмите «Взять квитанцию».",
        "Tap «Take receipt» first.",
    ),
    "❌ Спочатку візьміть заявку в роботу.": _tr(
        "❌ Сначала возьмите заявку в работу.",
        "❌ Take the request for processing first.",
    ),
    "Перевірте квитанцію вручну": _tr(
        "Проверьте квитанцию вручную", "Review the receipt manually"
    ),
    "Надішліть текст у форматі: Назва | ціна | опис": _tr(
        "Отправьте текст в формате: Название | цена | описание",
        "Send text in this format: Name | price | description",
    ),
    "❌ Введіть коректну ціну.": _tr(
        "❌ Введите корректную цену.", "❌ Enter a valid price."
    ),
    "❌ Ціна має бути числом не менше 0.": _tr(
        "❌ Цена должна быть числом не меньше 0.",
        "❌ Price must be a number no less than 0.",
    ),
    "Токен CryptoBot": _tr("Токен CryptoBot", "CryptoBot token"),
    "Ключ TON Center API": _tr("Ключ TON Center API", "TON Center API key"),
    "Адресу TON-гаманця": _tr("Адрес TON-кошелька", "TON wallet address"),
    "Адреса TON-гаманця": _tr("Адрес TON-кошелька", "TON wallet address"),
    "Дію скасовано": _tr("Действие отменено", "Action cancelled"),
    "Сума": _tr("Сумма", "Amount"),
    "Дата операції": _tr("Дата операции", "Operation date"),
    "Заявку": _tr("Заявка", "Request"),
    "Заявка": _tr("Заявка", "Request"),
    "Поточний контакт підтримки": _tr(
        "Текущий контакт поддержки", "Current support contact"
    ),
    "Надішліть повідомлення для розсилки": _tr(
        "Отправьте сообщение для рассылки", "Send a message to broadcast"
    ),
    "Спочатку виберіть користувача.": _tr(
        "Сначала выберите пользователя.", "Choose a user first."
    ),
    "Користувача не знайдено.": _tr(
        "Пользователь не найден.", "User not found."
    ),
    "Користувача не знайдено або це головний адміністратор.": _tr(
        "Пользователь не найден или это главный администратор.",
        "User not found or this is the main administrator.",
    ),
    "Модератора видалено.": _tr(
        "Модератор удалён.", "Moderator removed."
    ),
    "❌ Користувача не знайдено. Перевірте ID/username або попросіть його спочатку надіслати боту /start.": _tr(
        "❌ Пользователь не найден. Проверьте ID/username или попросите его сначала отправить боту /start.",
        "❌ User not found. Check the ID/username or ask them to send /start to the bot first.",
    ),
    "❌ Надішліть ID або username текстом.": _tr(
        "❌ Отправьте ID или username текстом.",
        "❌ Send an ID or username as text.",
    ),
    "❌ Введіть коректне ціле число.": _tr(
        "❌ Введите корректное целое число.",
        "❌ Enter a valid whole number.",
    ),
    "❌ Введіть коректну кількість Stars.": _tr(
        "❌ Введите корректное количество Stars.",
        "❌ Enter a valid Stars amount.",
    ),
    "❌ Введіть коректну кількість Stars від 50.": _tr(
        "❌ Введите корректное количество Stars от 50.",
        "❌ Enter a valid Stars amount of at least 50.",
    ),
    "❌ Введіть коректну кількість TON від 0.25.": _tr(
        "❌ Введите корректное количество TON от 0.25.",
        "❌ Enter a valid TON amount of at least 0.25.",
    ),
    "❌ Введіть коректне додатне число.": _tr(
        "❌ Введите корректное положительное число.",
        "❌ Enter a valid positive number.",
    ),
    "❌ Введіть коректне додатне ціле число.": _tr(
        "❌ Введите корректное положительное целое число.",
        "❌ Enter a valid positive whole number.",
    ),
    "❌ Введіть ціле число Stars від 1 до 100 000 000.": _tr(
        "❌ Введите целое число Stars от 1 до 100 000 000.",
        "❌ Enter a whole Stars amount from 1 to 100,000,000.",
    ),
    "❌ Не вдалося надіслати відповідь. Можливо, користувач заблокував бота.": _tr(
        "❌ Не удалось отправить ответ. Возможно, пользователь заблокировал бота.",
        "❌ Could not send the reply. The user may have blocked the bot.",
    ),
    "❌ Не вдалося знайти цей канал або групу. Перевірте посилання і права адміністратора бота.": _tr(
        "❌ Не удалось найти этот канал или группу. Проверьте ссылку и права администратора бота.",
        "❌ Could not find this channel or group. Check the link and the bot's administrator rights.",
    ),
    "❌ Такий chat_id уже є в списку каналів.": _tr(
        "❌ Такой chat_id уже есть в списке каналов.",
        "❌ This chat_id is already in the channel list.",
    ),
    "❌ Розіграш ще не досяг цілі або вже оброблений.": _tr(
        "❌ Розыгрыш ещё не достиг цели или уже обработан.",
        "❌ The giveaway has not reached its goal or was already processed.",
    ),
    "❌ У розіграші ще немає учасників.": _tr(
        "❌ В розыгрыше пока нет участников.",
        "❌ The giveaway has no participants yet.",
    ),
    "Цей розіграш уже не приймає внески.": _tr(
        "Этот розыгрыш больше не принимает взносы.",
        "This giveaway no longer accepts contributions.",
    ),
    "Згоду збережено для цього поповнення.": _tr(
        "Согласие сохранено для этого пополнения.",
        "Consent saved for this top-up.",
    ),
    "Ви отримали один квиток.": _tr(
        "Вы получили один билет.", "You received one ticket."
    ),
    "Ваш квиток збережено.": _tr(
        "Ваш билет сохранён.", "Your ticket was saved."
    ),
    "Створіть його, задайте ціль у Stars і напишіть умови.": _tr(
        "Создайте его, задайте цель в Stars и напишите условия.",
        "Create one, set a Stars goal, and add the rules.",
    ),
    "Бот має бути адміністратором кожного каналу або групи, інакше Telegram не зможе перевірити підписку.": _tr(
        "Бот должен быть администратором каждого канала или группы, иначе Telegram не сможет проверить подписку.",
        "The bot must be an administrator in every channel or group, otherwise Telegram cannot verify subscriptions.",
    ),
    "🔒 <b>Підписка потрібна</b>": _tr(
        "🔒 <b>Требуется подписка</b>", "🔒 <b>Subscription required</b>"
    ),
    "Щоб користуватися ботом, підпишіться на всі канали та групи нижче": _tr(
        "Чтобы пользоваться ботом, подпишитесь на все каналы и группы ниже",
        "To use the bot, subscribe to all channels and groups below",
    ),
    "📥 Взяти квитанцію": _tr(
        "📥 Взять квитанцию", "📥 Take receipt"
    ),
    "✅ Підтвердити": _tr("✅ Подтвердить", "✅ Confirm"),
    "❌ Відхилити": _tr("❌ Отклонить", "❌ Reject"),
    "📥 Взяти звернення": _tr(
        "📥 Взять обращение", "📥 Take request"
    ),
    "✍️ Відповісти": _tr("✍️ Ответить", "✍️ Reply"),
    "❌ Замовлення не знайдено.": _tr(
        "❌ Заказ не найден.", "❌ Order not found."
    ),
    "CryptoBot не повернув посилання на рахунок.": _tr(
        "CryptoBot не вернул ссылку на счёт.",
        "CryptoBot did not return an invoice link.",
    ),
    "❌ Оплата через Tonkeeper ще не налаштована адміністратором.": _tr(
        "❌ Оплата через Tonkeeper ещё не настроена администратором.",
        "❌ Tonkeeper payments have not been configured by the administrator.",
    ),
    "❌ CryptoBot ще не налаштований адміністратором.": _tr(
        "❌ CryptoBot ещё не настроен администратором.",
        "❌ CryptoBot has not been configured by the administrator.",
    ),
    "❌ Спочатку підпишіться на всі канали.": _tr(
        "❌ Сначала подпишитесь на все каналы.",
        "❌ Subscribe to all required channels first.",
    ),
    "⚠️ Квитанцію збережено, але повідомлення адміністратору не доставлено.": _tr(
        "⚠️ Квитанция сохранена, но сообщение администратору не доставлено.",
        "⚠️ The receipt was saved, but the administrator could not be notified.",
    ),
    "Платіж перевірено через платіжний сервіс.": _tr(
        "Платёж проверен через платёжный сервис.",
        "Payment verified through the payment service.",
    ),
    "Не вдалося відкрити файл квитанції:": _tr(
        "Не удалось открыть файл квитанции:",
        "Could not open the receipt file:",
    ),
    "❌ Не вдалося створити рахунок CryptoBot:": _tr(
        "❌ Не удалось создать счёт CryptoBot:",
        "❌ Could not create the CryptoBot invoice:",
    ),
    "❌ Не вдалося перевірити TON-платіж:": _tr(
        "❌ Не удалось проверить TON-платёж:",
        "❌ Could not verify the TON payment:",
    ),
    "❌ Не вдалося перевірити рахунок CryptoBot:": _tr(
        "❌ Не удалось проверить счёт CryptoBot:",
        "❌ Could not verify the CryptoBot invoice:",
    ),
    "💳 <b>Оберіть спосіб оплати:</b>": _tr(
        "💳 <b>Выберите способ оплаты:</b>",
        "💳 <b>Choose a payment method:</b>",
    ),
    "🧾 Надішліть фото або документ квитанції.": _tr(
        "🧾 Отправьте фото или документ квитанции.",
        "🧾 Send a receipt photo or document.",
    ),
    "⭐ Вкажіть кількість Stars, яку бажаєте продати.": _tr(
        "⭐ Укажите количество Stars, которое хотите продать.",
        "⭐ Enter the amount of Stars you want to sell.",
    ),
    "🧮 Вкажіть кількість Stars для розрахунку.": _tr(
        "🧮 Укажите количество Stars для расчёта.",
        "🧮 Enter the amount of Stars to calculate.",
    ),
    "✅ Звернення передано в підтримку.": _tr(
        "✅ Обращение передано в поддержку.",
        "✅ Your request was sent to support.",
    ),
    "🎁 <b>Новий розіграш</b>": _tr(
        "🎁 <b>Новый розыгрыш</b>", "🎁 <b>New giveaway</b>"
    ),
    "📜 Надішліть умови участі одним повідомленням.": _tr(
        "📜 Отправьте условия участия одним сообщением.",
        "📜 Send the participation rules in one message.",
    ),
    "Умови оновлено: тепер кожне поповнення потребує окремої згоди.": _tr(
        "Условия обновлены: теперь каждое пополнение требует отдельного согласия.",
        "Rules updated: each top-up now requires separate consent.",
    ),
    "✅ Контакт підтримки оновлено.": _tr(
        "✅ Контакт поддержки обновлён.",
        "✅ Support contact updated.",
    ),
    "✅ Канал відгуків оновлено.": _tr(
        "✅ Канал отзывов обновлён.", "✅ Reviews channel updated."
    ),
    "❌ Банк не знайдено.": _tr(
        "❌ Банк не найден.", "❌ Bank not found."
    ),
    "❌ Не вдалося визначити банк.": _tr(
        "❌ Не удалось определить банк.",
        "❌ Could not identify the bank.",
    ),
    "❌ Значення занадто коротке. Спробуйте ще раз.": _tr(
        "❌ Значение слишком короткое. Попробуйте ещё раз.",
        "❌ The value is too short. Try again.",
    ),
    "📢 Розсилку завершено.": _tr(
        "📢 Рассылка завершена.", "📢 Broadcast completed."
    ),
    "❌ Не вдалося надіслати відповідь.": _tr(
        "❌ Не удалось отправить ответ.",
        "❌ Could not send the reply.",
    ),
    "❌ Не вдалося створити платіж TON:": _tr(
        "❌ Не удалось создать TON-платёж:",
        "❌ Could not create the TON payment:",
    ),
    "❌ Мінімальна сума для виводу —": _tr(
        "❌ Минимальная сумма для вывода —",
        "❌ The minimum withdrawal amount is",
    ),
    "❌ Введіть ціле число. Мінімум для виводу —": _tr(
        "❌ Введите целое число. Минимум для вывода —",
        "❌ Enter a whole number. The minimum withdrawal is",
    ),
    "Замовлення вже оброблене або не існує.": _tr(
        "Заказ уже обработан или не существует.",
        "The order was already processed or does not exist.",
    ),
    "🔴 Вкажіть причину відхилення замовлення": _tr(
        "🔴 Укажите причину отклонения заказа",
        "🔴 Enter the reason for rejecting order",
    ),
    "🔴 Замовлення": _tr("🔴 Заказ", "🔴 Order"),
    "відхилено.": _tr("отклонен.", "rejected."),
    "🎉 <b>Вітаємо! Ви перемогли в розіграші": _tr(
        "🎉 <b>Поздравляем! Вы победили в розыгрыше",
        "🎉 <b>Congratulations! You won giveaway",
    ),
    "❌ У користувача лише": _tr(
        "❌ У пользователя только", "❌ The user only has"
    ),
    "Введіть меншу кількість або натисніть": _tr(
        "Введите меньшее количество или нажмите",
        "Enter a smaller amount or tap",
    ),
    "На балансі недостатньо Stars. Нічого не списано.": _tr(
        "На балансе недостаточно Stars. Ничего не списано.",
        "Not enough Stars in the balance. Nothing was deducted.",
    ),
    "🎉 <b>Нова реферальна винагорода!</b>": _tr(
        "🎉 <b>Новое реферальное вознаграждение!</b>",
        "🎉 <b>New referral reward!</b>",
    ),
})


def localize_text(chat_id, text):
    """Translate bot-controlled copy for the language selected by the user."""
    if not text:
        return text
    language = user_language(chat_id)
    if language == "uk":
        return text
    result = str(text)
    for source in sorted(LOCALIZED_TEXT, key=len, reverse=True):
        result = result.replace(source, LOCALIZED_TEXT[source][language])
    return result


def localize_markup(chat_id, markup):
    """Translate labels in reply and inline keyboards before sending them."""
    if not markup or user_language(chat_id) == "uk":
        return markup
    if hasattr(markup, "inline_keyboard"):
        rows = [
            [
                button.model_copy(
                    update={"text": localize_text(chat_id, button.text)}
                )
                for button in row
            ]
            for row in markup.inline_keyboard
        ]
        return markup.model_copy(update={"inline_keyboard": rows})
    if hasattr(markup, "keyboard"):
        rows = [
            [
                button.model_copy(
                    update={"text": localize_text(chat_id, button.text)}
                )
                for button in row
            ]
            for row in markup.keyboard
        ]
        return markup.model_copy(update={"keyboard": rows})
    return markup


CANCEL_LABELS = {
    "uk": ("Скасувати", "❌ Скасувати"),
    "ru": ("Отменить", "❌ Отменить"),
    "en": ("Cancel", "❌ Cancel"),
}
ALL_CANCEL_LABELS = tuple(label for labels in CANCEL_LABELS.values() for label in labels)


def user_language(user_id):
    row = db.execute("SELECT language FROM users WHERE id=?", (user_id,)).fetchone()
    language = row["language"] if row else ""
    return language if language in LANGUAGE_NAMES else "uk"


def language_labels(key):
    labels = set(MENU_LABELS[key].values())
    for language in LANGUAGE_NAMES:
        override = setting(f"menu_label:{key}:{language}")
        if override:
            labels.add(override)
    return tuple(labels)


def localized_menu_label(key, language):
    return menu_label(key, language)


def menu_key_from_text(text, language):
    value = (text or "").strip()
    for key in MENU_LABELS:
        if value == menu_label(key, language):
            return key
    return None


def language_selection_kb():
    b = InlineKeyboardBuilder()
    b.row(
        kb_button("🇺🇦 Українська", style="primary", callback_data="language:uk"),
        kb_button("🇷🇺 Русский", style="primary", callback_data="language:ru"),
    )
    b.row(
        kb_button("🇬🇧 English", style="primary", callback_data="language:en"),
    )
    return b.as_markup()


def language_selection_text():
    return (
        "✨ <b>Вітаємо у Spanix Stars!</b>\n\n"
        "Оберіть мову інтерфейсу / Выберите язык интерфейса / Choose your interface language:"
    )


def welcome_text(language):
    texts = {
        "uk": (
            f"{pe(WELCOME_EMOJI['stars'], '⭐')} <b>Вітаємо у {BOT_NAME}!</b>\n\n"
            f"{pe(WELCOME_EMOJI['stars'], '⭐')} Купуйте Stars\n"
            f"{pe(WELCOME_EMOJI['ton'], '🪙')} Купуйте TON\n"
            f"{pe(EMOJI_POOL[0], '⭐')} Оформлюйте Telegram Premium\n\n"
            "Швидко, зручно та без зайвих кроків.\n\n"
            "Оберіть потрібний розділ нижче 👇"
        ),
        "ru": (
            f"{pe(WELCOME_EMOJI['stars'], '⭐')} <b>Добро пожаловать в {BOT_NAME}!</b>\n\n"
            f"{pe(WELCOME_EMOJI['stars'], '⭐')} Покупайте Stars\n"
            f"{pe(WELCOME_EMOJI['ton'], '🪙')} Покупайте TON\n"
            f"{pe(EMOJI_POOL[0], '⭐')} Оформляйте Telegram Premium\n\n"
            "Быстро, удобно и без лишних шагов.\n\n"
            "Выберите нужный раздел ниже 👇"
        ),
        "en": (
            f"{pe(WELCOME_EMOJI['stars'], '⭐')} <b>Welcome to {BOT_NAME}!</b>\n\n"
            f"{pe(WELCOME_EMOJI['stars'], '⭐')} Buy Stars\n"
            f"{pe(WELCOME_EMOJI['ton'], '🪙')} Buy TON\n"
            f"{pe(EMOJI_POOL[0], '⭐')} Get Telegram Premium\n\n"
            "Fast, simple, and convenient.\n\n"
            "Choose a section below 👇"
        ),
    }
    return setting(f"welcome_text:{language}") or texts.get(language, texts["uk"])


def user_row(uid):
    return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def find_user_by_identifier(identifier):
    value = identifier.strip()
    if value.startswith("@"):
        value = value[1:]
    if not value:
        return None
    if value.isdigit():
        return user_row(int(value))
    return db.execute(
        "SELECT * FROM users WHERE username=? COLLATE NOCASE",
        (value,),
    ).fetchone()


def create_order(
    uid,
    order_type,
    quantity,
    amount,
    ton_wallet=None,
    withdrawal_source="main",
):
    cur = db.execute(
        """INSERT INTO orders(
               user_id,order_type,quantity,amount,status,created_at,ton_wallet,
               withdrawal_source
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (
            uid,
            order_type,
            str(quantity),
            float(amount),
            "waiting_payment",
            now(),
            ton_wallet,
            withdrawal_source,
        ),
    )
    db.commit()
    return cur.lastrowid


def review_channel_reference(raw_url):
    normalized = normalize_reviews_url(str(raw_url or ""))
    if not normalized:
        return None
    match = re.fullmatch(
        r"https://(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{4,31})/?",
        normalized,
    )
    return f"@{match.group(1)}" if match else None


async def resolve_reviews_channel(raw_url=None):
    url = raw_url or setting("reviews_url")
    reference = review_channel_reference(url)
    if not reference:
        raise ValueError(
            "Вкажіть публічний канал через @username або посилання https://t.me/username."
        )
    try:
        chat = await bot.get_chat(reference)
    except Exception as exc:
        raise ValueError(
            "Не вдалося знайти канал. Перевірте його @username та додайте бота до каналу."
        ) from exc
    if chat.type != "channel":
        raise ValueError("Посилання має вести саме на Telegram-канал.")
    me = await bot.get_me()
    try:
        membership = await bot.get_chat_member(chat.id, me.id)
    except Exception as exc:
        raise ValueError(
            "Не вдалося перевірити права бота. Додайте його адміністратором каналу."
        ) from exc
    if membership.status not in {"administrator", "creator"}:
        raise ValueError(
            "Додайте бота адміністратором каналу з правом публікувати повідомлення."
        )
    if (
        membership.status == "administrator"
        and not getattr(membership, "can_post_messages", False)
    ):
        raise ValueError(
            "У бота немає права публікувати повідомлення в цьому каналі."
        )
    return chat.id


def review_rating_kb(order_id, user_id):
    b = InlineKeyboardBuilder()
    b.row(*[
        kb_button(
            str(rating) if PREMIUM_EMOJI_ENABLED else f"{rating} ⭐",
            emoji_id=(WELCOME_EMOJI["stars"] if PREMIUM_EMOJI_ENABLED else None),
            style="primary",
            callback_data=f"review:rate:{order_id}:{rating}",
        )
        for rating in range(1, 6)
    ])
    order = db.execute("SELECT order_type FROM orders WHERE id=?", (order_id,)).fetchone()
    kind = order["order_type"] if order else "buy_stars"
    cta = {
        "buy_stars": ("⭐ Купити зірки", "menu:buy_stars"),
        "virtual_number": ("📱 Купити номер тут", "menu:games"),
        "nft_purchase": ("🎁 Купити NFT тут", "menu:nft"),
        "digital_product": ("🛍 Купити товар тут", "menu:digital"),
        "game_donation": ("🎮 Купити донат тут", "menu:games"),
        "buy_ton": ("💎 Купити TON", "menu:buy_ton"),
        "telegram_premium": ("⭐ Купити Premium", "menu:premium"),
    }.get(kind)
    if cta:
        b.row(kb_button(cta[0], style="success", callback_data=cta[1]))
    return b.as_markup()


def review_repurchase_kb(order_id):
    order = db.execute("SELECT order_type FROM orders WHERE id=?", (order_id,)).fetchone()
    kind = order["order_type"] if order else "buy_stars"
    cta = {
        "buy_stars": ("⭐ Купити зірки", "menu:buy_stars"),
        "virtual_number": ("📱 Купити номер тут", "menu:games"),
        "nft_purchase": ("🎁 Купити NFT тут", "menu:nft"),
        "digital_product": ("🛍 Купити товар тут", "menu:digital"),
        "game_donation": ("🎮 Купити донат тут", "menu:games"),
        "buy_ton": ("💎 Купити TON", "menu:buy_ton"),
        "telegram_premium": ("⭐ Купити Premium", "menu:premium"),
    }.get(kind)
    if not cta:
        return None
    b = InlineKeyboardBuilder()
    b.row(kb_button(cta[0], style="success", callback_data=cta[1]))
    return b.as_markup()


async def clear_review_inline_keyboard(call: CallbackQuery):
    """Remove an old review action row so it cannot be submitted twice."""
    message = call.message
    if not isinstance(message, Message):
        return
    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramAPIError as exc:
        # The message may already have been deleted or become inaccessible.
        print(f"Unable to clear review buttons: {exc}", flush=True)


def review_moderation_kb(order_id):
    b = InlineKeyboardBuilder()
    b.row(
        kb_button(
            "✅ Опублікувати",
            emoji_id=EMOJI_POOL[5],
            style="success",
            callback_data=f"review:approve:{order_id}",
        ),
        kb_button(
            "❌ Відхилити",
            emoji_id=EMOJI_POOL[3],
            style="danger",
            callback_data=f"review:reject:{order_id}",
        ),
    )
    return b.as_markup()


def review_admin_settings_kb():
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "🔢 Встановити наступний номер",
        emoji_id=MAIN_EMOJI["reviews"],
        style="primary",
        callback_data="adm:reviews:number",
    ))
    b.row(kb_button(
        "📢 Змінити канал відгуків",
        emoji_id=MAIN_EMOJI["reviews"],
        style="primary",
        callback_data="adm:reviews:channel",
    ))
    b.row(kb_button(
        "📝 Черга модерації",
        emoji_id=MAIN_EMOJI["reviews"],
        style="success",
        callback_data="adm:reviews:queue",
    ))
    b.row(back_inline("admin:back"))
    return b.as_markup()


def review_retry_kb(order_id):
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "🔁 Повторити публікацію",
        emoji_id=EMOJI_POOL[0],
        style="primary",
        callback_data=f"review:retry:{order_id}",
    ))
    return b.as_markup()


def review_prompt_text(user_id, order_id, order_type, withdrawal_source="main"):
    language = user_language(user_id)
    order = db.execute("SELECT quantity FROM orders WHERE id=?", (order_id,)).fetchone()
    quantity = esc(order["quantity"]) if order else ""
    success_emoji = pe(EMOJI_POOL[5], "✅")
    stars_emoji = pe(WELCOME_EMOJI["stars"], "⭐")
    ton_emoji = pe(WELCOME_EMOJI["ton"], "🪙")
    gift_emoji = pe(EMOJI_POOL[0], "🎁")
    if order_type == "withdraw_stars":
        if withdrawal_source == "referral":
            title = {
                "uk": (
                    f"{success_emoji} <b>Реферальний вивід #{order_id} виплачено!</b>\n\n"
                    f"{stars_emoji} Сума: <b>{quantity} Stars</b>\n\n"
                ),
                "ru": (
                    f"{success_emoji} <b>Реферальный вывод #{order_id} выплачен!</b>\n\n"
                    f"{stars_emoji} Сумма: <b>{quantity} Stars</b>\n\n"
                ),
                "en": (
                    f"{success_emoji} <b>Referral withdrawal #{order_id} has been paid!</b>\n\n"
                    f"{stars_emoji} Amount: <b>{quantity} Stars</b>\n\n"
                ),
            }[language]
        elif language == "ru":
            title = f"{success_emoji} <b>Вывод #{order_id} выплачен!</b>\n\n{stars_emoji} Сумма: <b>{quantity} Stars</b>\n\n"
        elif language == "en":
            title = f"{success_emoji} <b>Withdrawal #{order_id} has been paid!</b>\n\n{stars_emoji} Amount: <b>{quantity} Stars</b>\n\n"
        else:
            title = f"{success_emoji} <b>Вивід #{order_id} виплачено!</b>\n\n{stars_emoji} Сума: <b>{quantity} Stars</b>\n\n"
    else:
        title = {
            "uk": f"{success_emoji} <b>TON за замовленням #{order_id} відправлено!</b>\n\n{ton_emoji} Сума: <b>{quantity} TON</b>\n\n",
            "ru": f"{success_emoji} <b>TON по заказу #{order_id} отправлены!</b>\n\n{ton_emoji} Сумма: <b>{quantity} TON</b>\n\n",
            "en": f"{success_emoji} <b>TON for order #{order_id} has been sent!</b>\n\n{ton_emoji} Amount: <b>{quantity} TON</b>\n\n",
        }[language]

    if order_type == "buy_stars":
        title = {
            "uk": f"{success_emoji} <b>Покупку Stars за замовленням #{order_id} підтверджено!</b>\n\n{stars_emoji} Сума: <b>{quantity} Stars</b>\n\n",
            "ru": f"{success_emoji} <b>Покупка Stars по заказу #{order_id} подтверждена!</b>\n\n{stars_emoji} Сумма: <b>{quantity} Stars</b>\n\n",
            "en": f"{success_emoji} <b>Stars purchase #{order_id} is confirmed!</b>\n\n{stars_emoji} Amount: <b>{quantity} Stars</b>\n\n",
        }[language]
    elif order_type == "buy_ton":
        title = {
            "uk": f"{success_emoji} <b>TON за замовленням #{order_id} відправлено!</b>\n\n{ton_emoji} Сума: <b>{quantity} TON</b>\n\n",
            "ru": f"{success_emoji} <b>TON по заказу #{order_id} отправлены!</b>\n\n{ton_emoji} Сумма: <b>{quantity} TON</b>\n\n",
            "en": f"{success_emoji} <b>TON for order #{order_id} has been sent!</b>\n\n{ton_emoji} Amount: <b>{quantity} TON</b>\n\n",
        }[language]
    elif order_type == "telegram_premium":
        title = {
            "uk": f"{success_emoji} <b>Telegram Premium за замовленням #{order_id} активовано!</b>\n\n⭐ Термін: <b>{quantity} міс.</b>\n\n",
            "ru": f"{success_emoji} <b>Telegram Premium по заказу #{order_id} активирован!</b>\n\n⭐ Срок: <b>{quantity} мес.</b>\n\n",
            "en": f"{success_emoji} <b>Telegram Premium order #{order_id} is active!</b>\n\n⭐ Duration: <b>{quantity} months</b>\n\n",
        }[language]
    elif order_type == "game_donation":
        game_order = db.execute("SELECT admin_note FROM orders WHERE id=?", (order_id,)).fetchone()
        try:
            game_meta = json.loads(game_order["admin_note"] or "{}") if game_order else {}
        except (TypeError, ValueError):
            game_meta = {}
        game_name = esc(game_meta.get("game_name") or "гра")
        product_name = esc(game_meta.get("product_name") or "товар")
        game_quantity = esc(game_meta.get("quantity") or quantity)
        title = {
            "uk": f"{success_emoji} <b>Донат у грі {game_name} виконано!</b>\n\n🎮 {product_name}: <b>{game_quantity}</b>\n\n",
            "ru": f"{success_emoji} <b>Донат в игре {game_name} выполнен!</b>\n\n🎮 {product_name}: <b>{game_quantity}</b>\n\n",
            "en": f"{success_emoji} <b>{game_name} game top-up is complete!</b>\n\n🎮 {product_name}: <b>{game_quantity}</b>\n\n",
        }[language]
    elif order_type in {"nft", "nft_purchase", "digital_product", "virtual_number"}:
        title = {
            "uk": f"{success_emoji} <b>Замовлення #{order_id} виконано!</b>\n\n",
            "ru": f"{success_emoji} <b>Заказ #{order_id} выполнен!</b>\n\n",
            "en": f"{success_emoji} <b>Order #{order_id} is complete!</b>\n\n",
        }[language]

    bonus = {
        "uk": f"{gift_emoji} Якщо відгук буде схвалено й опубліковано, отримаєте 1-2 Stars {stars_emoji} на баланс.",
        "ru": f"{gift_emoji} Если отзыв будет одобрен и опубликован, вы получите 1-2 Stars {stars_emoji} на баланс.",
        "en": f"{gift_emoji} If approved and published, your review earns 1–2 Stars {stars_emoji}.",
    }[language]
    photo_prompt = {
        "uk": (
            "📸 Фото або скриншот покупки необов’язкові — за бажанням прикріпіть їх "
            "до повідомлення з коментарем."
        ),
        "ru": (
            "📸 Фото или скриншот покупки необязательны — при желании прикрепите их "
            "к сообщению с комментарием."
        ),
        "en": (
            "📸 A photo or screenshot is optional. If you want, attach it to the "
            "same message as your comment."
        ),
    }[language]
    rating_choice = {
        "uk": f"{stars_emoji} Щоб продовжити користування ботом, оберіть оцінку від 1 до 5, а потім надішліть коментар.",
        "ru": f"{stars_emoji} Чтобы продолжить пользоваться ботом, выберите оценку от 1 до 5, затем отправьте комментарий.",
        "en": f"{stars_emoji} To continue using the bot, choose a 1–5 rating and then send a comment.",
    }[language]
    return (
        f"{title}{bonus}\n\n"
        f"{photo_prompt}\n\n"
        f"{rating_choice}"
    )


def review_rating_prompt(user_id):
    language = user_language(user_id)
    stars_emoji = pe(WELCOME_EMOJI["stars"], "⭐")
    if language == "ru":
        return f"{stars_emoji} Выберите обязательную оценку от 1 до 5, чтобы продолжить. Затем отправьте комментарий; фото можно приложить к нему по желанию."
    if language == "en":
        return f"{stars_emoji} Choose a required 1–5 rating to continue. Then send a comment; you may attach a photo if you like."
    return f"{stars_emoji} Оберіть оцінку від 1 до 5, щоб продовжити. Потім надішліть коментар; фото можна прикріпити за бажанням."


def review_comment_prompt(user_id):
    language = user_language(user_id)
    review_emoji = pe(MAIN_EMOJI["reviews"], "💬")
    if language == "ru":
        return f"{review_emoji} 2️⃣ Отправьте комментарий одним сообщением. Фото/скриншот можно прикрепить к этому же сообщению — это необязательно. Комментарий обязателен."
    if language == "en":
        return f"{review_emoji} 2️⃣ Send your comment in one message. You may attach a photo/screenshot to that same message; it is optional. A comment is required."
    return f"{review_emoji} 2️⃣ Надішліть коментар одним повідомленням. Фото/скриншот можна прикріпити до цього ж повідомлення — це необов’язково. Коментар обов’язковий."


def review_text(user_id, ukrainian, russian, english):
    language = user_language(user_id)
    return {"ru": russian, "en": english}.get(language, ukrainian)


async def notify_review_submitted(user_id):
    try:
        await bot.send_message(
            user_id,
            review_text(
                user_id,
                "✅ Відгук надіслано в чергу адміністратору на перевірку. "
                "Тепер можна користуватися ботом.",
                "✅ Отзыв отправлен администратору на проверку. "
                "Теперь можно пользоваться ботом.",
                "✅ Your review was sent to the admin for moderation. "
                "You can now use the bot.",
            ),
            reply_markup=main_menu(user_id),
        )
    except Exception as exc:
        print(f"Unable to confirm review submission to {user_id}: {exc}", flush=True)


REVIEW_ELIGIBLE_ORDER_TYPES = (
    "buy_stars",
    "buy_ton",
    "withdraw_stars",
    "telegram_premium",
    "game_donation",
    "nft",
    "nft_purchase",
    "digital_product",
    "virtual_number",
)


def ensure_required_review_rows(user_id):
    """Recreate any missing mandatory review requests for completed orders."""
    db.execute(
        """INSERT OR IGNORE INTO customer_reviews(order_id,user_id,status,created_at)
           SELECT o.id,o.user_id,'waiting_rating',?
           FROM orders o
           LEFT JOIN customer_reviews r ON r.order_id=o.id
           WHERE o.user_id=?
             AND o.status='completed'
             AND o.order_type IN ('buy_stars','buy_ton','withdraw_stars','telegram_premium','game_donation','nft','nft_purchase','digital_product','virtual_number')
             AND r.order_id IS NULL""",
        (now(), user_id),
    )
    # Older versions let users skip reviews. Completed eligible orders from
    # those versions must be brought back into the required-review flow.
    db.execute(
        """UPDATE customer_reviews
           SET status='waiting_rating'
           WHERE user_id=? AND status='skipped'
             AND order_id IN (
                 SELECT id FROM orders
                 WHERE user_id=? AND status='completed'
                   AND order_type IN ('buy_stars','buy_ton','withdraw_stars','telegram_premium','game_donation','nft','nft_purchase','digital_product','virtual_number')
             )""",
        (user_id, user_id),
    )
    db.commit()


async def send_review_request(order):
    order = db.execute(
        "SELECT * FROM orders WHERE id=?",
        (order["id"],),
    ).fetchone()
    if not order or order["status"] != "completed":
        return
    if order["order_type"] not in REVIEW_ELIGIBLE_ORDER_TYPES:
        return
    ensure_required_review_rows(order["user_id"])
    active = db.execute(
        """SELECT * FROM customer_reviews
           WHERE user_id=? AND status IN ('waiting_rating','waiting_comment')
           ORDER BY created_at,order_id LIMIT 1""",
        (order["user_id"],),
    ).fetchone()
    if active and int(active["order_id"]) == int(order["id"]):
        await send_pending_review_prompt(order["user_id"])


def review_next_number():
    try:
        configured = int(setting("review_next_number") or 1)
    except (TypeError, ValueError):
        configured = 1
    return max(1, configured)


async def send_pending_review_prompt(user_id):
    ensure_required_review_rows(user_id)
    review = db.execute(
        """SELECT r.*,o.order_type,o.withdrawal_source
           FROM customer_reviews r
           JOIN orders o ON o.id=r.order_id
           WHERE r.user_id=? AND r.status IN ('waiting_rating','waiting_comment')
           ORDER BY r.created_at,r.order_id LIMIT 1""",
        (user_id,),
    ).fetchone()
    if not review:
        return False
    try:
        if review["status"] == "waiting_rating":
            await bot.send_message(
                user_id,
                review_prompt_text(
                    user_id,
                    review["order_id"],
                    review["order_type"],
                    review["withdrawal_source"],
                ),
                reply_markup=review_rating_kb(review["order_id"], user_id),
            )
        else:
            await bot.send_message(
                user_id,
                review_comment_prompt(user_id),
                reply_markup=review_repurchase_kb(review["order_id"]),
            )
        return True
    except Exception as exc:
        print(f"Unable to prompt user {user_id} for review: {exc}", flush=True)
        return False


async def send_review_moderation_notice(order_id, recipient_ids=None):
    review = db.execute(
        "SELECT * FROM customer_reviews WHERE order_id=?",
        (order_id,),
    ).fetchone()
    if not review or review["status"] != "pending_moderation":
        return False
    order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    user = db.execute(
        "SELECT first_name FROM users WHERE id=?",
        (review["user_id"],),
    ).fetchone()
    if not order or not user:
        return False
    text = (
        f"📝 <b>Новий відгук на модерацію</b>\n"
        f"Замовлення: <code>#{order_id}</code>\n"
        f"Клієнт: {esc(user['first_name'] or 'Клієнт')} "
        f"(<code>{review['user_id']}</code>)\n"
        f"Оцінка: <b>{int(review['rating'] or 0)}/5</b>\n\n"
        f"💬 {review['comment_html'] or esc(review['comment'])}"
    )
    delivered = await notify_admins(
        text,
        reply_markup=review_moderation_kb(order_id),
        photo=(
            review["screenshot_file_id"]
            if review["screenshot_type"] == "photo" else None
        ),
        document=(
            review["screenshot_file_id"]
            if review["screenshot_type"] == "document" else None
        ),
        recipient_ids=(
            recipient_ids
            if recipient_ids is not None
            else sorted(ADMIN_IDS)
        ),
    )
    return bool(delivered)


def review_purchase_line(order):
    amount = f"{float(order['amount']):g}"
    if order["order_type"] in {"nft", "nft_purchase", "digital_product", "virtual_number"}:
        meta = order_json_meta(order)
        product_name = esc(meta.get("product_name") or meta.get("number_title") or "товар")
        return f"🎁 Придбав {product_name} за {amount} грн"
    if order["order_type"] == "buy_ton":
        return f"💎 Придбав {esc(order['quantity'])} TON за {amount} грн"
    if order["order_type"] == "telegram_premium":
        return (
            f"⭐ Придбав Telegram Premium на "
            f"{esc(order['quantity'])} міс. за {amount} грн"
        )
    if order["order_type"] == "withdraw_stars":
        return f"⭐ Вивів {esc(order['quantity'])} Stars"
    return (
        f"⭐ Придбав {esc(order['quantity'])} Stars за {amount} грн"
    )


def review_caption(review, order, user):
    # Public reviews show first names only; never expose usernames or IDs.
    display_name = (user["first_name"] or "").strip() or "Клієнт"
    comment_html = review["comment_html"] or esc(review["comment"])
    rating = pe(WELCOME_EMOJI["stars"], "⭐") * max(
        1,
        min(5, int(review["rating"] or 1)),
    )
    review_number = review["review_no"]
    if review_number is None:
        raise ValueError("Номер відгуку ще не присвоєний.")
    purchase_date = str(order["created_at"] or now())[:10]
    comment_line = f"💬 Коментар:\n{comment_html}\n" if comment_html else ""
    return (
        f"💬 <b>Відгук #{review_number}</b>\n\n"
        f"👤 Клієнт: {esc(display_name)}\n"
        f"{comment_line}"
        f"{rating}\n\n"
        f"{review_purchase_line(order)}\n\n"
        f"📅 Дата: {esc(purchase_date)}\n\n"
        "SpanixStars — ваш надійний партнер!"
    )


async def publish_review(order_id):
    review = db.execute(
        "SELECT * FROM customer_reviews WHERE order_id=?",
        (order_id,),
    ).fetchone()
    if not review or review["status"] not in {"pending_moderation", "publish_failed"}:
        return False
    if (
        not (review["comment"] or "").strip()
        or not review["rating"]
        or not 1 <= int(review["rating"]) <= 5
    ):
        return False
    db.execute("BEGIN IMMEDIATE")
    try:
        review = db.execute(
            "SELECT * FROM customer_reviews WHERE order_id=?",
            (order_id,),
        ).fetchone()
        if not review or review["status"] not in {"pending_moderation", "publish_failed"}:
            db.rollback()
            return False
        if review["review_no"] is not None:
            review_number = int(review["review_no"])
        else:
            review_number = review_next_number()
            db.execute(
                "INSERT OR REPLACE INTO settings(key,value) VALUES('review_next_number',?)",
                (str(review_number + 1),),
            )
        cursor = db.execute(
            """UPDATE customer_reviews
               SET status='publishing',publish_error=NULL,
                   review_no=COALESCE(review_no,?)
               WHERE order_id=? AND status IN ('pending_moderation','publish_failed')""",
            (review_number, order_id),
        )
        if cursor.rowcount != 1:
            db.rollback()
            return False
        db.commit()
    except Exception:
        db.rollback()
        raise
    review = db.execute(
        "SELECT * FROM customer_reviews WHERE order_id=?",
        (order_id,),
    ).fetchone()
    try:
        channel_id = await resolve_reviews_channel()
        order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        user = db.execute("SELECT * FROM users WHERE id=?", (review["user_id"],)).fetchone()
        if not order or not user:
            raise RuntimeError("Не знайдено замовлення або дані клієнта.")
        caption = review_caption(review, order, user)
        if review["screenshot_file_id"] and review["screenshot_type"] == "document":
            sent = await bot.send_document(
                channel_id,
                document=review["screenshot_file_id"],
                caption=caption,
            )
        elif review["screenshot_file_id"]:
            sent = await bot.send_photo(
                channel_id,
                photo=review["screenshot_file_id"],
                caption=caption,
            )
        else:
            sent = await bot.send_message(channel_id, text=caption)
        bonus_stars = secrets.choice((1, 2))
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute(
            """UPDATE customer_reviews
               SET status='published',channel_message_id=?,published_at=?,
                   reward_stars=?,reward_paid=1,publish_error=NULL
               WHERE order_id=? AND status='publishing' AND reward_paid=0""",
            (sent.message_id, now(), bonus_stars, order_id),
        )
        if cursor.rowcount != 1:
            db.rollback()
            raise RuntimeError("Відгук уже був оброблений або не очікує публікації.")
        db.execute(
            "UPDATE users SET balance_stars=balance_stars+? WHERE id=?",
            (bonus_stars, review["user_id"]),
        )
        db.commit()
        try:
            await bot.send_message(
                review["user_id"],
                review_text(
                    review["user_id"],
                    f"✅ Дякуємо! Ваш відгук опубліковано в каналі.\n"
                    f"⭐ На баланс зараховано +{bonus_stars} Stars.",
                    f"✅ Спасибо! Ваш отзыв опубликован в канале.\n"
                    f"⭐ На баланс зачислено +{bonus_stars} Stars.",
                    f"✅ Your review is now published.\n"
                    f"⭐ +{bonus_stars} Stars have been added to your balance.",
                ),
            )
        except Exception:
            pass
        return True
    except Exception as exc:
        error = str(exc)[:1000]
        db.execute(
            "UPDATE customer_reviews SET status='publish_failed',publish_error=? WHERE order_id=?",
            (error, order_id),
        )
        db.commit()
        notice = (
            f"⚠️ Не вдалося опублікувати відгук за замовленням #{order_id}.\n"
            f"Помилка: <code>{esc(error)}</code>\n"
            "Перевірте налаштування каналу та права бота, потім повторіть публікацію."
        )
        await notify_admins(notice, reply_markup=review_retry_kb(order_id))
        try:
            await bot.send_message(
                review["user_id"],
                review_text(
                    review["user_id"],
                    "⚠️ Відгук збережено, але його поки не вдалося опублікувати. Адміністратор перевірить канал.",
                    "⚠️ Отзыв сохранён, но пока не удалось его опубликовать. Администратор проверит канал.",
                    "⚠️ Your review was saved, but could not be published yet. An administrator will check the channel.",
                ),
            )
        except Exception:
            pass
        return False


async def pending_review_message_filter(message: Message) -> bool:
    if not message.from_user:
        return False
    return db.execute(
        """SELECT 1 FROM customer_reviews
           WHERE user_id=? AND status IN ('waiting_rating','waiting_comment')
           LIMIT 1""",
        (message.from_user.id,),
    ).fetchone() is not None


def referral_purchase_stars(order):
    """Convert a completed purchase to Stars for the referral commission."""
    if order["order_type"] == "buy_stars":
        return max(0, int(order["quantity"]))
    if order["order_type"] in {"buy_ton", "nft", "telegram_premium"}:
        try:
            stars_rate = float(setting("stars_rate"))
            if stars_rate > 0:
                return max(0, int(float(order["amount"]) / stars_rate))
        except (TypeError, ValueError):
            pass
    return 0


def referral_percent():
    """Current commission percentage, editable from the admin panel."""
    try:
        value = float(setting("referral_percent"))
        return min(100.0, max(0.0, value))
    except (TypeError, ValueError):
        return 10.0


def is_valid_ton_wallet(value):
    """Accept common user-friendly TON wallet formats, never private keys."""
    wallet = (value or "").strip()
    if re.fullmatch(r"(?:EQ|UQ|kQ|0Q|Ef|Uf|kf|0f)[A-Za-z0-9_-]{46}", wallet):
        return True
    if re.fullmatch(r"0:[0-9a-fA-F]{64}", wallet):
        return True
    return False


def referral_available_stars(user_id):
    row = db.execute(
        """SELECT
               COALESCE(u.referral_balance_stars, 0)
               - COALESCE((
                   SELECT SUM(amount_stars)
                   FROM referral_withdrawals
                   WHERE user_id=? AND status='pending'
               ), 0)
               - COALESCE((
                   SELECT SUM(CAST(quantity AS INTEGER))
                   FROM orders
                   WHERE user_id=?
                     AND order_type='withdraw_stars'
                     AND withdrawal_source='referral'
                     AND status IN ('waiting_payment','waiting_review')
               ), 0) AS available
           FROM users u
           WHERE u.id=?""",
        (user_id, user_id, user_id),
    ).fetchone()
    return max(0, int(row["available"] or 0)) if row else 0


def referral_invited_count(user_id):
    row = db.execute(
        "SELECT COUNT(*) AS c FROM users WHERE referrer_id=?",
        (user_id,),
    ).fetchone()
    return int(row["c"] or 0) if row else 0


def referral_people_lines(user_id, limit=10, offset=0):
    """Return invited users, including those who have not made a purchase."""
    language = user_language(user_id)
    people = db.execute(
        """SELECT u.id,u.username,u.first_name,
                  COUNT(CASE WHEN o.status='completed'
                             AND o.order_type IN ('buy_stars','buy_ton','nft','nft_purchase','digital_product','game_donation','telegram_premium')
                             THEN 1 END) AS purchases,
                  COALESCE(SUM(CASE WHEN o.status='completed'
                                    AND o.order_type IN ('buy_stars','buy_ton','nft','nft_purchase','digital_product','game_donation','telegram_premium')
                                    THEN o.amount ELSE 0 END), 0) AS spent
           FROM users u
           LEFT JOIN orders o ON o.user_id=u.id
           WHERE u.referrer_id=?
           GROUP BY u.id
           ORDER BY u.registered_at DESC,u.id DESC
           LIMIT ? OFFSET ?""",
        (user_id, limit, offset),
    ).fetchall()
    lines = []
    for person in people:
        label = (
            f"@{esc(person['username'])}"
            if person["username"]
            else esc(person["first_name"] or f"ID {person['id']}")
        )
        if person["purchases"]:
            if language == "ru":
                line = (
                    f"• {label} — 🛍 покупок: <b>{person['purchases']}</b>, "
                    f"💳 оплачено: <b>{float(person['spent'] or 0):.2f} грн</b>"
                )
            elif language == "en":
                line = (
                    f"• {label} — 🛍 purchases: <b>{person['purchases']}</b>, "
                    f"💳 spent: <b>{float(person['spent'] or 0):.2f} UAH</b>"
                )
            else:
                line = (
                    f"• {label} — 🛍 покупок: <b>{person['purchases']}</b>, "
                    f"💳 сплачено: <b>{float(person['spent'] or 0):.2f} грн</b>"
                )
            lines.append(line)
        else:
            no_purchases = {
                "uk": "поки без підтверджених покупок",
                "ru": "пока без подтверждённых покупок",
                "en": "no confirmed purchases yet",
            }[language]
            lines.append(f"• {label} — {no_purchases}")
    return lines


def referral_report(user_id):
    user = user_row(user_id)
    invited = referral_invited_count(user_id) if user else 0
    buyers = db.execute(
        """SELECT COUNT(DISTINCT u.id) AS c
           FROM users u
           JOIN orders o ON o.user_id=u.id
           WHERE u.referrer_id=?
             AND o.status='completed'
             AND o.order_type IN ('buy_stars','buy_ton','nft','nft_purchase','digital_product','game_donation','telegram_premium')""",
        (user_id,),
    ).fetchone()["c"]
    total_earned = int(user["referral_earnings_stars"] or 0) if user else 0
    available = referral_available_stars(user_id)
    return invited, int(buyers or 0), total_earned, available, referral_people_lines(user_id)


def active_raffle():
    # Legacy raffle records are preserved for database compatibility, but
    # chance-based prize mechanics are disabled in this shop build.
    return None


def current_raffle():
    # Read-only legacy lookup, retained only so admins can review/cancel old records.
    return db.execute(
        "SELECT * FROM raffles WHERE status IN ('active','ready') ORDER BY id DESC LIMIT 1"
    ).fetchone()


def raffle_fee(stars: int) -> int:
    """Round 3% of an integer Stars purchase to the nearest whole Star."""
    return (max(0, int(stars)) * 3 + 50) // 100


def record_raffle_contribution(raffle_id: int, order_id: int, user_id: int, purchased_stars: int):
    fee = raffle_fee(purchased_stars)
    cur = db.execute(
        """INSERT OR IGNORE INTO raffle_contributions
           (raffle_id,order_id,user_id,purchased_stars,contributed_stars,created_at)
           VALUES(?,?,?,?,?,?)""",
        (raffle_id, order_id, user_id, purchased_stars, fee, now()),
    )
    if cur.rowcount == 0:
        return 0

    db.execute(
        "UPDATE raffles SET pool_stars=pool_stars+? WHERE id=? AND status='active'",
        (fee, raffle_id),
    )
    raffle = db.execute("SELECT target_stars,pool_stars,status FROM raffles WHERE id=?", (raffle_id,)).fetchone()
    if raffle and raffle["status"] == "active" and raffle["pool_stars"] >= raffle["target_stars"]:
        db.execute("UPDATE raffles SET status='ready' WHERE id=? AND status='active'", (raffle_id,))
    return fee


def kb_button(text, *, emoji_id=None, style=None, callback_data=None, url=None):
    kwargs = {"text": text}
    if emoji_id:
        kwargs["icon_custom_emoji_id"] = str(emoji_id)
    if style:
        kwargs["style"] = style
    if callback_data is not None:
        kwargs["callback_data"] = callback_data
    if url is not None:
        kwargs["url"] = url
    return InlineKeyboardButton(**kwargs)


def reply_button(text, *, emoji_id=None, style=None):
    # Kept only for compatibility with old helper references; customer menus
    # now use inline buttons attached to each bot message.
    kwargs = {"text": text}
    if emoji_id:
        kwargs["icon_custom_emoji_id"] = str(emoji_id)
    if style and style != "transparent":
        kwargs["style"] = style
    return InlineKeyboardButton(**kwargs)


def main_menu(user_id=None):
    language = user_language(user_id) if user_id else "uk"
    builder = InlineKeyboardBuilder()

    def add_row(*keys):
        builder.row(*[
            kb_button(
                localized_menu_label(key, language),
                emoji_id=menu_emoji(key),
                style=menu_style(key),
                callback_data=f"menu:{key}",
            )
            for key in keys
        ])

    add_row("buy_stars", "buy_ton", "premium")
    add_row("nft", "games", "digital")
    add_row("withdraw", "sell", "profile")
    add_row("calculator", "reviews", "support")
    add_row("referrals", "language")
    return builder.as_markup()


ASSET_DIR = Path(__file__).resolve().parent / "assets"

SCREEN_IMAGES = {
    "welcome": "0DAC8ABF-4E80-4DAC-9765-68EBFE20BAD1_1790276163145.png",
    "language": "0DAC8ABF-4E80-4DAC-9765-68EBFE20BAD1_1790276163145.png",
    "stars": "8F1E5F14-6C85-481A-89C0-CB8DDB51C6E7_1790276163149.png",
    "ton": "CBAC6203-7823-4978-B81E-835F062AFBAE_1790276163149.png",
    "premium": None,
    "sell": "IMG_0465_1790276163150.jpeg",
    "support": "IMG_0466_1790276163150.jpeg",
    "withdraw": "IMG_0467_1790276163150.jpeg",
    # There is no separate bundled profile asset yet.  It still gets its own
    # setting, so an administrator can upload a profile banner independently.
    "profile": "0DAC8ABF-4E80-4DAC-9765-68EBFE20BAD1_1790276163145.png",
    "referrals": "0DAC8ABF-4E80-4DAC-9765-68EBFE20BAD1_1790276163145.png",
    "calculator": "IMG_0468_1790276163150.jpeg",
    "reviews": "IMG_0469_1790276163150.jpeg",
}


async def replace_screen(
    user_id: int,
    text: str,
    *,
    image=None,
    reply_markup=None,
    screen_key=None,
):
    previous = db.execute(
        "SELECT chat_id, message_id FROM ui_messages WHERE user_id=?",
        (user_id,),
    ).fetchone()
    if previous:
        try:
            await bot.delete_message(
                chat_id=previous["chat_id"],
                message_id=previous["message_id"],
            )
        except Exception:
            pass

    custom_banner = setting(f"banner_{image}") if image else ""
    image_name = SCREEN_IMAGES.get(image) if image else None
    image_path = ASSET_DIR / image_name if image_name else None
    photo = custom_banner or (
        FSInputFile(str(image_path))
        if image_path and image_path.is_file()
        else None
    )
    if photo:
        try:
            sent = await bot.send_photo(
                chat_id=user_id,
                photo=photo,
                caption=text,
                reply_markup=reply_markup,
            )
        except Exception:
            # If an old Telegram file_id becomes unavailable, keep the bot
            # usable and fall back to the bundled image automatically.
            if custom_banner and image_path and image_path.is_file():
                sent = await bot.send_photo(
                    chat_id=user_id,
                    photo=FSInputFile(str(image_path)),
                    caption=text,
                    reply_markup=reply_markup,
                )
            else:
                sent = await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    reply_markup=reply_markup,
                )
    else:
        sent = await bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=reply_markup,
        )
    db.execute(
        "INSERT OR REPLACE INTO ui_messages(user_id,chat_id,message_id,screen_key) VALUES(?,?,?,?)",
        (user_id, user_id, sent.message_id, screen_key),
    )
    db.commit()
    return sent


def cancel_kb(language="uk"):
    cancel_text = {
        "uk": "Скасувати",
        "ru": "Отменить",
        "en": "Cancel",
    }.get(language, "Скасувати")
    builder = InlineKeyboardBuilder()
    builder.row(kb_button(cancel_text, emoji_id=EMOJI_POOL[3], style="danger", callback_data="cancel:state"))
    builder.row(back_inline("back_main", {
        "uk": "До меню", "ru": "В меню", "en": "Main menu"
    }.get(language, "До меню")))
    return builder.as_markup()


def back_inline(callback_data="back_main", text="Назад"):
    return kb_button(text, emoji_id=EMOJI_POOL[1], style="primary", callback_data=callback_data)


def stars_catalog_text(rate=None, recipient_note=""):
    if rate is None:
        rate = float(setting("stars_rate"))
    suffix = f"\n\n{recipient_note}" if recipient_note else ""
    return (
        f"{pe(MAIN_EMOJI['buy_stars'], '⭐')} <b>Оберіть кількість Stars</b>\n\n"
        f"💱 Актуальний курс: <b>{rate:g} грн / 1 Star</b>{suffix}"
    )


def stars_recipient_kb(user_id):
    language = user_language(user_id)
    labels = {
        "uk": ("👤 Для себе", "🎁 Для друга"),
        "ru": ("👤 Себе", "🎁 Другу"),
        "en": ("👤 For me", "🎁 For a friend"),
    }[language]
    builder = InlineKeyboardBuilder()
    builder.row(kb_button(labels[0], style="success", callback_data="stars:recipient:self"))
    builder.row(kb_button(labels[1], style="primary", callback_data="stars:recipient:friend"))
    builder.row(back_inline())
    return builder.as_markup()


def stars_recipient_note(user_id, data=None):
    data = data or {}
    if data.get("stars_target_type") == "friend":
        return f"🎁 Отримувач: <b>{esc(data.get('stars_target') or 'друг')}</b>"
    return {
        "uk": "👤 Отримувач: <b>ви</b>",
        "ru": "👤 Получатель: <b>вы</b>",
        "en": "👤 Recipient: <b>you</b>",
    }[user_language(user_id)]


def create_stars_order(user_id, quantity, amount, data=None):
    data = data or {}
    recipient_type = data.get("stars_target_type", "self")
    recipient = str(data.get("stars_target") or "") if recipient_type == "friend" else ""
    order_id = create_order(user_id, "buy_stars", quantity, amount)
    db.execute(
        "UPDATE orders SET admin_note=? WHERE id=?",
        (json.dumps({"recipient_type": recipient_type, "recipient": recipient}, ensure_ascii=False), order_id),
    )
    db.commit()
    return order_id


def stars_kb():
    b = InlineKeyboardBuilder()
    rate = float(setting("stars_rate"))
    for stars in (50, 100, 500, 1000):
        price = round(stars * rate, 2)
        b.row(kb_button(f"{stars} Stars — {price:g} грн", emoji_id=MAIN_EMOJI["buy_stars"], style="danger", callback_data=f"stars:{stars}"))
    b.row(kb_button("Власна кількість Stars", emoji_id=MAIN_EMOJI["buy_stars"], style="danger", callback_data="stars:custom"))
    b.row(back_inline("menu:buy_stars", "Змінити отримувача"))
    return b.as_markup()


async def refresh_stars_catalog_screens():
    text = stars_catalog_text()
    markup = stars_kb()
    screens = db.execute(
        "SELECT chat_id, message_id FROM ui_messages WHERE screen_key='stars_catalog'"
    ).fetchall()
    for screen in screens:
        try:
            await bot.edit_message_caption(
                chat_id=screen["chat_id"],
                message_id=screen["message_id"],
                caption=text,
                reply_markup=markup,
            )
        except Exception:
            try:
                await bot.edit_message_text(
                    chat_id=screen["chat_id"],
                    message_id=screen["message_id"],
                    text=text,
                    reply_markup=markup,
                )
            except Exception:
                # A user may have deleted the message or blocked the bot.
                pass


def payment_kb(order_id):
    b = InlineKeyboardBuilder()
    b.row(kb_button("Українська картка", emoji_id=EMOJI_POOL[0], style="danger", callback_data=f"pay:{order_id}:ua"))
    if ton_payment_configured():
        b.row(kb_button(
            "Tonkeeper (TON)",
            emoji_id=MAIN_EMOJI["buy_gram"],
            style="primary",
            callback_data=f"pay:{order_id}:ton",
        ))
    if payment_setting_configured("CRYPTO_BOT_TOKEN"):
        b.row(kb_button(
            "CryptoBot",
            emoji_id=EMOJI_POOL[2],
            style="primary",
            callback_data=f"pay:{order_id}:crypto",
        ))
    custom_methods = db.execute(
        """SELECT id,name,method_type
           FROM payment_methods
           WHERE active=1
           ORDER BY id"""
    ).fetchall()
    for method in custom_methods:
        icon = EMOJI_POOL[2] if method["method_type"] == "crypto" else EMOJI_POOL[3]
        b.row(kb_button(
            method["name"],
            emoji_id=icon,
            style="primary",
            callback_data=f"pay_method:{order_id}:{method['id']}",
        ))
    b.row(kb_button("Скасувати замовлення", emoji_id=EMOJI_POOL[3], style="danger", callback_data=f"cancel_order:{order_id}"))
    return b.as_markup()


def crypto_currency_kb(order_id):
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "TON",
        emoji_id=MAIN_EMOJI["buy_gram"],
        style="primary",
        callback_data=f"crypto_currency:{order_id}:TON",
    ))
    b.row(kb_button(
        "USDT",
        emoji_id=EMOJI_POOL[2],
        style="primary",
        callback_data=f"crypto_currency:{order_id}:USDT",
    ))
    b.row(back_inline(f"backpay:{order_id}"))
    return b.as_markup()


def ton_payment_configured():
    # Tonkeeper is enabled only when the receiving wallet and TON Center API
    # key are both configured. The key is required for an automatic payment
    # check after the user returns from Tonkeeper.
    return bool(
        payment_setting("TON_WALLET_ADDRESS")
        and payment_setting("TONCENTER_API_KEY")
    )


def crypto_payment_configured():
    return payment_setting_configured("CRYPTO_BOT_TOKEN")


def payment_decimal(value):
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.000000001"))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Некоректна сума платежу")
    if amount <= 0:
        raise ValueError("Сума платежу повинна бути більшою за нуль")
    return amount


def order_crypto_amount(order_amount, currency):
    rate_value = (
        setting("ton_payment_rate")
        if currency == "TON"
        else setting("usdt_rate")
    )
    rate = payment_decimal(rate_value)
    return (payment_decimal(order_amount) / rate).quantize(Decimal("0.000000001"))


def format_payment_amount(amount):
    return format(amount, "f").rstrip("0").rstrip(".") or "0"


def create_ton_payment_link(nanotons, comment):
    wallet = payment_setting("TON_WALLET_ADDRESS")
    return (
        f"https://app.tonkeeper.com/transfer/{quote(wallet, safe='')}"
        f"?amount={nanotons}&text={quote(comment, safe='')}"
    )


async def ton_api_call(method, params):
    api_key = payment_setting("TONCENTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Не задан ключ TON Center API для автоматичної перевірки платежу."
        )
    timeout = aiohttp.ClientTimeout(total=20)
    headers = {"X-API-Key": api_key} if api_key else {}
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            f"{TONCENTER_API_URL}/{method}",
            params=params,
            headers=headers,
        ) as response:
            result = await response.json(content_type=None)
            if response.status >= 400 or not result.get("ok"):
                raise RuntimeError(str(result))
            return result.get("result", [])


def transaction_comment(transaction):
    in_message = transaction.get("in_msg") or {}
    message = in_message.get("message")
    if message:
        return str(message)
    message_data = in_message.get("msg_data") or {}
    encoded_text = message_data.get("text")
    if message_data.get("@type") == "msg.dataText" and encoded_text:
        try:
            return base64.b64decode(encoded_text).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return ""
    return ""


async def find_ton_payment(comment, amount):
    transactions = await ton_api_call(
        "getTransactions",
        {"address": payment_setting("TON_WALLET_ADDRESS"), "limit": 100},
    )
    expected_nanotons = int(payment_decimal(amount) * Decimal("1000000000"))
    for transaction in transactions:
        if not isinstance(transaction, dict):
            continue
        in_message = transaction.get("in_msg") or {}
        try:
            received_nanotons = int(str(in_message.get("value", "0")))
        except (TypeError, ValueError):
            continue
        if (
            received_nanotons == expected_nanotons
            and transaction_comment(transaction) == comment
        ):
            return True
    return False


async def find_paid_ton_invoice(invoices):
    """Match one incoming TON transaction against every saved order invoice."""
    expected = {}
    invoice_created_times = []
    for invoice in invoices:
        try:
            nanotons = int(
                payment_decimal(invoice["amount"]) * Decimal("1000000000")
            )
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
        expected[(str(invoice["invoice_id"]), nanotons)] = invoice
        try:
            created_at = datetime.strptime(
                str(invoice["created_at"]), "%Y-%m-%d %H:%M:%S"
            )
            invoice_created_times.append(created_at.timestamp())
        except (KeyError, TypeError, ValueError, OSError):
            pass
    if not expected:
        return None

    oldest_invoice_time = min(invoice_created_times) if invoice_created_times else None
    cursor = None
    seen_cursors = set()
    # TON Center returns newest-first. Continue into archival history until
    # every possible payment is older than the oldest invoice being checked.
    for _ in range(1000):
        params = {
            "address": payment_setting("TON_WALLET_ADDRESS"),
            "limit": 100,
            "archival": "true",
        }
        if cursor:
            params.update(cursor)
        transactions = await ton_api_call("getTransactions", params)
        if not transactions:
            break

        oldest_transaction_time = None
        for transaction in transactions:
            if not isinstance(transaction, dict):
                continue
            in_message = transaction.get("in_msg") or {}
            try:
                received_nanotons = int(str(in_message.get("value", "0")))
            except (TypeError, ValueError):
                received_nanotons = -1
            key = (transaction_comment(transaction), received_nanotons)
            if key in expected:
                return expected[key]
            try:
                transaction_time = float(transaction.get("utime"))
                oldest_transaction_time = (
                    transaction_time
                    if oldest_transaction_time is None
                    else min(oldest_transaction_time, transaction_time)
                )
            except (TypeError, ValueError):
                pass

        last_transaction = transactions[-1]
        transaction_id = (
            last_transaction.get("transaction_id")
            if isinstance(last_transaction, dict)
            else None
        )
        if not isinstance(transaction_id, dict):
            break
        next_cursor = {
            "lt": str(transaction_id.get("lt") or ""),
            "hash": str(transaction_id.get("hash") or ""),
        }
        if not all(next_cursor.values()):
            break
        cursor_key = (next_cursor["lt"], next_cursor["hash"])
        if cursor_key in seen_cursors:
            break
        seen_cursors.add(cursor_key)
        cursor = next_cursor

        if (
            oldest_invoice_time is not None
            and oldest_transaction_time is not None
            and oldest_transaction_time < oldest_invoice_time - 300
        ):
            break
    return None


async def crypto_api_call(method, payload):
    token = payment_setting("CRYPTO_BOT_TOKEN")
    if not token:
        raise RuntimeError("CRYPTO_BOT_TOKEN не налаштований")
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{CRYPTO_API_URL}/{method}",
            json=payload,
            headers={"Crypto-Pay-API-Token": token},
        ) as response:
            result = await response.json(content_type=None)
            if response.status >= 400 or not result.get("ok"):
                raise RuntimeError(str(result))
            return result.get("result")


async def create_crypto_invoice(order, currency):
    amount = order_crypto_amount(order["amount"], currency)
    invoice = await crypto_api_call(
        "createInvoice",
        {
            "asset": currency,
            "amount": format_payment_amount(amount),
            "description": f"Spanix Stars: замовлення #{order['id']}",
            "payload": f"spanix_order:{order['id']}",
        },
    )
    if not isinstance(invoice, dict):
        raise RuntimeError("CryptoBot повернув некоректну відповідь.")
    return invoice, amount


def crypto_invoice_paid(invoice):
    return str((invoice or {}).get("status", "")).lower() in {"paid", "completed"}


def payment_invoices_for_order(order, provider):
    rows = db.execute(
        """SELECT * FROM payment_invoices
           WHERE order_id=? AND provider=? AND status='pending'
           ORDER BY id""",
        (order["id"], provider),
    ).fetchall()
    if rows:
        return rows
    # Keep compatibility with databases created before invoice history existed.
    if order["payment_method"] == provider and order["invoice_id"]:
        return [{
            "order_id": order["id"],
            "provider": provider,
            "invoice_id": order["invoice_id"],
            "payment_url": order["payment_url"],
            "currency": order["payment_currency"],
            "amount": order["payment_amount"],
            "created_at": order["created_at"],
            "status": "pending",
        }]
    return []


def save_payment_invoice(order_id, provider, invoice_id, payment_url, currency, amount):
    """Persist a new invoice without discarding earlier invoice identifiers."""
    db.execute("BEGIN IMMEDIATE")
    try:
        cursor = db.execute(
            """UPDATE orders SET payment_method=?,invoice_id=?,payment_url=?,
                      payment_currency=?,payment_amount=?
               WHERE id=? AND status='waiting_payment'""",
            (provider, invoice_id, payment_url, currency, float(amount), order_id),
        )
        if cursor.rowcount != 1:
            db.rollback()
            return False
        db.execute(
            """INSERT INTO payment_invoices(
                   order_id,provider,invoice_id,payment_url,currency,amount,status,created_at
               ) VALUES(?,?,?,?,?,?,'pending',?)""",
            (
                order_id,
                provider,
                str(invoice_id),
                payment_url,
                currency,
                float(amount),
                now(),
            ),
        )
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


def mark_order_payment_found(order_id, invoice_id, provider):
    """Move an unpaid order to review exactly once after an invoice is verified."""
    db.execute("BEGIN IMMEDIATE")
    try:
        cursor = db.execute(
            """UPDATE orders SET status='waiting_review'
               WHERE id=? AND status='waiting_payment'""",
            (order_id,),
        )
        if cursor.rowcount != 1:
            db.rollback()
            return False
        db.execute(
            """UPDATE payment_invoices SET status='paid'
               WHERE order_id=? AND provider=? AND invoice_id=?""",
            (order_id, provider, str(invoice_id)),
        )
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


async def notify_paid_order(order, payment_label):
    user = user_row(order["user_id"])
    wallet_line = (
        f"🪙 TON-адреса для переказу: <code>{esc(order['ton_wallet'])}</code>\n"
        if order["order_type"] == "buy_ton" and order["ton_wallet"]
        else ""
    )
    text = (
        f"🔔 <b>Оплата замовлення #{order['id']}</b>\n\n"
        f"👤 @{esc(user['username'] or 'без_username')}\n"
        f"🆔 <code>{user['id']}</code>\n"
        f"📦 {esc(order_type_title(order))}: <b>{esc(order_quantity_title(order))}</b>\n"
        f"{wallet_line}"
        f"💰 <b>{order['amount']:.2f} грн</b>\n"
        f"💳 {esc(payment_label)}\n"
        f"🕒 {order['created_at']}\n\n"
        "Платіж знайдений автоматично. Потрібне підтвердження адміністратора."
    )
    await notify_admins(text, admin_order_kb(order["id"]))


def bank_kb(order_id):
    b = InlineKeyboardBuilder()
    for name, code, _ in BANK_OPTIONS:
        b.row(kb_button(name, emoji_id=EMOJI_POOL[3], style="danger", callback_data=f"bank:{order_id}:{code}"))
    b.row(back_inline(f"backpay:{order_id}"))
    b.row(kb_button("Скасувати", emoji_id=EMOJI_POOL[3], style="danger", callback_data=f"cancel_order:{order_id}"))
    return b.as_markup()


def receipt_kb(order_id):
    b = InlineKeyboardBuilder()
    b.row(kb_button("Я оплатив — надіслати квитанцію", emoji_id=EMOJI_POOL[0], style="success", callback_data=f"receipt:{order_id}"))
    b.row(kb_button("Скасувати", emoji_id=EMOJI_POOL[3], style="danger", callback_data=f"cancel_order:{order_id}"))
    return b.as_markup()


def admin_order_kb(order_id):
    b = InlineKeyboardBuilder()
    order = db.execute(
        "SELECT order_type FROM orders WHERE id=?",
        (order_id,),
    ).fetchone()
    button_text = (
        "📥 Взяти заявку в роботу"
        if order and order["order_type"] == "withdraw_stars"
        else "📥 Взяти квитанцію"
    )
    b.row(kb_button(
        button_text,
        emoji_id=EMOJI_POOL[5],
        style="primary",
        callback_data=f"admin_claim:{order_id}",
    ))
    return b.as_markup()


def admin_order_review_kb(order_id):
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "✅ Підтвердити",
        emoji_id=EMOJI_POOL[0],
        style="success",
        callback_data=f"admin_ok:{order_id}",
    ))
    b.row(kb_button(
        "❌ Відхилити",
        emoji_id=EMOJI_POOL[3],
        style="danger",
        callback_data=f"admin_no:{order_id}",
    ))
    return b.as_markup()


def admin_ton_delivery_kb(order_id):
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "✅ TON відправлено",
        emoji_id=EMOJI_POOL[0],
        style="success",
        callback_data=f"admin_ton_sent:{order_id}",
    ))
    return b.as_markup()


def admin_premium_delivery_kb(order_id):
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "✅ Telegram Premium активовано",
        emoji_id=EMOJI_POOL[0],
        style="success",
        callback_data=f"admin_premium_sent:{order_id}",
    ))
    return b.as_markup()


def referral_withdrawal_kb(request_id):
    b = InlineKeyboardBuilder()
    b.row(
        kb_button(
            "✅ Подтвердить выплату",
            emoji_id=EMOJI_POOL[0],
            style="success",
            callback_data=f"refwd:approve:{request_id}",
        )
    )
    b.row(
        kb_button(
            "❌ Отклонить",
            emoji_id=EMOJI_POOL[3],
            style="danger",
            callback_data=f"refwd:reject:{request_id}",
        )
    )
    return b.as_markup()


def raffle_consent_kb(raffle_id, order_id):
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "Так, внести 3% з цього поповнення",
        emoji_id=EMOJI_POOL[0],
        style="success",
        callback_data=f"raffle_consent:{raffle_id}:{order_id}:yes",
    ))
    b.row(kb_button(
        "Відмовитись",
        emoji_id=EMOJI_POOL[3],
        style="danger",
        callback_data=f"raffle_consent:{raffle_id}:{order_id}:no",
    ))
    return b.as_markup()


def raffle_admin_kb(raffle):
    b = InlineKeyboardBuilder()
    if not raffle:
        b.row(kb_button("Створити розіграш", emoji_id=EMOJI_POOL[0], style="success", callback_data="adm:raffle:create"))
    elif raffle["status"] == "ready":
        b.row(kb_button("Провести розіграш", emoji_id=EMOJI_POOL[0], style="success", callback_data=f"raffle:draw:{raffle['id']}"))
    else:
        b.row(kb_button("Оновити дані", emoji_id=EMOJI_POOL[27], style="primary", callback_data="adm:raffle"))
    if raffle and raffle["status"] in {"active", "ready"}:
        b.row(kb_button(
            "Скасувати розіграш",
            emoji_id=EMOJI_POOL[3],
            style="danger",
            callback_data=f"raffle:cancel:{raffle['id']}",
        ))
    b.row(back_inline("admin:back"))
    return b.as_markup()


def raffle_cancel_confirm_kb(raffle_id):
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "Так, скасувати й повернути Stars",
        emoji_id=EMOJI_POOL[3],
        style="danger",
        callback_data=f"raffle:cancel_confirm:{raffle_id}",
    ))
    b.row(back_inline("adm:raffle", "Ні, повернутись"))
    return b.as_markup()


def bank_details_kb():
    b = InlineKeyboardBuilder()
    for name, code, _ in BANK_OPTIONS:
        b.row(kb_button(name, emoji_id=EMOJI_POOL[3], style="primary", callback_data=f"adm_bank:{code}"))
    b.row(back_inline("admin:back"))
    return b.as_markup()


def support_reply_kb(user_id):
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "📥 Взяти звернення",
        emoji_id=EMOJI_POOL[5],
        style="primary",
        callback_data=f"support_claim:{user_id}",
    ))
    return b.as_markup()


def support_reply_ready_kb(ticket_id):
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "✍️ Відповісти",
        emoji_id=MAIN_EMOJI["support"],
        style="primary",
        callback_data=f"support_reply:{ticket_id}",
    ))
    return b.as_markup()


def required_channels_admin_kb():
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "➕ Додати канал",
        emoji_id=EMOJI_POOL[0],
        style="success",
        callback_data="required_admin:add",
    ))
    for channel in db.execute(
        "SELECT * FROM required_channels ORDER BY id"
    ).fetchall():
        status = "увімкнено" if channel["active"] else "вимкнено"
        b.row(kb_button(
            f"#{channel['id']} {channel['title']} · {status}",
            emoji_id=EMOJI_POOL[27],
            style="primary",
            callback_data=f"required_admin:edit:{channel['id']}",
        ))
    b.row(back_inline("admin:back"))
    return b.as_markup()


def moderator_admin_kb():
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "➕ Додати модератора",
        emoji_id=EMOJI_POOL[0],
        style="success",
        callback_data="moderator:add",
    ))
    for moderator in db.execute(
        "SELECT * FROM moderators ORDER BY created_at DESC"
    ).fetchall():
        label = f"@{moderator['username']}" if moderator["username"] else str(moderator["user_id"])
        b.row(kb_button(
            f"🛡 {label}",
            emoji_id=EMOJI_POOL[27],
            style="primary",
            callback_data=f"moderator:remove:{moderator['user_id']}",
        ))
    b.row(back_inline("admin:back"))
    return b.as_markup()



def received_emoji_token(message, fallback="🎮"):
    for entity in (message.entities or []):
        if getattr(entity, "type", None) == "custom_emoji":
            custom_id = getattr(entity, "custom_emoji_id", None)
            if custom_id:
                return str(custom_id)
    return (message.text or "").strip() or fallback


def game_emoji_markup(value, fallback="🎮"):
    value = str(value or "").strip()
    if value.isdigit():
        return pe(value, fallback)
    return esc(value or fallback)


def game_admin_list_kb():
    b = InlineKeyboardBuilder()
    games = db.execute(
        "SELECT id,name,emoji,active FROM game_catalog ORDER BY id DESC"
    ).fetchall()
    for game in games:
        emoji_value = str(game["emoji"] or "🎮")
        emoji_id = emoji_value if emoji_value.isdigit() else None
        visible_emoji = "" if emoji_id else f"{emoji_value} "
        b.row(kb_button(
            f"{visible_emoji}{game['name']}"[:60],
            emoji_id=emoji_id,
            style="primary" if game["active"] else "danger",
            callback_data=f"adm:game:{game['id']}",
        ))
    b.row(kb_button("➕ Додати гру", style="success", callback_data="adm:game:add"))
    b.row(back_inline("admin:back"))
    return b.as_markup()


def game_detail_kb(game_id):
    b = InlineKeyboardBuilder()
    packages = db.execute(
        "SELECT id,product_name,quantity,price FROM game_packages "
        "WHERE game_id=? AND active=1 ORDER BY id",
        (game_id,),
    ).fetchall()
    for p in packages:
        b.row(kb_button(
            f"✏️ {p['product_name']} · {p['quantity']} · {p['price']:g} грн"[:60],
            style="primary", callback_data=f"adm:gamepkg_edit:{p['id']}",
        ))
    b.row(kb_button("➕ Додати пакет", style="success", callback_data=f"adm:gamepkg:add:{game_id}"))
    b.row(kb_button("🗑 Видалити гру", style="danger", callback_data=f"adm:game:delete:{game_id}"))
    b.row(back_inline("adm:games"))
    return b.as_markup()


def game_catalog_kb():
    b = InlineKeyboardBuilder()
    games = db.execute(
        "SELECT id,name,emoji FROM game_catalog WHERE active=1 ORDER BY id"
    ).fetchall()
    for game in games:
        emoji_value = str(game["emoji"] or "🎮")
        emoji_id = emoji_value if emoji_value.isdigit() else None
        visible_emoji = "" if emoji_id else f"{emoji_value} "
        b.row(kb_button(
            f"{visible_emoji}{game['name']}"[:60],
            emoji_id=emoji_id,
            style="primary", callback_data=f"game:open:{game['id']}",
        ))
    b.row(back_inline("back_main"))
    return b.as_markup()


def game_packages_kb(game_id):
    b = InlineKeyboardBuilder()
    game = db.execute("SELECT * FROM game_catalog WHERE id=? AND active=1", (game_id,)).fetchone()
    if not game:
        return b.as_markup()
    packages = db.execute(
        "SELECT * FROM game_packages WHERE game_id=? AND active=1 ORDER BY id",
        (game_id,),
    ).fetchall()
    for p in packages:
        emoji_value = str(p["emoji"] or "📦")
        emoji_id = emoji_value if emoji_value.isdigit() else None
        visible_emoji = "" if emoji_id else f"{emoji_value} "
        b.row(kb_button(
            f"{visible_emoji}{p['product_name']} · {p['quantity']} — {p['price']:g} грн"[:60],
            emoji_id=emoji_id,
            style="success", callback_data=f"game:buy:{p['id']}",
        ))
    if float(game["unit_price"] or 0) > 0:
        unit_emoji = str(game["unit_emoji"] or "📦")
        unit_emoji_id = unit_emoji if unit_emoji.isdigit() else None
        visible_unit_emoji = "" if unit_emoji_id else f"{unit_emoji} "
        b.row(kb_button(
            f"{visible_unit_emoji}Своя кількість",
            emoji_id=unit_emoji_id,
            style="primary", callback_data=f"game:custom:{game_id}",
        ))
    b.row(back_inline("game:list"))
    return b.as_markup()


def game_application_kb(order_id):
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "📨 Подати заявку адміністратору",
        style="success", callback_data=f"game:apply:{order_id}",
    ))
    return b.as_markup()


def admin_game_application_kb(order_id):
    b = InlineKeyboardBuilder()
    b.row(kb_button("✅ Прийняти заявку", style="success", callback_data=f"admin_game_accept:{order_id}"))
    b.row(kb_button("❌ Відхилити", style="danger", callback_data=f"admin_game_reject:{order_id}"))
    return b.as_markup()


def admin_game_delivery_kb(order_id):
    b = InlineKeyboardBuilder()
    b.row(kb_button("✅ Підтвердити донат", style="success", callback_data=f"admin_game_delivered:{order_id}"))
    return b.as_markup()


def game_order_meta(order):
    try:
        data = json.loads(order["admin_note"] or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def admin_menu_kb():
    b = InlineKeyboardBuilder()
    sections = [
        ("🛍 Каталоги товарів", "catalog"),
        ("💳 Оплати та курси", "payments"),
        ("📦 Замовлення і квитанції", "orders"),
        ("👥 Користувачі та статистика", "users"),
        ("💬 Відгуки", "reviews"),
        ("🎨 Оформлення бота", "appearance"),
        ("🆘 Підтримка та розсилка", "communication"),
        ("🔐 Безпека та доступ", "security"),
    ]
    for title, section in sections:
        b.row(kb_button(title, style="primary", callback_data=f"adm:section:{section}"))
    return b.as_markup()


def admin_section_kb(section):
    sections = {
        "catalog": [
            ("📱 Віртуальні номери", "adm:virtual_numbers"),
            ("🎁 NFT та товари", "adm:products"),
            ("⭐ Telegram Premium", "adm:premium"),
        ],
        "payments": [
            ("⭐ Курс Stars", "adm:stars_rate"),
            ("💎 Курс TON", "adm:ton_rate"),
            ("🪙 Курс TON для оплати", "adm:ton_payment_rate"),
            ("💵 Курс USDT", "adm:usdt_rate"),
            ("🏦 Реквізити карток", "adm:bank_details"),
            ("⚙️ Платіжні сервіси", "adm:payment_settings"),
        ],
        "orders": [
            ("📦 Черга замовлень", "adm:orders"),
            ("🧾 Квитанції та архів", "adm:receipts"),
        ],
        "users": [
            ("👥 Список користувачів", "adm:users"),
            ("📊 Статистика", "adm:stats"),
            ("⭐ Баланси Stars", "adm:balance"),
            ("👥 Реферальні заявки", "adm:referral_withdrawals"),
            ("📈 Реферальний відсоток", "adm:referral_percent"),
        ],
        "reviews": [
            ("⚙️ Налаштування відгуків", "adm:reviews"),
            ("📝 Модерація відгуків", "adm:reviews:queue"),
        ],
        "appearance": [("🎨 Банери, кнопки та емодзі", "adm:ui")],
        "communication": [
            ("🆘 Черга підтримки", "adm:support_queue"),
            ("⚙️ Налаштування підтримки", "adm:support"),
            ("📣 Розсилка", "adm:broadcast"),
        ],
        "security": [
            ("📢 Обов’язкові підписки", "adm:required_channels"),
            ("🛡 Модератори", "adm:moderators"),
        ],
    }
    b = InlineKeyboardBuilder()
    for title, callback in sections.get(section, []):
        b.row(kb_button(title, style="primary", callback_data=callback))
    b.row(back_inline("admin:back", "⬅️ До адмін-панелі"))
    return b.as_markup()


def moderator_menu_kb():
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "🧾 Квитанції",
        emoji_id=EMOJI_POOL[5],
        style="primary",
        callback_data="adm:receipts",
    ))
    b.row(kb_button(
        "🆘 Підтримка",
        emoji_id=MAIN_EMOJI["support"],
        style="primary",
        callback_data="adm:support_queue",
    ))
    return b.as_markup()


def payment_settings_kb():
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "⚡ Налаштувати все одним повідомленням",
        emoji_id=EMOJI_POOL[0],
        style="success",
        callback_data="adm:payment:bundle",
    ))
    b.row(kb_button(
        "Налаштувати CryptoBot",
        emoji_id=EMOJI_POOL[2],
        style="primary",
        callback_data="adm:payment:crypto",
    ))
    b.row(kb_button(
        "Налаштувати TON Center API",
        emoji_id=MAIN_EMOJI["buy_gram"],
        style="primary",
        callback_data="adm:payment:ton_api",
    ))
    b.row(kb_button(
        "Налаштувати TON-гаманець",
        emoji_id=MAIN_EMOJI["buy_gram"],
        style="primary",
        callback_data="adm:payment:ton_wallet",
    ))
    b.row(kb_button(
        "➕ Інші способи оплати",
        emoji_id=EMOJI_POOL[3],
        style="success",
        callback_data="adm:payment:methods",
    ))
    b.row(back_inline("admin:back"))
    return b.as_markup()


def payment_methods_admin_kb():
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "➕ Додати спосіб оплати",
        emoji_id=EMOJI_POOL[0],
        style="success",
        callback_data="payment_method:add",
    ))
    for method in db.execute(
        "SELECT id,name,method_type,active FROM payment_methods ORDER BY id"
    ).fetchall():
        status = "увімкнено" if method["active"] else "вимкнено"
        b.row(kb_button(
            f"{method['name']} · {status}",
            emoji_id=EMOJI_POOL[27],
            style="primary",
            callback_data=f"payment_method:edit:{method['id']}",
        ))
    b.row(back_inline("adm:payment_settings"))
    return b.as_markup()


def payment_method_edit_kb(method_id):
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "✏️ Змінити реквізити",
        emoji_id=EMOJI_POOL[27],
        style="primary",
        callback_data=f"payment_method:update:{method_id}",
    ))
    b.row(kb_button(
        "⏯ Увімкнути / вимкнути",
        emoji_id=EMOJI_POOL[16],
        style="primary",
        callback_data=f"payment_method:toggle:{method_id}",
    ))
    b.row(kb_button(
        "🗑 Видалити",
        emoji_id=EMOJI_POOL[3],
        style="danger",
        callback_data=f"payment_method:delete:{method_id}",
    ))
    b.row(back_inline("adm:payment:methods"))
    return b.as_markup()


def stats_kb():
    b = InlineKeyboardBuilder()
    b.row(kb_button("Оновити", emoji_id=EMOJI_POOL[27], style="primary", callback_data="adm:stats"))
    b.row(kb_button("Скинути статистику", emoji_id=EMOJI_POOL[3], style="danger", callback_data="adm:stats_reset"))
    b.row(back_inline("admin:back"))
    return b.as_markup()


def receipts_kb():
    b = InlineKeyboardBuilder()
    b.row(back_inline("admin:back"))
    return b.as_markup()


def receipts_reset_confirm_kb():
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "Так, скинути архів",
        emoji_id=EMOJI_POOL[3],
        style="danger",
        callback_data="adm:receipts_reset_confirm",
    ))
    b.row(back_inline("adm:receipts"))
    return b.as_markup()


def premium_admin_kb():
    b = InlineKeyboardBuilder()
    for months in (3, 6, 12):
        price = premium_price(months)
        price_text = f"{price:g} грн" if price else "не встановлена"
        b.row(kb_button(
            f"Змінити ціну · {months} міс. ({price_text})",
            emoji_id=EMOJI_POOL[4],
            style="primary",
            callback_data=f"premiumadm:price:{months}",
        ))
    b.row(back_inline("admin:back"))
    return b.as_markup()


def ui_admin_kb():
    b = InlineKeyboardBuilder()
    for key in UI_BANNER_KEYS:
        status = "✅" if setting(f"banner_{key}") else "▫️"
        b.row(
            kb_button(
                f"{status} 🖼 {UI_BANNER_TITLES[key]}",
                emoji_id=EMOJI_POOL[27],
                style="primary",
                callback_data=f"adm:ui:banner:{key}",
            ),
            kb_button(
                "♻️",
                emoji_id=EMOJI_POOL[3],
                style="danger",
                callback_data=f"adm:ui:banner_reset:{key}",
            ),
        )
    b.row(kb_button(
        "✏️ Кнопки головного меню",
        emoji_id=EMOJI_POOL[27],
        style="primary",
        callback_data="adm:ui:buttons",
    ))
    b.row(kb_button(
        "📝 Текст старту",
        emoji_id=EMOJI_POOL[27],
        style="primary",
        callback_data="adm:ui:welcome",
    ))
    b.row(kb_button(
        "♻️ Скинути оформлення",
        emoji_id=EMOJI_POOL[3],
        style="danger",
        callback_data="adm:ui:reset",
    ))
    b.row(back_inline("admin:back"))
    return b.as_markup()


def ui_buttons_admin_kb():
    b = InlineKeyboardBuilder()
    for key, title in MENU_BUTTON_TITLES.items():
        style = menu_style(key)
        style_label = "без кольору" if style == "transparent" else style
        b.row(kb_button(
            f"{title} · {style_label}",
            emoji_id=menu_emoji(key),
            style="primary",
            callback_data=f"adm:ui:button:{key}",
        ))
    b.row(back_inline("adm:ui"))
    return b.as_markup()


def ui_button_admin_kb(key):
    b = InlineKeyboardBuilder()
    for language, title in LANGUAGE_NAMES.items():
        b.row(kb_button(
            f"✏️ Текст · {title}",
            emoji_id=menu_emoji(key),
            style="primary",
            callback_data=f"adm:ui:label:{key}:{language}",
        ))
    b.row(kb_button(
        "✨ Premium Emoji кнопки",
        emoji_id=menu_emoji(key),
        style="primary",
        callback_data=f"adm:ui:emoji:{key}",
    ))
    b.row(
        kb_button("🔵 Primary", style="primary", callback_data=f"adm:ui:style:{key}:primary"),
        kb_button("🟢 Success", style="success", callback_data=f"adm:ui:style:{key}:success"),
        kb_button("🔴 Danger", style="danger", callback_data=f"adm:ui:style:{key}:danger"),
    )
    b.row(kb_button(
        "▫️ Прозора — без кольору",
        callback_data=f"adm:ui:style:{key}:transparent",
    ))
    b.row(kb_button(
        "♻️ Скинути цю кнопку",
        emoji_id=EMOJI_POOL[3],
        style="danger",
        callback_data=f"adm:ui:reset_button:{key}",
    ))
    b.row(back_inline("adm:ui:buttons"))
    return b.as_markup()


def ui_reset_confirm_kb():
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "Так, скинути оформлення",
        emoji_id=EMOJI_POOL[3],
        style="danger",
        callback_data="adm:ui:reset:confirm",
    ))
    b.row(back_inline("adm:ui"))
    return b.as_markup()


class Form(StatesGroup):
    game_name = State()
    game_emoji = State()
    game_method = State()
    game_unit_label = State()
    game_unit_emoji = State()
    game_unit_price = State()
    game_package_name = State()
    game_package_emoji = State()
    game_package_quantity = State()
    game_package_price = State()
    game_custom_quantity = State()
    game_package_edit_price = State()
    game_package_edit_name = State()
    game_package_edit_quantity = State()
    stars_friend_target = State()
    shop_product_name = State()
    shop_product_description = State()
    shop_product_emoji = State()
    shop_product_price = State()
    shop_product_edit_price = State()
    virtual_title = State()
    virtual_description = State()
    virtual_emoji = State()
    virtual_price = State()
    virtual_price_edit = State()
    virtual_admin_number = State()
    custom_stars = State()
    ton_amount = State()
    ton_wallet = State()
    calculator = State()
    support = State()
    withdraw = State()
    referral_withdraw_amount = State()
    sell_stars = State()
    broadcast = State()
    stars_rate = State()
    ton_rate = State()
    premium_price = State()
    ton_payment_rate = State()
    usdt_rate = State()
    referral_percent = State()
    support_setting = State()
    reject_reason = State()
    receipt = State()
    support_reply = State()
    bank_details = State()
    reviews_setting = State()
    review_number_setting = State()
    balance_target = State()
    balance_amount = State()
    raffle_target = State()
    raffle_rules = State()
    payment_crypto_token = State()
    payment_ton_api_key = State()
    payment_ton_wallet = State()
    payment_bundle = State()
    payment_method_name = State()
    payment_method_details = State()
    required_channel = State()
    moderator = State()
    ui_banner = State()
    ui_button_label = State()
    ui_button_emoji = State()
    ui_welcome_text = State()


_active_chat_id = contextvars.ContextVar("active_chat_id", default=None)


class LocalizedBot(Bot):
    """Translate all bot-controlled outgoing copy without touching user data."""

    async def send_message(self, chat_id, text, *args, **kwargs):
        kwargs["reply_markup"] = localize_markup(chat_id, kwargs.get("reply_markup"))
        text = localize_text(chat_id, text)
        try:
            return await super().send_message(chat_id, text, *args, **kwargs)
        except TelegramBadRequest as exc:
            if not should_retry_without_custom_emoji(
                exc, text=text, reply_markup=kwargs.get("reply_markup")
            ):
                raise
            kwargs["reply_markup"] = strip_custom_emoji_button_icons(kwargs.get("reply_markup"))
            return await super().send_message(
                chat_id,
                strip_custom_emoji_tags(text),
                *args,
                **kwargs,
            )

    async def send_photo(self, chat_id, photo, *args, **kwargs):
        kwargs["caption"] = localize_text(chat_id, kwargs.get("caption"))
        kwargs["reply_markup"] = localize_markup(chat_id, kwargs.get("reply_markup"))
        try:
            return await super().send_photo(chat_id, photo, *args, **kwargs)
        except TelegramBadRequest as exc:
            if not should_retry_without_custom_emoji(
                exc,
                caption=kwargs.get("caption"),
                reply_markup=kwargs.get("reply_markup"),
            ):
                raise
            kwargs["caption"] = strip_custom_emoji_tags(kwargs.get("caption"))
            kwargs["reply_markup"] = strip_custom_emoji_button_icons(kwargs.get("reply_markup"))
            return await super().send_photo(chat_id, photo, *args, **kwargs)

    async def send_document(self, chat_id, document, *args, **kwargs):
        kwargs["caption"] = localize_text(chat_id, kwargs.get("caption"))
        kwargs["reply_markup"] = localize_markup(chat_id, kwargs.get("reply_markup"))
        try:
            return await super().send_document(chat_id, document, *args, **kwargs)
        except TelegramBadRequest as exc:
            if not should_retry_without_custom_emoji(
                exc,
                caption=kwargs.get("caption"),
                reply_markup=kwargs.get("reply_markup"),
            ):
                raise
            kwargs["caption"] = strip_custom_emoji_tags(kwargs.get("caption"))
            kwargs["reply_markup"] = strip_custom_emoji_button_icons(kwargs.get("reply_markup"))
            return await super().send_document(chat_id, document, *args, **kwargs)

    async def send_video(self, chat_id, video, *args, **kwargs):
        kwargs["caption"] = localize_text(chat_id, kwargs.get("caption"))
        kwargs["reply_markup"] = localize_markup(chat_id, kwargs.get("reply_markup"))
        try:
            return await super().send_video(chat_id, video, *args, **kwargs)
        except TelegramBadRequest as exc:
            if not should_retry_without_custom_emoji(
                exc,
                caption=kwargs.get("caption"),
                reply_markup=kwargs.get("reply_markup"),
            ):
                raise
            kwargs["caption"] = strip_custom_emoji_tags(kwargs.get("caption"))
            kwargs["reply_markup"] = strip_custom_emoji_button_icons(kwargs.get("reply_markup"))
            return await super().send_video(chat_id, video, *args, **kwargs)

    async def edit_message_text(self, chat_id, message_id, text, *args, **kwargs):
        kwargs["reply_markup"] = localize_markup(chat_id, kwargs.get("reply_markup"))
        text = localize_text(chat_id, text)
        try:
            return await super().edit_message_text(
                chat_id, message_id, text, *args, **kwargs
            )
        except TelegramBadRequest as exc:
            if not should_retry_without_custom_emoji(
                exc, text=text, reply_markup=kwargs.get("reply_markup")
            ):
                raise
            kwargs["reply_markup"] = strip_custom_emoji_button_icons(kwargs.get("reply_markup"))
            return await super().edit_message_text(
                chat_id,
                message_id,
                strip_custom_emoji_tags(text),
                *args,
                **kwargs,
            )

    async def edit_message_caption(self, chat_id, message_id, *args, **kwargs):
        kwargs["caption"] = localize_text(chat_id, kwargs.get("caption"))
        kwargs["reply_markup"] = localize_markup(chat_id, kwargs.get("reply_markup"))
        try:
            return await super().edit_message_caption(
                chat_id, message_id, *args, **kwargs
            )
        except TelegramBadRequest as exc:
            if not should_retry_without_custom_emoji(
                exc,
                caption=kwargs.get("caption"),
                reply_markup=kwargs.get("reply_markup"),
            ):
                raise
            kwargs["caption"] = strip_custom_emoji_tags(kwargs.get("caption"))
            kwargs["reply_markup"] = strip_custom_emoji_button_icons(kwargs.get("reply_markup"))
            return await super().edit_message_caption(
                chat_id, message_id, *args, **kwargs
            )

    async def edit_message_reply_markup(self, chat_id, message_id, *args, **kwargs):
        kwargs["reply_markup"] = localize_markup(
            chat_id, kwargs.get("reply_markup")
        )
        try:
            return await super().edit_message_reply_markup(
                chat_id, message_id, *args, **kwargs
            )
        except TelegramBadRequest:
            if not has_custom_emoji_button_icons(kwargs.get("reply_markup")):
                raise
            kwargs["reply_markup"] = strip_custom_emoji_button_icons(
                kwargs.get("reply_markup")
            )
            return await super().edit_message_reply_markup(
                chat_id, message_id, *args, **kwargs
            )

    async def copy_message(self, chat_id, from_chat_id, message_id, *args, **kwargs):
        kwargs["reply_markup"] = localize_markup(chat_id, kwargs.get("reply_markup"))
        try:
            return await super().copy_message(
                chat_id, from_chat_id, message_id, *args, **kwargs
            )
        except TelegramBadRequest:
            if not has_custom_emoji_button_icons(kwargs.get("reply_markup")):
                raise
            kwargs["reply_markup"] = strip_custom_emoji_button_icons(kwargs.get("reply_markup"))
            return await super().copy_message(
                chat_id, from_chat_id, message_id, *args, **kwargs
            )

    async def answer_callback_query(
        self, callback_query_id, *args, **kwargs
    ):
        active_chat_id = _active_chat_id.get()
        if active_chat_id is not None and kwargs.get("text"):
            kwargs["text"] = localize_text(active_chat_id, kwargs["text"])
        return await super().answer_callback_query(
            callback_query_id, *args, **kwargs
        )


bot = LocalizedBot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
BOT_USERNAME = ""
_processed_start_messages: set[tuple[int, int]] = set()
_last_start_at: dict[int, float] = {}
START_DEDUP_SECONDS = 2.0


async def referral_link(user_id):
    global BOT_USERNAME
    if not BOT_USERNAME:
        try:
            me = await bot.get_me()
            BOT_USERNAME = me.username or ""
        except Exception as exc:
            print(f"Unable to resolve bot username for referral link: {exc}", flush=True)
    return (
        f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
        if BOT_USERNAME else ""
    )


@dp.errors()
async def ignore_forbidden_delivery(event: ErrorEvent):
    if isinstance(event.exception, TelegramForbiddenError):
        return True


async def notify_admins(
    text,
    reply_markup=None,
    photo=None,
    document=None,
    recipient_ids=None,
):
    delivered = 0
    for aid in staff_ids() if recipient_ids is None else recipient_ids:
        try:
            if photo:
                await bot.send_photo(aid, photo=photo, caption=text, reply_markup=reply_markup)
            elif document:
                await bot.send_document(aid, document=document, caption=text, reply_markup=reply_markup)
            else:
                await bot.send_message(aid, text, reply_markup=reply_markup)
            delivered += 1
            print(f"Admin notification delivered for {aid}.", flush=True)
        except Exception as exc:
            # A file_id can occasionally fail while a plain message still
            # works. Never lose the payment request silently.
            print(f"Admin notification failed for {aid}: {exc}")
            if photo or document:
                try:
                    await bot.send_message(aid, text, reply_markup=reply_markup)
                    delivered += 1
                except Exception as fallback_exc:
                    print(f"Admin text notification failed for {aid}: {fallback_exc}")
    if not staff_ids():
        print("Admin notification skipped: ADMIN_IDS is empty.")
    return delivered


async def configure_bot_commands():
    try:
        await bot.set_my_commands(
            [BotCommand(command="start", description="Відкрити головне меню")],
            scope=BotCommandScopeAllPrivateChats(),
        )
    except Exception as exc:
        print(f"Unable to configure private commands: {exc}", flush=True)
    for aid in staff_ids():
        try:
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Відкрити головне меню"),
                    BotCommand(command="admin", description="Відкрити адмін-панель"),
                ],
                scope=BotCommandScopeChat(chat_id=aid),
            )
        except Exception as exc:
            print(f"Unable to configure commands for {aid}: {exc}", flush=True)


async def show_welcome_screen(user_id, language):
    await replace_screen(
        user_id,
        welcome_text(language),
        image="welcome",
        reply_markup=main_menu(user_id),
    )


async def show_language_screen(user_id):
    await replace_screen(
        user_id,
        language_selection_text(),
        image="language",
        reply_markup=language_selection_kb(),
    )


async def show_required_subscription_screen(user_id):
    await replace_screen(
        user_id,
        required_subscription_text(),
        image="welcome",
        reply_markup=required_subscription_kb(),
    )


async def pending_review_gate(handler, event, data):
    user = getattr(event, "from_user", None)
    if not user:
        return await handler(event, data)
    staff_user = is_staff(user.id)
    ensure_required_review_rows(user.id)
    review = db.execute(
        """SELECT order_id,status FROM customer_reviews
           WHERE user_id=? AND status IN ('waiting_rating','waiting_comment')
           ORDER BY created_at,order_id LIMIT 1""",
        (user.id,),
    ).fetchone()
    if not review:
        return await handler(event, data)

    if isinstance(event, CallbackQuery):
        callback_data = event.data or ""
        parts = callback_data.split(":")
        if (
            len(parts) == 4
            and parts[:2] == ["review", "rate"]
            and review["status"] == "waiting_rating"
            and parts[2].isdigit()
            and int(parts[2]) == int(review["order_id"])
        ):
            return await handler(event, data)
        if staff_user and callback_data.startswith((
            "adm:",
            "admin:",
            "admin_",
            "moderator:",
            "required_admin:",
            "payment_method:",
            "premiumadm:",
            "balance_action:",
            "adm_bank:",
            "bank:",
            "refwd:",
            "support_claim:",
            "support_reply:",
            "review:approve:",
            "review:reject:",
            "review:retry:",
        )):
            # Keep staff controls usable, but recover the owner's pending
            # review prompt whenever it was missed on the completion callback.
            await send_pending_review_prompt(user.id)
            return await handler(event, data)
        await event.answer(
            "Спочатку залиште обов’язковий відгук.",
            show_alert=True,
        )
        await send_pending_review_prompt(user.id)
        return None

    if isinstance(event, Message):
        text = (event.text or "").strip()
        if staff_user and text.startswith("/admin"):
            await send_pending_review_prompt(user.id)
            return await handler(event, data)
        if (
            menu_key_from_text(text, user_language(user.id))
            or text in ALL_CANCEL_LABELS
            or text.startswith("/")
        ):
            await send_pending_review_prompt(user.id)
            return None
        # Route the user's content directly to the review collector so an
        # earlier menu/FSM handler cannot consume it while the account is locked.
        await collect_review_message(event)
        return None

    return await handler(event, data)


class RequiredSubscriptionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if not user or is_staff(user.id):
            return await handler(event, data)

        if required_channels():
            if isinstance(event, CallbackQuery):
                callback_data = event.data or ""
                allowed = callback_data.startswith(("language:", "required:"))
            else:
                text = getattr(event, "text", "") or ""
                allowed = text.startswith("/start")

            if not allowed and not await user_has_required_subscriptions(user.id):
                await show_required_subscription_screen(user.id)
                if isinstance(event, CallbackQuery):
                    await event.answer(
                        "Спочатку підпишіться на всі канали.",
                        show_alert=True,
                    )
                return None

        return await pending_review_gate(handler, event, data)


class LocaleContextMiddleware(BaseMiddleware):
    """Make callback alerts localizable as well as regular bot messages."""

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        token = _active_chat_id.set(user.id if user else None)
        try:
            return await handler(event, data)
        finally:
            _active_chat_id.reset(token)


dp.message.outer_middleware(LocaleContextMiddleware())
dp.callback_query.outer_middleware(LocaleContextMiddleware())
dp.message.outer_middleware(RequiredSubscriptionMiddleware())
dp.callback_query.outer_middleware(RequiredSubscriptionMiddleware())


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    now = asyncio.get_running_loop().time()
    start_key = (message.chat.id, message.message_id)
    last_start = _last_start_at.get(message.chat.id)
    if start_key in _processed_start_messages or (
        last_start is not None and now - last_start < START_DEDUP_SECONDS
    ):
        print(
            f"Ignored duplicate /start from chat {message.chat.id}.",
            flush=True,
        )
        return
    _processed_start_messages.add(start_key)
    _last_start_at[message.chat.id] = now
    if len(_processed_start_messages) > 5000:
        _processed_start_messages.clear()
    ensure_user(message.from_user, referrer_id_from_start(message.text))
    await state.clear()
    selected_language = user_language(message.from_user.id)
    stored_language = db.execute(
        "SELECT language FROM users WHERE id=?",
        (message.from_user.id,),
    ).fetchone()["language"]
    if stored_language not in LANGUAGE_NAMES:
        await show_language_screen(message.from_user.id)
        return
    if required_channels() and not await user_has_required_subscriptions(message.from_user.id):
        await show_required_subscription_screen(message.from_user.id)
        return
    await show_welcome_screen(message.from_user.id, selected_language)


@dp.callback_query(F.data.startswith("language:"))
async def choose_language(call: CallbackQuery, state: FSMContext):
    language = call.data.split(":", 1)[1]
    if language not in LANGUAGE_NAMES:
        await call.answer("Невідома мова.", show_alert=True)
        return
    ensure_user(call.from_user)
    current_language = db.execute(
        "SELECT language FROM users WHERE id=?",
        (call.from_user.id,),
    ).fetchone()["language"]
    # The language selector is also available from the inline main menu,
    # so the user can change language at any time.
    db.execute("UPDATE users SET language=? WHERE id=?", (language, call.from_user.id))
    db.commit()
    await state.clear()
    await call.answer(f"Мова: {LANGUAGE_NAMES[language]}")
    if required_channels() and not await user_has_required_subscriptions(call.from_user.id):
        await show_required_subscription_screen(call.from_user.id)
    else:
        await show_welcome_screen(call.from_user.id, language)


@dp.callback_query(F.data == "required:check")
async def check_required_subscriptions(call: CallbackQuery):
    if await user_has_required_subscriptions(call.from_user.id):
        await call.answer("✅ Підписку підтверджено!")
        await show_welcome_screen(call.from_user.id, user_language(call.from_user.id))
    else:
        await call.answer(
            "❌ Підпишіться на всі канали та спробуйте ще раз.",
            show_alert=True,
        )


@dp.message(Command("id"))
async def show_user_id(message: Message):
    await message.answer(
        f"🆔 Ваш Telegram ID: <code>{message.from_user.id}</code>\n\n"
        "Цей ID можна додати до змінної ADMIN_IDS."
    )


@dp.message(Command("admin"))
async def admin(message: Message):
    if not is_staff(message.from_user.id):
        return
    markup = admin_menu_kb() if is_admin(message.from_user.id) else moderator_menu_kb()
    await message.answer("⚙️ <b>Адмін-панель</b>", reply_markup=markup)


@dp.message(F.text.in_(ALL_CANCEL_LABELS))
async def cancel_state(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id in ADMIN_IDS:
        delete_setting(f"pending_bank:{message.from_user.id}")
    await replace_screen(
        message.from_user.id,
        "✅ Дію скасовано.\n\nОберіть потрібний розділ нижче 👇",
        image="welcome",
        reply_markup=main_menu(message.from_user.id),
    )


@dp.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    await replace_screen(
        call.from_user.id,
        "🏠 <b>Головне меню</b>\n\nОберіть потрібний розділ нижче 👇",
        image="welcome",
        reply_markup=main_menu(call.from_user.id),
    )


@dp.message(F.text.in_(language_labels("buy_stars")))
async def buy_stars(message: Message):
    await replace_screen(
        message.from_user.id,
        "⭐ <b>Кому бажаєте придбати Stars?</b>",
        image="stars",
        reply_markup=stars_recipient_kb(message.from_user.id),
        screen_key="stars_recipient",
    )


@dp.callback_query(F.data.startswith("stars:recipient:"))
async def stars_choose_recipient(call: CallbackQuery, state: FSMContext):
    choice = call.data.rsplit(":", 1)[1]
    if choice not in {"self", "friend"}:
        await call.answer("Невідомий отримувач.", show_alert=True)
        return
    await state.clear()
    if choice == "friend":
        await state.update_data(stars_target_type="friend")
        await state.set_state(Form.stars_friend_target)
        await call.answer()
        language = user_language(call.from_user.id)
        prompts = {
            "uk": "🎁 Надішліть @username або Telegram ID друга, якому призначені Stars. Не надсилайте паролі чи коди входу.",
            "ru": "🎁 Отправьте @username или Telegram ID друга, которому предназначены Stars. Не отправляйте пароли или коды входа.",
            "en": "🎁 Send your friend's @username or Telegram ID. Never send passwords or login codes.",
        }
        await replace_screen(call.from_user.id, prompts[language], image="stars", reply_markup=cancel_kb(language))
        return
    await state.update_data(stars_target_type="self", stars_target="")
    await call.answer()
    await replace_screen(
        call.from_user.id,
        stars_catalog_text(recipient_note=stars_recipient_note(call.from_user.id, {"stars_target_type": "self"})),
        image="stars", reply_markup=stars_kb(), screen_key="stars_catalog",
    )


@dp.message(Form.stars_friend_target)
async def stars_friend_target(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if raw.isdigit():
        if not 5 <= len(raw) <= 15:
            await message.answer("❌ Перевірте Telegram ID і надішліть його ще раз.", reply_markup=cancel_kb(user_language(message.from_user.id)))
            return
        target = raw
    else:
        username = raw[1:] if raw.startswith("@") else raw
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
            await message.answer("❌ Надішліть коректний @username або Telegram ID.", reply_markup=cancel_kb(user_language(message.from_user.id)))
            return
        target = "@" + username
    await state.update_data(stars_target_type="friend", stars_target=target)
    data = await state.get_data()
    await state.set_state(None)
    await replace_screen(
        message.from_user.id,
        stars_catalog_text(recipient_note=stars_recipient_note(message.from_user.id, data)),
        image="stars", reply_markup=stars_kb(), screen_key="stars_catalog",
    )


@dp.callback_query(F.data.startswith("stars:"))
async def stars_select(call: CallbackQuery, state: FSMContext):
    value = call.data.split(":", 1)[1]
    if value in {"recipient", "custom"}:
        if value == "custom":
            data = await state.get_data()
            if data.get("stars_target_type") not in {"self", "friend"}:
                await call.answer("Спочатку оберіть отримувача.", show_alert=True)
                return
            await state.set_state(Form.custom_stars)
            await call.answer()
            await replace_screen(
                call.from_user.id,
                "⭐ Вкажіть кількість Stars від <b>50</b>.",
                image="stars", reply_markup=cancel_kb(user_language(call.from_user.id)),
            )
        else:
            await call.answer("Оберіть отримувача.", show_alert=True)
        return
    try:
        stars = int(value)
    except (ValueError, TypeError):
        await call.answer("Некоректна кількість Stars.", show_alert=True)
        return
    if stars not in {50, 100, 500, 1000}:
        await call.answer("Цей пакет більше недоступний.", show_alert=True)
        return
    data = await state.get_data()
    if data.get("stars_target_type") not in {"self", "friend"}:
        await call.answer("Спочатку оберіть отримувача.", show_alert=True)
        return
    amount = round(stars * float(setting("stars_rate")), 2)
    oid = create_stars_order(call.from_user.id, stars, amount, data)
    await state.clear()
    await call.answer()
    recipient_line = stars_recipient_note(call.from_user.id, data)
    await replace_screen(
        call.from_user.id,
        f"💳 <b>До оплати:</b> {amount:.2f} грн\n"
        f"⭐ <b>Кількість:</b> {stars} Stars\n"
        f"{recipient_line}\n\n💳 Оберіть спосіб оплати:",
        image="stars", reply_markup=payment_kb(oid),
    )


@dp.message(Form.custom_stars)
async def custom_stars(message: Message, state: FSMContext):
    try:
        stars = int((message.text or "").strip())
        if stars < 50 or stars > 1_000_000:
            raise ValueError
    except (ValueError, TypeError):
        await replace_screen(
            message.from_user.id,
            "❌ Введіть цілу кількість Stars від 50 до 1 000 000.",
            image="stars", reply_markup=cancel_kb(user_language(message.from_user.id)),
        )
        return
    data = await state.get_data()
    if data.get("stars_target_type") not in {"self", "friend"}:
        await state.clear()
        await buy_stars(SimpleNamespace(from_user=message.from_user))
        return
    amount = round(stars * float(setting("stars_rate")), 2)
    oid = create_stars_order(message.from_user.id, stars, amount, data)
    await state.clear()
    await replace_screen(
        message.from_user.id,
        f"💳 <b>До оплати:</b> {amount:.2f} грн\n"
        f"⭐ <b>Кількість:</b> {stars} Stars\n"
        f"{stars_recipient_note(message.from_user.id, data)}\n\n"
        "💳 Оберіть спосіб оплати:",
        image="stars", reply_markup=payment_kb(oid),
    )


@dp.callback_query(F.data.startswith("pay:"))
async def choose_payment(call: CallbackQuery):
    await call.answer()
    _, oid_s, method = call.data.split(":")
    oid = int(oid_s)
    order = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not order or order["user_id"] != call.from_user.id:
        await replace_screen(
            call.from_user.id,
            "❌ Замовлення не знайдено.",
            image="stars",
            reply_markup=main_menu(call.from_user.id),
        )
        return
    if order["status"] != "waiting_payment":
        await call.message.answer("Це замовлення вже не очікує на оплату.")
        return
    if method == "ton":
        if not ton_payment_configured():
            await call.message.answer("❌ Оплата через Tonkeeper ще не налаштована адміністратором.")
            return
        try:
            existing_invoice = db.execute(
                """SELECT invoice_id,payment_url,amount
                   FROM payment_invoices
                   WHERE order_id=? AND provider='tonkeeper' AND currency='TON'
                     AND status='pending' AND payment_url IS NOT NULL
                   ORDER BY id DESC LIMIT 1""",
                (oid,),
            ).fetchone()
            if existing_invoice:
                ton_amount = payment_decimal(existing_invoice["amount"])
                pay_url = existing_invoice["payment_url"]
            else:
                ton_amount = order_crypto_amount(order["amount"], "TON")
                comment = f"spanix_order_{oid}_{secrets.token_hex(4)}"
                nanotons = int(ton_amount * Decimal("1000000000"))
                pay_url = create_ton_payment_link(nanotons, comment)
                if not save_payment_invoice(
                    oid, "tonkeeper", comment, pay_url, "TON", ton_amount
                ):
                    await call.message.answer("Це замовлення вже не очікує на оплату.")
                    return
        except (ValueError, RuntimeError) as exc:
            await call.message.answer(f"❌ Не вдалося створити платіж TON: {esc(str(exc))}")
            return
        b = InlineKeyboardBuilder()
        b.row(kb_button(
            "Перейти в Tonkeeper",
            emoji_id=MAIN_EMOJI["buy_gram"],
            style="success",
            url=pay_url,
        ))
        b.row(kb_button(
            "Перевірити оплату",
            emoji_id=EMOJI_POOL[27],
            style="primary",
            callback_data=f"ton_check:{oid}",
        ))
        b.row(kb_button(
            "Скасувати замовлення",
            emoji_id=EMOJI_POOL[3],
            style="danger",
            callback_data=f"cancel_order:{oid}",
        ))
        await replace_screen(
            call.from_user.id,
            f"🪙 <b>Оплата через Tonkeeper</b>\n\n"
            f"До оплати: <b>{format_payment_amount(ton_amount)} TON</b>\n"
            f"Замовлення: <b>#{oid}</b>\n\n"
            "Перейдіть у Tonkeeper, виконайте оплату та натисніть «Перевірити оплату».",
            image="stars",
            reply_markup=b.as_markup(),
        )
        return
    if method == "crypto":
        if not crypto_payment_configured():
            await call.message.answer("❌ CryptoBot ще не налаштований адміністратором.")
            return
        await replace_screen(
            call.from_user.id,
            f"🤖 <b>Оплата через CryptoBot</b>\n\n"
            f"Замовлення: <b>#{oid}</b>\n"
            "Оберіть валюту рахунку:",
            image="stars",
            reply_markup=crypto_currency_kb(oid),
        )
        return
    if method != "ua":
        return
    await replace_screen(
        call.from_user.id,
        "🏦 <b>Оберіть банк для оплати</b>",
        image="stars",
        reply_markup=bank_kb(oid),
    )


@dp.callback_query(F.data.startswith("crypto_currency:"))
async def choose_crypto_currency(call: CallbackQuery):
    await call.answer()
    _, oid_s, currency = call.data.split(":")
    oid = int(oid_s)
    order = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not order or order["user_id"] != call.from_user.id:
        return
    if order["status"] != "waiting_payment":
        await call.message.answer("Це замовлення вже не очікує на оплату.")
        return
    if currency not in {"TON", "USDT"}:
        return
    try:
        existing_invoice = db.execute(
            """SELECT invoice_id,payment_url,amount
               FROM payment_invoices
               WHERE order_id=? AND provider='cryptobot' AND currency=?
                 AND status='pending' AND payment_url IS NOT NULL
               ORDER BY id DESC LIMIT 1""",
            (oid, currency),
        ).fetchone()
        if existing_invoice:
            invoice_id = str(existing_invoice["invoice_id"])
            pay_url = existing_invoice["payment_url"]
            crypto_amount = payment_decimal(existing_invoice["amount"])
        else:
            invoice, crypto_amount = await create_crypto_invoice(order, currency)
            invoice_id = str(invoice.get("invoice_id") or invoice.get("id") or "")
            pay_url = invoice.get("bot_invoice_url") or invoice.get("mini_app_invoice_url")
            if not invoice_id or not pay_url:
                raise RuntimeError("CryptoBot не повернув посилання на рахунок.")
            if not save_payment_invoice(
                oid, "cryptobot", invoice_id, pay_url, currency, crypto_amount
            ):
                await call.message.answer("Це замовлення вже не очікує на оплату.")
                return
    except (ValueError, RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
        await call.message.answer(f"❌ Не вдалося створити рахунок CryptoBot: {esc(str(exc))}")
        return

    b = InlineKeyboardBuilder()
    b.row(kb_button(
        "Оплатити рахунок",
        emoji_id=EMOJI_POOL[2],
        style="success",
        url=pay_url,
    ))
    b.row(kb_button(
        "Перевірити оплату",
        emoji_id=EMOJI_POOL[27],
        style="primary",
        callback_data=f"crypto_check:{oid}",
    ))
    b.row(kb_button(
        "Скасувати замовлення",
        emoji_id=EMOJI_POOL[3],
        style="danger",
        callback_data=f"cancel_order:{oid}",
    ))
    await replace_screen(
        call.from_user.id,
        f"🤖 <b>Рахунок CryptoBot</b>\n\n"
        f"До оплати: <b>{format_payment_amount(crypto_amount)} {currency}</b>\n"
        f"Замовлення: <b>#{oid}</b>\n\n"
        "Відкрийте рахунок, оплатіть його та натисніть «Перевірити оплату».",
        image="stars",
        reply_markup=b.as_markup(),
    )


@dp.callback_query(F.data.startswith("pay_method:"))
async def choose_custom_payment_method(call: CallbackQuery):
    await call.answer()
    _, oid_s, method_s = call.data.split(":")
    oid = int(oid_s)
    method_id = int(method_s)
    order = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    method = db.execute(
        "SELECT id,name,method_type FROM payment_methods WHERE id=? AND active=1",
        (method_id,),
    ).fetchone()
    if not order or order["user_id"] != call.from_user.id or not method:
        await call.answer("Замовлення або спосіб оплати не знайдено.", show_alert=True)
        return
    if order["status"] != "waiting_payment":
        await call.message.answer("Це замовлення вже не очікує на оплату.")
        return
    details = payment_method_details(method_id)
    if not details:
        await call.message.answer(
            "❌ Реквізити цього способу оплати тимчасово недоступні."
        )
        return
    db.execute(
        "UPDATE orders SET payment_method=? WHERE id=?",
        (f"manual:{method_id}", oid),
    )
    db.commit()
    await replace_screen(
        call.from_user.id,
        f"💳 <b>{esc(method['name'])}</b>\n\n"
        f"Замовлення: <b>#{oid}</b>\n"
        f"Сума до оплати: <b>{order['amount']:.2f} грн</b>\n\n"
        f"📌 <b>Реквізити:</b>\n<code>{esc(details)}</code>\n\n"
        "Після оплати натисніть кнопку нижче та надішліть квитанцію.",
        image="stars",
        reply_markup=receipt_kb(oid),
    )


@dp.callback_query(F.data.startswith("ton_check:"))
async def check_ton_payment(call: CallbackQuery):
    await call.answer()
    oid = int(call.data.split(":")[1])
    order = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not order or order["user_id"] != call.from_user.id:
        return
    if order["status"] != "waiting_payment":
        await call.message.answer("✅ Платіж уже знайдений і замовлення передано в обробку.")
        return
    invoices = payment_invoices_for_order(order, "tonkeeper")
    if not invoices:
        await call.message.answer("❌ Спочатку створіть рахунок через Tonkeeper.")
        return
    try:
        paid_invoice = await find_paid_ton_invoice(invoices)
    except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
        await call.message.answer(f"❌ Не вдалося перевірити TON-платіж: {esc(str(exc))}")
        return
    if not paid_invoice:
        await call.message.answer("Платіж поки не знайдений.")
        return
    if not mark_order_payment_found(
        oid, paid_invoice["invoice_id"], "tonkeeper"
    ):
        await call.message.answer("Це замовлення вже обробляється.")
        return
    fresh_order = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    await notify_paid_order(fresh_order, "Tonkeeper (TON)")
    await replace_screen(
        call.from_user.id,
        f"✅ <b>Платіж знайдений</b>\n\nЗамовлення #{oid} передано адміністратору на підтвердження.",
        image="stars",
        reply_markup=main_menu(call.from_user.id),
    )


@dp.callback_query(F.data.startswith("crypto_check:"))
async def check_crypto_payment(call: CallbackQuery):
    await call.answer()
    oid = int(call.data.split(":")[1])
    order = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not order or order["user_id"] != call.from_user.id:
        return
    if order["status"] != "waiting_payment":
        await call.message.answer("✅ Платіж уже знайдений і замовлення передано в обробку.")
        return
    invoices = payment_invoices_for_order(order, "cryptobot")
    if not invoices:
        await call.message.answer("❌ Спочатку створіть рахунок CryptoBot.")
        return
    try:
        paid_invoice = None
        for start in range(0, len(invoices), 100):
            batch = invoices[start:start + 100]
            result = await crypto_api_call(
                "getInvoices",
                {
                    "invoice_ids": ",".join(
                        str(invoice["invoice_id"]) for invoice in batch
                    )
                },
            )
            returned = result if isinstance(result, list) else [result]
            returned_by_id = {
                str(item.get("invoice_id")): item
                for item in returned
                if isinstance(item, dict)
            }
            for saved_invoice in batch:
                invoice = returned_by_id.get(str(saved_invoice["invoice_id"]))
                if not invoice:
                    continue
                invoice_status = str(invoice.get("status", "")).lower()
                if invoice_status in {"expired", "cancelled"}:
                    db.execute(
                        """UPDATE payment_invoices SET status='expired'
                           WHERE order_id=? AND provider='cryptobot'
                             AND invoice_id=? AND status='pending'""",
                        (oid, str(saved_invoice["invoice_id"])),
                    )
                    db.commit()
                if crypto_invoice_paid(invoice):
                    paid_invoice = saved_invoice
                    break
            if paid_invoice:
                break
    except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
        await call.message.answer(f"❌ Не вдалося перевірити рахунок CryptoBot: {esc(str(exc))}")
        return
    if not paid_invoice:
        await call.message.answer("Рахунок ще не оплачений.")
        return
    if not mark_order_payment_found(
        oid, paid_invoice["invoice_id"], "cryptobot"
    ):
        await call.message.answer("Це замовлення вже обробляється.")
        return
    fresh_order = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    await notify_paid_order(
        fresh_order,
        f"CryptoBot ({paid_invoice['currency'] or order['payment_currency'] or '—'})",
    )
    await replace_screen(
        call.from_user.id,
        f"✅ <b>Платіж знайдений</b>\n\nЗамовлення #{oid} передано адміністратору на підтвердження.",
        image="stars",
        reply_markup=main_menu(call.from_user.id),
    )


@dp.callback_query(F.data.startswith("bank:"))
async def choose_bank(call: CallbackQuery):
    await call.answer()
    _, oid_s, method = call.data.split(":")
    oid = int(oid_s)
    order = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not order or order["user_id"] != call.from_user.id:
        return
    if order["status"] != "waiting_payment":
        await call.message.answer("Це замовлення вже не очікує на оплату.")
        return
    db.execute("UPDATE orders SET payment_method=? WHERE id=?", (method, oid))
    db.commit()
    details = setting(f"bank_{method}") or BANK_DETAILS.get(method, "Реквізити не налаштовані.")
    await replace_screen(
        call.from_user.id,
        f"💳 <b>Замовлення #{oid}</b>\n"
        f"💰 Сума до оплати: <b>{order['amount']:.2f} грн</b>\n\n"
        f"📌 <b>Реквізити:</b>\n<code>{esc(details)}</code>\n\n"
        "Після оплати надішліть квитанцію.",
        image="stars",
        reply_markup=receipt_kb(oid),
    )


@dp.callback_query(F.data.startswith("backpay:"))
async def back_payment(call: CallbackQuery):
    await call.answer()
    oid = int(call.data.split(":")[1])
    await replace_screen(
        call.from_user.id,
        "💳 <b>Оберіть спосіб оплати</b>",
        image="stars",
        reply_markup=payment_kb(oid),
    )


@dp.callback_query(F.data.startswith("receipt:"))
async def receipt_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    oid = int(call.data.split(":")[1])
    order = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not order or order["user_id"] != call.from_user.id:
        return
    if order["status"] != "waiting_payment":
        await call.message.answer("Це замовлення вже не очікує на квитанцію.")
        return
    await state.update_data(receipt_order=oid)
    await state.set_state(Form.receipt)
    await replace_screen(
        call.from_user.id,
        "🧾 Надішліть фото або документ квитанції.",
        image="stars",
        reply_markup=cancel_kb(),
    )


async def save_receipt(
    message: Message,
    state: FSMContext,
    file_id: str,
    file_type: str,
    file_name: str = "",
):
    data = await state.get_data()
    oid = data.get("receipt_order")
    if not oid:
        await state.clear()
        return
    order = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not order or order["user_id"] != message.from_user.id:
        await state.clear()
        return

    # Квитанції навмисно не розпізнаються автоматично: перевірку виконує
    # адміністратор або модератор у Telegram.
    receipt_data = {"verification": "manual"}
    ocr_text = ""
    receipt_error = ""
    parsed_at = now()
    db.execute("BEGIN IMMEDIATE")
    try:
        cursor = db.execute(
            """UPDATE orders
               SET receipt_file_id=?,receipt_type=?,receipt_ocr_text=?,
                   receipt_data_json=?,receipt_parsed_at=?,receipt_error=?,
                   status='waiting_review'
               WHERE id=? AND user_id=? AND status='waiting_payment'""",
            (
                file_id,
                file_type,
                ocr_text,
                json.dumps(receipt_data, ensure_ascii=False),
                parsed_at,
                receipt_error,
                oid,
                message.from_user.id,
            ),
        )
        if cursor.rowcount != 1:
            db.rollback()
            await state.clear()
            await message.answer("Це замовлення вже оброблено; квитанцію не прийнято.")
            return
        db.execute(
            """INSERT INTO receipt_archive(
                   order_id,user_id,receipt_file_id,receipt_type,receipt_file_name,
                   receipt_ocr_text,receipt_data_json,receipt_parsed_at,
                   receipt_error,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                oid,
                order["user_id"],
                file_id,
                file_type,
                file_name,
                ocr_text,
                json.dumps(receipt_data, ensure_ascii=False),
                parsed_at,
                receipt_error,
                parsed_at,
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    await state.clear()
    await replace_screen(
        message.from_user.id,
        f"{pe(EMOJI_POOL[5], '✅')} Квитанцію отримано.\n"
        f"📦 Замовлення #{oid} передано на перевірку.",
        image="stars",
        reply_markup=main_menu(message.from_user.id),
    )
    u = user_row(message.from_user.id)
    wallet_line = (
        f"🪙 TON-адреса для переказу: <code>{esc(order['ton_wallet'])}</code>\n"
        if order["order_type"] == "buy_ton" and order["ton_wallet"]
        else ""
    )
    text = (
        f"🔔 <b>Нове замовлення #{oid}</b>\n\n"
        f"👤 @{esc(u['username'] or 'без_username')}\n"
        f"🆔 <code>{u['id']}</code>\n"
        f"📦 {esc(order_type_title(order))}: <b>{esc(order_quantity_title(order))}</b>\n"
        f"{wallet_line}"
        f"💰 <b>{order['amount']:.2f} грн</b>\n"
        f"💳 {esc(order['payment_method'] or '—')}\n"
        f"🕒 {order['created_at']}\n\n"
        f"{receipt_summary(receipt_data, receipt_error)}"
    )
    sender_is_admin = is_staff(message.from_user.id)
    other_admin_ids = staff_ids() - {message.from_user.id} if sender_is_admin else None
    if file_type == "photo":
        delivered = await notify_admins(
            text,
            admin_order_kb(oid),
            photo=file_id,
            recipient_ids=other_admin_ids,
        )
    else:
        delivered = await notify_admins(
            text,
            admin_order_kb(oid),
            document=file_id,
            recipient_ids=other_admin_ids,
        )
    if sender_is_admin:
        try:
            if file_type == "photo":
                await bot.send_photo(
                    message.from_user.id,
                    photo=file_id,
                    caption=text,
                    reply_markup=admin_order_kb(oid),
                )
            else:
                await bot.send_document(
                    message.from_user.id,
                    document=file_id,
                    caption=text,
                    reply_markup=admin_order_kb(oid),
                )
            delivered += 1
            print(
                f"Admin notification delivered directly to sender {message.from_user.id}.",
                flush=True,
            )
        except Exception as exc:
            print(
                f"Direct admin notification failed for {message.from_user.id}: {exc}",
                flush=True,
            )
    if not delivered:
        await message.answer(
            "⚠️ Квитанцію збережено, але повідомлення адміністратору не доставлено.\n"
            "Перевірте, що ваш Telegram ID доданий у ADMIN_IDS, а адмін відкрив боту "
            "та натиснув /start.",
            reply_markup=main_menu(message.from_user.id),
        )


@dp.message(Form.receipt, F.photo)
async def receipt_photo(message: Message, state: FSMContext):
    await save_receipt(message, state, message.photo[-1].file_id, "photo")


@dp.message(Form.receipt, F.document)
async def receipt_document(message: Message, state: FSMContext):
    await save_receipt(
        message,
        state,
        message.document.file_id,
        "document",
        message.document.file_name or "",
    )


@dp.message(Form.receipt)
async def receipt_invalid(message: Message):
    await replace_screen(
        message.from_user.id,
        "❌ Надішліть саме фото або документ квитанції.",
        image="stars",
        reply_markup=cancel_kb(),
    )


@dp.callback_query(F.data.startswith("cancel_order:"))
async def cancel_order(call: CallbackQuery):
    await call.answer()
    oid = int(call.data.split(":")[1])
    order = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not order or order["user_id"] != call.from_user.id:
        return
    if order["status"] != "waiting_payment":
        await call.message.answer("Це замовлення вже оброблено або скасоване.")
        return
    active_invoice = db.execute(
        """SELECT 1 FROM payment_invoices
           WHERE order_id=? AND status='pending' LIMIT 1""",
        (oid,),
    ).fetchone()
    if active_invoice:
        await call.message.answer(
            "Для цього замовлення створено активний платіжний рахунок. "
            "Щоб не втратити можливу оплату, спочатку перевірте рахунок."
        )
        return
    db.execute("BEGIN IMMEDIATE")
    try:
        cursor = db.execute(
            """UPDATE orders SET status='cancelled'
               WHERE id=? AND user_id=? AND status='waiting_payment'""",
            (oid, call.from_user.id),
        )
        if cursor.rowcount != 1:
            db.rollback()
            await call.message.answer("Це замовлення вже оброблено або скасоване.")
            return
        db.commit()
    except Exception:
        db.rollback()
        raise
    await replace_screen(
        call.from_user.id,
        "❌ Дію скасовано.\n\nОберіть потрібний розділ нижче 👇",
        image="welcome",
        reply_markup=main_menu(call.from_user.id),
    )


@dp.message(F.text.in_(language_labels("buy_ton")))
async def buy_ton(message: Message, state: FSMContext):
    await state.set_state(Form.ton_amount)
    await replace_screen(
        message.from_user.id,
        f"{pe(MAIN_EMOJI['buy_gram'], '💎')} <b>Курс TON: {setting('ton_rate')} грн</b>\n\n"
        "💎 Вкажіть кількість TON для покупки.\n"
        "📌 Мінімальна кількість: <b>0.25 TON</b>",
        image="ton",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.ton_amount)
async def ton_amount(message: Message, state: FSMContext):
    try:
        ton = float(message.text.replace(",", "."))
        if not math.isfinite(ton) or ton < 0.25:
            raise ValueError
    except (ValueError, AttributeError):
        await replace_screen(
            message.from_user.id,
            "❌ Введіть коректну кількість TON від 0.25.",
            image="ton",
            reply_markup=cancel_kb(),
        )
        return
    amount = round(ton * float(setting("ton_rate")), 2)
    await state.update_data(buy_ton_amount=ton, buy_ton_total=amount)
    await state.set_state(Form.ton_wallet)
    await replace_screen(
        message.from_user.id,
        "🪙 <b>Надішліть TON-адресу для отримання TON</b>\n\n"
        "Підтримуються стандартні адреси <code>EQ…</code>, <code>UQ…</code>, <code>kQ…</code> та raw-формат <code>0:…</code>.\n"
        "Не надсилайте seed-фразу чи приватний ключ.",
        image="ton",
        reply_markup=cancel_kb(user_language(message.from_user.id)),
    )


@dp.message(Form.ton_wallet)
async def ton_wallet(message: Message, state: FSMContext):
    wallet = (message.text or "").strip()
    if not is_valid_ton_wallet(wallet):
        await replace_screen(
            message.from_user.id,
            "❌ Це не схоже на TON-адресу. Надішліть адресу у форматі EQ…, UQ… або 0:….\n"
            "Не надсилайте seed-фразу чи приватний ключ.",
            image="ton",
            reply_markup=cancel_kb(user_language(message.from_user.id)),
        )
        return
    data = await state.get_data()
    ton = float(data.get("buy_ton_amount") or 0)
    amount = float(data.get("buy_ton_total") or 0)
    if not math.isfinite(ton) or ton < 0.25 or not math.isfinite(amount) or amount <= 0:
        await state.clear()
        await replace_screen(
            message.from_user.id,
            "❌ Не вдалося зберегти суму замовлення TON. Створіть замовлення ще раз.",
            image="ton",
            reply_markup=main_menu(message.from_user.id),
        )
        return
    oid = create_order(
        message.from_user.id,
        "buy_ton",
        ton,
        amount,
        ton_wallet=wallet,
    )
    await state.clear()
    await replace_screen(
        message.from_user.id,
        f"💳 До оплати: <b>{amount:.2f} грн</b>\n"
        f"💎 Кількість: <b>{ton:g} TON</b>\n\n"
        f"🪙 Адреса отримання: <code>{esc(wallet)}</code>\n\n"
        "💳 Оберіть спосіб оплати:",
        image="ton",
        reply_markup=payment_kb(oid),
    )


@dp.message(F.text.in_(language_labels("withdraw")))
async def withdraw(message: Message, state: FSMContext):
    ensure_user(message.from_user)
    u = user_row(message.from_user.id)
    minimum = int(setting("min_withdraw"))
    balance = int(u["balance_stars"])
    if balance < minimum:
        await state.clear()
        await replace_screen(
            message.from_user.id,
            f"❌ <b>Недостатньо коштів</b>\n\n"
            f"Ваш баланс: <b>{balance} Stars</b>\n"
            f"Мінімум для виводу: <b>{minimum} Stars</b>.",
            image="withdraw",
            reply_markup=main_menu(message.from_user.id),
        )
        return
    await state.set_state(Form.withdraw)
    await replace_screen(
        message.from_user.id,
        f"⭐ Ваш баланс: <b>{balance} Stars</b>\n"
        f"📌 Мінімум для виводу: <b>{minimum} Stars</b>\n\n"
        "⭐ Вкажіть кількість Stars для виводу.",
        image="withdraw",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.withdraw)
async def withdraw_amount(message: Message, state: FSMContext):
    ensure_user(message.from_user)
    u = user_row(message.from_user.id)
    minimum = int(setting("min_withdraw"))
    balance = int(u["balance_stars"])
    try:
        amount = int((message.text or "").strip())
    except (ValueError, TypeError, AttributeError):
        await replace_screen(
            message.from_user.id,
            f"❌ Введіть ціле число. Мінімум для виводу — {minimum} Stars.",
            image="withdraw",
            reply_markup=cancel_kb(),
        )
        return
    if amount < minimum:
        await replace_screen(
            message.from_user.id,
            f"❌ Мінімальна сума для виводу — {minimum} Stars.",
            image="withdraw",
            reply_markup=cancel_kb(),
        )
        return
    if amount > balance:
        await replace_screen(
            message.from_user.id,
            f"❌ <b>Недостатньо коштів</b>\nВаш баланс: <b>{balance} Stars</b>.",
            image="withdraw",
            reply_markup=cancel_kb(),
        )
        return
    try:
        db.execute("BEGIN IMMEDIATE")
        debit = db.execute(
            """UPDATE users SET balance_stars=balance_stars-?
               WHERE id=? AND balance_stars>=?""",
            (amount, message.from_user.id, amount),
        )
        if debit.rowcount != 1:
            db.rollback()
            latest = user_row(message.from_user.id)
            current_balance = int(latest["balance_stars"] or 0) if latest else 0
            await replace_screen(
                message.from_user.id,
                f"❌ <b>Недостатньо коштів</b>\n"
                f"Ваш баланс: <b>{current_balance} Stars</b>.",
                image="withdraw",
                reply_markup=cancel_kb(),
            )
            return
        cur = db.execute(
            """INSERT INTO orders(
                   user_id,order_type,quantity,amount,status,created_at,
                   withdrawal_source,withdrawal_reserved
               ) VALUES(?, 'withdraw_stars', ?, 0, 'waiting_review', ?, 'main', 1)""",
            (message.from_user.id, str(amount), now()),
        )
        oid = cur.lastrowid
        db.commit()
    except Exception:
        db.rollback()
        raise
    await state.clear()
    await replace_screen(
        message.from_user.id,
        f"{pe(EMOJI_POOL[5], '✅')} Запит на вивід <b>#{oid}</b> створено.\n"
        "⏳ Очікуйте обробки заявки адміністратором.",
        image="withdraw",
        reply_markup=main_menu(message.from_user.id),
    )
    await notify_admins(
        f"📤 <b>Запит на вивід #{oid}</b>\n"
        f"👤 @{esc(message.from_user.username or 'без_username')}\n"
        f"🆔 <code>{message.from_user.id}</code>\n"
        f"⭐ Кількість: <b>{amount}</b>",
        admin_order_kb(oid),
    )


@dp.message(F.text.in_(language_labels("sell")))
async def sell_stars(message: Message, state: FSMContext):
    await state.set_state(Form.sell_stars)
    await replace_screen(
        message.from_user.id,
        "⭐ Вкажіть кількість Stars, яку бажаєте продати.",
        image="sell",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.sell_stars)
async def sell_stars_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text)
        if amount <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await replace_screen(
            message.from_user.id,
            "❌ Вкажіть коректну кількість Stars.",
            image="sell",
            reply_markup=cancel_kb(),
        )
        return
    await state.clear()
    await replace_screen(
        message.from_user.id,
        f"{pe(EMOJI_POOL[5], '✅')} Запит на продаж <b>{amount} Stars</b> прийнято.\n"
        f"🆘 Для отримання реквізитів зверніться до підтримки: {esc(setting('support'))}",
        image="sell",
        reply_markup=main_menu(message.from_user.id),
    )


def premium_catalog_text(user_id):
    language = user_language(user_id)
    texts = {
        "uk": (
            "⭐ <b>Telegram Premium</b>\n\n"
            "Оберіть термін підписки. Ціни встановлюються адміністратором."
        ),
        "ru": (
            "⭐ <b>Telegram Premium</b>\n\n"
            "Выберите срок подписки. Цены устанавливает администратор."
        ),
        "en": (
            "⭐ <b>Telegram Premium</b>\n\n"
            "Choose a subscription term. Prices are set by the administrator."
        ),
    }
    return texts[language]


def premium_catalog_kb(user_id):
    language = user_language(user_id)
    b = InlineKeyboardBuilder()
    for months in (3, 6, 12):
        price = premium_price(months)
        if price:
            b.row(kb_button(
                f"{premium_duration_label(months, language)} — {price:g} грн",
                emoji_id=MAIN_EMOJI["premium"],
                style="primary",
                callback_data=f"premium:buy:{months}",
            ))
    if not any(premium_price(months) for months in (3, 6, 12)):
        b.row(kb_button(
            {
                "uk": "Тарифи ще не налаштовані",
                "ru": "Тарифы ещё не настроены",
                "en": "Plans are not configured yet",
            }[language],
            callback_data="premium:price_missing",
        ))
    b.row(back_inline())
    return b.as_markup()


@dp.message(F.text.in_(language_labels("premium")))
async def premium_menu(message: Message):
    await replace_screen(
        message.from_user.id,
        premium_catalog_text(message.from_user.id),
        image="premium",
        reply_markup=premium_catalog_kb(message.from_user.id),
    )


@dp.callback_query(F.data == "premium:price_missing")
async def premium_price_missing(call: CallbackQuery):
    await call.answer(
        {
            "uk": "Адміністратор ще не встановив ціни на тарифи.",
            "ru": "Администратор ещё не установил цены на тарифы.",
            "en": "The administrator has not set plan prices yet.",
        }[user_language(call.from_user.id)],
        show_alert=True,
    )


@dp.callback_query(F.data.startswith("premium:buy:"))
async def premium_buy(call: CallbackQuery):
    try:
        months = int(call.data.rsplit(":", 1)[1])
    except (AttributeError, TypeError, ValueError):
        await call.answer("Некоректний тариф.", show_alert=True)
        return
    if months not in {3, 6, 12}:
        await call.answer("Некоректний тариф.", show_alert=True)
        return
    price = premium_price(months)
    if not price:
        await call.answer("Цей тариф ще не має встановленої ціни.", show_alert=True)
        return
    order_id = create_order(
        call.from_user.id,
        "telegram_premium",
        months,
        price,
    )
    await call.answer()
    language = user_language(call.from_user.id)
    text = {
        "uk": (
            f"⭐ <b>Telegram Premium на {premium_duration_label(months, language)}</b>\n"
            f"💰 До сплати: <b>{price:g} грн</b>\n\n"
            "💳 Оберіть спосіб оплати:"
        ),
        "ru": (
            f"⭐ <b>Telegram Premium на {premium_duration_label(months, language)}</b>\n"
            f"💰 К оплате: <b>{price:g} грн</b>\n\n"
            "💳 Выберите способ оплаты:"
        ),
        "en": (
            f"⭐ <b>Telegram Premium for {premium_duration_label(months, language)}</b>\n"
            f"💰 Total: <b>{price:g} UAH</b>\n\n"
            "💳 Choose a payment method:"
        ),
    }[language]
    await replace_screen(
        call.from_user.id,
        text,
        image="premium",
        reply_markup=payment_kb(order_id),
    )


def referral_screen_text(user_id, referral_url, page=0):
    invited, buyers, total_earned, available, _ = referral_report(user_id)
    page_size = 10
    page_count = max(1, math.ceil(invited / page_size))
    page = max(0, min(int(page), page_count - 1))
    people = referral_people_lines(user_id, page_size, page * page_size)
    language = user_language(user_id)
    referral_link_unavailable = {
        "uk": "Посилання тимчасово недоступне",
        "ru": "Ссылка временно недоступна",
        "en": "Referral link temporarily unavailable",
    }[language]
    if language == "ru":
        title = "👥 <b>Реферальная программа</b>"
        rules = (
            "📝 <b>Условия</b>\n"
            f"• Вы получаете <b>{referral_percent():g}%</b> с каждой подтверждённой покупки приглашённого пользователя.\n"
            "• Начисление происходит только после проверки оплаты.\n"
            "• Вывод реферальных Stars оформляется так же, как обычный вывод Stars.\n"
            f"• Минимальная сумма вывода: <b>{int(setting('min_withdraw'))} Stars</b>."
        )
        people_title = "🛍 <b>Ваши приглашённые</b>"
        empty = "Пока никто не приглашён."
        stats = (
            f"👤 Приглашено: <b>{invited}</b>\n"
            f"🛍 Покупали: <b>{buyers}</b>\n"
            f"💰 Всего заработано: <b>{total_earned} Stars</b>\n"
            f"💳 Доступно для вывода реферальных Stars: <b>{available} Stars</b>"
        )
        link_title = "🔗 <b>Ваша реферальная ссылка</b>"
    elif language == "en":
        title = "👥 <b>Referral program</b>"
        rules = (
            "📝 <b>Terms</b>\n"
            f"• You receive <b>{referral_percent():g}%</b> from each confirmed purchase by an invited user.\n"
            "• Rewards are credited after payment approval.\n"
            "• Referral Stars are withdrawn through the same process as regular Stars.\n"
            f"• Minimum withdrawal: <b>{int(setting('min_withdraw'))} Stars</b>."
        )
        people_title = "🛍 <b>Your referrals</b>"
        empty = "No one has joined yet."
        stats = (
            f"👤 Invited: <b>{invited}</b>\n"
            f"🛍 Buyers: <b>{buyers}</b>\n"
            f"💰 Earned: <b>{total_earned} Stars</b>\n"
            f"💳 Referral Stars available to withdraw: <b>{available} Stars</b>"
        )
        link_title = "🔗 <b>Your referral link</b>"
    else:
        title = "👥 <b>Реферальна програма</b>"
        rules = (
            "📝 <b>Умови</b>\n"
            f"• Ви отримуєте <b>{referral_percent():g}%</b> з кожної підтвердженої покупки запрошеного користувача.\n"
            "• Нарахування відбувається після перевірки оплати.\n"
            "• Виведення реферальних Stars оформлюється так само, як звичайне виведення Stars.\n"
            f"• Мінімальна сума виводу: <b>{int(setting('min_withdraw'))} Stars</b>."
        )
        people_title = "🛍 <b>Ваші запрошені</b>"
        empty = "Поки ніхто не запрошений."
        stats = (
            f"👤 Запрошено: <b>{invited}</b>\n"
            f"🛍 Купували: <b>{buyers}</b>\n"
            f"💰 Всього зароблено: <b>{total_earned} Stars</b>\n"
            f"💳 Доступно для виводу реферальних Stars: <b>{available} Stars</b>"
        )
        link_title = "🔗 <b>Ваше реферальне посилання</b>"
    people_text = "\n".join(people) if people else empty
    if language == "ru":
        page_note = f"\n\n📄 Страница {page + 1} из {page_count}"
    elif language == "en":
        page_note = f"\n\n📄 Page {page + 1} of {page_count}"
    else:
        page_note = f"\n\n📄 Сторінка {page + 1} з {page_count}"
    return (
        f"{title}\n\n"
        f"{link_title}\n<code>{esc(referral_url or referral_link_unavailable)}</code>\n\n"
        f"📊 <b>Статистика</b>\n{stats}\n\n"
        f"{rules}\n\n"
        f"{people_title}\n{people_text}{page_note}"
    )


def referrals_kb(user_id, page=0, total_pages=1):
    b = InlineKeyboardBuilder()
    available = referral_available_stars(user_id)
    language = user_language(user_id)
    withdraw_labels = {
        "uk": "💸 Вивести реферальні Stars",
        "ru": "💸 Вывести реферальные Stars",
        "en": "💸 Withdraw referral Stars",
    }
    if available >= int(setting("min_withdraw")):
        b.row(
            kb_button(
                withdraw_labels[language],
                emoji_id=MAIN_EMOJI["withdraw_stars"],
                style="success",
                callback_data="ref:withdraw",
            )
        )
    navigation = []
    if page > 0:
        navigation.append(
            kb_button(
                "⬅️ Попередня" if language == "uk" else
                "⬅️ Предыдущая" if language == "ru" else "⬅️ Previous",
                callback_data=f"ref:list:{page - 1}",
            )
        )
    if page + 1 < total_pages:
        navigation.append(
            kb_button(
                "Наступна ➡️" if language == "uk" else
                "Следующая ➡️" if language == "ru" else "Next ➡️",
                callback_data=f"ref:list:{page + 1}",
            )
        )
    if navigation:
        b.row(*navigation)
    back_labels = {
        "uk": "Назад",
        "ru": "Назад",
        "en": "Back",
    }
    b.row(back_inline(text=back_labels[language]))
    return b.as_markup()


def profile_referral_kb(user_id, available=None):
    available = (
        referral_available_stars(user_id)
        if available is None
        else int(available)
    )
    labels = {
        "uk": "💸 Вивести реферальні Stars",
        "ru": "💸 Вывести реферальные Stars",
        "en": "💸 Withdraw referral Stars",
    }
    referrals_labels = {
        "uk": "👥 Мої реферали",
        "ru": "👥 Мои рефералы",
        "en": "👥 My referrals",
    }
    language = user_language(user_id)
    b = InlineKeyboardBuilder()
    b.row(kb_button(
        referrals_labels[language],
        emoji_id=MENU_DEFAULT_EMOJI["referrals"],
        style="primary",
        callback_data="ref:list:0",
    ))
    b.row(kb_button(
        {"uk": "📦 Історія замовлень", "ru": "📦 История заказов", "en": "📦 Order history"}[language],
        style="primary", callback_data="profile:orders:0",
    ))
    if available >= int(setting("min_withdraw")):
        b.row(kb_button(
            labels[language],
            emoji_id=MAIN_EMOJI["withdraw_stars"],
            style="success",
            callback_data="ref:withdraw",
        ))
    return b.as_markup()


@dp.message(F.text.in_(language_labels("referrals")))
async def referrals(message: Message):
    ensure_user(message.from_user)
    referral_url = await referral_link(message.from_user.id)
    invited, _, _, _, _ = referral_report(message.from_user.id)
    pages = max(1, math.ceil(invited / 10))
    await replace_screen(
        message.from_user.id,
        referral_screen_text(message.from_user.id, referral_url, page=0),
        image="referrals",
        reply_markup=referrals_kb(message.from_user.id, 0, pages),
    )


@dp.callback_query(F.data.startswith("ref:list:"))
async def referral_list_page(call: CallbackQuery):
    try:
        page = max(0, int(call.data.rsplit(":", 1)[1]))
    except (AttributeError, TypeError, ValueError):
        language = user_language(call.from_user.id)
        invalid_page = {
            "uk": "Некоректний номер сторінки.",
            "ru": "Некорректный номер страницы.",
            "en": "Invalid page number.",
        }[language]
        await call.answer(invalid_page, show_alert=True)
        return
    await call.answer()
    ensure_user(call.from_user)
    referral_url = await referral_link(call.from_user.id)
    invited, _, _, _, _ = referral_report(call.from_user.id)
    pages = max(1, math.ceil(invited / 10))
    page = min(page, pages - 1)
    await replace_screen(
        call.from_user.id,
        referral_screen_text(call.from_user.id, referral_url, page=page),
        image="referrals",
        reply_markup=referrals_kb(call.from_user.id, page, pages),
    )


@dp.callback_query(F.data == "ref:withdraw")
async def referral_withdraw_start(call: CallbackQuery, state: FSMContext):
    ensure_user(call.from_user)
    available = referral_available_stars(call.from_user.id)
    minimum = int(setting("min_withdraw"))
    if available < minimum:
        language = user_language(call.from_user.id)
        message = {
            "uk": f"Мінімум для виводу — {minimum} Stars.",
            "ru": f"Минимум для вывода — {minimum} Stars.",
            "en": f"Minimum withdrawal is {minimum} Stars.",
        }[language]
        await call.answer(
            message,
            show_alert=True,
        )
        return
    await call.answer()
    await state.set_state(Form.referral_withdraw_amount)
    language = user_language(call.from_user.id)
    if language == "ru":
        prompt = (
            "💸 <b>Вывод реферальных Stars</b>\n\n"
            f"Доступно для вывода реферальных Stars: <b>{available} Stars</b>\n"
            f"Минимум: <b>{minimum} Stars</b>\n\n"
            "Введите количество Stars для вывода. Заявка будет обработана так же, "
            "как обычный вывод Stars."
        )
    elif language == "en":
        prompt = (
            "💸 <b>Withdraw referral Stars</b>\n\n"
            f"Referral Stars available to withdraw: <b>{available} Stars</b>\n"
            f"Minimum: <b>{minimum} Stars</b>\n\n"
            "Enter the amount. The request will use the same process as a regular "
            "Stars withdrawal."
        )
    else:
        prompt = (
            "💸 <b>Вивід реферальних Stars</b>\n\n"
            f"Доступно для виводу реферальних Stars: <b>{available} Stars</b>\n"
            f"Мінімум: <b>{minimum} Stars</b>\n\n"
            "Вкажіть кількість Stars. Заявка обробляється так само, як звичайне "
            "виведення Stars."
        )
    await replace_screen(
        call.from_user.id,
        prompt,
        image="referrals",
        reply_markup=cancel_kb(user_language(call.from_user.id)),
    )


@dp.message(Form.referral_withdraw_amount)
async def referral_withdraw_amount(message: Message, state: FSMContext):
    amount_raw = (message.text or "").strip()
    try:
        amount = int(amount_raw)
    except (TypeError, ValueError):
        amount = 0
    language = user_language(message.from_user.id)
    minimum = int(setting("min_withdraw"))
    available = referral_available_stars(message.from_user.id)
    if amount < minimum:
        error = {
            "uk": f"❌ Мінімальна сума виводу — <b>{minimum} Stars</b>.",
            "ru": f"❌ Минимальная сумма вывода — <b>{minimum} Stars</b>.",
            "en": f"❌ Minimum withdrawal is <b>{minimum} Stars</b>.",
        }[language]
        await replace_screen(
            message.from_user.id,
            error,
            image="referrals",
            reply_markup=cancel_kb(language),
        )
        return
    if amount > available:
        error = {
            "uk": f"❌ Доступно лише <b>{available} реферальних Stars</b>.",
            "ru": f"❌ Доступно только <b>{available} реферальных Stars</b>.",
            "en": f"❌ Only <b>{available} referral Stars</b> are available.",
        }[language]
        await replace_screen(
            message.from_user.id,
            error,
            image="referrals",
            reply_markup=cancel_kb(language),
        )
        return
    request_id = None
    try:
        db.execute("BEGIN IMMEDIATE")
        available = referral_available_stars(message.from_user.id)
        if amount < int(setting("min_withdraw")) or amount > available:
            db.rollback()
            await state.clear()
            retry_message = {
                "uk": "❌ Доступний баланс змінився. Перевірте його в розділі "
                "«Реферали» та створіть заявку ще раз.",
                "ru": "❌ Доступный баланс изменился. Проверьте его в разделе "
                "«Рефералы» и создайте заявку ещё раз.",
                "en": "❌ Your available balance changed. Check the Referrals "
                "section and submit the request again.",
            }[language]
            await replace_screen(
                message.from_user.id,
                retry_message,
                image="referrals",
                reply_markup=main_menu(message.from_user.id),
            )
            return
        cur = db.execute(
            """INSERT INTO orders(
                   user_id,order_type,quantity,amount,status,created_at,
                   withdrawal_source
               ) VALUES(?,'withdraw_stars',?,0,'waiting_review',?,'referral')""",
            (message.from_user.id, str(amount), now()),
        )
        request_id = cur.lastrowid
        db.commit()
    except Exception:
        db.rollback()
        raise
    await state.clear()
    if language == "ru":
        confirmation = (
            f"✅ <b>Заявка на вывод реферальных Stars #{request_id} создана</b>\n\n"
            f"💰 Сумма: <b>{amount} Stars</b>\n"
            "Администратор обработает её так же, как обычный вывод Stars."
        )
    elif language == "en":
        confirmation = (
            f"✅ <b>Referral Stars withdrawal request #{request_id} created</b>\n\n"
            f"💰 Amount: <b>{amount} Stars</b>\n"
            "An administrator will process it like a regular Stars withdrawal."
        )
    else:
        confirmation = (
            f"✅ <b>Заявку на вивід реферальних Stars #{request_id} створено</b>\n\n"
            f"💰 Сума: <b>{amount} Stars</b>\n"
            "Адміністратор обробить її так само, як звичайне виведення Stars."
        )
    await replace_screen(
        message.from_user.id,
        confirmation,
        image="referrals",
        reply_markup=main_menu(message.from_user.id),
    )
    user_label = (
        f"@{esc(message.from_user.username)}"
        if message.from_user.username
        else esc(message.from_user.first_name or "без username")
    )
    await notify_admins(
        f"💸 <b>Заявка на вивід реферальних Stars #{request_id}</b>\n\n"
        f"👤 Користувач: {user_label}\n"
        f"🆔 ID: <code>{message.from_user.id}</code>\n"
        f"💰 Сума: <b>{amount} Stars</b>\n"
        f"💳 Доступно після заявки: "
        f"<b>{referral_available_stars(message.from_user.id)} Stars</b>\n"
        f"📌 Джерело балансу: <b>реферальні Stars</b>",
        admin_order_kb(request_id),
    )


@dp.message(F.text.in_(language_labels("profile")))
async def profile(message: Message):
    ensure_user(message.from_user)
    u = user_row(message.from_user.id)
    language = user_language(message.from_user.id)
    referral_url = await referral_link(message.from_user.id)
    referral_link_text = esc(referral_url) if referral_url else {
        "uk": "Посилання тимчасово недоступне",
        "ru": "Ссылка временно недоступна",
        "en": "Referral link temporarily unavailable",
    }[language]
    ref_available = referral_available_stars(message.from_user.id)
    invited_count = referral_invited_count(message.from_user.id)
    labels = {
        "uk": {
            "title": "Ваш профіль",
            "status": "Статус",
            "registered": "Реєстрація",
            "balance": "Баланс",
            "available": "Доступно",
            "purchases": "Покупки",
            "bought_stars": "Придбано Stars",
            "bought_ton": "Придбано TON",
            "premium_months": "Telegram Premium (місяців)",
            "spent": "Витрачено",
            "referral_info": "Реферальна інформація",
            "invited": "Запрошено людей",
            "earned": "Зароблено",
            "referral_available": "Доступно для виводу реферальних Stars",
            "link": "Ваше посилання",
            "currency": "грн",
        },
        "ru": {
            "title": "Ваш профиль",
            "status": "Статус",
            "registered": "Регистрация",
            "balance": "Баланс",
            "available": "Доступно",
            "purchases": "Покупки",
            "bought_stars": "Куплено Stars",
            "bought_ton": "Куплено TON",
            "premium_months": "Telegram Premium (месяцев)",
            "spent": "Потрачено",
            "referral_info": "Реферальная информация",
            "invited": "Приглашено людей",
            "earned": "Заработано",
            "referral_available": "Доступно для вывода реферальных Stars",
            "link": "Ваша ссылка",
            "currency": "грн",
        },
        "en": {
            "title": "Your profile",
            "status": "Status",
            "registered": "Registered",
            "balance": "Balance",
            "available": "Available",
            "purchases": "Purchases",
            "bought_stars": "Stars purchased",
            "bought_ton": "TON purchased",
            "premium_months": "Telegram Premium (months)",
            "spent": "Spent",
            "referral_info": "Referral information",
            "invited": "People invited",
            "earned": "Earned",
            "referral_available": "Referral Stars available to withdraw",
            "link": "Your referral link",
            "currency": "UAH",
        },
    }[language]
    status_labels = {
        "uk": {"Новачок": "Новачок", "Клієнт": "Клієнт"},
        "ru": {"Новачок": "Новичок", "Клієнт": "Клиент"},
        "en": {"Новачок": "New user", "Клієнт": "Customer"},
    }
    status = status_labels[language].get(u["status"], u["status"] or "—")
    profile_text = (
        f"{pe(MAIN_EMOJI['profile'], '👤')} <b>{labels['title']}</b>\n\n"
        f"{pe(EMOJI_POOL[0], '🆔')} ID: <code>{u['id']}</code>\n"
        f"{pe(EMOJI_POOL[1], '👤')} Username: @{esc(u['username'] or '—')}\n"
        f"{pe(EMOJI_POOL[2], '📊')} {labels['status']}: <b>{esc(status)}</b>\n"
        f"{pe(EMOJI_POOL[3], '📅')} {labels['registered']}: "
        f"<b>{esc(u['registered_at'])}</b>\n\n"
        f"{pe(EMOJI_POOL[4], '💳')} <b>{labels['balance']}</b>\n"
        f"{pe(MAIN_EMOJI['buy_stars'], '⭐')} {labels['available']}: "
        f"<b>{int(u['balance_stars'] or 0)} Stars</b>\n\n"
        f"{pe(EMOJI_POOL[5], '📈')} <b>{labels['purchases']}</b>\n"
        f"{pe(MAIN_EMOJI['buy_stars'], '⭐')} {labels['bought_stars']}: "
        f"<b>{int(u['bought_stars'] or 0)}</b>\n"
        f"{pe(MAIN_EMOJI['buy_gram'], '💎')} {labels['bought_ton']}: "
        f"<b>{float(u['bought_ton'] or 0):g}</b>\n"
        f"{pe(MAIN_EMOJI['premium'], '⭐')} {labels['premium_months']}: "
        f"<b>{int(u['premium_months'] or 0)}</b>\n"
        f"{pe(EMOJI_POOL[7], '💰')} {labels['spent']}: "
        f"<b>{float(u['spent_uah'] or 0):.2f} {labels['currency']}</b>\n\n"
        f"{pe(EMOJI_POOL[8], '👥')} <b>{labels['referral_info']}</b>\n"
        f"• {labels['invited']}: <b>{invited_count}</b>\n"
        f"• {labels['earned']}: "
        f"<b>{int(u['referral_earnings_stars'] or 0)} Stars</b>\n"
        f"• {labels['referral_available']}: <b>{ref_available} Stars</b>\n"
        f"• {labels['link']}:\n{referral_link_text}"
    )
    await replace_screen(
        message.from_user.id,
        profile_text,
        image="profile",
        reply_markup=profile_referral_kb(message.from_user.id, ref_available),
    )


@dp.message(F.text.in_(language_labels("calculator")))
async def calculator(message: Message, state: FSMContext):
    await state.set_state(Form.calculator)
    await replace_screen(
        message.from_user.id,
        "🧮 Вкажіть кількість Stars для розрахунку.",
        image="calculator",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.calculator)
async def calculator_value(message: Message, state: FSMContext):
    try:
        stars = int(message.text)
        if stars < 1:
            raise ValueError
    except (ValueError, TypeError):
        await replace_screen(
            message.from_user.id,
            "❌ Введіть коректне ціле число.",
            image="calculator",
            reply_markup=cancel_kb(),
        )
        return
    amount = stars * float(setting("stars_rate"))
    await state.clear()
    await replace_screen(
        message.from_user.id,
        f"🧮 <b>Розрахунок</b>\n\n⭐ Stars: <b>{stars}</b>\n💰 Вартість: <b>{amount:.2f} грн</b>",
        image="calculator",
        reply_markup=main_menu(message.from_user.id),
    )


@dp.message(F.text.in_(language_labels("reviews")))
async def reviews(message: Message):
    b = InlineKeyboardMarkup(inline_keyboard=[[
        kb_button("Переглянути відгуки", emoji_id=MAIN_EMOJI["reviews"], style="primary", url=setting("reviews_url"))
    ]])
    await replace_screen(
        message.from_user.id,
        f"{pe(MAIN_EMOJI['reviews'], '💬')} <b>Відгуки наших клієнтів</b>\n\n"
        "Тут ви можете переглянути відгуки наших клієнтів 👇",
        image="reviews",
        reply_markup=b,
    )


@dp.message(F.text.in_(language_labels("support")))
async def support(message: Message, state: FSMContext):
    await state.set_state(Form.support)
    await replace_screen(
        message.from_user.id,
        f"{pe(MAIN_EMOJI['support'], '🆘')} <b>Опишіть ваше питання або проблему</b>\n\n"
        "За потреби можете додати фото або відео.",
        image="support",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.support)
async def support_message(message: Message, state: FSMContext):
    if message.content_type not in {ContentType.TEXT, ContentType.PHOTO, ContentType.VIDEO}:
        await message.answer("❌ Надішліть текст, фото або відео.")
        return
    await state.clear()
    ensure_user(message.from_user)
    u = user_row(message.from_user.id)
    ticket = db.execute(
        """INSERT INTO support_tickets(user_id,source_message_id,status,created_at)
           VALUES(?,?, 'open', ?)""",
        (message.from_user.id, message.message_id, now()),
    )
    db.commit()
    ticket_id = ticket.lastrowid
    reply_markup = support_reply_kb(ticket_id)
    text = (
        f"🆘 <b>Нове звернення в підтримку</b>\n"
        f"👤 @{esc(u['username'] or 'без_username')}\n"
        f"🆔 <code>{u['id']}</code>\n\n"
        f"{message_html(message) if message.text or message.caption else '[медіа]'}"
    )
    for aid in staff_ids():
        try:
            if message.photo:
                await bot.send_photo(aid, message.photo[-1].file_id, caption=text, reply_markup=reply_markup)
            elif message.video:
                await bot.send_video(aid, message.video.file_id, caption=text, reply_markup=reply_markup)
            else:
                await bot.send_message(aid, text, reply_markup=reply_markup)
        except Exception:
            pass
    await replace_screen(
        message.from_user.id,
        "✅ Звернення передано в підтримку.",
        image="support",
        reply_markup=main_menu(message.from_user.id),
    )


@dp.callback_query(F.data.startswith("support_claim:"))
async def support_claim(call: CallbackQuery):
    if not is_staff(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    ticket_id = int(call.data.split(":", 1)[1])
    ticket = db.execute(
        "SELECT * FROM support_tickets WHERE id=?", (ticket_id,)
    ).fetchone()
    if not ticket or ticket["status"] != "open":
        await call.answer("Звернення вже взято або закрито.", show_alert=True)
        return
    db.execute(
        """UPDATE support_tickets SET status='claimed',claimed_by=?,claimed_at=?
           WHERE id=? AND status='open'""",
        (call.from_user.id, now(), ticket_id),
    )
    db.commit()
    await call.answer("Звернення взято в роботу.")
    try:
        await call.message.edit_reply_markup(
            reply_markup=support_reply_ready_kb(ticket_id)
        )
    except Exception:
        pass
    await call.message.answer(
        f"📥 Звернення #{ticket_id} взято модератором <code>{call.from_user.id}</code>.\n"
        "Тепер можна відповісти клієнту."
    )


@dp.callback_query(F.data.startswith("support_reply:"))
async def support_reply_start(call: CallbackQuery, state: FSMContext):
    if not is_staff(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    ticket_id = int(call.data.split(":", 1)[1])
    ticket = db.execute(
        "SELECT * FROM support_tickets WHERE id=?", (ticket_id,)
    ).fetchone()
    if not ticket or ticket["status"] != "claimed" or ticket["claimed_by"] != call.from_user.id:
        await call.answer("Звернення вже закрито або ще не взяте в роботу.", show_alert=True)
        return
    await call.answer()
    await state.update_data(
        support_reply_ticket_id=ticket_id,
        support_reply_user_id=ticket["user_id"],
    )
    await state.set_state(Form.support_reply)
    await call.message.answer(
        "✍️ Напишіть відповідь клієнту.\nМожна надіслати текст, фото або відео.",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.support_reply)
async def send_support_reply(message: Message, state: FSMContext):
    if not is_staff(message.from_user.id):
        return
    data = await state.get_data()
    ticket_id = data.get("support_reply_ticket_id")
    user_id = data.get("support_reply_user_id")
    ticket = db.execute(
        "SELECT * FROM support_tickets WHERE id=?", (ticket_id,)
    ).fetchone() if ticket_id else None
    if not user_id or not ticket or ticket["claimed_by"] != message.from_user.id:
        await state.clear()
        await message.answer("❌ Звернення не взяте вами в роботу.")
        return
    await state.clear()
    try:
        await bot.copy_message(chat_id=user_id, from_chat_id=message.chat.id, message_id=message.message_id)
    except Exception:
        await message.answer(
            "❌ Не вдалося надіслати відповідь. Можливо, користувач заблокував бота.",
            reply_markup=main_menu(message.from_user.id),
        )
        return
    db.execute(
        "UPDATE support_tickets SET status='closed' WHERE id=?",
        (ticket_id,),
    )
    db.commit()
    await message.answer(
        f"✅ Відповідь на звернення #{ticket_id} надіслано клієнту.",
        reply_markup=moderator_menu_kb() if not is_admin(message.from_user.id) else admin_menu_kb(),
    )


async def send_pending_support_tickets(admin_chat_id):
    tickets = db.execute(
        """SELECT * FROM support_tickets
           WHERE status='open' ORDER BY id ASC"""
    ).fetchall()
    delivered = 0
    for ticket in tickets:
        markup = support_reply_kb(ticket["id"])
        try:
            await bot.copy_message(
                chat_id=admin_chat_id,
                from_chat_id=ticket["user_id"],
                message_id=ticket["source_message_id"],
                reply_markup=markup,
            )
            delivered += 1
        except Exception:
            await bot.send_message(
                admin_chat_id,
                f"🆘 <b>Звернення #{ticket['id']}</b>\n"
                f"🆔 Користувач: <code>{ticket['user_id']}</code>",
                reply_markup=markup,
            )
    return delivered


@dp.callback_query(F.data == "adm:support_queue")
async def adm_support_queue(call: CallbackQuery):
    if not is_staff(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    await call.answer()
    delivered = await send_pending_support_tickets(call.message.chat.id)
    count = db.execute(
        "SELECT COUNT(*) AS c FROM support_tickets WHERE status='open'"
    ).fetchone()["c"]
    await call.message.answer(
        f"🆘 Відкритих звернень: <b>{count}</b>\n"
        f"Повторно надіслано вам: <b>{delivered}</b>.",
        reply_markup=moderator_menu_kb() if not is_admin(call.from_user.id) else admin_menu_kb(),
    )


@dp.callback_query(F.data == "admin:back")
async def admin_back(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await call.answer()
    await state.clear()
    await call.message.edit_text("⚙️ <b>Адмін-панель</b>", reply_markup=admin_menu_kb())


@dp.callback_query(F.data == "adm:ui")
async def adm_ui(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    await call.answer()
    await state.clear()
    await call.message.edit_text(
        "🎨 <b>Оформлення бота</b>\n\n"
        "Тут можна змінити банери стартових екранів, тексти кнопок, "
        "кольори та Telegram Premium Emoji.\n\n"
        "Усі зміни зберігаються в SQLite і не скидаються після перезапуску.",
        reply_markup=ui_admin_kb(),
    )


@dp.callback_query(F.data.startswith("adm:ui:banner:"))
async def adm_ui_banner_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    banner_key = call.data.rsplit(":", 1)[1]
    if banner_key not in UI_BANNER_KEYS:
        await call.answer("Невідомий банер.", show_alert=True)
        return
    await call.answer()
    await state.update_data(ui_banner_key=banner_key)
    await state.set_state(Form.ui_banner)
    current = setting(f"banner_{banner_key}")
    label = UI_BANNER_TITLES[banner_key]
    await call.message.answer(
        f"🖼 <b>Банер: {esc(label)}</b>\n\n"
        f"Поточний стан: <b>{'власний банер' if current else 'стандартний банер'}</b>\n\n"
        "Надішліть нове фото одним повідомленням. Telegram file_id буде "
        "збережено, тому банер не зникне після перезапуску.",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.ui_banner, F.photo)
async def adm_ui_banner_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    banner_key = data.get("ui_banner_key")
    if banner_key not in UI_BANNER_KEYS:
        await state.clear()
        await message.answer("❌ Не вдалося визначити банер.", reply_markup=admin_menu_kb())
        return
    set_setting(f"banner_{banner_key}", message.photo[-1].file_id)
    await state.clear()
    await message.answer(
        "✅ Банер збережено. Він використовуватиметься надалі й не скинеться сам.",
        reply_markup=ui_admin_kb(),
    )


@dp.message(Form.ui_banner)
async def adm_ui_banner_invalid(message: Message):
    if is_admin(message.from_user.id):
        await message.answer("❌ Надішліть саме фото банера або натисніть «Скасувати».")


@dp.callback_query(F.data.startswith("adm:ui:banner_reset:"))
async def adm_ui_banner_reset(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    banner_key = call.data.rsplit(":", 1)[1]
    if banner_key not in UI_BANNER_KEYS:
        await call.answer("Невідомий банер.", show_alert=True)
        return
    await state.clear()
    delete_setting(f"banner_{banner_key}")
    await call.answer("Банер скинуто.")
    await call.message.edit_text(
        "🎨 <b>Оформлення бота</b>\n\n"
        "Виберіть банер для зміни. ✅ означає, що встановлено власне фото; "
        "▫️ — використовується стандартне зображення.",
        reply_markup=ui_admin_kb(),
    )


@dp.callback_query(F.data == "adm:ui:buttons")
async def adm_ui_buttons(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        "✏️ <b>Кнопки головного меню</b>\n\n"
        "Оберіть кнопку. Для кожної можна змінити колір, текст і Premium Emoji. "
        "Виберіть «Прозора — без кольору», щоб повернути стандартне оформлення Telegram.",
        reply_markup=ui_buttons_admin_kb(),
    )


@dp.callback_query(F.data.startswith("adm:ui:button:"))
async def adm_ui_button(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    key = call.data.rsplit(":", 1)[1]
    if key not in MENU_BUTTON_TITLES:
        await call.answer("Кнопку не знайдено.", show_alert=True)
        return
    await call.answer()
    label_lines = "\n".join(
        f"• {LANGUAGE_NAMES[language]}: <code>{esc(menu_label(key, language))}</code>"
        for language in LANGUAGE_NAMES
    )
    custom_emoji = setting(f"menu_emoji:{key}")
    emoji_status = custom_emoji or f"стандартний ({MENU_DEFAULT_EMOJI[key]})"
    await call.message.edit_text(
        f"✏️ <b>{esc(MENU_BUTTON_TITLES[key])}</b>\n\n"
        f"{label_lines}\n\n"
        f"🎨 Стиль: <b>{'без кольору' if menu_style(key) == 'transparent' else menu_style(key)}</b>\n"
        f"✨ Premium Emoji: <code>{esc(emoji_status)}</code>\n\n"
        "Premium Emoji надсилається окремим повідомленням у відповідному пункті.",
        reply_markup=ui_button_admin_kb(key),
    )


@dp.callback_query(F.data.startswith("adm:ui:label:"))
async def adm_ui_button_label_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    parts = call.data.split(":")
    if len(parts) != 5:
        await call.answer("Некоректні дані.", show_alert=True)
        return
    _, _, _, key, language = parts
    if key not in MENU_BUTTON_TITLES or language not in LANGUAGE_NAMES:
        await call.answer("Кнопку або мову не знайдено.", show_alert=True)
        return
    await call.answer()
    await state.update_data(ui_button_key=key, ui_button_language=language)
    await state.set_state(Form.ui_button_label)
    await call.message.answer(
        f"✏️ Надішліть новий текст кнопки <b>{esc(MENU_BUTTON_TITLES[key])}</b> "
        f"мовою <b>{LANGUAGE_NAMES[language]}</b>.\n\n"
        "Telegram-кнопки підтримують лише звичайний текст і Premium Emoji; "
        "жирний шрифт та цитати для напису кнопки недоступні.\n\n"
        "Можна додати Telegram Premium Emoji до повідомлення — він буде "
        "збережений як іконка кнопки.",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.ui_button_label)
async def adm_ui_button_label_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    value = (message.text or "").strip()
    if not value:
        await message.answer("❌ Текст кнопки не може бути порожнім.")
        return
    data = await state.get_data()
    key = data.get("ui_button_key")
    language = data.get("ui_button_language")
    if key not in MENU_BUTTON_TITLES or language not in LANGUAGE_NAMES:
        await state.clear()
        await message.answer("❌ Налаштування кнопки втрачено.", reply_markup=ui_admin_kb())
        return
    set_setting(f"menu_label:{key}:{language}", value)
    emoji_id = custom_emoji_id_from_message(message)
    if emoji_id:
        set_setting(f"menu_emoji:{key}", emoji_id)
    await state.clear()
    await message.answer(
        f"✅ Текст кнопки для {LANGUAGE_NAMES[language]} збережено.",
        reply_markup=ui_button_admin_kb(key),
    )


@dp.callback_query(F.data.startswith("adm:ui:emoji:"))
async def adm_ui_button_emoji_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    key = call.data.rsplit(":", 1)[1]
    if key not in MENU_BUTTON_TITLES:
        await call.answer("Кнопку не знайдено.", show_alert=True)
        return
    await call.answer()
    await state.update_data(ui_button_key=key)
    await state.set_state(Form.ui_button_emoji)
    await call.message.answer(
        f"✨ Надішліть одним повідомленням Telegram Premium Emoji "
        f"для кнопки «{esc(MENU_BUTTON_TITLES[key])}».",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.ui_button_emoji)
async def adm_ui_button_emoji_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    emoji_id = custom_emoji_id_from_message(message)
    if not emoji_id:
        await message.answer("❌ Не бачу Telegram Premium Emoji. Надішліть саме Premium Emoji.")
        return
    key = (await state.get_data()).get("ui_button_key")
    if key not in MENU_BUTTON_TITLES:
        await state.clear()
        await message.answer("❌ Налаштування кнопки втрачено.", reply_markup=ui_admin_kb())
        return
    set_setting(f"menu_emoji:{key}", emoji_id)
    await state.clear()
    await message.answer("✅ Premium Emoji кнопки збережено.", reply_markup=ui_button_admin_kb(key))


@dp.callback_query(F.data.startswith("adm:ui:style:"))
async def adm_ui_button_style(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    parts = call.data.split(":")
    if len(parts) != 5:
        await call.answer("Некоректні дані.", show_alert=True)
        return
    key, style = parts[3], parts[4]
    if key not in MENU_BUTTON_TITLES or style not in {
        "primary", "success", "danger", "transparent"
    }:
        await call.answer("Некоректний колір кнопки.", show_alert=True)
        return
    set_setting(f"menu_style:{key}", style)
    await adm_ui_button(call)


@dp.callback_query(F.data.startswith("adm:ui:reset_button:"))
async def adm_ui_reset_button(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    key = call.data.rsplit(":", 1)[1]
    if key not in MENU_BUTTON_TITLES:
        await call.answer("Кнопку не знайдено.", show_alert=True)
        return
    for language in LANGUAGE_NAMES:
        delete_setting(f"menu_label:{key}:{language}")
    delete_setting(f"menu_emoji:{key}")
    delete_setting(f"menu_style:{key}")
    await adm_ui_button(call)


@dp.callback_query(F.data == "adm:ui:welcome")
async def adm_ui_welcome_start(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    await call.answer()
    b = InlineKeyboardBuilder()
    for language, title in LANGUAGE_NAMES.items():
        b.row(kb_button(
            f"📝 {title}",
            emoji_id=EMOJI_POOL[27],
            style="primary",
            callback_data=f"adm:ui:welcome:{language}",
        ))
    b.row(back_inline("adm:ui"))
    await call.message.edit_text(
        "📝 <b>Текст стартового меню</b>\n\nОберіть мову для редагування тексту.",
        reply_markup=b.as_markup(),
    )


@dp.callback_query(F.data.startswith("adm:ui:welcome:"))
async def adm_ui_welcome_language(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    language = call.data.rsplit(":", 1)[1]
    if language not in LANGUAGE_NAMES:
        await call.answer("Мову не знайдено.", show_alert=True)
        return
    await call.answer()
    await state.update_data(ui_welcome_language=language)
    await state.set_state(Form.ui_welcome_text)
    current = setting(f"welcome_text:{language}") or "стандартний текст"
    await call.message.answer(
        f"📝 Надішліть новий текст старту для <b>{LANGUAGE_NAMES[language]}</b>.\n\n"
        f"Поточне значення: <code>{esc(current)}</code>\n\n"
        "Перед відправленням виділіть потрібні фрагменти та застосуйте "
        "форматування Telegram — жирний шрифт, курсив, цитату, посилання тощо. "
        "Бот збереже його автоматично; HTML-теги вводити не потрібно.",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.ui_welcome_text)
async def adm_ui_welcome_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    # aiogram serializes Telegram entities to safe HTML, preserving the
    # formatting the administrator applied in Telegram (bold, quotes, links,
    # custom emoji, and other supported entities).
    value = message_html(message).strip()
    language = (await state.get_data()).get("ui_welcome_language")
    if language not in LANGUAGE_NAMES or not value:
        await message.answer("❌ Надішліть текст старту одним повідомленням.")
        return
    set_setting(f"welcome_text:{language}", value)
    await state.clear()
    await message.answer("✅ Текст стартового меню збережено.", reply_markup=ui_admin_kb())


@dp.callback_query(F.data == "adm:ui:reset")
async def adm_ui_reset(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        "⚠️ <b>Скинути все оформлення?</b>\n\n"
        "Буде скинуто лише банери, тексти старту, тексти кнопок, кольори "
        "та Premium Emoji. Платежі, користувачі й замовлення не зміняться.",
        reply_markup=ui_reset_confirm_kb(),
    )


@dp.callback_query(F.data == "adm:ui:reset:confirm")
async def adm_ui_reset_confirm(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    for banner_key in UI_BANNER_KEYS:
        delete_setting(f"banner_{banner_key}")
    delete_settings_like("menu_label:%")
    delete_settings_like("menu_emoji:%")
    delete_settings_like("menu_style:%")
    delete_settings_like("welcome_text:%")
    await call.answer("Оформлення скинуто.")
    await call.message.edit_text(
        "✅ Оформлення повернуто до стандартних значень.",
        reply_markup=ui_admin_kb(),
    )


@dp.callback_query(F.data == "adm:required_channels")
async def adm_required_channels(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    await call.answer()
    channels = db.execute(
        "SELECT * FROM required_channels ORDER BY id"
    ).fetchall()
    if channels:
        lines = "\n".join(
            f"• <b>#{row['id']} {esc(row['title'])}</b> — "
            f"<code>{esc(row['chat_id'])}</code> — "
            f"{'увімкнено' if row['active'] else 'вимкнено'}"
            for row in channels
        )
    else:
        lines = "Канали ще не додані."
    await call.message.edit_text(
        "📢 <b>Обов’язкові підписки</b>\n\n"
        f"{lines}\n\n"
        "Бот має бути адміністратором кожного каналу або групи, "
        "інакше Telegram не зможе перевірити підписку.",
        reply_markup=required_channels_admin_kb(),
    )


@dp.callback_query(F.data == "required_admin:add")
async def required_channel_add_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await call.answer()
    await state.update_data(required_channel_id=None)
    await state.set_state(Form.required_channel)
    await call.message.answer(
        "➕ <b>Додати обов’язковий канал або групу</b>\n\n"
        "Надішліть одним повідомленням тільки публічне посилання:\n"
        "<code>https://t.me/username</code>\n\n"
        "Бот сам отримає назву, ID і перевірить, що він адміністратор "
        "каналу або групи.",
        reply_markup=cancel_kb(),
    )


@dp.callback_query(F.data.startswith("required_admin:edit:"))
async def required_channel_edit_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    channel_id = int(call.data.rsplit(":", 1)[1])
    channel = db.execute(
        "SELECT * FROM required_channels WHERE id=?", (channel_id,)
    ).fetchone()
    if not channel:
        await call.answer("Канал не знайдено.", show_alert=True)
        return
    await call.answer()
    builder = InlineKeyboardBuilder()
    builder.row(kb_button(
        "✏️ Змінити дані",
        emoji_id=EMOJI_POOL[27],
        style="primary",
        callback_data=f"required_admin:update:{channel_id}",
    ))
    builder.row(kb_button(
        "⏯ Увімкнути / вимкнути",
        emoji_id=EMOJI_POOL[16],
        style="primary",
        callback_data=f"required_admin:toggle:{channel_id}",
    ))
    builder.row(kb_button(
        "🗑 Видалити",
        emoji_id=EMOJI_POOL[3],
        style="danger",
        callback_data=f"required_admin:delete:{channel_id}",
    ))
    builder.row(back_inline("adm:required_channels"))
    await call.message.edit_text(
        f"📢 <b>{esc(channel['title'])}</b>\n\n"
        f"Chat ID: <code>{esc(channel['chat_id'])}</code>\n"
        f"Посилання: {esc(channel['invite_url'])}\n"
        f"Статус: <b>{'увімкнено' if channel['active'] else 'вимкнено'}</b>",
        reply_markup=builder.as_markup(),
    )


@dp.callback_query(F.data.startswith("required_admin:update:"))
async def required_channel_update_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    channel_id = int(call.data.rsplit(":", 1)[1])
    if not db.execute("SELECT 1 FROM required_channels WHERE id=?", (channel_id,)).fetchone():
        await call.answer("Канал не знайдено.", show_alert=True)
        return
    await call.answer()
    await state.update_data(required_channel_id=channel_id)
    await state.set_state(Form.required_channel)
    await call.message.answer(
        "✏️ Надішліть тільки нове публічне посилання:\n"
        "<code>https://t.me/username</code>",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.required_channel)
async def required_channel_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    raw_link = (message.text or "").strip()
    if not raw_link or "|" in raw_link:
        await message.answer(
            "❌ Надішліть тільки одне публічне посилання виду "
            "<code>https://t.me/username</code>."
        )
        return

    try:
        chat_id, title, invite_url = await resolve_required_chat_link(raw_link)
    except ValueError as exc:
        await message.answer(f"❌ {esc(exc)}")
        return
    except Exception as exc:
        print(f"Unable to resolve required chat link {raw_link!r}: {exc}", flush=True)
        await message.answer(
            "❌ Не вдалося знайти цей канал або групу. Перевірте посилання і "
            "права адміністратора бота."
        )
        return

    data = await state.get_data()
    channel_id = data.get("required_channel_id")
    try:
        if channel_id:
            db.execute(
                """UPDATE required_channels
                   SET title=?,chat_id=?,invite_url=?,updated_at=?
                   WHERE id=?""",
                (title, chat_id, invite_url, now(), channel_id),
            )
        else:
            db.execute(
                """INSERT INTO required_channels
                   (title,chat_id,invite_url,active,created_at,updated_at)
                   VALUES(?,?,?,1,?,?)""",
                (title, chat_id, invite_url, now(), now()),
            )
        db.commit()
    except sqlite3.IntegrityError:
        await message.answer("❌ Такий chat_id уже є в списку каналів.")
        return
    await state.clear()
    await message.answer(
        "✅ Обов’язковий канал збережено. Налаштування не скинеться після перезапуску.",
        reply_markup=required_channels_admin_kb(),
    )


@dp.callback_query(F.data.startswith("required_admin:toggle:"))
async def required_channel_toggle(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    channel_id = int(call.data.rsplit(":", 1)[1])
    db.execute(
        "UPDATE required_channels SET active=CASE active WHEN 1 THEN 0 ELSE 1 END, updated_at=? WHERE id=?",
        (now(), channel_id),
    )
    db.commit()
    await call.answer("Статус каналу оновлено.")
    await adm_required_channels(call)


@dp.callback_query(F.data.startswith("required_admin:delete:"))
async def required_channel_delete(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    channel_id = int(call.data.rsplit(":", 1)[1])
    db.execute("DELETE FROM required_channels WHERE id=?", (channel_id,))
    db.commit()
    await call.answer("Канал видалено.")
    await adm_required_channels(call)


@dp.callback_query(F.data == "adm:moderators")
async def adm_moderators(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        await call.answer("Цей розділ доступний тільки головному адміністратору.", show_alert=True)
        return
    await call.answer()
    moderators = db.execute(
        "SELECT * FROM moderators ORDER BY created_at DESC"
    ).fetchall()
    lines = "\n".join(
        f"• @{esc(row['username']) if row['username'] else 'без username'} "
        f"— <code>{row['user_id']}</code>"
        for row in moderators
    ) or "Модератори ще не додані."
    await call.message.edit_text(
        "🛡 <b>Модератори</b>\n\n"
        f"{lines}\n\n"
        "Модератор бачить квитанції та звернення підтримки, "
        "але не має доступу до налаштувань бота.",
        reply_markup=moderator_admin_kb(),
    )


@dp.callback_query(F.data == "moderator:add")
async def moderator_add_start(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    await call.answer()
    await state.set_state(Form.moderator)
    await call.message.answer(
        "➕ Надішліть Telegram ID або username користувача.\n"
        "Користувач має хоча б раз відкрити бота командою /start.",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.moderator)
async def moderator_save(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    target = find_user_by_identifier(message.text or "")
    if not target or target["id"] in OWNER_IDS:
        await message.answer("❌ Користувача не знайдено або це головний адміністратор.")
        return
    db.execute(
        """INSERT INTO moderators(user_id,username,first_name,added_by,created_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET
             username=excluded.username,first_name=excluded.first_name""",
        (
            target["id"],
            target["username"],
            target["first_name"],
            message.from_user.id,
            now(),
        ),
    )
    db.commit()
    refresh_staff_ids()
    await configure_bot_commands()
    await state.clear()
    await message.answer(
        f"✅ Модератора <code>{target['id']}</code> додано.",
        reply_markup=moderator_admin_kb(),
    )


@dp.callback_query(F.data.startswith("moderator:remove:"))
async def moderator_remove(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    moderator_id = int(call.data.rsplit(":", 1)[1])
    db.execute("DELETE FROM moderators WHERE user_id=?", (moderator_id,))
    db.commit()
    refresh_staff_ids()
    await configure_bot_commands()
    await call.answer("Модератора видалено.")
    await adm_moderators(call)


@dp.callback_query(F.data == "adm:raffle")
async def adm_raffle(call: CallbackQuery):
    await call.answer("Розіграші вимкнено в цій версії магазину.", show_alert=True)
    return
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Немає доступу.", show_alert=True)
        return
    await call.answer()
    raffle = current_raffle()
    if not raffle:
        text = (
            "🎁 <b>Розіграші</b>\n\n"
            "Активного розіграшу зараз немає.\n"
            "Створіть його, задайте ціль у Stars і напишіть умови."
        )
    else:
        entries = db.execute(
            "SELECT COUNT(*) AS total FROM raffle_entries WHERE raffle_id=?",
            (raffle["id"],),
        ).fetchone()["total"]
        status_text = "Ціль досягнута — можна проводити розіграш." if raffle["status"] == "ready" else "Набираються внески."
        text = (
            f"🎁 <b>Розіграш #{raffle['id']}</b>\n\n"
            f"📊 Статус: {status_text}\n"
            f"⭐ Пул: <b>{raffle['pool_stars']} / {raffle['target_stars']} Stars</b>\n"
            f"👥 Учасників: <b>{entries}</b>\n\n"
            f"📜 <b>Умови:</b>\n{esc(raffle['rules'])}"
        )
    await call.message.edit_text(text, reply_markup=raffle_admin_kb(raffle))


@dp.callback_query(F.data == "adm:raffle:create")
async def raffle_create_start(call: CallbackQuery, state: FSMContext):
    await call.answer("Створення розіграшів вимкнено.", show_alert=True)
    return
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Немає доступу.", show_alert=True)
        return
    if current_raffle():
        await call.answer("Спочатку завершіть поточний розіграш.", show_alert=True)
        return
    await call.answer()
    await state.set_state(Form.raffle_target)
    await call.message.answer(
        "🎁 <b>Новий розіграш</b>\n\n"
        "Введіть ціль пулу в цілих Stars. Коли внески досягнуть цієї суми, "
        "у меню з'явиться кнопка для випадкового вибору переможця.",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.raffle_target)
async def raffle_target_entered(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        target = int((message.text or "").strip())
        if target < 1 or target > 100_000_000:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer("❌ Введіть ціле число Stars від 1 до 100 000 000.")
        return
    await state.update_data(raffle_target=target)
    await state.set_state(Form.raffle_rules)
    await message.answer(
        "📜 Надішліть умови участі одним повідомленням.\n"
        "Надішліть <code>-</code>, щоб використати стандартні умови.",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.raffle_rules)
async def raffle_rules_entered(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    rules = (message.text or "").strip()
    if rules == "-":
        rules = (
            "Участь добровільна. Один запис на користувача. "
            "Після кожного підтвердженого поповнення Stars бот окремо запитує згоду. "
            "Лише після натискання кнопки згоди 3% саме з цього поповнення "
            "(округлення до цілої Stars) надходять у спільний пул. "
            "Відмова не списує Stars. "
            "Після досягнення цілі адмін проводить випадковий розіграш. "
            "Переможець отримує весь пул Stars на баланс."
        )
    if not rules or len(rules) > 3000:
        await message.answer("❌ Умови мають містити до 3000 символів.")
        return
    data = await state.get_data()
    target = int(data.get("raffle_target", 0))
    if current_raffle():
        await state.clear()
        await message.answer(
            "❌ Інший розіграш уже активний. Спочатку завершіть його.",
            reply_markup=admin_menu_kb(),
        )
        return
    cur = db.execute(
        """INSERT INTO raffles(target_stars,pool_stars,rules,status,created_by,created_at)
           VALUES(?,0,?,'active',?,?)""",
        (target, rules, message.from_user.id, now()),
    )
    db.commit()
    raffle_id = cur.lastrowid
    await state.clear()
    await message.answer(
        f"✅ <b>Розіграш #{raffle_id} створено</b>\n"
        f"🎯 Ціль: <b>{target} Stars</b>\n"
        "Запрошення надсилатиметься після підтвердження поповнення Stars.",
        reply_markup=raffle_admin_kb(current_raffle()),
    )


def raffle_consent_text(raffle, order, user_id):
    already_entered = db.execute(
        "SELECT 1 FROM raffle_entries WHERE raffle_id=? AND user_id=?",
        (raffle["id"], user_id),
    ).fetchone()
    contribution = raffle_fee(int(order["quantity"]))
    if already_entered:
        intro = (
            "Ви вже маєте один квиток у цьому розіграші. "
            "Вирішіть, чи внести 3% саме з цього поповнення."
        )
        refusal = "Якщо відмовитесь, ваш квиток залишиться, але з цього поповнення нічого не спишеться."
    else:
        intro = "Згода оформить вам один квиток у цьому розіграші."
        refusal = "Якщо відмовитесь, ви не будете додані до учасників і списання не буде."
    return (
        f"🎁 <b>Внесок у розіграш #{raffle['id']} з поповнення #{order['id']}?</b>\n\n"
        f"{esc(intro)}\n"
        f"⭐ Згода спише <b>{contribution} Stars</b> — 3% саме з поповнення "
        f"на {int(order['quantity'])} Stars, округлено до цілої Stars.\n"
        f"🔁 Для кожного наступного поповнення бот запитає дозвіл окремо.\n"
        f"↩️ {esc(refusal)}\n\n"
        f"🎯 Ціль пулу: <b>{raffle['target_stars']} Stars</b>\n"
        f"📜 <b>Умови:</b>\n{esc(raffle['rules'])}"
    )


@dp.callback_query(F.data.startswith("raffle_consent:"))
async def raffle_consent(call: CallbackQuery):
    await call.answer("Участь у розіграшах вимкнена.", show_alert=True)
    return
    try:
        _, raffle_s, order_s, decision = call.data.split(":")
        raffle_id, order_id = int(raffle_s), int(order_s)
    except (AttributeError, TypeError, ValueError):
        await call.answer("Не вдалося перевірити відповідь.", show_alert=True)
        return

    if decision not in {"yes", "no"} or not call.from_user:
        await call.answer("Некоректна відповідь.", show_alert=True)
        return

    contribution = 0
    already_contributed = False
    already_entered = False
    try:
        db.execute("BEGIN IMMEDIATE")
        raffle = db.execute("SELECT * FROM raffles WHERE id=?", (raffle_id,)).fetchone()
        order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if (
            not raffle
            or raffle["status"] not in {"active", "ready", "completed"}
            or not order
            or order["user_id"] != call.from_user.id
            or order["order_type"] != "buy_stars"
            or order["status"] != "completed"
        ):
            db.rollback()
            await call.answer("Не вдалося перевірити це поповнення або розіграш.", show_alert=True)
            return

        previous_response = db.execute(
            "SELECT decision FROM raffle_consents WHERE raffle_id=? AND order_id=?",
            (raffle_id, order_id),
        ).fetchone()
        if previous_response:
            db.rollback()
            await call.answer("Відповідь для цього поповнення вже збережена.", show_alert=True)
            return

        if decision == "no":
            db.execute(
                """INSERT INTO raffle_consents(raffle_id,order_id,user_id,decision,created_at)
                   VALUES(?,?,?,'declined',?)""",
                (raffle_id, order_id, call.from_user.id, now()),
            )
            db.commit()
        else:
            if raffle["status"] != "active":
                db.rollback()
                await call.answer("Цей розіграш уже не приймає внески.", show_alert=True)
                return

            prior_contribution = db.execute(
                "SELECT contributed_stars FROM raffle_contributions WHERE raffle_id=? AND order_id=?",
                (raffle_id, order_id),
            ).fetchone()
            already_contributed = prior_contribution is not None
            contribution = int(prior_contribution["contributed_stars"]) if prior_contribution else raffle_fee(
                int(order["quantity"])
            )
            entered = db.execute(
                "SELECT 1 FROM raffle_entries WHERE raffle_id=? AND user_id=?",
                (raffle_id, call.from_user.id),
            ).fetchone()
            already_entered = entered is not None
            if not entered:
                db.execute(
                    "INSERT INTO raffle_entries(raffle_id,user_id,joined_at) VALUES(?,?,?)",
                    (raffle_id, call.from_user.id, now()),
                )

            if not already_contributed:
                cursor = db.execute(
                    "UPDATE users SET balance_stars=balance_stars-? WHERE id=? AND balance_stars>=?",
                    (contribution, call.from_user.id, contribution),
                )
                if cursor.rowcount != 1:
                    db.rollback()
                    await call.answer(
                        "На балансі недостатньо Stars. Нічого не списано.",
                        show_alert=True,
                    )
                    return
                recorded = record_raffle_contribution(
                    raffle_id,
                    order_id,
                    call.from_user.id,
                    int(order["quantity"]),
                )
                if recorded != contribution:
                    raise RuntimeError("Raffle contribution could not be recorded exactly once")

            db.execute(
                """INSERT INTO raffle_consents(raffle_id,order_id,user_id,decision,created_at)
                   VALUES(?,?,?,'accepted',?)""",
                (raffle_id, order_id, call.from_user.id, now()),
            )
            db.commit()
    except Exception:
        db.rollback()
        raise

    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    if decision == "no":
        await call.answer("Відмову збережено. 0 Stars списано.")
        await call.message.answer(
            f"✅ Ви відмовились від внеску з поповнення #{order_id}. "
            "Списання не було."
        )
        return

    updated_user = user_row(call.from_user.id)
    updated_raffle = db.execute("SELECT * FROM raffles WHERE id=?", (raffle_id,)).fetchone()
    if already_contributed:
        contribution_line = (
            f"Ці <b>{contribution} Stars</b> з цього поповнення вже були враховані раніше; "
            "повторного списання не було."
        )
    else:
        contribution_line = (
            f"До пулу додано <b>{contribution} Stars</b> з цього поповнення."
        )
    entry_line = "Ваш квиток збережено." if already_entered else "Ви отримали один квиток."
    await call.answer("Згоду збережено для цього поповнення.")
    await call.message.answer(
        f"🎟 <b>Згоду на внесок з поповнення #{order_id} збережено.</b>\n\n"
        f"{entry_line}\n"
        f"{contribution_line}\n"
        f"💳 Баланс: <b>{updated_user['balance_stars']} Stars</b>\n"
        f"🎁 Пул: <b>{updated_raffle['pool_stars']} / "
        f"{updated_raffle['target_stars']} Stars</b>\n\n"
        "Наступного разу бот знову окремо запитає ваш дозвіл."
    )


@dp.callback_query(F.data.startswith("raffle_join:"))
async def refresh_legacy_raffle_invite(call: CallbackQuery):
    await call.answer("Це запрошення більше неактивне.", show_alert=True)
    return
    try:
        _, raffle_s, order_s = call.data.split(":")
        raffle_id, order_id = int(raffle_s), int(order_s)
    except (AttributeError, TypeError, ValueError):
        await call.answer("Це старе запрошення більше не діє.", show_alert=True)
        return

    raffle = db.execute("SELECT * FROM raffles WHERE id=?", (raffle_id,)).fetchone()
    order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if (
        not call.from_user
        or not raffle
        or raffle["status"] != "active"
        or not order
        or order["user_id"] != call.from_user.id
        or order["order_type"] != "buy_stars"
        or order["status"] != "completed"
    ):
        await call.answer("Це запрошення більше неактивне.", show_alert=True)
        return

    previous_contribution = db.execute(
        "SELECT contributed_stars FROM raffle_contributions WHERE raffle_id=? AND order_id=?",
        (raffle_id, order_id),
    ).fetchone()
    if previous_contribution:
        await call.answer(
            "Цей внесок уже врахований. Повторного списання не буде.",
            show_alert=True,
        )
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    await call.answer(
        "Умови оновлено: тепер кожне поповнення потребує окремої згоди.",
        show_alert=True,
    )
    await call.message.edit_text(
        raffle_consent_text(raffle, order, call.from_user.id),
        reply_markup=raffle_consent_kb(raffle_id, order_id),
    )


@dp.callback_query(F.data.startswith("raffle:cancel:"))
async def raffle_cancel_start(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Немає доступу.", show_alert=True)
        return
    try:
        raffle_id = int(call.data.rsplit(":", 1)[1])
    except (AttributeError, TypeError, ValueError):
        await call.answer("Некоректний номер розіграшу.", show_alert=True)
        return
    raffle = db.execute(
        "SELECT * FROM raffles WHERE id=? AND status IN ('active','ready')",
        (raffle_id,),
    ).fetchone()
    if not raffle:
        await call.answer("Цей розіграш уже завершений або скасований.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        f"⚠️ <b>Скасувати розіграш #{raffle_id}?</b>\n\n"
        f"Пул: <b>{int(raffle['pool_stars'])} Stars</b>.\n"
        "Після підтвердження всі внесені в пул Stars будуть повернуті учасникам, "
        "а розіграш більше не прийматиме внески.",
        reply_markup=raffle_cancel_confirm_kb(raffle_id),
    )


@dp.callback_query(F.data.startswith("raffle:cancel_confirm:"))
async def raffle_cancel_confirm(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Немає доступу.", show_alert=True)
        return
    try:
        raffle_id = int(call.data.rsplit(":", 1)[1])
    except (AttributeError, TypeError, ValueError):
        await call.answer("Некоректний номер розіграшу.", show_alert=True)
        return

    refunds = []
    try:
        db.execute("BEGIN IMMEDIATE")
        raffle = db.execute(
            "SELECT * FROM raffles WHERE id=? AND status IN ('active','ready')",
            (raffle_id,),
        ).fetchone()
        if not raffle:
            db.rollback()
            await call.answer("Цей розіграш уже завершений або скасований.", show_alert=True)
            return

        refunds = db.execute(
            """SELECT e.user_id,COALESCE(SUM(c.contributed_stars),0) AS stars
               FROM raffle_entries e
               LEFT JOIN raffle_contributions c
                 ON c.raffle_id=e.raffle_id AND c.user_id=e.user_id
               WHERE e.raffle_id=?
               GROUP BY e.user_id""",
            (raffle_id,),
        ).fetchall()
        cursor = db.execute(
            """UPDATE raffles SET status='cancelled'
               WHERE id=? AND status IN ('active','ready')""",
            (raffle_id,),
        )
        if cursor.rowcount != 1:
            db.rollback()
            await call.answer("Не вдалося скасувати цей розіграш.", show_alert=True)
            return
        for row in refunds:
            amount = int(row["stars"] or 0)
            if amount:
                db.execute(
                    "UPDATE users SET balance_stars=balance_stars+? WHERE id=?",
                    (amount, row["user_id"]),
                )
        db.commit()
    except Exception:
        db.rollback()
        raise

    await call.answer("Розіграш скасовано, внески повернуто.")
    total_refunded = sum(int(row["stars"] or 0) for row in refunds)
    await call.message.edit_text(
        f"✅ <b>Розіграш #{raffle_id} скасовано.</b>\n\n"
        f"Повернуто учасникам: <b>{total_refunded} Stars</b>.",
        reply_markup=raffle_admin_kb(None),
    )
    for row in refunds:
        try:
            amount = int(row["stars"] or 0)
            if amount:
                message = (
                    f"↩️ Розіграш #{raffle_id} скасовано. "
                    f"На ваш баланс повернуто <b>{amount} Stars</b>."
                )
            else:
                message = (
                    f"ℹ️ Розіграш #{raffle_id} скасовано. "
                    "Ваш квиток не мав внеску, тому баланс не змінювався."
                )
            await bot.send_message(
                int(row["user_id"]),
                message,
                reply_markup=main_menu(int(row["user_id"])),
            )
        except Exception as exc:
            print(
                f"Unable to notify raffle participant {row['user_id']} "
                f"about cancellation of raffle {raffle_id}: {exc}",
                flush=True,
            )


@dp.callback_query(F.data.startswith("raffle:draw:"))
async def raffle_draw(call: CallbackQuery):
    await call.answer("Проведення розіграшів вимкнено.", show_alert=True)
    return
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Немає доступу.", show_alert=True)
        return
    raffle_id = int(call.data.split(":")[2])
    await call.answer()

    try:
        db.execute("BEGIN IMMEDIATE")
        raffle = db.execute("SELECT * FROM raffles WHERE id=?", (raffle_id,)).fetchone()
        if not raffle or raffle["status"] != "ready" or raffle["pool_stars"] < raffle["target_stars"]:
            db.rollback()
            await call.message.answer("❌ Розіграш ще не досяг цілі або вже оброблений.")
            return
        participants = db.execute(
            "SELECT user_id FROM raffle_entries WHERE raffle_id=? ORDER BY joined_at,user_id",
            (raffle_id,),
        ).fetchall()
        if not participants:
            db.rollback()
            await call.message.answer("❌ У розіграші ще немає учасників.")
            return

        participant_ids = [int(row["user_id"]) for row in participants]
        winner_id = secrets.choice(participant_ids)
        prize_stars = int(raffle["pool_stars"])
        db.execute(
            "UPDATE users SET balance_stars=balance_stars+? WHERE id=?",
            (prize_stars, winner_id),
        )
        db.execute(
            """UPDATE raffles SET status='completed',winner_id=?,prize_stars=?,completed_at=?
               WHERE id=? AND status='ready'""",
            (winner_id, prize_stars, now(), raffle_id),
        )
        winner = user_row(winner_id)
        winner_balance = int(winner["balance_stars"])
        db.commit()
    except Exception:
        db.rollback()
        raise

    winner_label = f"@{winner['username']}" if winner["username"] else f"ID {winner_id}"
    await call.message.edit_text(
        f"🏆 <b>Розіграш #{raffle_id} завершено!</b>\n\n"
        f"Переможець: <b>{esc(winner_label)}</b>\n"
        f"Приз зараховано: <b>{prize_stars} Stars</b>\n"
        f"Учасників: <b>{len(participant_ids)}</b>",
        reply_markup=admin_menu_kb(),
    )
    try:
        await bot.send_message(
            winner_id,
            f"🎉 <b>Вітаємо! Ви перемогли в розіграші #{raffle_id}!</b>\n\n"
            f"⭐ На ваш баланс зараховано <b>{prize_stars} Stars</b>.\n"
            f"Поточний баланс: <b>{winner_balance} Stars</b>.",
            reply_markup=main_menu(winner_id),
        )
    except Exception:
        pass

    for participant_id in participant_ids:
        if participant_id == winner_id:
            continue
        try:
            await bot.send_message(
                participant_id,
                f"🏁 Розіграш #{raffle_id} завершено.\n"
                f"Переможець: <b>{esc(winner_label)}</b>.\n"
                f"Приз <b>{prize_stars} Stars</b> зараховано на його баланс.",
            )
        except Exception:
            pass


@dp.callback_query(F.data == "adm:stars_rate")
async def adm_stars_rate(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.answer()
    await state.set_state(Form.stars_rate)
    await call.message.answer(
        f"⭐ Поточний курс Stars: <b>{setting('stars_rate')} грн</b>\nВведіть новий курс:",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.stars_rate)
async def set_stars_rate(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        value = float(message.text.replace(",", "."))
        if not math.isfinite(value) or value <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введіть коректне додатне число.")
        return
    set_setting("stars_rate", value)
    await refresh_stars_catalog_screens()
    await state.clear()
    await message.answer(
        f"{pe(EMOJI_POOL[5], '✅')} Курс Stars успішно оновлено: <b>{value:g} грн</b>",
        reply_markup=main_menu(message.from_user.id),
    )


@dp.callback_query(F.data == "adm:ton_rate")
async def adm_ton_rate(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.answer()
    await state.set_state(Form.ton_rate)
    await call.message.answer(
        f"💎 Поточний курс TON: <b>{setting('ton_rate')} грн</b>\nВведіть новий курс:",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.ton_rate)
async def set_ton_rate(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        value = float(message.text.replace(",", "."))
        if not math.isfinite(value) or value <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введіть коректне додатне число.")
        return
    set_setting("ton_rate", value)
    await state.clear()
    await message.answer(
        f"{pe(EMOJI_POOL[5], '✅')} Курс TON успішно оновлено: <b>{value:g} грн</b>",
        reply_markup=main_menu(message.from_user.id),
    )


@dp.callback_query(F.data == "adm:ton_payment_rate")
async def adm_ton_payment_rate(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.answer()
    await state.set_state(Form.ton_payment_rate)
    await call.message.answer(
        f"🪙 Поточний курс TON для оплати: "
        f"<b>{setting('ton_payment_rate')} грн за 1 TON</b>\n"
        "Введіть окремий курс для оплат через Tonkeeper та CryptoBot:",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.ton_payment_rate)
async def set_ton_payment_rate(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        value = float(message.text.replace(",", "."))
        if not math.isfinite(value) or value <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введіть коректне додатне число.")
        return
    set_setting("ton_payment_rate", value)
    await state.clear()
    await message.answer(
        f"{pe(EMOJI_POOL[5], '✅')} Курс TON для криптооплати оновлено: "
        f"<b>{value:g} грн за 1 TON</b>",
        reply_markup=admin_menu_kb(),
    )


@dp.callback_query(F.data == "adm:usdt_rate")
async def adm_usdt_rate(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.answer()
    await state.set_state(Form.usdt_rate)
    await call.message.answer(
        f"💵 Поточний курс USDT: <b>{setting('usdt_rate')} грн</b>\nВведіть новий курс:",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.usdt_rate)
async def set_usdt_rate(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        value = float(message.text.replace(",", "."))
        if not math.isfinite(value) or value <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введіть коректне додатне число.")
        return
    set_setting("usdt_rate", value)
    await state.clear()
    await message.answer(
        f"{pe(EMOJI_POOL[5], '✅')} Курс USDT успішно оновлено: <b>{value:g} грн</b>",
        reply_markup=admin_menu_kb(),
    )


@dp.callback_query(F.data == "adm:referral_percent")
async def adm_referral_percent(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.answer()
    await state.set_state(Form.referral_percent)
    await call.message.answer(
        f"👥 <b>Реферальная ставка</b>\n\n"
        f"Сейчас: <b>{referral_percent():g}%</b>\n"
        "Введите новый процент от 0 до 100.\n"
        "Изменение применяется только к новым подтверждённым покупкам.",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.referral_percent)
async def set_referral_percent(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        value = float((message.text or "").replace(",", "."))
        if not math.isfinite(value) or value < 0 or value > 100:
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        await message.answer("❌ Введите процент от 0 до 100.")
        return
    set_setting("referral_percent", value)
    await state.clear()
    await message.answer(
        f"✅ Реферальная ставка изменена на <b>{value:g}%</b>.",
        reply_markup=admin_menu_kb(),
    )


@dp.callback_query(F.data == "adm:support")
async def adm_support(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.answer()
    await state.set_state(Form.support_setting)
    await call.message.answer(
        f"🆘 Поточний контакт підтримки: <b>{esc(setting('support'))}</b>\nВведіть новий @username:",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.support_setting)
async def set_support(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    value = message.text.strip()
    if not value.startswith("@"):
        await message.answer("❌ Вкажіть username у форматі @username.")
        return
    set_setting("support", value)
    await state.clear()
    await message.answer(
        "✅ Контакт підтримки оновлено.",
        reply_markup=main_menu(message.from_user.id),
    )


@dp.callback_query(F.data == "adm:reviews")
async def adm_reviews(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.answer()
    await call.message.answer(
        "💬 <b>Налаштування відгуків</b>\n\n"
        f"Канал: <code>{esc(setting('reviews_url'))}</code>\n"
        f"Наступний номер: <b>{review_next_number()}</b>\n\n"
        "Налаштуйте канал і номер або відкрийте чергу модерації.",
        reply_markup=review_admin_settings_kb(),
    )


@dp.callback_query(F.data == "adm:reviews:channel")
async def adm_review_channel_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Немає доступу.", show_alert=True)
        return
    await call.answer()
    await state.set_state(Form.reviews_setting)
    await call.message.answer(
        "💬 <b>Канал відгуків</b>\n\n"
        f"Поточне посилання:\n<code>{esc(setting('reviews_url'))}</code>\n\n"
        "Надішліть @username каналу або посилання, наприклад:\n"
        "<code>@spanix_reviews</code>\n"
        "<code>https://t.me/spanix_reviews</code>\n\n"
        "Перед збереженням додайте бота адміністратором каналу з правом публікувати повідомлення.",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.reviews_setting)
async def set_reviews(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    if not message.text:
        await message.answer("❌ Надішліть username або посилання на Telegram-канал.")
        return
    url = normalize_reviews_url(message.text)
    if not url:
        await message.answer("❌ Не вдалося розпізнати канал.\nВикористайте @username або посилання https://t.me/...")
        return
    try:
        await resolve_reviews_channel(url)
    except ValueError as exc:
        await message.answer(f"❌ {esc(exc)}")
        return
    set_setting("reviews_url", url)
    await state.clear()
    await message.answer(
        "✅ Канал відгуків оновлено.",
        reply_markup=review_admin_settings_kb(),
    )


@dp.callback_query(F.data == "adm:reviews:number")
async def adm_review_number_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Немає доступу.", show_alert=True)
        return
    await call.answer()
    await state.set_state(Form.review_number_setting)
    await call.message.answer(
        "🔢 <b>Нумерація відгуків</b>\n\n"
        f"Наступний номер зараз: <b>{review_next_number()}</b>\n"
        "Надішліть номер, з якого починати наступну публікацію. "
        "Наприклад: <code>15</code>. Бот використає саме цей номер, "
        "навіть якщо він уже зустрічався.",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.review_number_setting)
async def set_review_number(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    value = (message.text or "").strip()
    if not value.isdigit() or not 1 <= int(value) <= 2_000_000_000:
        await message.answer("❌ Надішліть ціле число від 1 до 2 000 000 000.")
        return
    number = int(value)
    set_setting("review_next_number", number)
    await state.clear()
    await message.answer(
        f"✅ Наступний опублікований відгук отримає номер <b>{number}</b>.\n"
        "Нумерація далі продовжиться з наступного числа.",
        reply_markup=review_admin_settings_kb(),
    )


@dp.callback_query(F.data == "adm:reviews:queue")
async def adm_review_queue(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Немає доступу.", show_alert=True)
        return
    await call.answer()
    pending_count = db.execute(
        """SELECT COUNT(*) AS total FROM customer_reviews
           WHERE status IN ('pending_moderation','publish_failed')"""
    ).fetchone()["total"]
    if not pending_count:
        await call.message.answer(
            "✅ Черга модерації порожня.",
            reply_markup=review_admin_settings_kb(),
        )
        return
    reviews = db.execute(
        """SELECT order_id,status,publish_error
           FROM customer_reviews
           WHERE status IN ('pending_moderation','publish_failed')
           ORDER BY created_at,order_id LIMIT 20"""
    ).fetchall()
    await call.message.answer(
        f"📝 <b>Черга модерації: {pending_count}</b>\n"
        "Відгуки надіслані нижче. Якщо список більший за 20, "
        "повторно відкрийте чергу після обробки цієї сторінки."
    )
    for review in reviews:
        if review["status"] == "pending_moderation":
            await send_review_moderation_notice(
                review["order_id"],
                recipient_ids=[call.from_user.id],
            )
        else:
            await bot.send_message(
                call.from_user.id,
                f"⚠️ Публікація відгуку для замовлення "
                f"#{review['order_id']} не вдалася.\n"
                f"<code>{esc(review['publish_error'] or 'Невідома помилка')}</code>",
                reply_markup=review_retry_kb(review["order_id"]),
            )


@dp.callback_query(F.data.startswith("review:rate:"))
async def review_rate(call: CallbackQuery):
    try:
        _, _, order_id_text, rating_text = call.data.split(":")
        order_id = int(order_id_text)
        rating = int(rating_text)
    except (AttributeError, TypeError, ValueError):
        await call.answer("Некоректна оцінка.", show_alert=True)
        return
    if not 1 <= rating <= 5:
        await call.answer("Оцінка має бути від 1 до 5.", show_alert=True)
        return
    cursor = db.execute(
        """UPDATE customer_reviews
           SET rating=?,
               status=CASE
                   WHEN NULLIF(TRIM(COALESCE(comment,'')),'') IS NOT NULL
                       THEN 'pending_moderation'
                   ELSE 'waiting_comment'
               END
           WHERE order_id=? AND user_id=? AND status='waiting_rating'""",
        (rating, order_id, call.from_user.id),
    )
    db.commit()
    if cursor.rowcount != 1:
        await clear_review_inline_keyboard(call)
        await call.answer("Цей запит на відгук уже недоступний.", show_alert=True)
        return
    # Removing the buttons prevents a second tap from submitting a stale
    # rating after the review has advanced to its comment step.
    await clear_review_inline_keyboard(call)
    await call.answer(f"Оцінка: {rating}/5")
    review = db.execute(
        "SELECT status FROM customer_reviews WHERE order_id=? AND user_id=?",
        (order_id, call.from_user.id),
    ).fetchone()
    if review and review["status"] == "pending_moderation":
        await send_review_moderation_notice(order_id)
        await notify_review_submitted(call.from_user.id)
        await send_pending_review_prompt(call.from_user.id)
        return
    await bot.send_message(
        call.from_user.id,
        review_comment_prompt(call.from_user.id),
        reply_markup=review_repurchase_kb(order_id),
    )


async def resend_required_review_step(order_id, user_id):
    review = db.execute(
        "SELECT status FROM customer_reviews WHERE order_id=? AND user_id=?",
        (order_id, user_id),
    ).fetchone()
    if not review:
        return False
    status = review["status"]
    if status == "waiting_screenshot":
        db.execute(
            """UPDATE customer_reviews SET status='waiting_rating'
               WHERE order_id=? AND user_id=? AND status='waiting_screenshot'""",
            (order_id, user_id),
        )
        db.commit()
        status = "waiting_rating"
    if status == "waiting_rating":
        await bot.send_message(
            user_id,
            review_rating_prompt(user_id),
            reply_markup=review_rating_kb(order_id, user_id),
        )
    elif status == "waiting_comment":
        await bot.send_message(
            user_id,
            review_comment_prompt(user_id),
        )
    else:
        return False
    return True


@dp.callback_query(F.data.startswith("review:skip_photo:"))
async def review_skip_photo(call: CallbackQuery):
    try:
        order_id = int(call.data.rsplit(":", 1)[1])
    except (AttributeError, TypeError, ValueError):
        await call.answer("Некоректний запит.", show_alert=True)
        return
    await clear_review_inline_keyboard(call)
    if not await resend_required_review_step(order_id, call.from_user.id):
        await call.answer("Цей запит на відгук уже недоступний.", show_alert=True)
        return
    await call.answer("Фото не потрібне — оберіть оцінку.")


@dp.callback_query(F.data.startswith("review:skip_all:"))
async def review_skip_all(call: CallbackQuery):
    try:
        order_id = int(call.data.rsplit(":", 1)[1])
    except (AttributeError, TypeError, ValueError):
        await call.answer("Некоректний запит.", show_alert=True)
        return
    await clear_review_inline_keyboard(call)
    if not await resend_required_review_step(order_id, call.from_user.id):
        await call.answer("Цей запит на відгук уже недоступний.", show_alert=True)
        return
    await call.answer("Оцініть замовлення й завершіть відгук.")


@dp.callback_query(F.data.startswith("review:skip:"))
async def review_skip_comment(call: CallbackQuery):
    try:
        order_id = int(call.data.rsplit(":", 1)[1])
    except (AttributeError, TypeError, ValueError):
        await call.answer("Некоректний запит.", show_alert=True)
        return
    review = db.execute(
        "SELECT status FROM customer_reviews WHERE order_id=? AND user_id=?",
        (order_id, call.from_user.id),
    ).fetchone()
    if not review or review["status"] != "waiting_comment":
        await clear_review_inline_keyboard(call)
        await call.answer("Цей запит на відгук уже недоступний.", show_alert=True)
        return
    await clear_review_inline_keyboard(call)
    await call.answer(
        "Коментар обов’язковий. Надішліть його одним повідомленням.",
        show_alert=True,
    )
    await bot.send_message(
        call.from_user.id,
        review_comment_prompt(call.from_user.id),
        reply_markup=review_repurchase_kb(order_id),
    )


@dp.callback_query(F.data.startswith("review:approve:"))
async def review_approve(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    try:
        order_id = int(call.data.rsplit(":", 1)[1])
    except (AttributeError, TypeError, ValueError):
        await call.answer("Некоректний номер замовлення.", show_alert=True)
        return
    review = db.execute(
        "SELECT status FROM customer_reviews WHERE order_id=?",
        (order_id,),
    ).fetchone()
    if not review or review["status"] not in {"pending_moderation", "publish_failed"}:
        await call.answer("Цей відгук уже оброблено.", show_alert=True)
        await clear_review_inline_keyboard(call)
        return
    first = db.execute(
        """SELECT order_id,status FROM customer_reviews
           WHERE status IN ('pending_moderation','publish_failed')
           ORDER BY created_at,order_id LIMIT 1"""
    ).fetchone()
    if first and int(first["order_id"]) != order_id:
        await call.answer(
            f"Спочатку обробіть відгук #{first['order_id']} — "
            "нумерація публікацій іде послідовно.",
            show_alert=True,
        )
        return
    await call.answer("Публікую відгук…")
    await clear_review_inline_keyboard(call)
    published = await publish_review(order_id)
    if not published:
        current = db.execute(
            "SELECT status FROM customer_reviews WHERE order_id=?",
            (order_id,),
        ).fetchone()
        if current and current["status"] == "publish_failed":
            try:
                await call.message.answer(
                    f"⚠️ Публікація відгуку для замовлення #{order_id} не вдалася. "
                    "Можна повторити після виправлення налаштувань.",
                    reply_markup=review_retry_kb(order_id),
                )
            except Exception:
                pass


@dp.callback_query(F.data.startswith("review:reject:"))
async def review_reject(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    try:
        order_id = int(call.data.rsplit(":", 1)[1])
    except (AttributeError, TypeError, ValueError):
        await call.answer("Некоректний номер замовлення.", show_alert=True)
        return
    cursor = db.execute(
        """UPDATE customer_reviews SET status='rejected'
           WHERE order_id=? AND status='pending_moderation'""",
        (order_id,),
    )
    db.commit()
    if cursor.rowcount != 1:
        await call.answer("Цей відгук уже оброблено.", show_alert=True)
        await clear_review_inline_keyboard(call)
        return
    await clear_review_inline_keyboard(call)
    await call.answer("Відгук відхилено.")
    review = db.execute(
        "SELECT user_id FROM customer_reviews WHERE order_id=?",
        (order_id,),
    ).fetchone()
    if review:
        try:
            await bot.send_message(
                review["user_id"],
                review_text(
                    review["user_id"],
                    "Ваш відгук не опубліковано після модерації. Ви можете й надалі користуватися ботом.",
                    "Ваш отзыв не опубликован после модерации. Вы можете продолжать пользоваться ботом.",
                    "Your review was not published after moderation. You can continue using the bot.",
                ),
            )
        except Exception:
            pass


@dp.callback_query(F.data.startswith("review:retry:"))
async def review_retry(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    try:
        order_id = int(call.data.rsplit(":", 1)[1])
    except (AttributeError, TypeError, ValueError):
        await call.answer("Некоректний запит.", show_alert=True)
        return
    review = db.execute(
        "SELECT status FROM customer_reviews WHERE order_id=?",
        (order_id,),
    ).fetchone()
    if not review or review["status"] != "publish_failed":
        await call.answer("Цей відгук не очікує повторної публікації.", show_alert=True)
        return
    await call.answer("Повторюю публікацію…")
    await publish_review(order_id)
    review = db.execute(
        "SELECT status FROM customer_reviews WHERE order_id=?",
        (order_id,),
    ).fetchone()
    if review and review["status"] == "published":
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass


@dp.callback_query(F.data == "adm:bank_details")
async def adm_bank_details(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.answer()
    await call.message.answer(
        "💳 <b>Реквізити карток</b>\n\nОберіть банк, щоб переглянути або змінити реквізити:",
        reply_markup=bank_details_kb(),
    )


@dp.callback_query(F.data.startswith("adm_bank:"))
async def adm_bank_choose(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.answer()
    method = call.data.split(":", 1)[1]
    bank_names = {code: name for name, code, _ in BANK_OPTIONS}
    if method not in bank_names:
        await call.message.answer("❌ Банк не знайдено.")
        return
    current = setting(f"bank_{method}") or "не налаштовані"
    await state.update_data(bank_method=method)
    await state.set_state(Form.bank_details)
    set_setting(f"pending_bank:{call.from_user.id}", method)
    await call.message.answer(
        f"💳 <b>{bank_names[method]}</b>\n\n"
        f"Поточні реквізити:\n<code>{esc(current)}</code>\n\n"
        "Надішліть нові реквізити одним повідомленням.\n"
        "Можна вказати номер картки, ім'я отримувача та додаткову інформацію.",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.bank_details)
async def set_bank_details(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    value = (message.text or "").strip()
    if len(value) < 3:
        await message.answer("❌ Вкажіть коректні реквізити одним повідомленням.")
        return
    data = await state.get_data()
    pending_key = f"pending_bank:{message.from_user.id}"
    method = data.get("bank_method") or setting(pending_key)
    if not method:
        await state.clear()
        await message.answer("❌ Не вдалося визначити банк.")
        return
    set_setting(f"bank_{method}", value)
    delete_setting(pending_key)
    await state.clear()
    await message.answer(
        "✅ Реквізити збережено. Вони вже використовуються в нових замовленнях.",
        reply_markup=admin_menu_kb(),
    )


@dp.callback_query(F.data == "adm:payment_settings")
async def adm_payment_settings(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.answer()
    crypto_status = "налаштований" if crypto_payment_configured() else "не налаштований"
    ton_api_status = (
        "налаштований"
        if payment_setting_configured("TONCENTER_API_KEY")
        else "не налаштований"
    )
    ton_wallet_status = (
        "налаштований"
        if payment_setting_configured("TON_WALLET_ADDRESS")
        else "не налаштований"
    )
    await call.message.answer(
        "💳 <b>Платіжні сервіси</b>\n\n"
        f"CryptoBot: <b>{crypto_status}</b>\n"
        f"TON Center API: <b>{ton_api_status}</b> (обов'язково для Tonkeeper)\n"
        f"TON-гаманець: <b>{ton_wallet_status}</b>\n\n"
        "Для Tonkeeper обов'язково вказати і адресу TON-гаманця, і ключ TON Center API. "
        "Тільки після цього бот покаже оплату через Tonkeeper та зробить автоматичну перевірку. "
        "Значення зберігаються зашифровано та не показуються в чаті.",
        reply_markup=payment_settings_kb(),
    )


@dp.callback_query(F.data == "adm:payment:methods")
async def adm_payment_methods(
    call: CallbackQuery,
    state: FSMContext | None = None,
    *,
    answer_callback: bool = True,
):
    if call.from_user.id not in ADMIN_IDS:
        return
    if answer_callback:
        await call.answer()
    if state is not None:
        await state.clear()
    methods = db.execute(
        "SELECT id,name,method_type,active FROM payment_methods ORDER BY id"
    ).fetchall()
    if methods:
        summary = "\n".join(
            f"• <b>{esc(row['name'])}</b> — {esc(row['method_type'])}, "
            f"{'увімкнено' if row['active'] else 'вимкнено'}"
            for row in methods
        )
    else:
        summary = "Інші способи оплати ще не додані."
    await call.message.edit_text(
        "💳 <b>Інші способи оплати</b>\n\n"
        f"{summary}\n\n"
        "Додавайте банки, криптовалюти або будь-які ручні способи. "
        "Клієнт побачить реквізити та зможе надіслати квитанцію на ручну перевірку.",
        reply_markup=payment_methods_admin_kb(),
    )


def parse_payment_bundle(raw_text):
    aliases = {
        "cryptobot": "CRYPTO_BOT_TOKEN",
        "cryptobot_token": "CRYPTO_BOT_TOKEN",
        "crypto_bot_token": "CRYPTO_BOT_TOKEN",
        "crypto_pay_api_token": "CRYPTO_BOT_TOKEN",
        "toncenter": "TONCENTER_API_KEY",
        "toncenter_api": "TONCENTER_API_KEY",
        "toncenter_api_key": "TONCENTER_API_KEY",
        "ton_api_key": "TONCENTER_API_KEY",
        "tonwallet": "TON_WALLET_ADDRESS",
        "ton_wallet": "TON_WALLET_ADDRESS",
        "ton_wallet_address": "TON_WALLET_ADDRESS",
        "wallet": "TON_WALLET_ADDRESS",
    }
    values = {}
    chunks = re.split(r"[\r\n;]+", raw_text or "")
    for chunk in chunks:
        line = chunk.strip()
        if not line:
            continue
        if "=" in line:
            key, value = line.split("=", 1)
        elif ":" in line:
            key, value = line.split(":", 1)
        else:
            continue
        normalized_key = re.sub(r"[^a-z0-9_]+", "_", key.strip().lower()).strip("_")
        setting_name = aliases.get(normalized_key)
        value = value.strip().strip("`'\"")
        if setting_name and value:
            values[setting_name] = value
    return values


@dp.callback_query(F.data == "adm:payment:bundle")
async def adm_payment_bundle_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.answer()
    await state.set_state(Form.payment_bundle)
    await call.message.answer(
        "⚡ <b>Налаштувати платежі одним повідомленням</b>\n\n"
        "Вставте потрібні рядки у форматі:\n"
        "<code>CRYPTO_BOT_TOKEN=ваш_токен</code>\n"
        "<code>TONCENTER_API_KEY=ваш_ключ</code>\n"
        "<code>TON_WALLET_ADDRESS=ваша_адреса</code>\n\n"
        "Для Tonkeeper потрібні обидва рядки: TONCENTER_API_KEY і TON_WALLET_ADDRESS. "
        "Повідомлення буде видалено після збереження.",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.payment_bundle)
async def save_payment_bundle(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    values = parse_payment_bundle(message.text or "")
    if not values:
        await message.answer(
            "❌ Не знайдено жодного параметра. Надішліть рядки "
            "CRYPTO_BOT_TOKEN=..., TONCENTER_API_KEY=... або TON_WALLET_ADDRESS=..."
        )
        return
    try:
        for setting_name, value in values.items():
            if len(value) < 6:
                raise ValueError(f"{setting_name} занадто короткий")
            save_payment_setting(setting_name, value, message.from_user.id)
    except (RuntimeError, ValueError) as exc:
        await message.answer(f"❌ Налаштування не збережено: {esc(str(exc))}")
        return
    try:
        await message.delete()
    except Exception:
        pass
    await state.clear()
    configured = []
    if payment_setting_configured("CRYPTO_BOT_TOKEN"):
        configured.append("CryptoBot")
    if ton_payment_configured():
        configured.append("Tonkeeper")
    elif (
        payment_setting_configured("TON_WALLET_ADDRESS")
        or payment_setting_configured("TONCENTER_API_KEY")
    ):
        configured.append("Tonkeeper: потрібні TONCENTER_API_KEY + TON_WALLET_ADDRESS")
    await message.answer(
        "✅ Платежі налаштовано без SESSION_SECRET.\n"
        f"Активовано: <b>{esc(', '.join(configured) or '—')}</b>\n"
        "Тепер клієнтам доступні автоматичні рахунки та перевірка оплати.",
        reply_markup=admin_menu_kb(),
    )


@dp.callback_query(F.data == "payment_method:add")
async def payment_method_add_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.answer()
    await state.set_state(Form.payment_method_name)
    await call.message.answer(
        "➕ <b>Новий спосіб оплати</b>\n\n"
        "Надішліть назву та тип одним повідомленням у форматі:\n"
        "<code>Назва | bank</code>\n\n"
        "Доступні типи: <code>bank</code>, <code>crypto</code>, <code>other</code>.",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.payment_method_name)
async def payment_method_name_save(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = [part.strip() for part in (message.text or "").split("|", 1)]
    name = parts[0] if parts else ""
    method_type = parts[1].lower() if len(parts) == 2 else "other"
    if not name or len(name) > 60 or method_type not in {"bank", "crypto", "other"}:
        await message.answer(
            "❌ Формат: <code>Назва | bank</code>. "
            "Тип має бути bank, crypto або other."
        )
        return
    await state.update_data(payment_method_name=name, payment_method_type=method_type)
    await state.set_state(Form.payment_method_details)
    await message.answer(
        f"✅ Назву «{esc(name)}» збережено.\n\n"
        "Тепер надішліть реквізити або інструкцію для клієнта одним повідомленням. "
        "Наприклад: номер картки, адресу гаманця, мережу та коментар.",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.payment_method_details)
async def payment_method_details_save(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    details = (message.text or "").strip()
    if len(details) < 3 or len(details) > 4000:
        await message.answer("❌ Реквізити мають містити від 3 до 4000 символів.")
        return
    data = await state.get_data()
    method_id = data.get("payment_method_id")
    if method_id:
        existing = db.execute(
            "SELECT name,method_type FROM payment_methods WHERE id=?",
            (method_id,),
        ).fetchone()
        if not existing:
            await state.clear()
            await message.answer(
                "❌ Спосіб оплати не знайдено.",
                reply_markup=payment_methods_admin_kb(),
            )
            return
        name = existing["name"]
        method_type = existing["method_type"]
    else:
        name = data.get("payment_method_name")
        method_type = data.get("payment_method_type", "other")
    try:
        save_payment_method(name, method_type, details, method_id=method_id)
    except (RuntimeError, sqlite3.IntegrityError) as exc:
        await message.answer(
            "❌ Не вдалося зберегти спосіб оплати. "
            "Перевірте, що назва не дублюється.\n"
            f"<code>{esc(str(exc))}</code>"
        )
        return
    try:
        await message.delete()
    except Exception:
        pass
    await state.clear()
    await message.answer(
        "✅ Спосіб оплати додано. Він уже доступний клієнтам.",
        reply_markup=payment_methods_admin_kb(),
    )


@dp.callback_query(F.data.startswith("payment_method:edit:"))
async def payment_method_edit(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    method_id = int(call.data.rsplit(":", 1)[1])
    method = db.execute(
        "SELECT id,name,method_type,active FROM payment_methods WHERE id=?",
        (method_id,),
    ).fetchone()
    if not method:
        await call.answer("Спосіб оплати не знайдено.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        f"💳 <b>{esc(method['name'])}</b>\n\n"
        f"Тип: <b>{esc(method['method_type'])}</b>\n"
        f"Статус: <b>{'увімкнено' if method['active'] else 'вимкнено'}</b>\n\n"
        "Реквізити приховані та зберігаються зашифровано.",
        reply_markup=payment_method_edit_kb(method_id),
    )


@dp.callback_query(F.data.startswith("payment_method:update:"))
async def payment_method_update_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    method_id = int(call.data.rsplit(":", 1)[1])
    method = db.execute(
        "SELECT id,name FROM payment_methods WHERE id=?", (method_id,)
    ).fetchone()
    if not method:
        await call.answer("Спосіб оплати не знайдено.", show_alert=True)
        return
    await call.answer()
    await state.update_data(payment_method_id=method_id)
    await state.set_state(Form.payment_method_details)
    await call.message.answer(
        f"✏️ Надішліть нові реквізити для «{esc(method['name'])}».",
        reply_markup=cancel_kb(),
    )


@dp.callback_query(F.data.startswith("payment_method:toggle:"))
async def payment_method_toggle(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    method_id = int(call.data.rsplit(":", 1)[1])
    db.execute(
        "UPDATE payment_methods SET active=CASE active WHEN 1 THEN 0 ELSE 1 END, updated_at=? WHERE id=?",
        (now(), method_id),
    )
    db.commit()
    await call.answer("Статус способу оплати оновлено.")
    method_id = int(call.data.rsplit(":", 1)[1])
    method = db.execute(
        "SELECT id,name,method_type,active FROM payment_methods WHERE id=?",
        (method_id,),
    ).fetchone()
    if method:
        await call.message.edit_text(
            f"💳 <b>{esc(method['name'])}</b>\n\n"
            f"Тип: <b>{esc(method['method_type'])}</b>\n"
            f"Статус: <b>{'увімкнено' if method['active'] else 'вимкнено'}</b>\n\n"
            "Реквізити приховані та зберігаються зашифровано.",
            reply_markup=payment_method_edit_kb(method_id),
        )


@dp.callback_query(F.data.startswith("payment_method:delete:"))
async def payment_method_delete(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    method_id = int(call.data.rsplit(":", 1)[1])
    db.execute("DELETE FROM payment_methods WHERE id=?", (method_id,))
    db.commit()
    await call.answer("Спосіб оплати видалено.")
    await adm_payment_methods(call, answer_callback=False)


@dp.callback_query(F.data == "adm:payment:crypto")
async def adm_payment_crypto(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.answer()
    await state.set_state(Form.payment_crypto_token)
    await call.message.answer(
        "🤖 Надішліть Crypto Pay API Token одним повідомленням.\n"
        "Його можна отримати у @CryptoBot → Crypto Pay → My Apps.",
        reply_markup=cancel_kb(),
    )


@dp.callback_query(F.data == "adm:payment:ton_api")
async def adm_payment_ton_api(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.answer()
    await state.set_state(Form.payment_ton_api_key)
    await call.message.answer(
        "🪙 Надішліть ключ TON Center API одним повідомленням.\n\n"
        "Це поле обов'язкове для автоматичної перевірки платежів через Tonkeeper. "
        "Без нього кнопка Tonkeeper у способах оплати не активується.",
        reply_markup=cancel_kb(),
    )


@dp.callback_query(F.data == "adm:payment:ton_wallet")
async def adm_payment_ton_wallet(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.answer()
    await state.set_state(Form.payment_ton_wallet)
    await call.message.answer(
        "🪙 Надішліть адресу TON-гаманця для отримання платежів.",
        reply_markup=cancel_kb(),
    )


async def save_payment_secret_from_message(message, state, setting_name, label):
    if message.from_user.id not in ADMIN_IDS:
        return
    value = (message.text or "").strip()
    if len(value) < 6:
        await message.answer("❌ Значення занадто коротке. Спробуйте ще раз.")
        return
    try:
        save_payment_setting(setting_name, value, message.from_user.id)
    except RuntimeError as exc:
        await message.answer(f"❌ {esc(str(exc))}")
        return
    try:
        await message.delete()
    except Exception:
        pass
    await state.clear()
    await message.answer(
        f"✅ {label} збережено. Значення не показується в адмін-панелі.",
        reply_markup=admin_menu_kb(),
    )


@dp.message(Form.payment_crypto_token)
async def save_crypto_token(message: Message, state: FSMContext):
    await save_payment_secret_from_message(
        message,
        state,
        "CRYPTO_BOT_TOKEN",
        "Токен CryptoBot",
    )


@dp.message(Form.payment_ton_api_key)
async def save_ton_api_key(message: Message, state: FSMContext):
    await save_payment_secret_from_message(
        message,
        state,
        "TONCENTER_API_KEY",
        "Ключ TON Center API",
    )


@dp.message(Form.payment_ton_wallet)
async def save_ton_wallet(message: Message, state: FSMContext):
    await save_payment_secret_from_message(
        message,
        state,
        "TON_WALLET_ADDRESS",
        "Адресу TON-гаманця",
    )


@dp.callback_query(F.data == "adm:broadcast")
async def adm_broadcast(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.answer()
    await state.set_state(Form.broadcast)
    await call.message.answer(
        "📢 Надішліть повідомлення для розсилки: текст, фото або відео з підписом.",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.broadcast)
async def do_broadcast(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    users = db.execute("SELECT id FROM users").fetchall()
    ok = 0
    for row in users:
        try:
            await bot.copy_message(chat_id=row["id"], from_chat_id=message.chat.id, message_id=message.message_id)
            ok += 1
            await asyncio.sleep(0.04)
        except Exception:
            pass
    await state.clear()
    await message.answer(
        f"📢 Розсилку завершено.\n✅ Доставлено: {ok}",
        reply_markup=main_menu(message.from_user.id),
    )


@dp.callback_query(F.data == "adm:balance")
async def adm_balance(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Немає доступу.", show_alert=True)
        return
    await call.answer()
    await state.clear()
    await state.set_state(Form.balance_target)
    await call.message.answer(
        "⭐ <b>Зміна балансу Stars</b>\n\n"
        "Надішліть числовий Telegram ID або username користувача "
        "(наприклад, <code>123456789</code> чи <code>@username</code>).\n"
        "Користувач має хоча б раз відкрити бота командою /start.",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.balance_target)
async def balance_target(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    if not message.text:
        await message.answer("❌ Надішліть ID або username текстом.")
        return
    target = find_user_by_identifier(message.text)
    if not target:
        await message.answer(
            "❌ Користувача не знайдено. Перевірте ID/username або попросіть його "
            "спочатку надіслати боту /start."
        )
        return

    await state.update_data(balance_target_id=target["id"])
    username = f"@{esc(target['username'])}" if target["username"] else esc(target["first_name"] or "без username")
    b = InlineKeyboardBuilder()
    b.row(
        kb_button("Додати Stars", emoji_id=EMOJI_POOL[0], style="success", callback_data="balance_action:add"),
        kb_button("Забрати Stars", emoji_id=EMOJI_POOL[3], style="danger", callback_data="balance_action:remove"),
    )
    await message.answer(
        f"👤 Користувач: <b>{username}</b>\n"
        f"🆔 ID: <code>{target['id']}</code>\n"
        f"⭐ Поточний баланс: <b>{target['balance_stars']} Stars</b>\n\n"
        "Оберіть дію:",
        reply_markup=b.as_markup(),
    )


@dp.callback_query(F.data.startswith("balance_action:"))
async def balance_action(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Немає доступу.", show_alert=True)
        return
    data = await state.get_data()
    target_id = data.get("balance_target_id")
    action = call.data.split(":", 1)[1]
    if action not in {"add", "remove"} or not target_id or not user_row(target_id):
        await call.answer("Спочатку виберіть користувача.", show_alert=True)
        return

    await call.answer()
    await state.update_data(balance_action=action)
    await state.set_state(Form.balance_amount)
    verb = "додати" if action == "add" else "забрати"
    await call.message.answer(
        f"Введіть цілу кількість Stars, яку потрібно {verb}.",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.balance_amount)
async def balance_amount(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введіть додатне ціле число Stars.")
        return

    data = await state.get_data()
    target_id = data.get("balance_target_id")
    action = data.get("balance_action")
    if not target_id or action not in {"add", "remove"}:
        await state.clear()
        await message.answer("❌ Сеанс зміни балансу завершився. Почніть знову в адмін-панелі.")
        return

    db.execute("BEGIN IMMEDIATE")
    try:
        target = db.execute(
            "SELECT balance_stars FROM users WHERE id=?",
            (target_id,),
        ).fetchone()
        if not target:
            db.rollback()
            await state.clear()
            await message.answer("❌ Користувача більше немає в базі.")
            return

        before = int(target["balance_stars"] or 0)
        if action == "remove" and amount > before:
            db.rollback()
            await message.answer(
                f"❌ У користувача лише <b>{before} Stars</b>. Введіть меншу кількість "
                "або натисніть «Скасувати»."
            )
            return

        after = before + amount if action == "add" else before - amount
        if after > 9_223_372_036_854_775_807:
            db.rollback()
            await message.answer("❌ Такий баланс завеликий.")
            return
        db.execute("UPDATE users SET balance_stars=? WHERE id=?", (after, target_id))
        db.execute(
            """INSERT INTO balance_adjustments
               (user_id,admin_id,action,amount,balance_before,balance_after,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (target_id, message.from_user.id, action, amount, before, after, now()),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    await state.clear()
    operation = "додано" if action == "add" else "забрано"
    notification_error = False
    try:
        await bot.send_message(
            target_id,
            f"⭐ Адміністратор змінив ваш баланс: {operation} <b>{amount} Stars</b>.\n"
            f"Поточний баланс: <b>{after} Stars</b>.",
        )
    except Exception:
        notification_error = True

    result = (
        f"✅ {operation.capitalize()} <b>{amount} Stars</b> користувачу "
        f"<code>{target_id}</code>.\n"
        f"Баланс: <b>{before} → {after} Stars</b>."
    )
    if notification_error:
        result += "\n⚠️ Баланс змінено, але повідомлення користувачу надіслати не вдалося."
    await message.answer(result, reply_markup=admin_menu_kb())


def admin_users_kb(page, has_next):
    b = InlineKeyboardBuilder()
    nav = []
    if page > 0:
        nav.append(kb_button("⬅️", callback_data=f"adm:users:page:{page-1}"))
    if has_next:
        nav.append(kb_button("➡️", callback_data=f"adm:users:page:{page+1}"))
    if nav:
        b.row(*nav)
    b.row(kb_button("📤 Експорт усіх користувачів CSV", style="success", callback_data="adm:users:export"))
    b.row(back_inline("admin:back", "⬅️ До адмін-панелі"))
    return b.as_markup()


async def render_admin_users(chat_id, page=0):
    page = max(0, int(page))
    page_size = 25
    count = db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    rows = db.execute(
        "SELECT id,username,first_name,balance_stars,registered_at FROM users ORDER BY id DESC LIMIT ? OFFSET ?",
        (page_size, page * page_size),
    ).fetchall()
    lines = []
    for user in rows:
        label = f"@{esc(user['username'])}" if user['username'] else esc(user['first_name'] or "без username")
        lines.append(
            f"• {label} — <code>{user['id']}</code> — {int(user['balance_stars'] or 0)} Stars"
        )
    listing = "\n".join(lines) if lines else "Користувачів поки немає."
    has_next = (page + 1) * page_size < count
    await bot.send_message(
        chat_id,
        f"👥 <b>Усього користувачів: {count}</b>\n"
        f"Сторінка: {page + 1} · Показано: {len(rows)}\n\n{listing}",
        reply_markup=admin_users_kb(page, has_next),
    )


@dp.callback_query(F.data == "adm:users")
async def adm_users(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    await call.answer()
    await render_admin_users(call.message.chat.id, 0)


@dp.callback_query(F.data.startswith("adm:users:page:"))
async def adm_users_page(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    try:
        page = max(0, int(call.data.rsplit(":", 1)[1]))
    except ValueError:
        await call.answer("Некоректна сторінка.", show_alert=True)
        return
    await call.answer()
    await render_admin_users(call.message.chat.id, page)


def export_users_csv():
    export_dir = PROJECT_DIR / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    export_path = export_dir / f"users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    rows = db.execute(
        "SELECT id,username,first_name,language,balance_stars,bought_stars,bought_ton,premium_months,spent_uah,invited,referrer_id,status,registered_at FROM users ORDER BY id"
    ).fetchall()
    headers = ["Telegram ID", "Username", "Ім'я", "Мова", "Баланс Stars", "Придбано Stars", "Придбано TON", "Premium місяців", "Витрачено UAH", "Запрошено", "Referrer ID", "Статус", "Реєстрація"]
    with export_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([row[key] for key in ("id", "username", "first_name", "language", "balance_stars", "bought_stars", "bought_ton", "premium_months", "spent_uah", "invited", "referrer_id", "status", "registered_at")])
    return export_path, len(rows)


@dp.callback_query(F.data == "adm:users:export")
async def adm_users_export(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    path, count = export_users_csv()
    await call.answer("CSV сформовано.")
    await bot.send_document(
        call.message.chat.id,
        FSInputFile(str(path)),
        caption=f"📤 Експорт користувачів: {count} записів.",
    )

RECEIPT_EXPORT_HEADERS = [
    "№",
    "№ замовлення",
    "Дата замовлення",
    "ID користувача",
    "Username",
    "Тип замовлення",
    "Кількість",
    "Сума замовлення, грн",
    "Спосіб оплати",
    "Статус замовлення",
    "Статус OCR",
    "№ квитанції",
    "Банк",
    "Очікуваний банк",
    "Перевірка банку",
    "ID транзакції",
    "ID операції",
    "RRN",
    "Статус платежу",
    "Дата/час операції",
    "Валюта",
    "Відправник",
    "Банк відправника",
    "Код банку відправника",
    "Рахунок відправника",
    "Картка відправника",
    "Платіжна система відправника",
    "Платіжний інструмент відправника",
    "Одержувач",
    "Банк одержувача",
    "Код банку одержувача",
    "Рахунок одержувача",
    "Картка одержувача",
    "Платіжна система одержувача",
    "Платіжний інструмент одержувача",
    "Сума з квитанції, грн",
    "Перевірка суми",
    "Комісія, грн",
    "Сума з комісією, грн",
    "Код авторизації",
    "Призначення платежу",
    "Коментар",
    "Торговець",
    "Ідентифікатор пристрою",
    "Тип файлу квитанції",
    "Ім'я файлу квитанції",
    "ID файлу квитанції",
    "Дата розбору OCR",
    "Помилка OCR",
    "Повний OCR текст",
]


def export_receipts_csv():
    export_dir = PROJECT_DIR / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    export_path = export_dir / f"receipts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    reset_at = setting("receipts_reset_at")
    receipt_filter = ""
    receipt_params = ()
    if reset_at:
        receipt_filter = " AND archive.created_at >= ?"
        receipt_params = (reset_at,)
    rows = db.execute(
        f"""SELECT orders.*, users.username,
                   archive.receipt_file_id AS archive_file_id,
                   archive.receipt_type AS archive_receipt_type,
                   archive.receipt_file_name AS archive_file_name,
                   archive.receipt_ocr_text AS archive_ocr_text,
                   archive.receipt_data_json AS archive_data_json,
                   archive.receipt_error AS archive_error,
                   archive.receipt_parsed_at AS archive_receipt_parsed_at,
                   archive.created_at AS archive_created_at
            FROM receipt_archive AS archive
            JOIN orders ON orders.id=archive.order_id
            LEFT JOIN users ON users.id=archive.user_id
            WHERE 1=1{receipt_filter}
            ORDER BY archive.id ASC""",
        receipt_params,
    ).fetchall()
    with export_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=RECEIPT_EXPORT_HEADERS)
        writer.writeheader()
        for row_number, order in enumerate(rows, start=1):
            try:
                receipt = json.loads(order["archive_data_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                receipt = {}
            writer.writerow({
                "№": row_number,
                "№ замовлення": order["id"],
                "Дата замовлення": order["archive_created_at"] or order["created_at"],
                "ID користувача": order["user_id"],
                "Username": f"@{order['username']}" if order["username"] else "",
                "Тип замовлення": order["order_type"],
                "Кількість": order["quantity"],
                "Сума замовлення, грн": f"{order['amount']:.2f}",
                "Спосіб оплати": order["payment_method"] or "",
                "Статус замовлення": order["status"],
                "Статус OCR": receipt.get("ocr_status", "Потрібна перевірка"),
                "№ квитанції": receipt.get("receipt_number", ""),
                "Банк": receipt.get("bank", ""),
                "Очікуваний банк": receipt.get("expected_bank", ""),
                "Перевірка банку": receipt.get("bank_match", ""),
                "ID транзакції": receipt.get("transaction_id", ""),
                "ID операції": receipt.get("operation_id", ""),
                "RRN": receipt.get("rrn", ""),
                "Статус платежу": receipt.get("status", ""),
                "Дата/час операції": receipt.get("operation_datetime", ""),
                "Валюта": receipt.get("currency", ""),
                "Відправник": receipt.get("sender_name", ""),
                "Банк відправника": receipt.get("sender_bank", ""),
                "Код банку відправника": receipt.get("sender_bank_code", ""),
                "Рахунок відправника": receipt.get("sender_account", ""),
                "Картка відправника": receipt.get("sender_card", ""),
                "Платіжна система відправника": receipt.get("sender_payment_system", ""),
                "Платіжний інструмент відправника": receipt.get("sender_instrument", ""),
                "Одержувач": receipt.get("receiver_name", ""),
                "Банк одержувача": receipt.get("receiver_bank", ""),
                "Код банку одержувача": receipt.get("receiver_bank_code", ""),
                "Рахунок одержувача": receipt.get("receiver_account", ""),
                "Картка одержувача": receipt.get("receiver_card", ""),
                "Платіжна система одержувача": receipt.get("receiver_payment_system", ""),
                "Платіжний інструмент одержувача": receipt.get("receiver_instrument", ""),
                "Сума з квитанції, грн": receipt.get("amount", ""),
                "Перевірка суми": receipt.get("amount_match", ""),
                "Комісія, грн": receipt.get("fee", ""),
                "Сума з комісією, грн": receipt.get("total_amount", ""),
                "Код авторизації": receipt.get("authorization_code", ""),
                "Призначення платежу": receipt.get("payment_purpose", ""),
                "Коментар": receipt.get("comment", ""),
                "Торговець": receipt.get("merchant", ""),
                "Ідентифікатор пристрою": receipt.get("device_id", ""),
                "Тип файлу квитанції": order["archive_receipt_type"] or "",
                "Ім'я файлу квитанції": order["archive_file_name"] or "",
                "ID файлу квитанції": order["archive_file_id"] or "",
                "Дата розбору OCR": order["archive_receipt_parsed_at"] or "",
                "Помилка OCR": order["archive_error"] or "",
                "Повний OCR текст": order["archive_ocr_text"] or "",
            })
    return export_path, len(rows)


async def send_pending_receipts(admin_chat_id):
    rows = db.execute(
        """SELECT orders.*, users.username, users.first_name
           FROM orders
           LEFT JOIN users ON users.id=orders.user_id
           WHERE orders.status='waiting_review'
             AND (
                 orders.receipt_file_id IS NOT NULL
                 OR orders.payment_method IN ('tonkeeper', 'cryptobot')
             )
           ORDER BY orders.id ASC"""
    ).fetchall()
    delivered = 0
    for order in rows:
        try:
            try:
                receipt_data = json.loads(order["receipt_data_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                receipt_data = {}
            username = f"@{esc(order['username'])}" if order["username"] else "без username"
            wallet_line = (
                f"🪙 TON-адреса для переказу: <code>{esc(order['ton_wallet'])}</code>\n"
                if order["order_type"] == "buy_ton" and order["ton_wallet"]
                else ""
            )
            text = (
                f"🧾 <b>Очікує підтвердження: замовлення #{order['id']}</b>\n\n"
                f"👤 {username}\n"
                f"🆔 <code>{order['user_id']}</code>\n"
                f"📦 {esc(order_type_title(order))}: <b>{esc(order_quantity_title(order))}</b>\n"
                f"{wallet_line}"
                f"💰 <b>{order['amount']:.2f} грн</b>\n"
                f"💳 {esc(order['payment_method'] or '—')}\n"
            )
            if order["payment_currency"] and order["payment_amount"]:
                text += (
                    f"🪙 Оплачено: <b>{order['payment_amount']:g} "
                    f"{esc(order['payment_currency'])}</b>\n"
                )
            text += f"🕒 {order['created_at']}\n\n"
            if order["receipt_file_id"]:
                text += receipt_summary(receipt_data, order["receipt_error"] or "")
            else:
                text += "Платіж перевірено через платіжний сервіс."

            markup = admin_order_kb(order["id"])
            if order["receipt_file_id"] and order["receipt_type"] == "photo":
                await bot.send_photo(
                    admin_chat_id,
                    photo=order["receipt_file_id"],
                    caption=text,
                    reply_markup=markup,
                )
            elif order["receipt_file_id"]:
                await bot.send_document(
                    admin_chat_id,
                    document=order["receipt_file_id"],
                    caption=text,
                    reply_markup=markup,
                )
            else:
                await bot.send_message(admin_chat_id, text, reply_markup=markup)
            delivered += 1
        except Exception as exc:
            print(f"Unable to send pending receipt #{order['id']}: {exc}", flush=True)
    return delivered


async def send_all_receipts(admin_chat_id):
    """Send every stored receipt file, oldest first, without an archive reset."""
    rows = db.execute(
        """SELECT archive.*, orders.order_type, orders.quantity, orders.amount,
                  orders.payment_method, orders.status AS order_status,
                  orders.claimed_by, users.username
           FROM receipt_archive AS archive
           JOIN orders ON orders.id=archive.order_id
           LEFT JOIN users ON users.id=archive.user_id
           ORDER BY archive.id ASC"""
    ).fetchall()
    delivered = 0
    for row in rows:
        text = (
            f"🧾 <b>Квитанція #{row['id']}</b>\n"
            f"📦 Замовлення: <b>#{row['order_id']}</b>\n"
            f"👤 @{esc(row['username'] or 'без_username')} "
            f"(<code>{row['user_id']}</code>)\n"
            f"💰 Сума замовлення: <b>{row['amount']:.2f} грн</b>\n"
            f"💳 Оплата: {esc(row['payment_method'] or '—')}\n"
            f"📊 Статус: <b>{esc(row['order_status'])}</b>\n"
            f"🕒 Отримано: {esc(row['created_at'])}\n\n"
            "🔎 Перевірка виконується вручну."
        )
        markup = (
            admin_order_kb(row["order_id"])
            if row["order_status"] == "waiting_review" and not row["claimed_by"]
            else None
        )
        try:
            if row["receipt_type"] == "photo":
                await bot.send_photo(
                    admin_chat_id,
                    photo=row["receipt_file_id"],
                    caption=text,
                    reply_markup=markup,
                )
            else:
                await bot.send_document(
                    admin_chat_id,
                    document=row["receipt_file_id"],
                    caption=text,
                    reply_markup=markup,
                )
            delivered += 1
            await asyncio.sleep(0.04)
        except Exception as exc:
            print(f"Unable to send archived receipt #{row['id']}: {exc}", flush=True)
    return delivered, len(rows)


@dp.callback_query(F.data == "adm:receipts")
async def adm_receipts(call: CallbackQuery):
    if not is_staff(call.from_user.id):
        return
    await call.answer("Надсилаю всі квитанції за весь час…")
    sent_count, total_count = await send_all_receipts(call.message.chat.id)
    await call.message.answer(
        f"🧾 <b>Квитанції</b>\n\n"
        f"За весь час у базі: <b>{total_count}</b>\n"
        f"Успішно надіслано: <b>{sent_count}</b>\n\n"
        "Старі записи не видаляються і не скидаються автоматично.",
        reply_markup=moderator_menu_kb() if not is_admin(call.from_user.id) else admin_menu_kb(),
    )


@dp.callback_query(F.data == "adm:receipts_export")
async def adm_receipts_export(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.answer("Формую таблицю квитанцій…")
    export_path, exported_count = export_receipts_csv()
    if not exported_count:
        await call.message.answer(
            "🧾 Поточний архів квитанцій порожній.",
            reply_markup=receipts_kb(),
        )
        return
    await bot.send_document(
        call.message.chat.id,
        FSInputFile(str(export_path)),
        caption=f"📄 Таблиця квитанцій: {exported_count} записів.",
    )


@dp.callback_query(F.data == "adm:receipts_reset")
async def adm_receipts_reset(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.answer()
    await call.message.answer(
        "⚠️ <b>Скинути поточний архів квитанцій?</b>\n\n"
        "Старі записи не будуть видалені з бази, але зникнуть із поточного "
        "списку та наступної вигрузки. Нові квитанції почнуть новий архів.",
        reply_markup=receipts_reset_confirm_kb(),
    )


@dp.callback_query(F.data == "adm:receipts_reset_confirm")
async def adm_receipts_reset_confirm(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    set_setting("receipts_reset_at", now())
    await call.answer("Архів квитанцій скинуто")
    await call.message.answer(
        "✅ Поточний архів квитанцій скинуто. Старі записи залишилися в базі.",
        reply_markup=receipts_kb(),
    )


@dp.callback_query(F.data == "adm:stats")
async def adm_stats(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.answer()
    await render_admin_stats(call.message)


@dp.callback_query(F.data == "adm:referral_withdrawals")
async def adm_referral_withdrawals(call: CallbackQuery):
    if not is_staff(call.from_user.id):
        return
    await call.answer()
    rows = db.execute(
        """SELECT rw.*,u.username,u.first_name
           FROM referral_withdrawals rw
           JOIN users u ON u.id=rw.user_id
           WHERE rw.status='pending'
           ORDER BY rw.id DESC
           LIMIT 30"""
    ).fetchall()
    if not rows:
        await call.message.answer(
            "💸 Нет заявок на реферальный вывод, ожидающих обработки.",
            reply_markup=admin_menu_kb(),
        )
        return
    await call.message.answer(
        f"💸 <b>Заявки на реферальный вывод</b>\n"
        f"📌 Текущая ставка: <b>{referral_percent():g}%</b>"
    )
    for request in rows:
        invited, buyers, total_earned, available, people = referral_report(
            request["user_id"]
        )
        user_label = (
            f"@{esc(request['username'])}"
            if request["username"]
            else esc(request["first_name"] or "без username")
        )
        people_text = "\n".join(people[:10]) if people else "• Нет приглашённых пользователей."
        await call.message.answer(
            f"💸 <b>Заявка #{request['id']}</b>\n"
            f"👤 {user_label} · <code>{request['user_id']}</code>\n"
            f"💰 Сумма: <b>{request['amount_stars']} Stars</b>\n"
            f"🪙 TON-кошелёк: <code>{esc(request['ton_wallet'])}</code>\n"
            f"🕒 {esc(request['created_at'])}\n\n"
            f"👥 Приглашено: <b>{invited}</b> · Покупали: <b>{buyers}</b>\n"
            f"📈 Всего заработано: <b>{total_earned} Stars</b>\n"
            f"💳 Доступно сейчас: <b>{available} Stars</b>\n\n"
            "🛍 <b>Приглашённые и покупки:</b>\n" + people_text,
            reply_markup=referral_withdrawal_kb(request["id"]),
        )


async def render_admin_stats(message: Message):
    reset_at = setting("stats_reset_at")
    user_where = "WHERE registered_at >= ?" if reset_at else ""
    order_where = "WHERE created_at >= ?" if reset_at else ""
    user_params = (reset_at,) if reset_at else ()
    order_params = (reset_at,) if reset_at else ()
    users = db.execute(
        f"SELECT COUNT(*) c FROM users {user_where}", user_params
    ).fetchone()["c"]
    orders = db.execute(
        f"SELECT COUNT(*) c FROM orders {order_where}", order_params
    ).fetchone()["c"]
    done = db.execute(
        f"SELECT COUNT(*) c FROM orders {order_where}"
        f"{' AND' if order_where else ' WHERE'} status='completed'",
        order_params,
    ).fetchone()["c"]
    waiting = db.execute(
        f"SELECT COUNT(*) c FROM orders {order_where}"
        f"{' AND' if order_where else ' WHERE'} "
        "status IN ('waiting_payment','waiting_review','waiting_ton_delivery',"
        "'waiting_premium_delivery','waiting_game_application','waiting_game_admin_approval',"
        "'waiting_game_details','waiting_game_delivery')",
        order_params,
    ).fetchone()["c"]
    rejected = db.execute(
        f"SELECT COUNT(*) c FROM orders {order_where}"
        f"{' AND' if order_where else ' WHERE'} status='rejected'",
        order_params,
    ).fetchone()["c"]
    money = db.execute(
        f"SELECT COALESCE(SUM(amount),0) s FROM orders {order_where}"
        f"{' AND' if order_where else ' WHERE'} status='completed'",
        order_params,
    ).fetchone()["s"]
    receipt_where = "WHERE created_at >= ?" if reset_at else ""
    receipt_params = (reset_at,) if reset_at else ()
    receipt_count = db.execute(
        f"SELECT COUNT(*) c FROM receipt_archive {receipt_where}",
        receipt_params,
    ).fetchone()["c"]
    recent_users = db.execute(
        f"SELECT id,username,first_name FROM users "
        f"{user_where} ORDER BY registered_at DESC,id DESC LIMIT 10",
        user_params,
    ).fetchall()
    user_lines = []
    for user in recent_users:
        label = f"@{esc(user['username'])}" if user["username"] else esc(user["first_name"] or "без username")
        user_lines.append(f"• {label} — <code>{user['id']}</code>")
    recent_user_list = "\n".join(user_lines) if user_lines else "Поки немає користувачів."
    period = (
        f"з {esc(reset_at)}"
        if reset_at
        else "за весь час"
    )
    await message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"🗓 Період: <b>{period}</b>\n\n"
        f"👥 Користувачів: {users}\n"
        f"📦 Замовлень: {orders}\n"
        f"✅ Виконано: {done}\n"
        f"⏳ Очікують перевірки або переказу: {waiting}\n"
        f"🔴 Відхилено: {rejected}\n"
        f"💰 Підтверджено оплат: {money:.2f} грн\n\n"
        f"🧾 Квитанцій: {receipt_count} (перевірка вручну)\n"
        "🔒 Налаштування та статистика зберігаються в SQLite і не скидаються самі.\n\n"
        f"👤 <b>Останні користувачі</b>\n{recent_user_list}"
        ,
        reply_markup=stats_kb(),
    )


@dp.callback_query(F.data == "adm:stats_reset")
async def adm_stats_reset(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    reset_at = now()
    # This changes only the reporting period. Historical rows and settings stay
    # in the database and are never deleted by a restart.
    set_setting("stats_reset_at", reset_at)
    await call.answer("Період статистики оновлено")
    await render_admin_stats(call.message)


@dp.callback_query(F.data == "adm:orders")
async def adm_orders(call: CallbackQuery):
    if not is_staff(call.from_user.id):
        return
    await call.answer()
    # Show unpaid receipts and products awaiting manual delivery.
    rows = db.execute(
        "SELECT * FROM orders WHERE status IN "
        "('waiting_review','waiting_ton_delivery','waiting_premium_delivery','waiting_game_admin_approval','waiting_game_delivery','waiting_product_delivery','waiting_stars_delivery','waiting_virtual_number_request','waiting_virtual_number_delivery','waiting_virtual_number_code_requested','waiting_virtual_number_final') "
        "ORDER BY id DESC LIMIT 30"
    ).fetchall()
    if not rows:
        await call.message.answer("📦 Немає заявок на перевірку чи видачу.")
        return
    for order in rows:
        u = user_row(order["user_id"])
        order_label = (
            "Вивід реферальних Stars"
            if order["order_type"] == "withdraw_stars"
            and order["withdrawal_source"] == "referral"
            else "Вивід Stars"
            if order["order_type"] == "withdraw_stars"
            else "Telegram Premium"
            if order["order_type"] == "telegram_premium"
            else "Донат гри"
            if order["order_type"] == "game_donation"
            else "NFT"
            if order["order_type"] == "nft_purchase"
            else "Товар"
            if order["order_type"] == "digital_product"
            else "Віртуальний номер"
            if order["order_type"] == "virtual_number"
            else order["order_type"]
        )
        quantity_label = (
            esc(premium_months_uk(order["quantity"]))
            if order["order_type"] == "telegram_premium"
            else esc(order["quantity"])
        )
        wallet_line = (
            f"🪙 TON-адреса для переказу: <code>{esc(order['ton_wallet'])}</code>\n"
            if order["order_type"] == "buy_ton" and order["ton_wallet"]
            else ""
        )
        if order["status"] == "waiting_game_delivery":
            action_markup = admin_game_delivery_kb(order["id"])
        elif order["status"] == "waiting_product_delivery":
            action_markup = product_delivery_kb(order["id"])
        elif order["status"] == "waiting_virtual_number_delivery":
            action_markup = virtual_number_admin_delivery_kb(order["id"])
        elif order["status"] == "waiting_virtual_number_final":
            action_markup = virtual_number_admin_finish_kb(order["id"])
        elif order["status"] == "waiting_stars_delivery":
            action_markup = stars_delivery_kb(order["id"])
        elif order["status"] == "waiting_game_admin_approval":
            action_markup = admin_game_application_kb(order["id"])
        elif order["status"] == "waiting_ton_delivery":
            action_markup = admin_ton_delivery_kb(order["id"])
        elif order["status"] == "waiting_premium_delivery":
            action_markup = admin_premium_delivery_kb(order["id"])
        elif not order["claimed_by"]:
            action_markup = admin_order_kb(order["id"])
        else:
            action_markup = None
        await call.message.answer(
            f"📦 <b>Замовлення #{order['id']}</b>\n"
            f"👤 @{esc(u['username'] or 'без_username')}\n"
            f"🆔 <code>{order['user_id']}</code>\n"
            f"📌 Тип: {esc(order_label)}\n"
            f"📌 Кількість: {quantity_label}\n"
            f"{wallet_line}"
            f"💰 Сума: <b>{order['amount']:.2f} грн</b>\n"
            f"📊 Статус: {esc(order['status'])}\n"
            f"📥 Взяв у роботу: <code>{order['claimed_by']}</code>\n"
            f"💳 Оплата: {esc(order['payment_method'] or '—')}",
            reply_markup=action_markup,
        )




@dp.callback_query(F.data == "adm:games")
async def adm_games(call: CallbackQuery):
    if not is_staff(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    await call.answer()
    await call.message.answer("🎮 <b>Каталог ігор</b>\nОберіть гру або додайте нову:", reply_markup=game_admin_list_kb())


@dp.callback_query(F.data == "adm:game:add")
async def adm_game_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Лише головний адміністратор.", show_alert=True)
        return
    await call.answer()
    await state.clear()
    await state.set_state(Form.game_name)
    await call.message.answer("Введіть назву гри, наприклад Roblox або Standoff 2:")


@dp.message(Form.game_name)
async def adm_game_name_save(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name or len(name) > 60:
        await message.answer("Назва має містити від 1 до 60 символів.")
        return
    await state.update_data(game_name=name)
    await state.set_state(Form.game_emoji)
    await message.answer("Надішліть Premium Emoji ID або звичайний емодзі для гри:")


@dp.message(Form.game_emoji)
async def adm_game_emoji_save(message: Message, state: FSMContext):
    emoji = received_emoji_token(message)
    if not emoji:
        await message.answer("Надішліть емодзі або його ID.")
        return
    await state.update_data(game_emoji=emoji)
    await state.set_state(Form.game_method)
    b = InlineKeyboardBuilder()
    b.row(kb_button("🔐 Вхід в акаунт через заявку", style="primary", callback_data="adm:game_method:account"))
    b.row(kb_button("🆔 За ID або нікнеймом", style="success", callback_data="adm:game_method:identifier"))
    await message.answer("Оберіть спосіб виконання донату:", reply_markup=b.as_markup())


@dp.callback_query(F.data.startswith("adm:game_method:"))
async def adm_game_method(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Лише головний адміністратор.", show_alert=True)
        return
    method = call.data.rsplit(":", 1)[1]
    if method not in {"account", "identifier"}:
        await call.answer("Невідомий спосіб.", show_alert=True)
        return
    await state.update_data(game_method=method)
    await state.set_state(Form.game_unit_label)
    await call.answer()
    await call.message.answer("Введіть назву одиниці для власної кількості: наприклад Robux або Gold:")


@dp.message(Form.game_unit_label)
async def adm_game_unit_label(message: Message, state: FSMContext):
    label = (message.text or "").strip()
    if not label or len(label) > 40:
        await message.answer("Введіть назву до 40 символів.")
        return
    await state.update_data(game_unit_label=label)
    await state.set_state(Form.game_unit_emoji)
    await message.answer("Надішліть емодзі або Premium Emoji ID для цієї одиниці:")


@dp.message(Form.game_unit_emoji)
async def adm_game_unit_emoji(message: Message, state: FSMContext):
    emoji = received_emoji_token(message, "📦")
    await state.update_data(game_unit_emoji=emoji)
    await state.set_state(Form.game_unit_price)
    await message.answer("Введіть ціну за 1 одиницю у гривнях. Вкажіть 0, якщо власна кількість не потрібна:")


@dp.message(Form.game_unit_price)
async def adm_game_unit_price(message: Message, state: FSMContext):
    try:
        price = Decimal((message.text or "").strip().replace(",", "."))
        if price < 0 or price > Decimal("1000000"):
            raise ValueError
    except Exception:
        await message.answer("Введіть коректну невід'ємну ціну.")
        return
    data = await state.get_data()
    cur = db.execute(
        """INSERT INTO game_catalog(name,emoji,delivery_method,unit_label,unit_emoji,unit_price,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (data["game_name"], data["game_emoji"], data["game_method"],
         data["game_unit_label"], data["game_unit_emoji"], float(price), now()),
    )
    db.commit()
    game_id = cur.lastrowid
    await state.clear()
    await message.answer(
        f"✅ Гру <b>{esc(data['game_name'])}</b> додано.\n"
        "Тепер можна додати готові пакети або налаштувати інші ігри.",
        reply_markup=game_detail_kb(game_id),
    )


@dp.callback_query(F.data.startswith("adm:game:"))
async def adm_game_detail(call: CallbackQuery):
    if not is_staff(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    raw = call.data.split(":")
    if len(raw) == 4 and raw[2] == "delete":
        if not is_admin(call.from_user.id):
            await call.answer("Лише головний адміністратор.", show_alert=True)
            return
        game_id = int(raw[3])
        db.execute("UPDATE game_catalog SET active=0 WHERE id=?", (game_id,))
        db.execute("UPDATE game_packages SET active=0 WHERE game_id=?", (game_id,))
        db.commit()
        await call.answer("Гру приховано з каталогу.")
        await call.message.answer("🎮 Каталог ігор:", reply_markup=game_admin_list_kb())
        return
    game_id = int(raw[2])
    game = db.execute("SELECT * FROM game_catalog WHERE id=?", (game_id,)).fetchone()
    if not game:
        await call.answer("Гру не знайдено.", show_alert=True)
        return
    await call.answer()
    method = "Вхід в акаунт через заявку" if game["delivery_method"] == "account" else "ID / нікнейм"
    await call.message.answer(
        f"{game_emoji_markup(game['emoji'])} <b>{esc(game['name'])}</b>\n"
        f"Спосіб: <b>{method}</b>\n"
        f"Власна кількість: {esc(game['unit_label'])} · {float(game['unit_price']):g} грн за 1",
        reply_markup=game_detail_kb(game_id),
    )


@dp.callback_query(F.data.startswith("adm:gamepkg:add:"))
async def adm_game_package_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Лише головний адміністратор.", show_alert=True)
        return
    game_id = int(call.data.rsplit(":", 1)[1])
    await state.clear()
    await state.update_data(package_game_id=game_id)
    await state.set_state(Form.game_package_name)
    await call.answer()
    await call.message.answer("Введіть назву товару пакета, наприклад Robux або Gold:")


@dp.message(Form.game_package_name)
async def adm_game_package_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name or len(name) > 40:
        await message.answer("Назва має містити від 1 до 40 символів.")
        return
    await state.update_data(package_name=name)
    await state.set_state(Form.game_package_emoji)
    await message.answer("Надішліть Premium Emoji ID або звичайний емодзі товару:")


@dp.message(Form.game_package_emoji)
async def adm_game_package_emoji(message: Message, state: FSMContext):
    emoji = received_emoji_token(message, "📦")
    await state.update_data(package_emoji=emoji)
    await state.set_state(Form.game_package_quantity)
    await message.answer("Введіть кількість у пакеті, наприклад 800:")


@dp.message(Form.game_package_quantity)
async def adm_game_package_quantity(message: Message, state: FSMContext):
    quantity = (message.text or "").strip()
    if not quantity or len(quantity) > 30:
        await message.answer("Введіть коректну кількість до 30 символів.")
        return
    await state.update_data(package_quantity=quantity)
    await state.set_state(Form.game_package_price)
    await message.answer("Введіть повну ціну цього пакета у гривнях:")


@dp.message(Form.game_package_price)
async def adm_game_package_price(message: Message, state: FSMContext):
    try:
        price = Decimal((message.text or "").strip().replace(",", "."))
        if price <= 0 or price > Decimal("1000000"):
            raise ValueError
    except Exception:
        await message.answer("Введіть коректну ціну більше нуля.")
        return
    data = await state.get_data()
    db.execute(
        """INSERT INTO game_packages(game_id,product_name,emoji,quantity,price)
           VALUES(?,?,?,?,?)""",
        (data["package_game_id"], data["package_name"], data["package_emoji"],
         data["package_quantity"], float(price)),
    )
    db.commit()
    await state.clear()
    await message.answer("✅ Пакет додано.", reply_markup=game_detail_kb(data["package_game_id"]))


@dp.callback_query(F.data.startswith("adm:gamepkg:"))
async def adm_game_package_delete(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Лише головний адміністратор.", show_alert=True)
        return
    package_id = int(call.data.rsplit(":", 1)[1])
    row = db.execute("SELECT game_id FROM game_packages WHERE id=?", (package_id,)).fetchone()
    if not row:
        await call.answer("Пакет не знайдено.", show_alert=True)
        return
    db.execute("UPDATE game_packages SET active=0 WHERE id=?", (package_id,))
    db.commit()
    await call.answer("Пакет приховано.")
    await call.message.answer("Оновлений список пакетів:", reply_markup=game_detail_kb(row["game_id"]))


@dp.message(F.text.in_({"📱 Віртуальні номери", "📱 Виртуальные номера", "📱 Virtual numbers"}))
async def open_games_from_menu(message: Message):
    await game_list_from_message(message)


async def game_list_from_message(message: Message):
    await replace_screen(
        message.from_user.id, "🎮 <b>Оберіть гру для донату:</b>",
        image="welcome", reply_markup=game_catalog_kb(),
    )


@dp.callback_query(F.data == "game:list")
async def game_list(call: CallbackQuery):
    await call.answer()
    await replace_screen(
        call.from_user.id, "🎮 <b>Оберіть гру для донату:</b>",
        image="welcome", reply_markup=game_catalog_kb(),
    )


@dp.callback_query(F.data.startswith("game:open:"))
async def game_open(call: CallbackQuery):
    game_id = int(call.data.rsplit(":", 1)[1])
    game = db.execute("SELECT * FROM game_catalog WHERE id=? AND active=1", (game_id,)).fetchone()
    if not game:
        await call.answer("Гру не знайдено.", show_alert=True)
        return
    await call.answer()
    method = "через заявку адміністратору" if game["delivery_method"] == "account" else "за ID або нікнеймом"
    await replace_screen(
        call.from_user.id,
        f"{game_emoji_markup(game['emoji'])} <b>{esc(game['name'])}</b>\n\n"
        f"Спосіб донату: <b>{method}</b>\nОберіть пакет:",
        image="welcome", reply_markup=game_packages_kb(game_id),
    )


async def begin_game_order(user_id, game, product_name, product_emoji, quantity, amount):
    meta = {
        "game_id": int(game["id"]),
        "game_name": game["name"],
        "delivery_method": game["delivery_method"],
        "product_name": product_name,
        "product_emoji": product_emoji,
        "quantity": str(quantity),
        "user_identifier": "",
    }
    oid = create_order(user_id, "game_donation", f"{product_name} {quantity}", amount)
    db.execute("UPDATE orders SET admin_note=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), oid))
    db.commit()
    await replace_screen(
        user_id,
        f"🎮 <b>{esc(game['name'])}</b>\n"
        f"{game_emoji_markup(product_emoji, '📦')} {esc(product_name)}: <b>{esc(quantity)}</b>\n"
        f"💰 До оплати: <b>{amount:.2f} грн</b>\n\nОберіть спосіб оплати:",
        image="welcome", reply_markup=payment_kb(oid),
    )


@dp.callback_query(F.data.startswith("game:buy:"))
async def game_buy_package(call: CallbackQuery):
    package_id = int(call.data.rsplit(":", 1)[1])
    package = db.execute("SELECT * FROM game_packages WHERE id=? AND active=1", (package_id,)).fetchone()
    game = db.execute("SELECT * FROM game_catalog WHERE id=? AND active=1", (package["game_id"],)).fetchone() if package else None
    if not package or not game:
        await call.answer("Пакет більше недоступний.", show_alert=True)
        return
    await call.answer()
    await begin_game_order(
        call.from_user.id, game, package["product_name"], package["emoji"],
        package["quantity"], float(package["price"]),
    )


@dp.callback_query(F.data.startswith("game:custom:"))
async def game_custom_start(call: CallbackQuery, state: FSMContext):
    game_id = int(call.data.rsplit(":", 1)[1])
    game = db.execute("SELECT * FROM game_catalog WHERE id=? AND active=1", (game_id,)).fetchone()
    if not game or float(game["unit_price"] or 0) <= 0:
        await call.answer("Власна кількість недоступна.", show_alert=True)
        return
    await call.answer()
    await state.update_data(game_id=game_id)
    await state.set_state(Form.game_custom_quantity)
    await call.message.answer(
        f"Введіть кількість {esc(game['unit_label'])}. Ціна за 1: <b>{float(game['unit_price']):g} грн</b>."
    )


@dp.message(Form.game_custom_quantity)
async def game_custom_quantity_or_identifier(message: Message, state: FSMContext):
    data = await state.get_data()
    raw = (message.text or "").strip().replace(",", ".")
    try:
        qty = Decimal(raw)
        if qty <= 0 or qty > Decimal("1000000000"):
            raise ValueError
    except Exception:
        await message.answer("Введіть коректну додатну кількість.")
        return
    game = db.execute("SELECT * FROM game_catalog WHERE id=? AND active=1", (data["game_id"],)).fetchone()
    if not game:
        await state.clear()
        await message.answer("Гру не знайдено.")
        return
    amount = (qty * Decimal(str(game["unit_price"]))).quantize(Decimal("0.01"))
    if amount <= 0:
        await message.answer("Сума замовлення має бути більшою за 0,01 грн. Збільште кількість.")
        return
    await state.clear()
    await begin_game_order(
        message.from_user.id, game, game["unit_label"], game["unit_emoji"],
        format(qty.normalize(), "f"), float(amount),
    )


async def process_game_identifier(message: Message):
    raw = (message.text or "").strip()
    if not raw or len(raw) > 100:
        await message.answer("Введіть ID або нікнейм (до 100 символів).")
        return
    order = db.execute(
        "SELECT * FROM orders WHERE user_id=? AND order_type='game_donation' "
        "AND status='waiting_game_details' ORDER BY id DESC LIMIT 1",
        (message.from_user.id,),
    ).fetchone()
    if not order:
        return
    order_id = int(order["id"])
    meta = game_order_meta(order)
    meta["user_identifier"] = raw
    db.execute(
        "UPDATE orders SET admin_note=?,status='waiting_game_delivery' WHERE id=? AND status='waiting_game_details'",
        (json.dumps(meta, ensure_ascii=False), order_id),
    )
    db.commit()
    await message.answer("✅ Дані для донату передано адміністратору. Очікуйте виконання.")
    await notify_admins(
        f"🎮 <b>Донат гри — замовлення #{order_id}</b>\n"
        f"Гра: <b>{esc(meta.get('game_name'))}</b>\n"
        f"Товар: <b>{esc(meta.get('product_name'))} {esc(meta.get('quantity'))}</b>\n"
        f"Користувач: <code>{message.from_user.id}</code>\n"
        f"ID / нікнейм: <code>{esc(raw)}</code>\n"
        f"Сума: <b>{order['amount']:.2f} грн</b>",
        reply_markup=admin_game_delivery_kb(order_id),
    )


@dp.callback_query(F.data.startswith("game:apply:"))
async def game_apply(call: CallbackQuery):
    order_id = int(call.data.rsplit(":", 1)[1])
    order = db.execute(
        "SELECT * FROM orders WHERE id=? AND user_id=? AND order_type='game_donation'",
        (order_id, call.from_user.id),
    ).fetchone()
    if not order or order["status"] != "waiting_game_application":
        await call.answer("Заявка вже подана або замовлення не готове.", show_alert=True)
        return
    meta = game_order_meta(order)
    cur = db.execute(
        "UPDATE orders SET status='waiting_game_admin_approval' WHERE id=? AND status='waiting_game_application'",
        (order_id,),
    )
    db.commit()
    if cur.rowcount != 1:
        await call.answer("Заявку вже оброблено.", show_alert=True)
        return
    await call.answer("Заявку надіслано адміністратору.")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("📨 Заявку на донат передано адміністрації. Очікуйте відповіді.")
    await notify_admins(
        f"📨 <b>Нова заявка на донат через вхід в акаунт #{order_id}</b>\n"
        f"Гра: <b>{esc(meta.get('game_name'))}</b>\n"
        f"Товар: <b>{esc(meta.get('product_name'))} {esc(meta.get('quantity'))}</b>\n"
        f"Покупець: <code>{order['user_id']}</code>\n"
        f"Сума: <b>{order['amount']:.2f} грн</b>\n\n"
        "Зв'яжіться з покупцем поза ботом. Не просіть надсилати пароль або коди в чат бота.",
        reply_markup=admin_game_application_kb(order_id),
    )


@dp.callback_query(F.data.startswith("admin_game_accept:"))
async def admin_game_accept(call: CallbackQuery):
    if not is_staff(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    order_id = int(call.data.rsplit(":", 1)[1])
    order = db.execute("SELECT * FROM orders WHERE id=? AND order_type='game_donation'", (order_id,)).fetchone()
    if not order or order["status"] != "waiting_game_admin_approval":
        await call.answer("Заявка вже оброблена.", show_alert=True)
        return
    cur = db.execute(
        "UPDATE orders SET status='waiting_game_delivery' WHERE id=? AND status='waiting_game_admin_approval'",
        (order_id,),
    )
    db.commit()
    if cur.rowcount != 1:
        await call.answer("Заявка вже оброблена.", show_alert=True)
        return
    await call.answer("Заявку прийнято.")
    await call.message.edit_reply_markup(reply_markup=admin_game_delivery_kb(order_id))
    await bot.send_message(
        order["user_id"],
        f"✅ Заявку на донат гри за замовленням #{order_id} прийнято. Адміністратор зв'яжеться з вами для узгодження.",
        reply_markup=main_menu(order["user_id"]),
    )


@dp.callback_query(F.data.startswith("admin_game_reject:"))
async def admin_game_reject(call: CallbackQuery):
    if not is_staff(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    order_id = int(call.data.rsplit(":", 1)[1])
    cur = db.execute(
        "UPDATE orders SET status='rejected' WHERE id=? AND order_type='game_donation' AND status='waiting_game_admin_approval'",
        (order_id,),
    )
    db.commit()
    if cur.rowcount != 1:
        await call.answer("Заявка вже оброблена.", show_alert=True)
        return
    order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    await call.answer("Заявку відхилено.")
    await call.message.edit_reply_markup(reply_markup=None)
    await bot.send_message(order["user_id"], f"❌ Заявку на донат #{order_id} відхилено. Напишіть у підтримку для уточнення.")


@dp.callback_query(F.data.startswith("admin_game_delivered:"))
async def admin_game_delivered(call: CallbackQuery):
    if not is_staff(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    order_id = int(call.data.rsplit(":", 1)[1])
    db.execute("BEGIN IMMEDIATE")
    order = db.execute("SELECT * FROM orders WHERE id=? AND order_type='game_donation'", (order_id,)).fetchone()
    if not order or order["status"] != "waiting_game_delivery":
        db.rollback()
        await call.answer("Донат уже підтверджено або замовлення не очікує видачі.", show_alert=True)
        return
    cur = db.execute(
        "UPDATE orders SET status='completed' WHERE id=? AND status='waiting_game_delivery'",
        (order_id,),
    )
    if cur.rowcount != 1:
        db.rollback()
        await call.answer("Замовлення вже оброблено.", show_alert=True)
        return
    db.execute(
        "UPDATE users SET spent_uah=spent_uah+?,status='Клієнт' WHERE id=?",
        (order["amount"], order["user_id"]),
    )
    db.commit()
    await call.answer("Донат позначено виконаним.")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(f"✅ Донат за замовленням #{order_id} підтверджено.")
    meta = game_order_meta(order)
    await bot.send_message(
        order["user_id"],
        f"🎉 <b>Донат успішно виконано!</b>\n\n"
        f"🎮 {esc(meta.get('game_name'))}\n"
        f"📦 {esc(meta.get('product_name'))}: <b>{esc(meta.get('quantity'))}</b>\n"
        f"Дякуємо за покупку! Будемо вдячні за ваш відгук.",
        reply_markup=main_menu(order["user_id"]),
    )
    await send_review_request(order)


@dp.callback_query(F.data == "adm:premium")
async def adm_premium(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Немає доступу.", show_alert=True)
        return
    await call.answer()
    prices_text = "\n".join(
        f"• {months} міс.: <b>{premium_price(months):g} грн</b>"
        if premium_price(months)
        else f"• {months} міс.: <i>ціну не встановлено</i>"
        for months in (3, 6, 12)
    )
    await call.message.answer(
        "⭐ <b>Telegram Premium</b>\n\n"
        f"{prices_text}\n\n"
        "Оберіть тариф, щоб встановити або змінити ціну:",
        reply_markup=premium_admin_kb(),
    )


@dp.callback_query(F.data.startswith("premiumadm:price:"))
async def premium_price_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Немає доступу.", show_alert=True)
        return
    try:
        months = int(call.data.rsplit(":", 1)[1])
    except (AttributeError, TypeError, ValueError):
        await call.answer("Некоректний термін.", show_alert=True)
        return
    if months not in {3, 6, 12}:
        await call.answer("Некоректний термін.", show_alert=True)
        return
    await call.answer()
    await state.update_data(premium_price_months=months)
    await state.set_state(Form.premium_price)
    current_price = premium_price(months)
    await call.message.answer(
        f"⭐ <b>Telegram Premium на {premium_months_uk(months)}</b>\n"
        f"Поточна ціна: <b>{current_price:g} грн</b>\n\n"
        "Надішліть нову ціну в гривнях. Для активації тарифу введіть суму більше нуля.",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.premium_price)
async def premium_price_save(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        price = float((message.text or "").strip().replace(",", "."))
        if not math.isfinite(price) or price <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Ціна має бути додатним числом, наприклад 499 або 499.50.")
        return
    data = await state.get_data()
    months = int(data.get("premium_price_months", 0))
    if months not in {3, 6, 12}:
        await state.clear()
        await message.answer("❌ Тариф не знайдено.", reply_markup=admin_menu_kb())
        return
    set_setting(f"premium_price_{months}", f"{price:.2f}")
    await state.clear()
    await message.answer(
        f"✅ Ціну Telegram Premium на {months} міс. встановлено: <b>{price:g} грн</b>.",
        reply_markup=premium_admin_kb(),
    )


@dp.callback_query(F.data.startswith("admin_claim:"))
async def admin_claim(call: CallbackQuery):
    if not is_staff(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    oid = int(call.data.split(":")[1])
    try:
        db.execute("BEGIN IMMEDIATE")
        order = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not order or order["status"] != "waiting_review":
            db.rollback()
            await call.answer("Цю заявку вже оброблено.", show_alert=True)
            return
        if order["claimed_by"] and order["claimed_by"] != call.from_user.id:
            db.rollback()
            await call.answer("Цю заявку вже взяв інший модератор.", show_alert=True)
            return
        db.execute(
            "UPDATE orders SET claimed_by=?,claimed_at=? WHERE id=? AND claimed_by IS NULL",
            (call.from_user.id, now(), oid),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    await call.answer("Заявку взято в роботу.")
    try:
        await call.message.edit_reply_markup(reply_markup=admin_order_review_kb(oid))
    except Exception:
        pass
    await call.message.answer(
        f"📥 Заявку #{oid} взяв у роботу модератор <code>{call.from_user.id}</code>.\n"
        "Тепер доступні кнопки підтвердження або відхилення."
    )


@dp.callback_query(F.data.startswith("admin_ok:"))
async def admin_ok(call: CallbackQuery):
    if not is_staff(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    oid = int(call.data.split(":")[1])
    stars_credited = None
    raffle_offer = None
    referral_bonus = 0
    referral_recipient_id = None
    try:
        db.execute("BEGIN IMMEDIATE")
        order = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not order or order["status"] not in ("waiting_review", "waiting_payment"):
            db.rollback()
            await call.answer("Замовлення вже оброблене або не існує.", show_alert=True)
            return
        if order["claimed_by"] != call.from_user.id:
            db.rollback()
            await call.answer("Спочатку натисніть «Взяти квитанцію».", show_alert=True)
            return
        if order["order_type"] == "buy_ton" and not is_valid_ton_wallet(
            order["ton_wallet"]
        ):
            db.rollback()
            await call.answer(
                "У замовленні немає коректної TON-адреси. Не підтверджуйте переказ.",
                show_alert=True,
            )
            return

        if order["order_type"] == "buy_stars":
            purchased_stars = int(order["quantity"])
            order_meta = order_json_meta(order)
            if order_meta.get("recipient_type") == "friend":
                # Third-party Stars delivery is fulfilled manually by staff;
                # the buyer's internal Stars balance is not credited.
                db.execute("UPDATE orders SET status='waiting_stars_delivery' WHERE id=?", (oid,))
                stars_credited = 0
            else:
                # Self-purchases are credited to the buyer's internal balance.
                stars_credited = purchased_stars
                db.execute("UPDATE orders SET status='completed' WHERE id=?", (oid,))
                db.execute(
                    """UPDATE users SET balance_stars=balance_stars+?, bought_stars=bought_stars+?,
                       spent_uah=spent_uah+?, status='Клієнт' WHERE id=?""",
                    (stars_credited, purchased_stars, order["amount"], order["user_id"]),
                )
        elif order["order_type"] == "buy_ton":
            # Payment approval is not delivery approval. Keep the order
            # pending until staff confirms the TON was sent to the buyer.
            db.execute(
                "UPDATE orders SET status='waiting_ton_delivery' WHERE id=?",
                (oid,),
            )
        elif order["order_type"] == "telegram_premium":
            months = int(order["quantity"])
            if months not in {3, 6, 12}:
                db.rollback()
                await call.answer("Некоректний термін Telegram Premium.", show_alert=True)
                return
            # Payment confirmation and actual Premium activation are separate.
            db.execute(
                "UPDATE orders SET status='waiting_premium_delivery' WHERE id=?",
                (oid,),
            )
        elif order["order_type"] == "game_donation":
            meta = game_order_meta(order)
            next_status = (
                "waiting_game_application"
                if meta.get("delivery_method") == "account"
                else "waiting_game_details"
            )
            db.execute("UPDATE orders SET status=? WHERE id=?", (next_status, oid))
        elif order["order_type"] in {"nft_purchase", "digital_product"}:
            db.execute("UPDATE orders SET status='waiting_product_delivery' WHERE id=?", (oid,))
        elif order["order_type"] == "virtual_number":
            db.execute("UPDATE orders SET status='waiting_virtual_number_request' WHERE id=?", (oid,))
        elif order["order_type"] == "withdraw_stars":
            if order["withdrawal_source"] == "referral":
                referral_balance = db.execute(
                    "SELECT referral_balance_stars FROM users WHERE id=?",
                    (order["user_id"],),
                ).fetchone()
                amount = int(order["quantity"])
                if (
                    not referral_balance
                    or int(referral_balance["referral_balance_stars"] or 0) < amount
                ):
                    db.rollback()
                    await call.answer(
                        "Недостатньо реферальних Stars для підтвердження заявки.",
                        show_alert=True,
                    )
                    return
                db.execute(
                    """UPDATE users
                       SET referral_balance_stars=referral_balance_stars-?
                       WHERE id=? AND referral_balance_stars>=?""",
                    (amount, order["user_id"], amount),
                )
            else:
                amount = int(order["quantity"])
                if not order["withdrawal_reserved"]:
                    # Older pending requests were not reserved at creation.
                    # Debit them conditionally now so they cannot overdraw.
                    debit = db.execute(
                        """UPDATE users SET balance_stars=balance_stars-?
                           WHERE id=? AND balance_stars>=?""",
                        (amount, order["user_id"], amount),
                    )
                    if debit.rowcount != 1:
                        db.rollback()
                        await call.answer(
                            "Недостатньо Stars на балансі для підтвердження цієї заявки.",
                            show_alert=True,
                        )
                        return
                cursor = db.execute(
                    """UPDATE orders
                       SET status='completed',withdrawal_reserved=0
                       WHERE id=? AND status='waiting_review'""",
                    (oid,),
                )
                if cursor.rowcount != 1:
                    db.rollback()
                    await call.answer("Заявку вже оброблено.", show_alert=True)
                    return
            if order["withdrawal_source"] == "referral":
                db.execute(
                    "UPDATE orders SET status='completed' WHERE id=?",
                    (oid,),
                )
        else:
            db.execute("UPDATE orders SET status='completed' WHERE id=?", (oid,))

        if order["order_type"] == "withdraw_stars":
            db.execute(
                """INSERT OR IGNORE INTO customer_reviews(order_id,user_id,status,created_at)
                   VALUES(?,?,?,?)""",
                (oid, order["user_id"], "waiting_rating", now()),
            )

        # Premium referral credit is awarded only after staff confirms
        # activation, not merely when the payment is approved.
        referral_base_stars = (
            0
            if order["order_type"] == "telegram_premium"
            else referral_purchase_stars(order)
        )
        buyer = db.execute(
            "SELECT referrer_id FROM users WHERE id=?", (order["user_id"],)
        ).fetchone()
        referral_recipient_id = buyer["referrer_id"] if buyer else None
        if (
            referral_base_stars > 0
            and referral_recipient_id
            and referral_recipient_id != order["user_id"]
            and not order["referral_rewarded"]
        ):
            referral_bonus = int(
                referral_base_stars * referral_percent() / 100
            )
            referrer_exists = db.execute(
                "SELECT 1 FROM users WHERE id=?", (referral_recipient_id,)
            ).fetchone()
            if referral_bonus > 0 and referrer_exists:
                db.execute(
                    """UPDATE users
                       SET referral_balance_stars=referral_balance_stars+?,
                           referral_earnings_stars=referral_earnings_stars+?
                       WHERE id=?""",
                    (referral_bonus, referral_bonus, referral_recipient_id),
                )
            else:
                referral_bonus = 0
            db.execute(
                "UPDATE orders SET referral_rewarded=1 WHERE id=?",
                (oid,),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise

    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    if order["order_type"] == "buy_ton":
        await call.message.answer(
            f"✅ <b>Оплату замовлення TON #{oid} підтверджено.</b>\n\n"
            f"💎 Кількість: <b>{esc(order['quantity'])} TON</b>\n"
            f"🪙 Адреса отримувача: <code>{esc(order['ton_wallet'] or '—')}</code>\n\n"
            "Після фактичного переказу натисніть кнопку нижче.",
            reply_markup=admin_ton_delivery_kb(oid),
        )
    elif order["order_type"] == "telegram_premium":
        await call.message.answer(
            f"✅ <b>Оплату Telegram Premium #{oid} підтверджено.</b>\n"
            f"Термін: <b>{premium_months_uk(order['quantity'])}</b>\n"
            "Після активації натисніть кнопку нижче.",
            reply_markup=admin_premium_delivery_kb(oid),
        )
    elif order["order_type"] == "buy_stars" and order_json_meta(order).get("recipient_type") == "friend":
        meta = order_json_meta(order)
        recipient = meta.get("recipient") or "—"
        await call.message.answer(
            f"✅ <b>Оплату Stars для друга за #{oid} підтверджено.</b>\n"
            f"Отримувач: <code>{esc(recipient)}</code>\n"
            "Після фактичного надсилання Stars натисніть кнопку нижче.",
            reply_markup=stars_delivery_kb(oid),
        )
    else:
        await call.message.answer(
            f"{pe(EMOJI_POOL[5], '✅')} Замовлення #{oid} підтверджено."
        )

    if order["order_type"] == "buy_stars":
        meta = order_json_meta(order)
        if meta.get("recipient_type") == "friend":
            recipient = meta.get("recipient") or "—"
            user_text = (
                f"✅ <b>Оплату замовлення #{oid} підтверджено.</b>\n\n"
                f"⭐ Кількість: <b>{esc(order['quantity'])} Stars</b>\n"
                f"🎁 Отримувач: <b>{esc(recipient)}</b>\n"
                "Адміністратор виконує надсилання. Повідомлення про завершення надійде окремо."
            )
        else:
            current_balance = user_row(order["user_id"])["balance_stars"]
            user_text = (
                f"{pe(EMOJI_POOL[5], '✅')} <b>Поповнення #{oid} підтверджено!</b>\n\n"
                f"⭐ На баланс зараховано: <b>{stars_credited} Stars</b>\n"
                f"💳 Поточний баланс: <b>{current_balance} Stars</b>"
            )
        await bot.send_message(
            order["user_id"], user_text, reply_markup=main_menu(order["user_id"]),
        )

        if raffle_offer:
            current = db.execute(
                "SELECT * FROM raffles WHERE id=? AND status='active'",
                (raffle_offer["id"],),
            ).fetchone()
            if current:
                await bot.send_message(
                    order["user_id"],
                    raffle_consent_text(current, order, order["user_id"]),
                    reply_markup=raffle_consent_kb(current["id"], oid),
                )
    elif order["order_type"] == "buy_ton":
        try:
            await bot.send_message(
                order["user_id"],
                f"✅ <b>Оплату замовлення #{oid} підтверджено.</b>\n\n"
                f"💎 До відправлення: <b>{esc(order['quantity'])} TON</b>\n"
                f"🪙 Адреса отримання: <code>{esc(order['ton_wallet'] or '—')}</code>\n\n"
                "Очікуйте повідомлення після переказу TON.",
                reply_markup=main_menu(order["user_id"]),
            )
        except Exception as exc:
            print(
                f"Unable to notify buyer {order['user_id']} about TON payment approval: {exc}",
                flush=True,
            )
    elif order["order_type"] == "telegram_premium":
        await bot.send_message(
            order["user_id"],
            f"✅ <b>Оплату замовлення Telegram Premium #{oid} підтверджено.</b>\n\n"
            f"⭐ Термін: <b>{premium_months_uk(order['quantity'])}</b>\n"
            "Адміністратор готує активацію. Ми надішлемо повідомлення після її завершення.",
            reply_markup=main_menu(order["user_id"]),
        )
    elif order["order_type"] == "virtual_number":
        meta = order_json_meta(order)
        await bot.send_message(
            order["user_id"],
            f"✅ <b>Оплату за віртуальний номер #{oid} підтверджено.</b>\n\n"
            f"📱 {esc(meta.get('number_title') or order['quantity'])}\n"
            "Натисніть «Запросити номер», коли будете готові. Коди підтвердження отримуйте тільки напряму від офіційного сервісу.",
            reply_markup=virtual_number_user_kb(oid, "number"),
        )
    elif order["order_type"] in {"nft_purchase", "digital_product"}:
        meta = order_json_meta(order)
        product_label = "NFT" if order["order_type"] == "nft_purchase" else "Товар"
        await bot.send_message(
            order["user_id"],
            f"✅ <b>Оплату за {product_label.lower()} #{oid} підтверджено.</b>\n\n"
            f"🎁 {esc(meta.get('product_name') or order['quantity'])}\n"
            "Адміністратор готує видачу. Ви отримаєте повідомлення після завершення.",
            reply_markup=main_menu(order["user_id"]),
        )
    elif order["order_type"] == "game_donation":
        meta = game_order_meta(order)
        if meta.get("delivery_method") == "account":
            await bot.send_message(
                order["user_id"],
                f"✅ <b>Оплату за донат {esc(meta.get('game_name'))} підтверджено!</b>\n\n"
                "Натисніть кнопку нижче, щоб подати заявку адміністратору. "
                "Адміністратор зв'яжеться з вами для узгодження передачі.",
                reply_markup=game_application_kb(oid),
            )
        else:
            await bot.send_message(
                order["user_id"],
                f"✅ <b>Оплату за донат {esc(meta.get('game_name'))} підтверджено!</b>\n\n"
                f"Надішліть ваш ID або нікнейм у грі одним повідомленням.",
                reply_markup=main_menu(order["user_id"]),
            )
    elif order["order_type"] == "withdraw_stars":
        # The payout confirmation and review request are combined below so
        # the customer receives the requested single, complete message.
        pass
    else:
        await bot.send_message(
            order["user_id"],
            f"{pe(EMOJI_POOL[5], '✅')} <b>Замовлення #{oid} підтверджено!</b>",
            reply_markup=main_menu(order["user_id"]),
        )

    if referral_bonus and referral_recipient_id:
        try:
            language = user_language(referral_recipient_id)
            reward_message = {
                "uk": (
                    "🎉 <b>Нова реферальна винагорода!</b>\n\n"
                    "Ваш запрошений користувач здійснив покупку.\n"
                    f"💰 На реферальний баланс зараховано: <b>{referral_bonus} Stars</b>\n"
                    "💳 Доступно для виводу реферальних Stars: "
                    f"<b>{referral_available_stars(referral_recipient_id)} Stars</b>"
                ),
                "ru": (
                    "🎉 <b>Новая реферальная награда!</b>\n\n"
                    "Ваш приглашённый пользователь совершил покупку.\n"
                    f"💰 На реферальный баланс начислено: <b>{referral_bonus} Stars</b>\n"
                    "💳 Доступно для вывода реферальных Stars: "
                    f"<b>{referral_available_stars(referral_recipient_id)} Stars</b>"
                ),
                "en": (
                    "🎉 <b>New referral reward!</b>\n\n"
                    "Someone you invited made a purchase.\n"
                    f"💰 Added to your referral balance: <b>{referral_bonus} Stars</b>\n"
                    "💳 Referral Stars available to withdraw: "
                    f"<b>{referral_available_stars(referral_recipient_id)} Stars</b>"
                ),
            }[language]
            await bot.send_message(
                referral_recipient_id,
                reward_message,
                reply_markup=main_menu(referral_recipient_id),
            )
        except Exception as exc:
            print(
                f"Unable to notify referrer {referral_recipient_id}: {exc}",
                flush=True,
            )

    if order["order_type"] in {"withdraw_stars", "buy_stars"}:
        await send_review_request(order)


@dp.callback_query(F.data.startswith("admin_ton_sent:"))
async def admin_ton_sent(call: CallbackQuery):
    if not is_staff(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    try:
        order_id = int(call.data.rsplit(":", 1)[1])
    except (AttributeError, TypeError, ValueError):
        await call.answer("Некоректний номер замовлення.", show_alert=True)
        return

    try:
        db.execute("BEGIN IMMEDIATE")
        order = db.execute(
            "SELECT * FROM orders WHERE id=? AND order_type='buy_ton'",
            (order_id,),
        ).fetchone()
        if not order or order["status"] != "waiting_ton_delivery":
            db.rollback()
            await call.answer(
                "Це замовлення вже оброблено або не очікує переказу.",
                show_alert=True,
            )
            return
        if not is_valid_ton_wallet(order["ton_wallet"]):
            db.rollback()
            await call.answer(
                "У замовленні немає коректної TON-адреси.",
                show_alert=True,
            )
            return
        cursor = db.execute(
            """UPDATE orders SET status='completed'
               WHERE id=? AND order_type='buy_ton' AND status='waiting_ton_delivery'""",
            (order_id,),
        )
        if cursor.rowcount != 1:
            db.rollback()
            await call.answer("Замовлення вже оброблено.", show_alert=True)
            return
        db.execute(
            """UPDATE users
               SET bought_ton=bought_ton+?,spent_uah=spent_uah+?,status='Клієнт'
               WHERE id=?""",
            (float(order["quantity"]), order["amount"], order["user_id"]),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    await call.answer("Переказ TON підтверджено.")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer(f"✅ Переказ TON за замовленням #{order_id} відмічено.")
    try:
        await bot.send_message(
            order["user_id"],
            f"✅ <b>TON за замовленням #{order_id} відправлено!</b>\n\n"
            f"💎 Сума: <b>{esc(order['quantity'])} TON</b>\n"
            f"🪙 Адреса отримання: <code>{esc(order['ton_wallet'] or '—')}</code>",
            reply_markup=main_menu(order["user_id"]),
        )
    except Exception as exc:
        print(
            f"Unable to notify buyer {order['user_id']} about TON delivery: {exc}",
            flush=True,
        )
    await send_review_request(order)


@dp.callback_query(F.data.startswith("admin_premium_sent:"))
async def admin_premium_sent(call: CallbackQuery):
    if not is_staff(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    try:
        order_id = int(call.data.rsplit(":", 1)[1])
    except (AttributeError, TypeError, ValueError):
        await call.answer("Некоректний номер замовлення.", show_alert=True)
        return

    referral_bonus = 0
    referral_recipient_id = None
    try:
        db.execute("BEGIN IMMEDIATE")
        order = db.execute(
            "SELECT * FROM orders WHERE id=? AND order_type='telegram_premium'",
            (order_id,),
        ).fetchone()
        if not order or order["status"] != "waiting_premium_delivery":
            db.rollback()
            await call.answer(
                "Це замовлення вже оброблено або не очікує активації.",
                show_alert=True,
            )
            return
        months = int(order["quantity"])
        if months not in {3, 6, 12}:
            db.rollback()
            await call.answer("Некоректний термін Telegram Premium.", show_alert=True)
            return
        cursor = db.execute(
            """UPDATE orders SET status='completed'
               WHERE id=? AND order_type='telegram_premium'
                 AND status='waiting_premium_delivery'""",
            (order_id,),
        )
        if cursor.rowcount != 1:
            db.rollback()
            await call.answer("Замовлення вже оброблено.", show_alert=True)
            return
        db.execute(
            """UPDATE users
               SET premium_months=COALESCE(premium_months,0)+?,
                   spent_uah=spent_uah+?,status='Клієнт'
               WHERE id=?""",
            (months, order["amount"], order["user_id"]),
        )

        referral_base_stars = referral_purchase_stars(order)
        buyer = db.execute(
            "SELECT referrer_id FROM users WHERE id=?",
            (order["user_id"],),
        ).fetchone()
        referral_recipient_id = buyer["referrer_id"] if buyer else None
        if (
            referral_base_stars > 0
            and referral_recipient_id
            and referral_recipient_id != order["user_id"]
            and not order["referral_rewarded"]
        ):
            referrer_exists = db.execute(
                "SELECT 1 FROM users WHERE id=?",
                (referral_recipient_id,),
            ).fetchone()
            if referrer_exists:
                referral_bonus = int(
                    referral_base_stars * referral_percent() / 100
                )
                if referral_bonus > 0:
                    db.execute(
                        """UPDATE users
                           SET referral_balance_stars=referral_balance_stars+?,
                               referral_earnings_stars=referral_earnings_stars+?
                           WHERE id=?""",
                        (
                            referral_bonus,
                            referral_bonus,
                            referral_recipient_id,
                        ),
                    )
                db.execute(
                    "UPDATE orders SET referral_rewarded=1 WHERE id=?",
                    (order_id,),
                )
        db.commit()
    except Exception:
        db.rollback()
        raise

    await call.answer("Активацію Telegram Premium підтверджено.")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer(
        f"✅ Telegram Premium за замовленням #{order_id} на "
        f"{premium_months_uk(months)} видано."
    )
    language = user_language(order["user_id"])
    delivery_message = {
        "uk": (
            f"✅ <b>Telegram Premium на {premium_months_uk(months)} активовано!</b>\n\n"
            "Дякуємо за замовлення."
        ),
        "ru": (
            f"✅ <b>Telegram Premium на {premium_duration_label(months, 'ru')} активирован!</b>\n\n"
            "Спасибо за заказ."
        ),
        "en": (
            f"✅ <b>Telegram Premium for {premium_duration_label(months, 'en')} is active!</b>\n\n"
            "Thank you for your order."
        ),
    }[language]
    try:
        await bot.send_message(
            order["user_id"],
            delivery_message,
            reply_markup=main_menu(order["user_id"]),
        )
    except Exception as exc:
        print(
            f"Unable to notify buyer {order['user_id']} about Premium delivery: {exc}",
            flush=True,
        )
    if referral_bonus and referral_recipient_id:
        try:
            language = user_language(referral_recipient_id)
            reward_message = {
                "uk": (
                    "🎉 <b>Нова реферальна винагорода!</b>\n\n"
                    "Ваш запрошений користувач активував Telegram Premium.\n"
                    f"💰 На баланс зараховано: <b>{referral_bonus} Stars</b>\n"
                    "💳 Доступно для виводу: "
                    f"<b>{referral_available_stars(referral_recipient_id)} Stars</b>"
                ),
                "ru": (
                    "🎉 <b>Новая реферальная награда!</b>\n\n"
                    "Ваш приглашённый пользователь активировал Telegram Premium.\n"
                    f"💰 На баланс начислено: <b>{referral_bonus} Stars</b>\n"
                    "💳 Доступно для вывода: "
                    f"<b>{referral_available_stars(referral_recipient_id)} Stars</b>"
                ),
                "en": (
                    "🎉 <b>New referral reward!</b>\n\n"
                    "Someone you invited activated Telegram Premium.\n"
                    f"💰 Added to your balance: <b>{referral_bonus} Stars</b>\n"
                    "💳 Available to withdraw: "
                    f"<b>{referral_available_stars(referral_recipient_id)} Stars</b>"
                ),
            }[language]
            await bot.send_message(
                referral_recipient_id,
                reward_message,
                reply_markup=main_menu(referral_recipient_id),
            )
        except Exception as exc:
            print(
                f"Unable to notify referrer {referral_recipient_id} "
                f"about Premium order {order_id}: {exc}",
                flush=True,
            )
    await send_review_request(order)


@dp.callback_query(F.data.startswith("refwd:approve:"))
async def referral_withdrawal_approve(call: CallbackQuery):
    if not is_staff(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    request_id = int(call.data.rsplit(":", 1)[1])
    try:
        db.execute("BEGIN IMMEDIATE")
        request = db.execute(
            "SELECT * FROM referral_withdrawals WHERE id=?", (request_id,)
        ).fetchone()
        if not request or request["status"] != "pending":
            db.rollback()
            await call.answer("Заявку вже оброблено.", show_alert=True)
            return
        balance = db.execute(
            "SELECT referral_balance_stars FROM users WHERE id=?",
            (request["user_id"],),
        ).fetchone()
        if not balance or int(balance["referral_balance_stars"] or 0) < int(request["amount_stars"]):
            db.rollback()
            await call.answer(
                "Недостатньо реферального балансу для підтвердження.",
                show_alert=True,
            )
            return
        db.execute(
            """UPDATE users
               SET referral_balance_stars=MAX(referral_balance_stars-?,0)
               WHERE id=?""",
            (request["amount_stars"], request["user_id"]),
        )
        db.execute(
            """UPDATE referral_withdrawals
               SET status='paid',admin_id=?,processed_at=?
               WHERE id=? AND status='pending'""",
            (call.from_user.id, now(), request_id),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    await call.answer("Заявку підтверджено як виплачену.")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await bot.send_message(
        request["user_id"],
        f"✅ <b>Реферальний вивід #{request_id} підтверджено!</b>\n\n"
        f"💰 Сума: <b>{request['amount_stars']} Stars</b>\n"
        f"🪙 TON-адреса: <code>{esc(request['ton_wallet'])}</code>\n\n"
        "Адміністратор позначив виплату виконаною.",
        reply_markup=main_menu(request["user_id"]),
    )


@dp.callback_query(F.data.startswith("refwd:reject:"))
async def referral_withdrawal_reject(call: CallbackQuery):
    if not is_staff(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    request_id = int(call.data.rsplit(":", 1)[1])
    cur = db.execute(
        """UPDATE referral_withdrawals
           SET status='rejected',admin_id=?,admin_note=?,processed_at=?
           WHERE id=? AND status='pending'""",
        (call.from_user.id, "Отклонено администратором", now(), request_id),
    )
    changed = cur.rowcount
    db.commit()
    if not changed:
        await call.answer("Заявку вже оброблено.", show_alert=True)
        return
    request = db.execute(
        "SELECT * FROM referral_withdrawals WHERE id=?", (request_id,)
    ).fetchone()
    await call.answer("Заявку відхилено.")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await bot.send_message(
        request["user_id"],
        f"❌ <b>Реферальний вивід #{request_id} відхилено.</b>\n\n"
        "Реферальний баланс не списано. Перевірте TON-адресу та створіть нову заявку.",
        reply_markup=main_menu(request["user_id"]),
    )


@dp.callback_query(F.data.startswith("admin_no:"))
async def admin_no(call: CallbackQuery, state: FSMContext):
    if not is_staff(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    oid = int(call.data.split(":")[1])
    order = db.execute(
        "SELECT claimed_by,status FROM orders WHERE id=?", (oid,)
    ).fetchone()
    if (
        not order
        or order["claimed_by"] != call.from_user.id
        or order["status"] != "waiting_review"
    ):
        await call.answer("Спочатку натисніть «Взяти квитанцію».", show_alert=True)
        return
    await call.answer()
    await state.update_data(reject_order=oid)
    await state.set_state(Form.reject_reason)
    await call.message.answer(
        f"🔴 Вкажіть причину відхилення замовлення #{oid}:",
        reply_markup=cancel_kb(),
    )


@dp.message(Form.reject_reason)
async def reject_reason(message: Message, state: FSMContext):
    if not is_staff(message.from_user.id):
        return
    data = await state.get_data()
    oid = data.get("reject_order")
    reason = (message.text or message.caption or "").strip() or "Без причини"
    reason_html = message_html(message).strip() or esc(reason)
    try:
        db.execute("BEGIN IMMEDIATE")
        order = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if (
            not order
            or order["claimed_by"] != message.from_user.id
            or order["status"] != "waiting_review"
        ):
            db.rollback()
            await state.clear()
            await message.answer("❌ Заявку вже оброблено або ви не взяли її в роботу.")
            return
        cursor = db.execute(
            """UPDATE orders
               SET status='rejected',admin_note=?,admin_note_html=?,
                   withdrawal_reserved=0
               WHERE id=? AND claimed_by=? AND status='waiting_review'""",
            (reason, reason_html, oid, message.from_user.id),
        )
        if cursor.rowcount != 1:
            db.rollback()
            await state.clear()
            await message.answer("❌ Заявку вже оброблено.")
            return
        if (
            order["order_type"] == "withdraw_stars"
            and order["withdrawal_source"] != "referral"
            and order["withdrawal_reserved"]
        ):
            db.execute(
                "UPDATE users SET balance_stars=balance_stars+? WHERE id=?",
                (int(order["quantity"]), order["user_id"]),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    await state.clear()
    await message.answer(
        f"🔴 Замовлення #{oid} відхилено.",
        reply_markup=main_menu(message.from_user.id),
    )
    if order["order_type"] == "withdraw_stars" and order["withdrawal_source"] == "referral":
        language = user_language(order["user_id"])
        rejection = {
            "uk": (
                f"🔴 <b>Вашу заявку на вивід реферальних Stars #{oid} відхилено.</b>\n\n"
                f"Реферальний баланс не списано.\nПричина: {reason_html}"
            ),
            "ru": (
                f"🔴 <b>Ваша заявка на вывод реферальных Stars #{oid} отклонена.</b>\n\n"
                f"Реферальный баланс не списан.\nПричина: {reason_html}"
            ),
            "en": (
                f"🔴 <b>Your referral Stars withdrawal request #{oid} was rejected.</b>\n\n"
                f"Your referral balance was not deducted.\nReason: {reason_html}"
            ),
        }[language]
    else:
        rejection = (
            f"🔴 <b>Ваше замовлення #{oid} відхилено.</b>\n\nПричина: {reason_html}"
        )
    await bot.send_message(order["user_id"], rejection)


async def collect_review_message(message: Message):
    review = db.execute(
        """SELECT * FROM customer_reviews
           WHERE user_id=? AND status IN ('waiting_rating','waiting_comment')
           ORDER BY created_at,order_id LIMIT 1""",
        (message.from_user.id,),
    ).fetchone()
    if not review:
        return

    image_file_id = None
    screenshot_type = None
    if message.photo:
        image_file_id = message.photo[-1].file_id
        screenshot_type = "photo"
    elif (
        message.document
        and (message.document.mime_type or "").lower().startswith("image/")
    ):
        image_file_id = message.document.file_id
        screenshot_type = "document"

    comment = (message.text or message.caption or "").strip()
    if image_file_id and not comment:
        await bot.send_message(
            message.from_user.id,
            review_text(
                message.from_user.id,
                "Фото теж необов’язкове. Якщо додаєте його, надішліть фото з коментарем у підписі одним повідомленням; або надішліть лише текст коментаря.",
                "Фото тоже необязательно. Если добавляете его, отправьте фото с комментарием в подписи одним сообщением; либо отправьте только текст комментария.",
                "A photo is optional. If you include one, send it with the comment in the caption as one message, or send a text-only comment.",
            ),
        )
        if review["status"] == "waiting_rating":
            await bot.send_message(
                message.from_user.id,
                review_rating_prompt(message.from_user.id),
                reply_markup=review_rating_kb(
                    review["order_id"],
                    message.from_user.id,
                ),
            )
        else:
            await bot.send_message(
                message.from_user.id,
                review_comment_prompt(message.from_user.id),
            )
        return
    if not comment and not image_file_id:
        await bot.send_message(
            message.from_user.id,
            (
                review_rating_prompt(message.from_user.id)
                if review["status"] == "waiting_rating"
                else review_comment_prompt(message.from_user.id)
            ),
            reply_markup=(
                review_rating_kb(review["order_id"], message.from_user.id)
                if review["status"] == "waiting_rating" else None
            ),
        )
        return
    if not image_file_id and not message.text:
        await bot.send_message(
            message.from_user.id,
            review_text(
                message.from_user.id,
                "Надішліть текст коментаря або фото з підписом-коментарем.",
                "Отправьте текст комментария или фото с подписью-комментарием.",
                "Send a text comment or a photo with a comment in its caption.",
            ),
        )
        return
    if len(comment) > 500:
        await bot.send_message(
            message.from_user.id,
            review_text(
                message.from_user.id,
                "Скоротіть коментар до 500 символів.",
                "Сократите комментарий до 500 символов.",
                "Please shorten the comment to 500 characters.",
            ),
        )
        return
    next_status = (
        "pending_moderation" if review["rating"] is not None else "waiting_rating"
    )
    cursor = db.execute(
        """UPDATE customer_reviews
           SET screenshot_file_id=COALESCE(?,screenshot_file_id),
               screenshot_type=COALESCE(?,screenshot_type),
               comment=?,comment_html=?,status=?
           WHERE order_id=? AND user_id=?
             AND status IN ('waiting_rating','waiting_comment')""",
        (
            image_file_id,
            screenshot_type,
            comment,
            message_html(message).strip() or esc(comment),
            next_status,
            review["order_id"],
            message.from_user.id,
        ),
    )
    db.commit()
    if cursor.rowcount == 1:
        if next_status == "pending_moderation":
            await send_review_moderation_notice(review["order_id"])
            await notify_review_submitted(message.from_user.id)
            await send_pending_review_prompt(message.from_user.id)
        else:
            await bot.send_message(
                message.from_user.id,
                review_rating_prompt(message.from_user.id),
                reply_markup=review_rating_kb(
                    review["order_id"],
                    message.from_user.id,
                ),
            )




# ---------------------------------------------------------------------------
# Spanix full shop update: inline navigation, product catalogues and fulfilment
# ---------------------------------------------------------------------------

def order_json_meta(order):
    try:
        raw = order["admin_note"] or "{}"
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (KeyError, TypeError, ValueError):
        return {}


def stars_delivery_kb(order_id):
    b = InlineKeyboardBuilder()
    b.row(kb_button("✅ Підтвердити надсилання Stars", style="success", callback_data=f"admin_stars_delivered:{order_id}"))
    return b.as_markup()


def product_delivery_kb(order_id):
    b = InlineKeyboardBuilder()
    b.row(kb_button("✅ Підтвердити видачу товару", style="success", callback_data=f"admin_product_delivered:{order_id}"))
    return b.as_markup()


def product_emoji_markup(value, fallback="🎁"):
    return game_emoji_markup(value, fallback)


def product_catalog_kb(category):
    b = InlineKeyboardBuilder()
    rows = db.execute(
        "SELECT id,name,emoji,price FROM shop_products WHERE category=? AND active=1 ORDER BY id DESC",
        (category,),
    ).fetchall()
    for row in rows:
        emoji_value = str(row["emoji"] or "🎁")
        emoji_id = emoji_value if emoji_value.isdigit() else None
        visible_emoji = "" if emoji_id else f"{esc(emoji_value)} "
        b.row(kb_button(
            f"{visible_emoji}{row['name']} · {float(row['price']):g} грн"[:60],
            emoji_id=emoji_id, style="success", callback_data=f"product:open:{row['id']}",
        ))
    b.row(back_inline())
    return b.as_markup()


def admin_products_menu_kb():
    b = InlineKeyboardBuilder()
    b.row(kb_button("➕ Додати NFT", style="success", callback_data="adm:product:add:nft"))
    b.row(kb_button("➕ Додати товар", style="success", callback_data="adm:product:add:digital"))
    b.row(kb_button("📋 Каталог NFT", style="primary", callback_data="adm:product:list:nft"))
    b.row(kb_button("📋 Товари", style="primary", callback_data="adm:product:list:digital"))
    b.row(back_inline("adm:section:catalog", "⬅️ До каталогів"))
    return b.as_markup()


def admin_product_list_kb(category):
    b = InlineKeyboardBuilder()
    rows = db.execute(
        "SELECT id,name,price,active FROM shop_products WHERE category=? ORDER BY id DESC",
        (category,),
    ).fetchall()
    for row in rows:
        status = "✅" if row["active"] else "⏸"
        b.row(kb_button(
            f"{status} {row['name']} · {float(row['price']):g} грн"[:60],
            style="primary" if row["active"] else "danger",
            callback_data=f"adm:product:view:{row['id']}",
        ))
    b.row(kb_button("➕ Додати", style="success", callback_data=f"adm:product:add:{category}"))
    b.row(back_inline("adm:products"))
    return b.as_markup()


def admin_product_detail_kb(product_id, active):
    b = InlineKeyboardBuilder()
    b.row(kb_button("✏️ Змінити ціну", style="primary", callback_data=f"adm:product:price:{product_id}"))
    b.row(kb_button("⏯ Увімкнути / приховати" if not active else "⏸ Приховати", style="success" if not active else "danger", callback_data=f"adm:product:toggle:{product_id}"))
    b.row(back_inline("adm:products"))
    return b.as_markup()


async def show_product_catalog(user_id, category):
    language = user_language(user_id)
    titles = {
        "nft": {"uk": "🎁 <b>Каталог NFT</b>\\nОберіть NFT для придбання. Оренда не надається.", "ru": "🎁 <b>Каталог NFT</b>\\nВыберите NFT для покупки. Аренды нет.", "en": "🎁 <b>NFT catalogue</b>\\nChoose an NFT to purchase. Rentals are not offered."},
        "digital": {"uk": "🛍 <b>Товари</b>\\nОберіть потрібний товар.", "ru": "🛍 <b>Товары</b>\\nВыберите товар.", "en": "🛍 <b>Products</b>\\nChoose a product."},
    }
    has_products = db.execute(
        "SELECT 1 FROM shop_products WHERE category=? AND active=1 LIMIT 1",
        (category,),
    ).fetchone() is not None
    empty = {
        "uk": "\n\nПоки що в цьому каталозі немає товарів. Загляньте пізніше.",
        "ru": "\n\nВ этом каталоге пока нет товаров. Загляните позже.",
        "en": "\n\nThere are no products in this catalogue yet. Please check back later.",
    }[language]
    await replace_screen(
        user_id, titles[category][language] + ("" if has_products else empty),
        image="welcome", reply_markup=product_catalog_kb(category),
    )


@dp.callback_query(F.data.startswith("adm:section:"))
async def open_admin_section(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    section = call.data.rsplit(":", 1)[1]
    titles = {
        "catalog": "🛍 <b>Каталоги товарів</b>",
        "payments": "💳 <b>Оплати та курси</b>",
        "orders": "📦 <b>Замовлення і квитанції</b>",
        "users": "👥 <b>Користувачі та статистика</b>",
        "reviews": "💬 <b>Відгуки</b>",
        "appearance": "🎨 <b>Оформлення бота</b>",
        "communication": "🆘 <b>Підтримка та розсилка</b>",
        "security": "🔐 <b>Безпека та доступ</b>",
    }
    if section not in titles:
        await call.answer("Розділ не знайдено.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(titles[section], reply_markup=admin_section_kb(section))


@dp.callback_query(F.data == "adm:products")
async def admin_products_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    await call.answer()
    await call.message.answer("🎁 <b>Каталог NFT та товарів</b>\\nДодавання означає продаж товару; оренди NFT немає.", reply_markup=admin_products_menu_kb())


@dp.callback_query(F.data.startswith("adm:product:add:"))
async def admin_product_add_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    category = call.data.rsplit(":", 1)[1]
    if category not in {"nft", "digital"}:
        await call.answer("Невідома категорія.", show_alert=True)
        return
    await state.clear()
    await state.update_data(shop_product_category=category)
    await state.set_state(Form.shop_product_name)
    await call.answer()
    await call.message.answer("Введіть назву товару:", reply_markup=cancel_kb())


@dp.message(Form.shop_product_name)
async def admin_product_name_save(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name or len(name) > 80:
        await message.answer("Назва має містити від 1 до 80 символів.", reply_markup=cancel_kb())
        return
    await state.update_data(shop_product_name=name)
    await state.set_state(Form.shop_product_description)
    await message.answer("Введіть опис товару (або надішліть - щоб пропустити):", reply_markup=cancel_kb())


@dp.message(Form.shop_product_description)
async def admin_product_description_save(message: Message, state: FSMContext):
    description = (message.text or "").strip()
    if description == "-":
        description = ""
    if len(description) > 700:
        await message.answer("Опис має бути до 700 символів.", reply_markup=cancel_kb())
        return
    await state.update_data(shop_product_description=description)
    await state.set_state(Form.shop_product_emoji)
    await message.answer("Надішліть емодзі або Premium Emoji для товару (або - для 🎁):", reply_markup=cancel_kb())


@dp.message(Form.shop_product_emoji)
async def admin_product_emoji_save(message: Message, state: FSMContext):
    emoji = received_emoji_token(message, "🎁")
    if emoji == "-":
        emoji = "🎁"
    if len(emoji) > 100:
        await message.answer("Надішліть одне емодзі або Premium Emoji.", reply_markup=cancel_kb())
        return
    await state.update_data(shop_product_emoji=emoji)
    await state.set_state(Form.shop_product_price)
    await message.answer("Введіть ціну товару в гривнях (наприклад 125.50):", reply_markup=cancel_kb())


@dp.message(Form.shop_product_price)
async def admin_product_price_save(message: Message, state: FSMContext):
    try:
        price = Decimal((message.text or "").strip().replace(",", "."))
        if not price.is_finite() or price <= 0 or price > Decimal("10000000"):
            raise ValueError
    except Exception:
        await message.answer("Введіть коректну ціну більше нуля.", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    db.execute(
        "INSERT INTO shop_products(category,name,description,emoji,price,created_at) VALUES(?,?,?,?,?,?)",
        (data["shop_product_category"], data["shop_product_name"], data.get("shop_product_description", ""), data.get("shop_product_emoji", "🎁"), float(price), now()),
    )
    db.commit()
    await state.clear()
    await message.answer("✅ Товар додано до каталогу.", reply_markup=admin_products_menu_kb())


@dp.callback_query(F.data.startswith("adm:product:list:"))
async def admin_product_list(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    category = call.data.rsplit(":", 1)[1]
    if category not in {"nft", "digital"}:
        await call.answer("Невідома категорія.", show_alert=True)
        return
    await call.answer()
    await call.message.answer("📋 <b>Товари каталогу</b>", reply_markup=admin_product_list_kb(category))


@dp.callback_query(F.data.startswith("adm:product:view:"))
async def admin_product_view(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    try:
        product_id = int(call.data.rsplit(":", 1)[1])
    except ValueError:
        await call.answer("Некоректний ID товару.", show_alert=True)
        return
    row = db.execute("SELECT * FROM shop_products WHERE id=?", (product_id,)).fetchone()
    if not row:
        await call.answer("Товар не знайдено.", show_alert=True)
        return
    await call.answer()
    category_label = "NFT" if row["category"] == "nft" else "Товар"
    await call.message.answer(
        f"{product_emoji_markup(row['emoji'])} <b>{esc(row['name'])}</b>\\n"
        f"Категорія: {category_label}\\nЦіна: <b>{float(row['price']):g} грн</b>\\n"
        f"Статус: {'активний' if row['active'] else 'прихований'}\\n\\n{esc(row['description'])}",
        reply_markup=admin_product_detail_kb(product_id, bool(row["active"])),
    )


@dp.callback_query(F.data.startswith("adm:product:toggle:"))
async def admin_product_toggle(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    product_id = int(call.data.rsplit(":", 1)[1])
    row = db.execute("SELECT active FROM shop_products WHERE id=?", (product_id,)).fetchone()
    if not row:
        await call.answer("Товар не знайдено.", show_alert=True)
        return
    new_value = 0 if row["active"] else 1
    db.execute("UPDATE shop_products SET active=? WHERE id=?", (new_value, product_id))
    db.commit()
    await call.answer("Статус товару оновлено.")
    fresh = db.execute("SELECT * FROM shop_products WHERE id=?", (product_id,)).fetchone()
    await call.message.answer(
        f"Товар: <b>{esc(fresh['name'])}</b>\\nСтатус: {'активний' if fresh['active'] else 'прихований'}",
        reply_markup=admin_product_detail_kb(product_id, bool(fresh["active"])),
    )


@dp.callback_query(F.data.startswith("adm:product:price:"))
async def admin_product_edit_price_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    product_id = int(call.data.rsplit(":", 1)[1])
    if not db.execute("SELECT 1 FROM shop_products WHERE id=?", (product_id,)).fetchone():
        await call.answer("Товар не знайдено.", show_alert=True)
        return
    await state.clear()
    await state.update_data(shop_product_edit_id=product_id)
    await state.set_state(Form.shop_product_edit_price)
    await call.answer()
    await call.message.answer("Введіть нову ціну в гривнях:", reply_markup=cancel_kb())


@dp.message(Form.shop_product_edit_price)
async def admin_product_edit_price_save(message: Message, state: FSMContext):
    try:
        price = Decimal((message.text or "").strip().replace(",", "."))
        if not price.is_finite() or price <= 0 or price > Decimal("10000000"):
            raise ValueError
    except Exception:
        await message.answer("Введіть коректну ціну більше нуля.", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    db.execute("UPDATE shop_products SET price=? WHERE id=?", (float(price), data["shop_product_edit_id"]))
    db.commit()
    await state.clear()
    await message.answer("✅ Ціну оновлено.", reply_markup=admin_products_menu_kb())


@dp.callback_query(F.data.startswith("product:open:"))
async def product_open(call: CallbackQuery):
    try:
        product_id = int(call.data.rsplit(":", 1)[1])
    except ValueError:
        await call.answer("Некоректний товар.", show_alert=True)
        return
    row = db.execute("SELECT * FROM shop_products WHERE id=? AND active=1", (product_id,)).fetchone()
    if not row:
        await call.answer("Товар більше недоступний.", show_alert=True)
        return
    await call.answer()
    category_label = "NFT" if row["category"] == "nft" else {"uk": "Товар", "ru": "Товар", "en": "Digital good"}[user_language(call.from_user.id)]
    builder = InlineKeyboardBuilder()
    builder.row(kb_button("🛒 Придбати", style="success", callback_data=f"product:buy:{product_id}"))
    builder.row(back_inline(f"product:list:{row['category']}"))
    await replace_screen(
        call.from_user.id,
        f"{product_emoji_markup(row['emoji'])} <b>{esc(row['name'])}</b>\\n"
        f"{category_label}\\n\\n{esc(row['description'])}\\n\\n"
        f"💰 Ціна: <b>{float(row['price']):g} грн</b>\\n\\n"
        "Після оплати адміністратор підтвердить фактичну видачу товару.",
        image="welcome", reply_markup=builder.as_markup(),
    )


@dp.callback_query(F.data.startswith("product:list:"))
async def product_list_callback(call: CallbackQuery):
    category = call.data.rsplit(":", 1)[1]
    if category not in {"nft", "digital"}:
        await call.answer("Невідома категорія.", show_alert=True)
        return
    await call.answer()
    await show_product_catalog(call.from_user.id, category)


@dp.callback_query(F.data.startswith("product:buy:"))
async def product_buy(call: CallbackQuery):
    try:
        product_id = int(call.data.rsplit(":", 1)[1])
    except ValueError:
        await call.answer("Некоректний товар.", show_alert=True)
        return
    row = db.execute("SELECT * FROM shop_products WHERE id=? AND active=1", (product_id,)).fetchone()
    if not row:
        await call.answer("Товар більше недоступний.", show_alert=True)
        return
    order_type = "nft_purchase" if row["category"] == "nft" else "digital_product"
    meta = {"product_id": product_id, "product_name": row["name"], "category": row["category"]}
    order_id = create_order(call.from_user.id, order_type, row["name"], float(row["price"]))
    db.execute("UPDATE orders SET admin_note=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), order_id))
    db.commit()
    await call.answer()
    await replace_screen(
        call.from_user.id,
        f"🛒 <b>{esc(row['name'])}</b>\\n💰 До оплати: <b>{float(row['price']):g} грн</b>\\n\\nОберіть спосіб оплати:",
        image="welcome", reply_markup=payment_kb(order_id),
    )


@dp.callback_query(F.data.startswith("admin_product_delivered:"))
async def admin_product_delivered(call: CallbackQuery):
    if not is_staff(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    order_id = int(call.data.rsplit(":", 1)[1])
    db.execute("BEGIN IMMEDIATE")
    order = db.execute("SELECT * FROM orders WHERE id=? AND order_type IN ('nft_purchase','digital_product')", (order_id,)).fetchone()
    if not order or order["status"] != "waiting_product_delivery":
        db.rollback()
        await call.answer("Замовлення вже оброблено.", show_alert=True)
        return
    cur = db.execute("UPDATE orders SET status='completed' WHERE id=? AND status='waiting_product_delivery'", (order_id,))
    if cur.rowcount != 1:
        db.rollback()
        await call.answer("Замовлення вже оброблено.", show_alert=True)
        return
    db.execute("UPDATE users SET spent_uah=spent_uah+?,status='Клієнт' WHERE id=?", (order["amount"], order["user_id"]))
    db.commit()
    await call.answer("Видачу підтверджено.")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(f"✅ Видачу товару за замовленням #{order_id} підтверджено.")
    meta = order_json_meta(order)
    await bot.send_message(
        order["user_id"],
        f"🎉 <b>Замовлення #{order_id} виконано!</b>\\n\\n"
        f"🎁 Товар: <b>{esc(meta.get('product_name') or order['quantity'])}</b>\\n"
        "Дякуємо за покупку! Будемо вдячні за відгук.",
        reply_markup=main_menu(order["user_id"]),
    )
    fresh = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    await send_review_request(fresh)


@dp.callback_query(F.data.startswith("admin_stars_delivered:"))
async def admin_stars_delivered(call: CallbackQuery):
    if not is_staff(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    order_id = int(call.data.rsplit(":", 1)[1])
    db.execute("BEGIN IMMEDIATE")
    order = db.execute("SELECT * FROM orders WHERE id=? AND order_type='buy_stars'", (order_id,)).fetchone()
    if not order or order["status"] != "waiting_stars_delivery":
        db.rollback()
        await call.answer("Замовлення вже оброблено.", show_alert=True)
        return
    meta = order_json_meta(order)
    if meta.get("recipient_type") != "friend":
        db.rollback()
        await call.answer("Це не замовлення Stars для друга.", show_alert=True)
        return
    cur = db.execute("UPDATE orders SET status='completed' WHERE id=? AND status='waiting_stars_delivery'", (order_id,))
    if cur.rowcount != 1:
        db.rollback()
        await call.answer("Замовлення вже оброблено.", show_alert=True)
        return
    db.execute("UPDATE users SET bought_stars=bought_stars+?,spent_uah=spent_uah+?,status='Клієнт' WHERE id=?", (int(order["quantity"]), order["amount"], order["user_id"]))
    db.commit()
    await call.answer("Надсилання позначено виконаним.")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(f"✅ Stars за замовленням #{order_id} позначено надісланими.")
    await bot.send_message(
        order["user_id"],
        f"🎉 <b>Stars за замовленням #{order_id} надіслані!</b>\\n\\n"
        f"⭐ Кількість: <b>{esc(order['quantity'])} Stars</b>\\n"
        f"🎁 Отримувач: <b>{esc(meta.get('recipient') or 'друг')}</b>\\n"
        "Дякуємо за покупку! Будемо вдячні за відгук.",
        reply_markup=main_menu(order["user_id"]),
    )
    fresh = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    await send_review_request(fresh)


@dp.callback_query(F.data.startswith("adm:gamepkg_edit:"))
async def admin_game_package_edit(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    package_id = int(call.data.rsplit(":", 1)[1])
    package = db.execute("SELECT * FROM game_packages WHERE id=?", (package_id,)).fetchone()
    if not package:
        await call.answer("Пакет не знайдено.", show_alert=True)
        return
    b = InlineKeyboardBuilder()
    b.row(kb_button("✏️ Назва товару", style="primary", callback_data=f"adm:gamepkg_name:{package_id}"))
    b.row(kb_button("✏️ Кількість", style="primary", callback_data=f"adm:gamepkg_quantity:{package_id}"))
    b.row(kb_button("💰 Ціна", style="primary", callback_data=f"adm:gamepkg_price:{package_id}"))
    b.row(kb_button("🙈 Приховати пакет", style="danger", callback_data=f"adm:gamepkg:{package_id}"))
    b.row(back_inline(f"adm:game:{package['game_id']}"))
    await call.answer()
    await call.message.answer(
        f"📦 <b>{esc(package['product_name'])}</b>\\nКількість: {esc(package['quantity'])}\\nЦіна: {float(package['price']):g} грн",
        reply_markup=b.as_markup(),
    )


@dp.callback_query(F.data.startswith("adm:gamepkg_price:"))
async def admin_game_package_price_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    package_id = int(call.data.rsplit(":", 1)[1])
    if not db.execute("SELECT 1 FROM game_packages WHERE id=?", (package_id,)).fetchone():
        await call.answer("Пакет не знайдено.", show_alert=True)
        return
    await state.clear()
    await state.update_data(game_package_edit_id=package_id)
    await state.set_state(Form.game_package_edit_price)
    await call.answer()
    await call.message.answer("Введіть нову ціну пакета в гривнях:", reply_markup=cancel_kb())


@dp.message(Form.game_package_edit_price)
async def admin_game_package_price_save(message: Message, state: FSMContext):
    try:
        price = Decimal((message.text or "").strip().replace(",", "."))
        if not price.is_finite() or price <= 0 or price > Decimal("10000000"):
            raise ValueError
    except Exception:
        await message.answer("Введіть коректну ціну більше нуля.", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    db.execute("UPDATE game_packages SET price=? WHERE id=?", (float(price), data["game_package_edit_id"]))
    db.commit()
    await state.clear()
    await message.answer("✅ Ціну пакета оновлено.", reply_markup=admin_menu_kb())


@dp.callback_query(F.data.startswith("adm:gamepkg_name:"))
async def admin_game_package_name_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    package_id = int(call.data.rsplit(":", 1)[1])
    if not db.execute("SELECT 1 FROM game_packages WHERE id=?", (package_id,)).fetchone():
        await call.answer("Пакет не знайдено.", show_alert=True)
        return
    await state.clear()
    await state.update_data(game_package_edit_id=package_id)
    await state.set_state(Form.game_package_edit_name)
    await call.answer()
    await call.message.answer("Введіть нову назву пакета:", reply_markup=cancel_kb())


@dp.message(Form.game_package_edit_name)
async def admin_game_package_name_save(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name or len(name) > 80:
        await message.answer("Назва має містити 1–80 символів.", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    db.execute("UPDATE game_packages SET product_name=? WHERE id=?", (name, data["game_package_edit_id"]))
    db.commit()
    await state.clear()
    await message.answer("✅ Назву пакета оновлено.", reply_markup=admin_menu_kb())


@dp.callback_query(F.data.startswith("adm:gamepkg_quantity:"))
async def admin_game_package_quantity_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.", show_alert=True)
        return
    package_id = int(call.data.rsplit(":", 1)[1])
    if not db.execute("SELECT 1 FROM game_packages WHERE id=?", (package_id,)).fetchone():
        await call.answer("Пакет не знайдено.", show_alert=True)
        return
    await state.clear()
    await state.update_data(game_package_edit_id=package_id)
    await state.set_state(Form.game_package_edit_quantity)
    await call.answer()
    await call.message.answer("Введіть нову кількість пакета:", reply_markup=cancel_kb())


@dp.message(Form.game_package_edit_quantity)
async def admin_game_package_quantity_save(message: Message, state: FSMContext):
    quantity = (message.text or "").strip()
    if not quantity or len(quantity) > 40:
        await message.answer("Кількість має містити 1–40 символів.", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    db.execute("UPDATE game_packages SET quantity=? WHERE id=?", (quantity, data["game_package_edit_id"]))
    db.commit()
    await state.clear()
    await message.answer("✅ Кількість пакета оновлено.", reply_markup=admin_menu_kb())


@dp.callback_query(F.data == "cancel:state")
async def cancel_inline_state(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    if is_admin(call.from_user.id):
        await call.message.answer("Дію скасовано.", reply_markup=admin_menu_kb())
    else:
        await replace_screen(
            call.from_user.id,
            {"uk": "✅ Дію скасовано. Оберіть розділ нижче 👇", "ru": "✅ Действие отменено. Выберите раздел ниже 👇", "en": "✅ Action cancelled. Choose a section below 👇"}[user_language(call.from_user.id)],
            image="welcome", reply_markup=main_menu(call.from_user.id),
        )


@dp.callback_query(F.data.startswith("profile:orders:"))
async def profile_orders(call: CallbackQuery):
    try:
        page = max(0, int(call.data.rsplit(":", 1)[1]))
    except ValueError:
        page = 0
    page_size = 8
    total = db.execute("SELECT COUNT(*) c FROM orders WHERE user_id=?", (call.from_user.id,)).fetchone()["c"]
    pages = max(1, math.ceil(total / page_size))
    page = min(page, pages - 1)
    rows = db.execute(
        "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
        (call.from_user.id, page_size, page * page_size),
    ).fetchall()
    language = user_language(call.from_user.id)
    status_labels = {
        "uk": {"waiting_payment": "очікує оплати", "waiting_review": "перевірка квитанції", "waiting_ton_delivery": "переказ TON", "waiting_premium_delivery": "активація Premium", "waiting_game_application": "очікує заявку", "waiting_game_admin_approval": "заявка адміністратору", "waiting_game_details": "очікує ID / нік", "waiting_game_delivery": "виконання донату", "waiting_product_delivery": "видача товару", "waiting_stars_delivery": "надсилання Stars", "waiting_virtual_number_request": "очікує запиту номера", "waiting_virtual_number_delivery": "підготовка номера", "waiting_virtual_number_code_request": "очікує запиту коду", "waiting_virtual_number_code_requested": "запит передано адміну", "waiting_virtual_number_final": "перевірка завершення", "completed": "виконано", "rejected": "відхилено", "cancelled": "скасовано"},
        "ru": {"waiting_payment": "ожидает оплаты", "waiting_review": "проверка квитанции", "waiting_ton_delivery": "перевод TON", "waiting_premium_delivery": "активация Premium", "waiting_game_application": "ожидает заявку", "waiting_game_admin_approval": "заявка администратору", "waiting_game_details": "ожидает ID / ник", "waiting_game_delivery": "выполнение доната", "waiting_product_delivery": "выдача товара", "waiting_stars_delivery": "отправка Stars", "waiting_virtual_number_request": "ожидает запроса номера", "waiting_virtual_number_delivery": "подготовка номера", "waiting_virtual_number_code_request": "ожидает запроса кода", "waiting_virtual_number_code_requested": "запрос передан админу", "waiting_virtual_number_final": "проверка завершения", "completed": "выполнено", "rejected": "отклонено", "cancelled": "отменено"},
        "en": {"waiting_payment": "awaiting payment", "waiting_review": "receipt review", "waiting_ton_delivery": "TON transfer", "waiting_premium_delivery": "Premium activation", "waiting_game_application": "application pending", "waiting_game_admin_approval": "admin application", "waiting_game_details": "awaiting game ID / nickname", "waiting_game_delivery": "game top-up", "waiting_product_delivery": "product delivery", "waiting_stars_delivery": "Stars delivery", "waiting_virtual_number_request": "awaiting number request", "waiting_virtual_number_delivery": "number preparation", "waiting_virtual_number_code_request": "awaiting code request", "waiting_virtual_number_code_requested": "request sent to admin", "waiting_virtual_number_final": "completion review", "completed": "completed", "rejected": "rejected", "cancelled": "cancelled"},
    }[language]
    type_labels = {"uk": {"buy_stars": "Stars", "buy_ton": "TON", "telegram_premium": "Telegram Premium", "game_donation": "Донат гри", "nft_purchase": "NFT", "digital_product": "Товар", "virtual_number": "Віртуальний номер", "withdraw_stars": "Виведення Stars"}, "ru": {"buy_stars": "Stars", "buy_ton": "TON", "telegram_premium": "Telegram Premium", "game_donation": "Донат игры", "nft_purchase": "NFT", "digital_product": "Товар", "virtual_number": "Виртуальный номер", "withdraw_stars": "Вывод Stars"}, "en": {"buy_stars": "Stars", "buy_ton": "TON", "telegram_premium": "Telegram Premium", "game_donation": "Game top-up", "nft_purchase": "NFT", "digital_product": "Product", "virtual_number": "Virtual number", "withdraw_stars": "Stars withdrawal"}}[language]
    if rows:
        lines = []
        for row in rows:
            meta = order_json_meta(row)
            label = type_labels.get(row["order_type"], row["order_type"])
            if row["order_type"] in {"nft_purchase", "digital_product", "virtual_number"}:
                label += f" · {meta.get('product_name') or meta.get('number_title') or row['quantity']}"
            elif row["order_type"] == "game_donation":
                label += f" · {meta.get('game_name') or ''} {meta.get('product_name') or ''}"
            elif row["order_type"] == "buy_stars" and meta.get("recipient_type") == "friend":
                label += f" · {meta.get('recipient') or 'friend'}"
            lines.append(
                f"<b>#{row['id']}</b> {esc(label)}\\n"
                f"{esc(row['created_at'])} · {float(row['amount']):.2f} грн\\n"
                f"Статус: <b>{esc(status_labels.get(row['status'], row['status']))}</b>"
            )
        text = "📦 <b>Історія замовлень</b>\\n\\n" + "\\n\\n".join(lines)
    else:
        text = {"uk": "📦 <b>Історія замовлень</b>\\n\\nПокупок ще немає.", "ru": "📦 <b>История заказов</b>\\n\\nПокупок пока нет.", "en": "📦 <b>Order history</b>\\n\\nNo orders yet."}[language]
    b = InlineKeyboardBuilder()
    nav = []
    if page > 0:
        nav.append(kb_button("⬅️", callback_data=f"profile:orders:{page-1}"))
    if page + 1 < pages:
        nav.append(kb_button("➡️", callback_data=f"profile:orders:{page+1}"))
    if nav:
        b.row(*nav)
    b.row(back_inline("menu:profile", "⬅️ До профілю"))
    await call.answer()
    await replace_screen(call.from_user.id, text, image="profile", reply_markup=b.as_markup())


@dp.callback_query(F.data.startswith("menu:"))
async def inline_main_menu_dispatch(call: CallbackQuery, state: FSMContext):
    key = call.data.split(":", 1)[1]
    await call.answer()
    if key == "language":
        await state.clear()
        await show_language_screen(call.from_user.id)
        return
    if key == "games":
        await show_virtual_number_catalog(call.from_user.id)
        return
    if key == "nft":
        await show_product_catalog(call.from_user.id, "nft")
        return
    if key == "digital":
        await show_product_catalog(call.from_user.id, "digital")
        return
    handlers = {
        "buy_stars": (buy_stars, False),
        "buy_ton": (buy_ton, True),
        "premium": (premium_menu, False),
        "withdraw": (withdraw, True),
        "sell": (sell_stars, True),
        "profile": (profile, False),
        "referrals": (referrals, False),
        "calculator": (calculator, True),
        "reviews": (reviews, False),
        "support": (support, True),
    }
    handler_data = handlers.get(key)
    if not handler_data:
        await call.answer("Невідомий розділ.", show_alert=True)
        return
    handler, takes_state = handler_data
    fake_message = SimpleNamespace(from_user=call.from_user)
    if takes_state:
        await handler(fake_message, state)
    else:
        await handler(fake_message)

# ---------------------------------------------------------------------------
# Virtual phone numbers: catalogue + safe, manual fulfilment workflow.
# Verification/OTP codes are never collected, stored, or forwarded by this bot.
# ---------------------------------------------------------------------------
def virtual_number_catalog_kb():
    b = InlineKeyboardBuilder()
    rows = db.execute("SELECT id,title,emoji,price FROM virtual_number_catalog WHERE active=1 ORDER BY id DESC").fetchall()
    for row in rows:
        em = str(row["emoji"] or "📱")
        eid = em if em.isdigit() else None
        label = "" if eid else f"{em} "
        b.row(kb_button(f"{label}{row['title']} · {float(row['price']):g} грн"[:60], emoji_id=eid, style="success", callback_data=f"vnum:open:{row['id']}"))
    b.row(back_inline())
    return b.as_markup()

def virtual_number_admin_catalog_kb():
    b = InlineKeyboardBuilder()
    rows = db.execute("SELECT id,title,price,active FROM virtual_number_catalog ORDER BY id DESC").fetchall()
    for row in rows:
        mark = "✅" if row["active"] else "⏸"
        b.row(kb_button(f"{mark} {row['title']} · {float(row['price']):g} грн"[:60], style="primary", callback_data=f"adm:vnum:view:{row['id']}"))
    b.row(kb_button("➕ Додати тип номера", style="success", callback_data="adm:vnum:add"))
    b.row(back_inline("adm:section:catalog"))
    return b.as_markup()

def virtual_number_admin_delivery_kb(order_id):
    b=InlineKeyboardBuilder(); b.row(kb_button("📱 Видати номер",style="success",callback_data=f"adm:vnum:deliver:{order_id}")); return b.as_markup()

def virtual_number_admin_finish_kb(order_id):
    b=InlineKeyboardBuilder(); b.row(kb_button("✅ Підтвердити завершення",style="success",callback_data=f"adm:vnum:finish:{order_id}")); return b.as_markup()

def virtual_number_user_kb(order_id, stage="number"):
    b=InlineKeyboardBuilder()
    if stage == "number":
        b.row(kb_button("📨 Запросити номер",style="success",callback_data=f"vnum:request_number:{order_id}"))
    elif stage == "code":
        b.row(kb_button("🔐 Запросити код",style="success",callback_data=f"vnum:request_code:{order_id}"))
        b.row(kb_button("🔁 Запросити код повторно",style="primary",callback_data=f"vnum:request_code:{order_id}"))
        b.row(kb_button("✅ Я завершив вхід",style="success",callback_data=f"vnum:logged:{order_id}"))
    return b.as_markup()

async def show_virtual_number_catalog(user_id):
    rows=db.execute("SELECT 1 FROM virtual_number_catalog WHERE active=1 LIMIT 1").fetchone()
    lang=user_language(user_id)
    title={"uk":"📱 <b>Віртуальні номери</b>\nОберіть тип номера.","ru":"📱 <b>Виртуальные номера</b>\nВыберите тип номера.","en":"📱 <b>Virtual numbers</b>\nChoose a number type."}[lang]
    if not rows:
        title += {"uk":"\n\nПоки що номерів немає. Загляньте пізніше.","ru":"\n\nПока номеров нет. Загляните позже.","en":"\n\nNo numbers are available yet. Please check back later."}[lang]
    await replace_screen(user_id,title,image="welcome",reply_markup=virtual_number_catalog_kb())

@dp.callback_query(F.data == "adm:virtual_numbers")
async def admin_virtual_numbers(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Немає доступу.",show_alert=True); return
    await call.answer(); await call.message.answer("📱 <b>Каталог віртуальних номерів</b>",reply_markup=virtual_number_admin_catalog_kb())

@dp.callback_query(F.data == "adm:vnum:add")
async def admin_vnum_add(call: CallbackQuery,state:FSMContext):
    if not is_admin(call.from_user.id): await call.answer("Немає доступу.",show_alert=True); return
    await state.clear(); await state.set_state(Form.virtual_title); await call.answer(); await call.message.answer("Введіть назву типу номера / країну / сервіс:",reply_markup=cancel_kb())

@dp.message(Form.virtual_title)
async def admin_vnum_title(message:Message,state:FSMContext):
    val=(message.text or "").strip()
    if not val or len(val)>80: await message.answer("Назва має бути 1–80 символів.",reply_markup=cancel_kb()); return
    await state.update_data(vnum_title=val); await state.set_state(Form.virtual_description); await message.answer("Введіть опис або - щоб пропустити:",reply_markup=cancel_kb())

@dp.message(Form.virtual_description)
async def admin_vnum_desc(message:Message,state:FSMContext):
    val=(message.text or "").strip(); await state.update_data(vnum_desc="" if val=="-" else val[:500]); await state.set_state(Form.virtual_emoji); await message.answer("Надішліть емодзі або Premium Emoji:",reply_markup=cancel_kb())

@dp.message(Form.virtual_emoji)
async def admin_vnum_emoji(message:Message,state:FSMContext):
    val=received_emoji_token(message,"📱"); await state.update_data(vnum_emoji=val if val!="-" else "📱"); await state.set_state(Form.virtual_price); await message.answer("Введіть ціну в гривнях:",reply_markup=cancel_kb())

@dp.message(Form.virtual_price)
async def admin_vnum_price(message:Message,state:FSMContext):
    try:
        price=Decimal((message.text or "").strip().replace(",","."))
        if not price.is_finite() or price<=0 or price>Decimal("10000000"): raise ValueError
    except Exception:
        await message.answer("Вкажіть коректну ціну більше нуля.",reply_markup=cancel_kb()); return
    d=await state.get_data()
    db.execute("INSERT INTO virtual_number_catalog(title,description,emoji,price,created_at) VALUES(?,?,?,?,?)",(d["vnum_title"],d.get("vnum_desc",""),d.get("vnum_emoji","📱"),float(price),now())); db.commit(); await state.clear()
    await message.answer("✅ Тип номера додано.",reply_markup=virtual_number_admin_catalog_kb())

@dp.callback_query(F.data.startswith("adm:vnum:view:"))
async def admin_vnum_view(call:CallbackQuery):
    if not is_admin(call.from_user.id): await call.answer("Немає доступу.",show_alert=True); return
    pid=int(call.data.rsplit(":",1)[1]); row=db.execute("SELECT * FROM virtual_number_catalog WHERE id=?",(pid,)).fetchone()
    if not row: await call.answer("Не знайдено.",show_alert=True); return
    b=InlineKeyboardBuilder(); b.row(kb_button("✏️ Змінити ціну",style="primary",callback_data=f"adm:vnum:price:{pid}")); b.row(kb_button("⏯ Увімкнути / приховати",style="primary",callback_data=f"adm:vnum:toggle:{pid}")); b.row(back_inline("adm:virtual_numbers")); await call.answer(); await call.message.answer(f"📱 <b>{esc(row['title'])}</b>\n{esc(row['description'])}\nЦіна: <b>{float(row['price']):g} грн</b>",reply_markup=b.as_markup())

@dp.callback_query(F.data.startswith("adm:vnum:price:"))
async def admin_vnum_price_start(call:CallbackQuery,state:FSMContext):
    if not is_admin(call.from_user.id): await call.answer("Немає доступу.",show_alert=True); return
    pid=int(call.data.rsplit(":",1)[1]); row=db.execute("SELECT title,price FROM virtual_number_catalog WHERE id=?",(pid,)).fetchone()
    if not row: await call.answer("Не знайдено.",show_alert=True); return
    await state.clear(); await state.update_data(vnum_edit_id=pid); await state.set_state(Form.virtual_price_edit); await call.answer()
    await call.message.answer(f"Нова ціна для <b>{esc(row['title'])}</b> (зараз {float(row['price']):g} грн):",reply_markup=cancel_kb())

@dp.message(Form.virtual_price_edit)
async def admin_vnum_price_edit_save(message:Message,state:FSMContext):
    if not is_admin(message.from_user.id): return
    try:
        price=Decimal((message.text or "").strip().replace(",","."))
        if not price.is_finite() or price<=0 or price>Decimal("10000000"): raise ValueError
    except Exception:
        await message.answer("Вкажіть коректну ціну більше нуля.",reply_markup=cancel_kb()); return
    d=await state.get_data(); pid=int(d.get("vnum_edit_id",0)); db.execute("UPDATE virtual_number_catalog SET price=? WHERE id=?",(float(price),pid)); db.commit(); await state.clear()
    await message.answer(f"✅ Ціну оновлено: <b>{float(price):g} грн</b>.",reply_markup=virtual_number_admin_catalog_kb())

@dp.callback_query(F.data.startswith("adm:vnum:toggle:"))
async def admin_vnum_toggle(call:CallbackQuery):
    if not is_admin(call.from_user.id): await call.answer("Немає доступу.",show_alert=True); return
    pid=int(call.data.rsplit(":",1)[1]); row=db.execute("SELECT active FROM virtual_number_catalog WHERE id=?",(pid,)).fetchone()
    if not row: await call.answer("Не знайдено.",show_alert=True); return
    db.execute("UPDATE virtual_number_catalog SET active=? WHERE id=?",(0 if row['active'] else 1,pid)); db.commit(); await call.answer("Оновлено."); await call.message.answer("📱 Каталог номерів",reply_markup=virtual_number_admin_catalog_kb())

@dp.callback_query(F.data.startswith("vnum:open:"))
async def virtual_number_open(call:CallbackQuery):
    pid=int(call.data.rsplit(":",1)[1]); row=db.execute("SELECT * FROM virtual_number_catalog WHERE id=? AND active=1",(pid,)).fetchone()
    if not row: await call.answer("Цей тип номера вже недоступний.",show_alert=True); return
    b=InlineKeyboardBuilder(); b.row(kb_button("🛒 Придбати",style="success",callback_data=f"vnum:buy:{pid}")); b.row(back_inline("menu:games")); await call.answer()
    await replace_screen(call.from_user.id,f"📱 <b>{esc(row['title'])}</b>\n{esc(row['description'])}\n\n💰 Ціна: <b>{float(row['price']):g} грн</b>\n\nПісля підтвердження оплати зможете запросити номер. Перевірочні коди надсилаються лише безпосередньо офіційним сервісом користувачу, бот їх не збирає і не пересилає.",image="welcome",reply_markup=b.as_markup())

@dp.callback_query(F.data.startswith("vnum:buy:"))
async def virtual_number_buy(call:CallbackQuery):
    pid=int(call.data.rsplit(":",1)[1]); row=db.execute("SELECT * FROM virtual_number_catalog WHERE id=? AND active=1",(pid,)).fetchone()
    if not row: await call.answer("Недоступно.",show_alert=True); return
    meta={"catalog_id":pid,"number_title":row['title'],"product_name":row['title']}
    oid=create_order(call.from_user.id,"virtual_number",row['title'],float(row['price']))
    db.execute("UPDATE orders SET admin_note=? WHERE id=?",(json.dumps(meta,ensure_ascii=False),oid)); db.commit(); await call.answer()
    await replace_screen(call.from_user.id,f"📱 <b>{esc(row['title'])}</b>\n💰 До оплати: <b>{float(row['price']):g} грн</b>\n\nОберіть спосіб оплати:",image="welcome",reply_markup=payment_kb(oid))

@dp.callback_query(F.data.startswith("adm:vnum:deliver:"))
async def admin_vnum_deliver_start(call:CallbackQuery,state:FSMContext):
    if not is_staff(call.from_user.id): await call.answer("Немає доступу.",show_alert=True); return
    oid=int(call.data.rsplit(":",1)[1]); order=db.execute("SELECT * FROM orders WHERE id=? AND order_type='virtual_number' AND status='waiting_virtual_number_delivery'",(oid,)).fetchone()
    if not order: await call.answer("Замовлення вже оброблено.",show_alert=True); return
    await state.clear(); await state.update_data(vnum_order_id=oid); await state.set_state(Form.virtual_admin_number); await call.answer(); await call.message.answer(f"Надішліть номер для замовлення #{oid}. Не надсилайте сюди OTP/коди входу.",reply_markup=cancel_kb())

@dp.message(Form.virtual_admin_number)
async def admin_vnum_number_save(message:Message,state:FSMContext):
    if not is_staff(message.from_user.id): return
    d=await state.get_data(); oid=int(d.get("vnum_order_id",0)); number=(message.text or "").strip()
    if len(number)<5 or len(number)>40: await message.answer("Перевірте формат номера.",reply_markup=cancel_kb()); return
    order=db.execute("SELECT * FROM orders WHERE id=? AND order_type='virtual_number' AND status='waiting_virtual_number_delivery'",(oid,)).fetchone()
    if not order: await state.clear(); await message.answer("Замовлення не знайдено або вже оброблене."); return
    meta=order_json_meta(order); meta["assigned_number"]=number
    db.execute("UPDATE orders SET admin_note=?,status='waiting_virtual_number_code_request' WHERE id=?",(json.dumps(meta,ensure_ascii=False),oid)); db.commit(); await state.clear()
    b=InlineKeyboardBuilder(); b.row(kb_button("🔐 Запросити код",style="success",callback_data=f"vnum:request_code:{oid}")); b.row(kb_button("🔁 Запросити код повторно",style="primary",callback_data=f"vnum:request_code:{oid}"))
    await bot.send_message(order['user_id'],f"📱 Номер для замовлення #{oid}: <code>{esc(number)}</code>\n\nКод підтвердження отримуйте безпосередньо в офіційному застосунку/сервісі. Не надсилайте коди входу адміністраторам або в бот.",reply_markup=b.as_markup())
    await message.answer(f"✅ Номер для замовлення #{oid} передано покупцю.")

@dp.callback_query(F.data.startswith("vnum:request_number:"))
async def virtual_number_request_number(call:CallbackQuery):
    oid=int(call.data.rsplit(":",1)[1])
    order=db.execute("SELECT * FROM orders WHERE id=? AND user_id=? AND order_type='virtual_number'",(oid,call.from_user.id)).fetchone()
    if not order or order["status"]!="waiting_virtual_number_request":
        await call.answer("Запит уже оброблено або замовлення неактивне.",show_alert=True); return
    db.execute("UPDATE orders SET status='waiting_virtual_number_delivery' WHERE id=? AND status='waiting_virtual_number_request'",(oid,)); db.commit()
    meta=order_json_meta(order)
    admin_kb=virtual_number_admin_delivery_kb(oid)
    await notify_admins(f"📱 Покупець запросив номер. Замовлення #{oid} — {esc(meta.get('number_title') or order['quantity'])}. Підготуйте номер і видайте його через кнопку.",reply_markup=admin_kb)
    await call.answer("Запит передано адміністратору.")
    await call.message.answer("📨 Запит на номер передано адміністратору. Щойно номер буде готовий, він з'явиться тут.")

@dp.callback_query(F.data.startswith("vnum:request_code:"))
async def virtual_number_request_code(call:CallbackQuery):
    oid=int(call.data.rsplit(":",1)[1]); order=db.execute("SELECT * FROM orders WHERE id=? AND user_id=? AND order_type='virtual_number'",(oid,call.from_user.id)).fetchone()
    if not order or order['status'] not in {'waiting_virtual_number_code_request','waiting_virtual_number_code_requested'}: await call.answer("Кнопка вже неактивна.",show_alert=True); return
    meta=order_json_meta(order); last=float(meta.get("last_code_request",0) or 0); import time
    if time.time()-last<120: await call.answer(f"Повторний запит доступний через {max(1,int(120-(time.time()-last)))} сек.",show_alert=True); return
    meta['last_code_request']=time.time(); db.execute("UPDATE orders SET admin_note=?,status='waiting_virtual_number_code_requested' WHERE id=?",(json.dumps(meta,ensure_ascii=False),oid)); db.commit()
    await notify_admins(f"📨 Покупець запросив код для номера, замовлення #{oid}. Код входу не надсилайте в бот — покупець має отримати його безпосередньо від офіційного сервісу.")
    await call.answer("Запит передано адміністратору."); await call.message.answer("📨 Запит передано адміністратору. Перевірочний код отримуйте напряму від офіційного сервісу; бот не приймає та не пересилає коди.")

@dp.callback_query(F.data.startswith("vnum:logged:"))
async def virtual_number_logged(call:CallbackQuery):
    oid=int(call.data.rsplit(":",1)[1]); order=db.execute("SELECT * FROM orders WHERE id=? AND user_id=? AND order_type='virtual_number'",(oid,call.from_user.id)).fetchone()
    if not order or order['status'] not in {'waiting_virtual_number_code_request','waiting_virtual_number_code_requested'}: await call.answer("Замовлення неактивне.",show_alert=True); return
    db.execute("UPDATE orders SET status='waiting_virtual_number_final' WHERE id=?",(oid,)); db.commit(); await notify_admins(f"✅ Покупець повідомив, що завершив роботу з номером. Замовлення #{oid}.",reply_markup=virtual_number_admin_finish_kb(oid)); await call.answer("Повідомлення передано адміністратору."); await call.message.answer("Дякуємо! Адміністратор перевірить завершення замовлення.")

@dp.callback_query(F.data.startswith("adm:vnum:finish:"))
async def admin_vnum_finish(call:CallbackQuery):
    if not is_staff(call.from_user.id): await call.answer("Немає доступу.",show_alert=True); return
    oid=int(call.data.rsplit(":",1)[1]); db.execute("BEGIN IMMEDIATE"); order=db.execute("SELECT * FROM orders WHERE id=? AND order_type='virtual_number'",(oid,)).fetchone()
    if not order or order['status']!='waiting_virtual_number_final': db.rollback(); await call.answer("Замовлення вже оброблене.",show_alert=True); return
    cur=db.execute("UPDATE orders SET status='completed' WHERE id=? AND status='waiting_virtual_number_final'",(oid,))
    if cur.rowcount!=1: db.rollback(); await call.answer("Вже оброблено.",show_alert=True); return
    db.execute("UPDATE users SET spent_uah=spent_uah+?,status='Клієнт' WHERE id=?",(order['amount'],order['user_id'])); db.commit(); await call.answer("Завершено."); await call.message.edit_reply_markup(reply_markup=None)
    meta=order_json_meta(order); await bot.send_message(order['user_id'],f"✅ <b>Замовлення #{oid} завершено!</b>\n📱 {esc(meta.get('number_title','Віртуальний номер'))}\nДякуємо за покупку! Залиште, будь ласка, відгук.",reply_markup=main_menu(order['user_id']))
    fresh=db.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone(); await send_review_request(fresh)


@dp.message()
async def fallback(message: Message, state: FSMContext):
    ensure_user(message.from_user)
    pending_game = db.execute(
        "SELECT 1 FROM orders WHERE user_id=? AND order_type='game_donation' "
        "AND status='waiting_game_details' LIMIT 1",
        (message.from_user.id,),
    ).fetchone()
    if pending_game and message.text:
        await process_game_identifier(message)
        return
    # Handler filters for the built-in labels are created at startup. This
    # fallback keeps newly edited button labels live immediately, without
    # requiring a restart after an administrator changes a label.
    dynamic_key = menu_key_from_text(message.text, user_language(message.from_user.id))
    if dynamic_key:
        handlers = {
            "buy_stars": buy_stars,
            "buy_ton": buy_ton,
            "premium": premium_menu,
            "withdraw": withdraw,
            "sell": sell_stars,
            "profile": profile,
            "referrals": referrals,
            "calculator": calculator,
            "reviews": reviews,
            "support": support,
            "games": lambda m: show_virtual_number_catalog(m.from_user.id),
            "nft": lambda m: show_product_catalog(m.from_user.id, "nft"),
            "digital": lambda m: show_product_catalog(m.from_user.id, "digital"),
        }
        handler = handlers.get(dynamic_key)
        if handler:
            if dynamic_key in {"buy_ton", "withdraw", "calculator", "support"}:
                await handler(message, state)
            else:
                await handler(message)
            return
    if message.from_user.id in ADMIN_IDS:
        pending_key = f"pending_bank:{message.from_user.id}"
        method = setting(pending_key)
        if method and message.text:
            value = message.text.strip()
            if len(value) < 3:
                await message.answer("❌ Вкажіть коректні реквізити одним повідомленням.")
                return
            set_setting(f"bank_{method}", value)
            delete_setting(pending_key)
            await state.clear()
            await message.answer(
                "✅ Реквізити збережено. Вони вже використовуються в нових замовленнях.",
                reply_markup=admin_menu_kb(),
            )
            return
    await message.answer(
        "🤔 Не вдалося розпізнати команду. Оберіть потрібний розділ у меню.",
        reply_markup=main_menu(message.from_user.id),
    )


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задано BOT_TOKEN у файлі .env. Скопіюйте .env.example у .env і заповніть BOT_TOKEN та OWNER_ID.")
    if not OWNER_IDS:
        raise RuntimeError("Не задано OWNER_ID або ADMIN_IDS. Вкажіть ID власника в .env.")
    print(f"SQLite database: {DB_PATH}")
    if not ADMIN_IDS:
        print("ADMIN_IDS не задано: надішліть боту /id, додайте ID в ADMIN_IDS і перезапустіть бота.")
    await configure_bot_commands()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
