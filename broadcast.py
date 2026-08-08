"""
Рассылки: ручная (через /broadcast или admin UI) и автоматическая —
случайно, примерно раз в 50 скачиваний (не по расписанию), выбирает одно
из трёх готовых напоминаний и шлёт всем пользователям.
"""
import asyncio
from typing import Dict

from aiogram import Bot
from aiogram.types import Message, LinkPreviewOptions

from config import BROADCAST_MAX_USERS, BROADCAST_DELAY_SEC
from helpers import html_escape, to_html_simple
from storage import store
from admin_log_file import log_admin
from logging_channel import log_event, format_user_for_log
from keyboards import broadcast_cancel_kb

# ================== STATE ==================
pending_admin_broadcast: Dict[int, str] = {}
pending_admin_broadcast_text: Dict[int, str] = {}
pending_admin_broadcast_source: Dict[int, str] = {}
pending_admin_broadcast_cancel: Dict[int, bool] = {}

# ================== PRESET TEXTS ==================
REMINDER_MSG = (
    "📌 **Не забывай** \n\n"
    "Если что-то не работает или есть вопрос — команда **/support** покажет, куда писать.\n\n"
    "*Мы всегда на связи* 🙌"
)

DONATE_REMINDER_MSG = (
    "💛 **Нравится бот?**\n\n"
    "Если TikSaves помогает — поддержи проект через **/donate**: Telegram Stars ⭐️ или крипта 💲.\n\n"
    "Любая помощь идёт на хостинг и развитие бота — спасибо! 🙏"
)

REFERRAL_REMINDER_MSG = (
    "🎁 **А ты знал про подарки?** \n\n"
    "Приглашай друзей в TikSaves и копи баллы на реальные Telegram-подарки — сердечки, розы, кольца и даже алмазы 💎\n\n"
    "**Как это работает:**\n"
    "1️⃣ Забери свою ссылку — команда **/ref**\n"
    "2️⃣ Скинь её другу\n"
    "3️⃣ Как только он скачает первое видео — тебе баллы 🎟\n\n"
    "Там же — магазин подарков, твои заявки и топ рефереров.\n"
    "*Заходи в /ref прямо сейчас* 👀"
)

# ~1 рассылка на каждые 50 скачиваний, но по-настоящему случайно (не жёсткий
# счётчик "ровно на 50-м") — проверяется на каждом успешном скачивании.
# ПРИМЕЧАНИЕ: фактическая отправка теперь идёт ТОЛЬКО тому, кто скачал
# (см. referral.py:_maybe_send_random_personal_reminder), а не всем — эти
# константы используются оттуда напрямую по импорту текстов.
RANDOM_REMINDER_CHANCE = 1 / 50


async def do_broadcast(message: Message, admin_id: int, admin_label: str, raw_text: str, *, already_html: bool = False) -> None:
    users = store.get_all_user_ids()
    if not users:
        await message.answer("Пока нет пользователей для рассылки.", parse_mode="HTML")
        return
    if len(users) > BROADCAST_MAX_USERS:
        await message.answer(f"⚠️ Слишком много пользователей ({len(users)}). Лимит: {BROADCAST_MAX_USERS}.", parse_mode="HTML")
        return

    html = raw_text if already_html else to_html_simple(raw_text)

    log_admin(admin_id, "broadcast", f"len={len(raw_text)} users={len(users)}")
    await log_event(
        message.bot,
        "broadcast",
        [
            "📣 Категория: <b>Рассылка</b>",
            "🚀 Старт",
            f"👤 Кто: <b>{format_user_for_log(admin_label, admin_id)}</b>",
            f"👥 Получателей: <b>{len(users)}</b>",
        ],
    )

    pending_admin_broadcast_cancel[admin_id] = False
    status = await message.answer(
        f"📣 Запускаю рассылку…\nПолучателей: {len(users)}",
        parse_mode="HTML",
        reply_markup=broadcast_cancel_kb(),
    )
    sent = 0
    for u in users:
        if pending_admin_broadcast_cancel.get(admin_id):
            break
        try:
            await message.bot.send_message(u, html, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(BROADCAST_DELAY_SEC)

    if pending_admin_broadcast_cancel.get(admin_id):
        await status.edit_text(f"⛔ Рассылка остановлена: {sent}/{len(users)}", parse_mode="HTML")
        await log_event(
            message.bot,
            "broadcast",
            [
                "📣 Категория: <b>Рассылка</b>",
                "⛔ Остановлена",
                f"👤 Кто: <b>{format_user_for_log(admin_label, admin_id)}</b>",
                f"✅ Отправлено: <b>{sent}/{len(users)}</b>",
            ],
        )
        pending_admin_broadcast_cancel.pop(admin_id, None)
        return

    await status.edit_text(f"✅ Рассылка завершена: {sent}/{len(users)}")
    await log_event(
        message.bot,
        "broadcast",
        [
            "📣 Категория: <b>Рассылка</b>",
            "🏁 Завершена",
            f"👤 Кто: <b>{format_user_for_log(admin_label, admin_id)}</b>",
            f"✅ Отправлено: <b>{sent}/{len(users)}</b>",
        ],
    )
    pending_admin_broadcast_cancel.pop(admin_id, None)


async def do_broadcast_system(bot: Bot, kind: str, raw_text: str) -> None:
    users = store.get_all_user_ids()
    if not users:
        await log_event(
            bot,
            "broadcast",
            [
                "📣 Категория: <b>Авто-рассылка</b>",
                f"🧩 Тип: <b>{html_escape(kind)}</b>",
                "ℹ️ Пользователей нет",
            ],
        )
        return
    if len(users) > BROADCAST_MAX_USERS:
        await log_event(
            bot,
            "broadcast",
            [
                "📣 Категория: <b>Авто-рассылка</b>",
                f"🧩 Тип: <b>{html_escape(kind)}</b>",
                f"⚠️ Слишком много пользователей: <b>{len(users)}</b>",
            ],
        )
        return

    html = to_html_simple(raw_text)
    await log_event(
        bot,
        "broadcast",
        [
            "📣 Категория: <b>Авто-рассылка</b>",
            f"🧩 Тип: <b>{html_escape(kind)}</b>",
            f"👥 Получателей: <b>{len(users)}</b>",
            "🚀 Старт",
        ],
    )

    sent = 0
    for u in users:
        try:
            await bot.send_message(u, html, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(BROADCAST_DELAY_SEC)

    await log_event(
        bot,
        "broadcast",
        [
            "📣 Категория: <b>Авто-рассылка</b>",
            f"🧩 Тип: <b>{html_escape(kind)}</b>",
            f"✅ Отправлено: <b>{sent}/{len(users)}</b>",
            "🏁 Завершена",
        ],
    )



