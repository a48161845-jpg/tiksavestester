"""
Антиспам без страйков: просто cooldown + бан при многократном флуде.
Страйки убраны — пользователь получает бан напрямую
только если спамит во время кулдауна слишком много раз.
"""
import time
from typing import Optional
from collections import defaultdict

from aiogram import Bot
from aiogram.types import Message

from config import (
    BAN_DURATION_SEC,
    BAN_REASON_SPAM,
    BAN_REASON_DL,
    SUPPORT_USERNAME,
)
from helpers import html_escape, code, format_msk
from storage import store
from admin_log_file import log_admin
from logging_channel import log_event, format_user_for_log

# Счётчик нарушений кулдауна (в памяти, сбрасывается с ботом)
# Бан выдаётся после BAN_SPAM_VIOLATIONS нарушений за одну сессию кулдауна
BAN_SPAM_VIOLATIONS = 4
_spam_violations: dict = defaultdict(int)
_spam_cd_session: dict = {}   # uid -> ts начала текущей сессии кулдауна


def _reset_violation_if_needed(uid: int) -> None:
    """Если сессия кулдауна закончилась — сбрасываем счётчик нарушений."""
    sess_start = _spam_cd_session.get(uid, 0)
    from config import SPAM_COOLDOWN_SEC
    if sess_start and time.time() - sess_start > SPAM_COOLDOWN_SEC + 5:
        _spam_violations.pop(uid, None)
        _spam_cd_session.pop(uid, None)


async def add_spam_strike(bot: Bot, uid: int, label: str, reason: str) -> Optional[int]:
    """
    Вместо страйков — накапливаем нарушения в памяти.
    После BAN_SPAM_VIOLATIONS нарушений — бан.
    """
    _reset_violation_if_needed(uid)
    _spam_cd_session.setdefault(uid, time.time())
    _spam_violations[uid] += 1
    violations = _spam_violations[uid]

    await log_event(
        bot,
        "gate_spam",
        [
            "⚠️ Категория: <b>Спам/флуд</b>",
            f"👤 User/id: <b>{format_user_for_log(label, uid)}</b>",
            f"🔢 Нарушений в сессии: <b>{violations}/{BAN_SPAM_VIOLATIONS}</b>",
            f"📌 Причина: <b>{html_escape(reason)}</b>",
        ],
    )

    if violations >= BAN_SPAM_VIOLATIONS:
        until = int(time.time()) + BAN_DURATION_SEC
        store.set_ban(uid, until, BAN_REASON_SPAM, by=0)
        store.inc_ban()
        _spam_violations.pop(uid, None)
        _spam_cd_session.pop(uid, None)

        log_admin(0, "autoban_spam", f"target={uid} until={until} reason={BAN_REASON_SPAM}")
        await log_event(
            bot,
            "autoban",
            [
                "🚫 Категория: <b>Авто-бан (флуд)</b>",
                f"👤 User/id: <b>{format_user_for_log(label, uid)}</b>",
                f"⏳ До: <b>{format_msk(until)} МСК</b>",
                f"📌 Причина: <b>{html_escape(BAN_REASON_SPAM)}</b>",
            ],
        )
        return until

    return None


async def add_download_strike(
    bot: Bot,
    uid: int,
    label: str,
    reason: str,
    *,
    src: Optional[str] = None,
) -> Optional[int]:
    """
    Лимит скачиваний: просто логируем, бан не выдаём автоматически.
    Пользователь просто ждёт окно DL_WINDOW_SEC.
    """
    await log_event(
        bot,
        "dlerr",
        [
            "⚠️ Категория: <b>Лимит скачиваний</b>",
            f"👤 User/id: <b>{format_user_for_log(label, uid)}</b>",
            f"📌 Причина: <b>{html_escape(reason)}</b>",
            f"🔗 Ссылка: {code(src or '')}" if src else "🔗 Ссылка: -",
        ],
    )
    return None


async def ban_message(message: Message, who_label: str, until: int, reason: str) -> None:
    uid = message.from_user.id
    await message.answer(
        "🚫 <b>Вы заблокированы.</b>\n\n"
        f"👤 User/id: <b>{format_user_for_log(who_label, uid)}</b>\n"
        f"⏳ Бан до: <b>{format_msk(until)} МСК</b>\n"
        f"📌 Причина: <b>{html_escape(reason)}</b>\n\n"
        f"🆘 Поддержка: {html_escape(SUPPORT_USERNAME)}",
        parse_mode="HTML",
    )
