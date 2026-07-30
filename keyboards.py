"""
Инлайн-клавиатуры и текстовые константы, которые показываются пользователю.
Здесь нет бизнес-логики — только разметка интерфейса.
"""
import urllib.parse
from typing import List

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import SUPPORT_USERNAME, CRYPTO_DONATE_URL, BOT_SHARE_URL, STARS_MIN, STARS_MAX, GIFTS
from helpers import html_escape, code

# ================== STATS / TOP KEYBOARDS ==================
def stats_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 День", callback_data="ad:stats:d"),
                InlineKeyboardButton(text="🗓 Неделя", callback_data="ad:stats:n"),
                InlineKeyboardButton(text="🗓 Месяц", callback_data="ad:stats:m"),
            ],
            [
                InlineKeyboardButton(text="📆 Год", callback_data="ad:stats:y"),
                InlineKeyboardButton(text="📊 Всё время", callback_data="ad:stats:all"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="ad:back"),
                InlineKeyboardButton(text="❌ Закрыть", callback_data="ad:close"),
            ],
        ]
    )

def top_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 День", callback_data="ad:top:d"),
                InlineKeyboardButton(text="🗓 Неделя", callback_data="ad:top:n"),
                InlineKeyboardButton(text="🗓 Месяц", callback_data="ad:top:m"),
            ],
            [
                InlineKeyboardButton(text="📆 Год", callback_data="ad:top:y"),
                InlineKeyboardButton(text="📊 Всё время", callback_data="ad:top:all"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="ad:back"),
                InlineKeyboardButton(text="❌ Закрыть", callback_data="ad:close"),
            ],
        ]
    )

# ================== START ==================
START_TEXT = (
    "👋 Привет!\n\n"
    "Я скачиваю видео, фото (слайдшоу) и музыку из TikTok.\n"
    "📎 Просто отправь ссылку — остальное сделаю сам.\n\n"
    "🧾 Помощь: /help\n"
    "💛 Поддержать бота: /donate\n"
    "🆘 Поддержка: /support\n"
    "📊 Моя статистика: /me\n"
    "🎁 Реферальная система и подарки: /ref"
)

# ================== DONATE ==================
def donate_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Донат звёздами", callback_data="donate:stars")],
            [InlineKeyboardButton(text="💲 Донат криптой", url=CRYPTO_DONATE_URL)],
            [InlineKeyboardButton(text="🆘 Поддержка", callback_data="donate:support")],
        ]
    )

def stars_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ 10", callback_data="stars:10"),
                InlineKeyboardButton(text="⭐ 50", callback_data="stars:50"),
                InlineKeyboardButton(text="⭐ 100", callback_data="stars:100"),
            ],
            [
                InlineKeyboardButton(text="⭐ 250", callback_data="stars:250"),
                InlineKeyboardButton(text="⭐ 500", callback_data="stars:500"),
                InlineKeyboardButton(text="⭐ 1000", callback_data="stars:1000"),
            ],
            [InlineKeyboardButton(text="✍️ Другая сумма", callback_data="stars:custom")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="donate:back")],
        ]
    )

DONATE_TEXT = (
    "📌 <b>Поддержать бота</b>\n\n"
    "Спасибо, что пользуешься ботом! Донат помогает держать всё стабильно и быстро:\n"
    "• хостинг и трафик 24/7\n"
    "• поддержка прокси/апи\n"
    "• новые функции и улучшения\n\n"
    "Выбери удобный способ 👇"
)
STARS_MENU_TEXT = (
    "⭐ <b>Telegram Stars</b>\n\n"
    "Самый быстрый способ поддержки прямо в Telegram.\n"
    f"Выбери сумму ({STARS_MIN}-{STARS_MAX} ⭐) или введи свою:"
)
SUPPORT_TEXT = (
    "🆘 <b>Поддержка</b>\n\n"
    f"Если есть вопросы/проблемы - пиши сюда: {html_escape(SUPPORT_USERNAME)}\n"
    "Сразу укажи ссылку и что именно не работает 🙌"
)
SHARE_TEXT = "Нашел топового бота для скачивания видео и фото из TikTok. Переходи ☝️"

# ================== HELP ==================
HELP_TEXT = (
    "🧾 <b>Помощь по боту</b>\n\n"
    "📎 Просто отправь ссылку на TikTok — бот предложит варианты.\n\n"
    "Кнопки и что они делают:\n"
    "• 🎬 Скачать видео — пришлю видео без водяных знаков (если доступно)\n"
    "• 🖼️ Скачать фото — выбор фото или всё сразу (слайдшоу)\n"
    "• 🎵 Скачать музыку — скачивание звука/музыки\n"
    "• 💛 Донат — поддержка проекта\n"
    "• 🆘 Поддержка — связь с админом\n\n"
    "🎁 Приглашай друзей и получай баллы на подарки: /ref\n\n"
    "⚠️ Лимиты:\n"
    "• Частые запросы ограничены кулдауном\n"
    "• Много фото за раз — лимит объёма"
)

def help_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎬 Скачать видео", callback_data="help:video"),
                InlineKeyboardButton(text="🖼️ Скачать фото", callback_data="help:photo"),
            ],
            [
                InlineKeyboardButton(text="🎵 Скачать музыку", callback_data="help:music"),
            ],
            [
                InlineKeyboardButton(text="⚠️ Лимиты", callback_data="help:limits"),
            ],
            [
                InlineKeyboardButton(text="💛 Донат", callback_data="help:donate"),
                InlineKeyboardButton(text="🆘 Поддержка", callback_data="help:support"),
            ],
            [
                InlineKeyboardButton(text="❌ Закрыть", callback_data="help:close"),
            ],
        ]
    )

def help_section_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="help:back"),
                InlineKeyboardButton(text="❌ Закрыть", callback_data="help:close"),
            ]
        ]
    )

HELP_SECTIONS = {
    "video": (
        "🎬 <b>Скачать видео</b>\n\n"
        "1) Отправь ссылку на TikTok\n"
        "2) Выбери «Скачать видео»\n"
        "3) Получишь видео в чате"
    ),
    "photo": (
        "🖼️ <b>Скачать фото</b>\n\n"
        "1) Отправь ссылку на TikTok с слайдшоу\n"
        "2) Выбери фото по номерам или скачай всё\n"
        "3) Получишь альбом с фото"
    ),
    "music": (
        "🎵 <b>Скачать музыку</b>\n\n"
        "Нажми «Скачать музыку» после обработки ссылки — пришлю аудио файл."
    ),
    "limits": (
        "⚠️ <b>Лимиты</b>\n\n"
        "Слишком частые запросы ограничены кулдауном — подожди немного и попробуй снова.\n"
        "При систематическом флуде возможна временная блокировка."
    ),
    "donate": (
        "💛 <b>Донат</b>\n\n"
        "Поддержка проекта через Stars или крипту. Спасибо!"
    ),
    "support": (
        "🆘 <b>Поддержка</b>\n\n"
        f"Пиши: {html_escape(SUPPORT_USERNAME)}\n"
        "Укажи ссылку и что не работает."
    ),
}

# ================== POST-DOWNLOAD / VIDEO KEYBOARDS ==================
def _share_url() -> str:
    """Ссылка «Поделиться»: в шаре подставляется url, текст — про бота (ссылка вставляется сама)."""
    share_url = urllib.parse.quote_plus(BOT_SHARE_URL)
    share_text = urllib.parse.quote_plus(SHARE_TEXT)
    return f"https://t.me/share/url?url={share_url}&text={share_text}"

def post_download_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💛 Донат", callback_data="donate:open"),
                InlineKeyboardButton(text="🔗 Поделиться", url=_share_url()),
            ]
        ]
    )

def under_video_kb(has_music: bool = False, has_description: bool = False, req_id: str = "") -> InlineKeyboardMarkup:
    """Кнопки под скачанным видео: Музыка (если есть), Описание (если есть), Донат, Поделиться."""
    top_row: List[InlineKeyboardButton] = []
    if has_music:
        top_row.append(InlineKeyboardButton(text="🎵 Музыка", callback_data=f"dl:audio:{req_id}"))
    if has_description:
        top_row.append(InlineKeyboardButton(text="📝 Описание", callback_data=f"dl:desc:{req_id}"))

    bottom_row: List[InlineKeyboardButton] = [
        InlineKeyboardButton(text="💛 Донат", callback_data="donate:open"),
        InlineKeyboardButton(text="🔗 Поделиться", url=_share_url()),
    ]

    rows = [top_row, bottom_row] if top_row else [bottom_row]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def video_choice_kb() -> InlineKeyboardMarkup:
    """Только «Скачать видео» и «Отмена» — кнопка музыки перенесена под видео."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎬 Скачать видео", callback_data="vd:video")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="vd:cancel")],
        ]
    )

# ================== ADMIN UI ==================
def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="ad:stats"),
                InlineKeyboardButton(text="🏆 Топ", callback_data="ad:top"),
            ],
            [
                InlineKeyboardButton(text="🚫 Бан-лист", callback_data="ad:banlist"),
                InlineKeyboardButton(text="🗄 Дамп БД", callback_data="ad:dbfile"),
            ],
            [
                InlineKeyboardButton(text="📌 Напоминание", callback_data="ad:reminder"),
                InlineKeyboardButton(text="📢 Реклама", callback_data="ad:advert"),
            ],
            [
                InlineKeyboardButton(text="👑 Администраторы", callback_data="ad:adminlist"),
            ],
            [
                InlineKeyboardButton(text="🧾 Команды", callback_data="ad:help"),
                InlineKeyboardButton(text="❌ Закрыть", callback_data="ad:close"),
            ],
        ]
    )

def admin_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="ad:back"),
                InlineKeyboardButton(text="❌ Закрыть", callback_data="ad:close"),
            ]
        ]
    )

ADMIN_MENU_TEXT = (
    "🛠 <b>Админ-панель</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    "📊 <b>Статистика</b> — выбор периода\n"
    "🏆 <b>Топ</b> — лидеры по периоду\n"
    "🚫 <b>Бан-лист</b> — активные баны\n"
    "🗄 <b>Дамп БД</b> — скачать базу данных\n"
    "👑 <b>Администраторы</b> — список и управление\n"
    "📌 <b>Напоминание</b> / 📢 <b>Реклама</b> — рассылки\n"
    "🧾 <b>Команды</b> — полный список\n"
)

ADMIN_HELP_TEXT = (
    "🧾 <b>Команды администратора</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"

    "📊 <b>Статистика</b>\n"
    f"├ {code('/stats d')} — день\n"
    f"├ {code('/stats n')} — неделя\n"
    f"├ {code('/stats m')} — месяц\n"
    f"├ {code('/stats y')} — год\n"
    f"├ {code('/stats all')} — всё время\n"
    f"└ {code('/stats 2026-02-01 2026-02-07')} — диапазон\n\n"

    "🏆 <b>Топ пользователей</b>\n"
    f"├ {code('/top d')} {code('/top n')} {code('/top m')} {code('/top y')} {code('/top all')}\n"
    f"└ {code('/top 2026-02-01 2026-02-07')} — диапазон\n\n"

    "🚫 <b>Баны</b>\n"
    f"├ {code('/ban ID 2h причина')} — забанить\n"
    f"├ {code('/unban ID')} — разбанить\n"
    f"├ {code('/banlist')} — список банов\n"
    f"└ {code('/baninfo ID')} — информация о бане\n\n"

    "👑 <b>Администраторы</b>\n"
    f"├ {code('/adminlist')} — список всех админов\n"
    f"├ {code('/adminadd ID')} — добавить (только суперадмин)\n"
    f"└ {code('/admindel ID')} — удалить (только суперадмин)\n\n"

    "👤 <b>Пользователь</b>\n"
    f"└ {code('/info ID')} — информация о пользователе\n\n"

    "🗄 <b>База данных</b>\n"
    f"├ {code('/dbfile')} — дамп БД файлом\n"
    f"└ {code('/dblog')} — отчёт в лог-канал\n\n"

    "📣 <b>Рассылка</b>\n"
    f"├ {code('/broadcast текст')} — своя рассылка\n"
    f"├ {code('/reminder_message')} — напоминание\n"
    f"└ {code('/advertisement_message')} — реклама\n"
)

def admin_broadcast_confirm_kb(kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Отправить", callback_data=f"ad:send:{kind}"),
            ],
            [
                InlineKeyboardButton(text="❌ Закрыть", callback_data="ad:close"),
            ],
        ]
    )

def broadcast_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⛔ Остановить рассылку", callback_data="ad:bcancel")],
        ]
    )


# ================== REFERRAL / GIFT SHOP ==================
def ref_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Магазин подарков", callback_data="ref:shop")],
            [InlineKeyboardButton(text="👥 Мои рефералы", callback_data="ref:myrefs")],
            [InlineKeyboardButton(text="📦 Мои заявки", callback_data="ref:myrequests")],
            [InlineKeyboardButton(text="🏆 Топ рефереров", callback_data="ref:top")],
            [InlineKeyboardButton(text="📖 Как это работает", callback_data="ref:howitworks")],
        ]
    )


def ref_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="ref:back")]])


def gift_shop_kb(balance: int) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for g in GIFTS:
        lock = "" if balance >= g["price"] else "🔒 "
        row.append(InlineKeyboardButton(text=f"{lock}{g['emoji']} {g['name']}", callback_data=f"gift:buy:{g['key']}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="ref:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def gift_confirm_kb(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"gift:confirm:{key}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="gift:cancel"),
            ]
        ]
    )


def gift_admin_kb(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Выдать", callback_data=f"admgift:ok:{req_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admgift:no:{req_id}"),
            ]
        ]
    )
