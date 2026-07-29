"""
«Шлюзы» перед обработкой входящих сообщений и callback-кнопок:
проверка бана и анти-флуд лимит (без страйков).
"""
import time

from aiogram.types import Message, CallbackQuery

from config import MSG_SPAM, BAN_REASON_SPAM, log
from helpers import html_escape, is_admin, is_chatty_message
from storage import store
from limiters import lim
from logging_channel import log_event, format_user_for_log
from strikes import add_spam_strike, ban_message

# Забаненный пользователь может продолжать писать боту — не шлём в канал
# отдельное сообщение на КАЖДУЮ такую попытку, а логируем не чаще, чем раз в это окно.
_BAN_LOG_COOLDOWN_SEC = 600
_last_ban_log: dict = {}


def _should_log_ban(uid: int) -> bool:
    now = time.time()
    last = _last_ban_log.get(uid, 0)
    if now - last < _BAN_LOG_COOLDOWN_SEC:
        return False
    _last_ban_log[uid] = now
    return True


async def gate_message(message: Message, label: str) -> bool:
    uid = message.from_user.id
    ban = store.get_ban(uid)
    if ban:
        log.info("gate_message: user banned uid=%s label=%s reason=%s", uid, label, ban.get("reason", ""))
        if _should_log_ban(uid):
            await log_event(
                message.bot,
                "gate_ban",
                [
                    "🚫 Категория: <b>Сообщение заблокировано (бан)</b>",
                    f"👤 User/id: <b>{format_user_for_log(label, uid)}</b>",
                    f"📋 Причина: <b>{html_escape(str(ban.get('reason', 'Не указана')))}</b>",
                ],
            )
        await ban_message(message, label, int(ban.get("until", 0)), str(ban.get("reason", "Не указана")))
        return False

    if is_admin(uid):
        return True

    text = (message.text or "").strip()

    if is_chatty_message(text) or text.startswith("/"):
        ok, wait, started_cd_now = lim.spam_hit_or_cd(uid)
        if ok:
            return True

        # Флуд во время кулдауна — может привести к бану (без страйков)
        if started_cd_now:
            until_ban = await add_spam_strike(message.bot, uid, label, "Флуд/слишком часто")
            if until_ban:
                ban2 = store.get_ban(uid)
                if ban2:
                    await ban_message(message, label, int(ban2.get("until", 0)), str(ban2.get("reason", BAN_REASON_SPAM)))
                return False

        log.info("gate_message: spam limit uid=%s wait=%s", uid, wait)
        if lim.spam_can_warn(uid):
            await message.answer(MSG_SPAM.format(n=wait), parse_mode="HTML")
        return False

    return True


async def gate_callback(call: CallbackQuery, label: str) -> bool:
    uid = call.from_user.id
    ban = store.get_ban(uid)
    if ban:
        log.info("gate_callback: user banned uid=%s", uid)
        if _should_log_ban(uid):
            await log_event(
                call.bot,
                "gate_ban",
                [
                    "🚫 Категория: <b>Кнопка заблокирована (бан)</b>",
                    f"👤 User/id: <b>{format_user_for_log(label, uid)}</b>",
                ],
            )
        await call.answer("Вы в бане.", show_alert=True)
        return False

    if is_admin(uid):
        return True

    data = call.data or ""
    if data.startswith("pk:"):
        return True

    ok, wait, started_cd_now = lim.spam_hit_or_cd(uid)
    if ok:
        return True

    if started_cd_now:
        until_ban = await add_spam_strike(call.bot, uid, label, "Флуд/слишком часто (кнопки)")
        if until_ban:
            await call.answer("Бан за флуд.", show_alert=True)
            return False

    log.info("gate_callback: spam limit uid=%s wait=%s data=%s", uid, wait, data[:50] if data else "")
    await call.answer(MSG_SPAM.format(n=wait), show_alert=True)
    return False
