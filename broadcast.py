"""
Рассылки: ручная (через /broadcast или admin UI) и автоматическая по расписанию
(напоминание/реклама каждые 4 дня), плюс готовые тексты-шаблоны.
"""
import asyncio
from datetime import timedelta
from typing import Dict

from aiogram import Bot
from aiogram.types import Message, LinkPreviewOptions

from config import BROADCAST_MAX_USERS, BROADCAST_DELAY_SEC, log
from helpers import html_escape, msk_now, to_html_simple
from storage import store
from admin_log_file import log_admin
from logging_channel import log_event, format_user_for_log
from keyboards import broadcast_cancel_kb

# ================== STATE ==================
pending_admin_broadcast: Dict[int, str] = {}
pending_admin_broadcast_text: Dict[int, str] = {}
pending_admin_broadcast_source: Dict[int, str] = {}
pending_admin_broadcast_cancel: Dict[int, bool] = {}

# uid -> timestamp: ждём от админа следующее сообщение как текст рассылки
# (с сохранением форматирования — жирный/курсив/моно и т.п., применённого
# через выделение текста в Telegram).
waiting_custom_broadcast: Dict[int, float] = {}
WAITING_CUSTOM_BROADCAST_TTL_SEC = 300

# ================== PRESET TEXTS ==================
REMINDER_MSG = (
    "**Напоминание** 📌\n\n"
    "**🆘 Поддержка**\n"
    "Если есть вопрос или проблема - команда **/support** подскажет, куда писать.\n"
    "Также можно писать напрямую: **@tiksavesbotsupport**\n\n"
    "**💛 Донат**\n"
    "Команда **/donate** - если бот помогает, можно поддержать развитие:\n\n"
    "• ⭐️ донат через **Telegram Stars**\n"
    "• 💲 донат **криптой**\n\n"
    "*Спасибо, что пользуетесь ботом 🙌*"
)

ADVERTISEMENT_MSG = (
    "Друзья, привет! 😄\n\n"
    "Наш бот помогает скачивать контент из TikTok 🎬 без лишних подписок и каналов.\n\n"
    "Если вам нравится - расскажите друзьям и знакомым. Это реально помогает проекту.\n"
    "Спасибо! Команда TIKSAVES 💛"
)


async def do_broadcast(message: Message, admin_id: int, admin_label: str, raw_text: str, *, already_html: bool = False) -> None:
    users = list(store.data.get("users", []))
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
    users = list(store.data.get("users", []))
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


def _broadcast_state() -> Dict[str, str]:
    store.data.setdefault("broadcast_state", {})
    return store.data["broadcast_state"]


async def broadcast_schedule_loop(bot: Bot) -> None:
    # Напоминание — раз в неделю (в 15:00); реклама бота — раз в 4 дня (в 20:00)
    while True:
        try:
            now = msk_now()
            today_str = now.strftime("%Y-%m-%d")
            week_mod = now.date().toordinal() % 7
            day_mod = now.date().toordinal() % 4
            state = _broadcast_state()

            if now.hour == 15 and now.minute == 0 and week_mod == 0:
                if state.get("last_reminder") != today_str:
                    await do_broadcast_system(bot, "reminder", REMINDER_MSG)
                    state["last_reminder"] = today_str
                    store._mark_dirty()
                    log.info("broadcast: reminder sent at 15:00 (weekly cycle)")

            if now.hour == 20 and now.minute == 0 and day_mod == 0:
                if state.get("last_advert") != today_str:
                    await do_broadcast_system(bot, "advert", ADVERTISEMENT_MSG)
                    state["last_advert"] = today_str
                    store._mark_dirty()
                    log.info("broadcast: advert sent at 20:00 (4-day cycle)")

            # Спим до следующей минуты, чтобы не пропустить 15:00 / 20:00
            next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
            wait_sec = (next_minute - now).total_seconds()
            await asyncio.sleep(max(1, min(60, wait_sec)))
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.exception("broadcast_schedule_loop: %s", e)
            await asyncio.sleep(60)
