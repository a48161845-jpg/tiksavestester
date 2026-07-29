"""
Административные команды: баны, информация о пользователе, рассылки.
"""
import time
from typing import Optional

from aiogram import F
from aiogram.filters import Command
from aiogram.types import Message

from globals_state import dp
from helpers import html_escape, code, is_admin, parse_duration, format_msk
from storage import store
from user_label import resolve_user_label
from gates import gate_message
from logging_channel import log_event, log_admin_action_to_channel, format_user_for_log
from admin_log_file import log_admin
from keyboards import admin_broadcast_confirm_kb
from broadcast import (
    pending_admin_broadcast,
    pending_admin_broadcast_text,
    pending_admin_broadcast_source,
    waiting_custom_broadcast,
    WAITING_CUSTOM_BROADCAST_TTL_SEC,
)


@dp.message(Command("ban"))
async def ban_cmd(message: Message):
    admin_id = message.from_user.id
    admin_label = await resolve_user_label(message.bot, admin_id)
    store.set_user_label(admin_id, admin_label)

    if not is_admin(admin_id):
        return
    if not await gate_message(message, admin_label):
        return

    parts = (message.text or "").split(maxsplit=3)
    if len(parts) < 4 or not parts[1].isdigit():
        await message.answer(
            "❌ Формат:\n"
            f"{code('/ban 123 2h причина')}\n"
            "Длительность: 30m, 6h, 2d, 1d12h, 3h30m",
            parse_mode="HTML",
        )
        return

    uid = int(parts[1])
    dur_raw = parts[2]
    reason = parts[3].strip()

    # Нельзя банить админов
    if is_admin(uid):
        await message.answer("❌ Нельзя банить администратора.", parse_mode="HTML")
        return

    existing = store.get_ban(uid)
    if existing:
        until_existing = int(existing.get("until", 0))
        reason_existing = html_escape(str(existing.get("reason", "Не указана")))
        who_label = await resolve_user_label(message.bot, uid)
        store.set_user_label(uid, who_label)
        await message.answer(
            "ℹ️ Пользователь уже в бане.\n\n"
            f"👤 Кого: <b>{format_user_for_log(who_label, uid)}</b>\n"
            f"⏳ До: <b>{format_msk(until_existing)} МСК</b>\n"
            f"📌 Причина: <b>{reason_existing}</b>",
            parse_mode="HTML",
        )
        return

    try:
        seconds = parse_duration(dur_raw)
    except ValueError:
        await message.answer("❌ Неверное время. Пример: 2h, 30m, 1d12h", parse_mode="HTML")
        return

    until = int(time.time()) + seconds
    target_label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, target_label)

    store.set_ban(uid, until=until, reason=reason, by=admin_id)
    store.inc_ban()
    log_admin(admin_id, "ban", f"target={uid} until={until} reason={reason}")

    await log_event(
        message.bot,
        "userban",
        [
            "🚫 Категория: <b>Блокировка (ручная)</b>",
            f"🙅‍♂️ Кого: <b>{format_user_for_log(target_label, uid)}</b>",
            f"👑 Кто: <b>{format_user_for_log(admin_label, admin_id)}</b>",
            f"⏳ До: <b>{format_msk(until)} МСК</b>",
            f"📌 Причина: <b>{html_escape(reason)}</b>",
        ],
    )

    await message.answer(
        "🛑 Пользователь забанен.\n\n"
        f"👤 Кого: <b>{format_user_for_log(target_label, uid)}</b>\n"
        f"⏳ До: <b>{format_msk(until)} МСК</b>\n"
        f"📌 Причина: <b>{html_escape(reason)}</b>",
        parse_mode="HTML",
    )


@dp.message(Command("unban"))
async def unban_cmd(message: Message):
    admin_id = message.from_user.id
    admin_label = await resolve_user_label(message.bot, admin_id)
    store.set_user_label(admin_id, admin_label)

    if not is_admin(admin_id):
        return
    if not await gate_message(message, admin_label):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer(f"Использование: {code('/unban 123')}", parse_mode="HTML")
        return

    uid = int(parts[1])
    existed = store.unban(uid)

    target_label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, target_label)

    log_admin(admin_id, "unban", f"target={uid} existed={existed}")

    await log_event(
        message.bot,
        "userunban",
        [
            "✅ Категория: <b>Разблокировка</b>",
            f"🙋‍♂️ Кого: <b>{format_user_for_log(target_label, uid)}</b>",
            f"👑 Кто: <b>{format_user_for_log(admin_label, admin_id)}</b>",
            f"📍 Был в бане: <b>{'да' if existed else 'нет'}</b>",
        ],
    )
    if existed:
        await message.answer(f"✅ Разбан: <b>{format_user_for_log(target_label, uid)}</b>", parse_mode="HTML")
    else:
        await message.answer(f"ℹ️ Пользователь не в бане: <b>{format_user_for_log(target_label, uid)}</b>", parse_mode="HTML")


@dp.message(Command("banlist"))
async def banlist_cmd(message: Message):
    admin_id = message.from_user.id
    admin_label = await resolve_user_label(message.bot, admin_id)
    store.set_user_label(admin_id, admin_label)

    if not is_admin(admin_id):
        return
    if not await gate_message(message, admin_label):
        return

    bans = store.list_bans()
    log_admin(admin_id, "banlist", f"count={len(bans)}")
    await log_admin_action_to_channel(
        message.bot,
        "Просмотр бан-листа",
        [f"👤 Кто: <b>{format_user_for_log(admin_label, admin_id)}</b>", f"🚫 Кол-во: <b>{len(bans)}</b>"],
    )

    if not bans:
        await message.answer("✅ Активных банов нет.")
        return

    lines = ["🚫 <b>Активные баны</b>\n\n"]
    for uid2, until, reason, _by in bans[:100]:
        who_label = store.get_user_label(uid2)
        lines.append(
            f"• <b>{format_user_for_log(who_label, uid2)}</b> - до <b>{format_msk(until)} МСК</b>\n"
            f"  Причина: <i>{html_escape(reason)}</i>\n\n"
        )
    await message.answer("".join(lines), parse_mode="HTML")


@dp.message(Command("baninfo"))
async def baninfo_cmd(message: Message):
    admin_id = message.from_user.id
    admin_label = await resolve_user_label(message.bot, admin_id)
    store.set_user_label(admin_id, admin_label)

    if not is_admin(admin_id):
        return
    if not await gate_message(message, admin_label):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer(f"Использование: {code('/baninfo 123')}", parse_mode="HTML")
        return

    uid = int(parts[1])
    ban = store.get_ban(uid)
    who_label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, who_label)

    log_admin(admin_id, "baninfo", f"target={uid} banned={'yes' if ban else 'no'}")
    await log_admin_action_to_channel(
        message.bot,
        "Просмотр бана",
        [
            f"👤 Кто: <b>{format_user_for_log(admin_label, admin_id)}</b>",
            f"🙋‍♂️ Кого: <b>{format_user_for_log(who_label, uid)}</b>",
        ],
    )

    if not ban:
        await message.answer(f"ℹ️ Не в бане: <b>{format_user_for_log(who_label, uid)}</b>", parse_mode="HTML")
        return

    until = int(ban.get("until", 0))
    reason = html_escape(str(ban.get("reason", "Не указана")))
    by = int(ban.get("by", 0))
    by_label = store.get_user_label(by)

    await message.answer(
        "🚫 <b>Информация о бане</b>\n\n"
        f"👤 Пользователь: <b>{format_user_for_log(who_label, uid)}</b>\n"
        f"⏳ До: <b>{format_msk(until)} МСК</b>\n"
        f"📌 Причина: <b>{reason}</b>\n"
        f"👑 Кто выдал: <b>{format_user_for_log(by_label, by)}</b>",
        parse_mode="HTML",
    )


@dp.message(Command("info"))
async def info_cmd(message: Message):
    admin_id = message.from_user.id
    admin_label = await resolve_user_label(message.bot, admin_id)
    store.set_user_label(admin_id, admin_label)

    if not is_admin(admin_id):
        return
    if not await gate_message(message, admin_label):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer(f"Использование: {code('/info 123')} или {code('/info @username')}", parse_mode="HTML")
        return

    raw = parts[1].strip()
    uid: Optional[int] = None
    if raw.isdigit():
        uid = int(raw)
    else:
        username = raw[1:] if raw.startswith("@") else raw
        try:
            chat = await message.bot.get_chat(username)
            uid = int(chat.id)
        except Exception:
            # fallback to stored usernames
            found_uid: Optional[int] = None
            for uid_str, label in (store.data.get("users_map", {}) or {}).items():
                if f"@{username}".lower() in str(label).lower():
                    found_uid = int(uid_str)
                    break
            if found_uid is None:
                await message.answer("❌ Пользователь не найден. Проверь ID или username.", parse_mode="HTML")
                return
            uid = found_uid
    who_label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, who_label)

    first_seen_ts = int((store.data.get("first_seen", {}) or {}).get(str(uid), 0))
    last_seen_ts = int((store.data.get("last_seen", {}) or {}).get(str(uid), 0))
    joined = format_msk(first_seen_ts) if first_seen_ts > 0 else "неизвестно"
    last_seen = format_msk(last_seen_ts) if last_seen_ts > 0 else "неизвестно"

    us_dl = (store.data.get("user_stats", {}) or {}).get("downloads", {}) or {}
    rec = us_dl.get(str(uid), {}) or {}
    v_sent = int(rec.get("video_sent", 0))
    p_sent = int(rec.get("photos_sent", 0))
    v_ops = int(rec.get("video_ops", 0))
    p_ops = int(rec.get("photo_ops", 0))
    a_sent = int(rec.get("audio_sent", 0))

    stars_by_user = (store.data.get("user_stats", {}) or {}).get("stars", {}) or {}
    stars = int(stars_by_user.get(str(uid), 0))

    ban = store.get_ban(uid)
    if ban:
        ban_text = f"🚫 Бан: <b>да</b> (до <b>{format_msk(int(ban.get('until', 0)))} МСК</b>)"
    else:
        ban_text = "🚫 Бан: <b>нет</b>"

    await message.answer(
        "👤 <b>Информация о пользователе</b>\n\n"
        f"👤 Пользователь: <b>{format_user_for_log(who_label, uid)}</b>\n"
        f"🕒 Первый визит: <b>{joined}</b>\n"
        f"🕒 Последняя активность: <b>{last_seen}</b>\n"
        f"{ban_text}\n\n"
        f"🎬 Видео скачано: <b>{v_ops}</b> операций (файлов: <b>{v_sent}</b>)\n"
        f"🖼️ Фото скачано: <b>{p_ops}</b> операций (фото: <b>{p_sent}</b>)\n"
        f"🎵 Музыка скачано: <b>{a_sent}</b>\n"
        f"⭐ Stars пожертвовано: <b>{stars}</b>\n",
        parse_mode="HTML",
    )


def _waiting_for_custom_broadcast(message: Message) -> bool:
    uid = message.from_user.id if message.from_user else None
    if not uid:
        return False
    ts = waiting_custom_broadcast.get(uid)
    if not ts:
        return False
    if time.time() - ts > WAITING_CUSTOM_BROADCAST_TTL_SEC:
        waiting_custom_broadcast.pop(uid, None)
        return False
    return True


@dp.message(F.text, _waiting_for_custom_broadcast)
async def custom_broadcast_text_msg(message: Message):
    """
    Ловит следующее сообщение админа после нажатия "✍️ Своя рассылка" в панели.
    В отличие от /broadcast (где нужно вручную отрезать текст команды),
    тут всё сообщение целиком — это текст рассылки, поэтому форматирование
    (жирный/курсив/моно/ссылки и т.п., применённое через выделение текста
    в Telegram) просто берём из message.html_text без всякой ручной магии
    со сдвигом entity — надёжнее и без риска сломать разметку по краям.
    """
    admin_id = message.from_user.id
    waiting_custom_broadcast.pop(admin_id, None)

    if not is_admin(admin_id):
        return

    text_raw = (message.text or "").strip()
    if not text_raw:
        await message.answer("❌ Пустое сообщение, рассылка отменена.")
        return

    broadcast_html = message.html_text or html_escape(text_raw)

    pending_admin_broadcast[admin_id] = "custom"
    pending_admin_broadcast_text[admin_id] = broadcast_html
    pending_admin_broadcast_source[admin_id] = "panel"
    users_cnt = len(store.data.get("users", []))
    await message.answer(
        "📣 <b>Подтверждение рассылки</b>\n\n"
        "Тип: <b>Своя рассылка</b>\n"
        "Форматирование: сохранено ✅\n"
        f"Получателей: <b>{users_cnt}</b>\n\n"
        "Так и отправить?",
        parse_mode="HTML",
        reply_markup=admin_broadcast_confirm_kb("custom"),
    )


@dp.message(Command("broadcast"))
async def broadcast_cmd(message: Message):
    admin_id = message.from_user.id
    admin_label = await resolve_user_label(message.bot, admin_id)
    store.set_user_label(admin_id, admin_label)

    if not is_admin(admin_id):
        return
    if not await gate_message(message, admin_label):
        return

    # Extract text after /broadcast, preserving Telegram entities as HTML
    from aiogram.utils.text_decorations import html_decoration
    msg_text = message.text or ""
    msg_entities = message.entities or []

    # Find where the command word ends (first space after /broadcast)
    cmd_end = msg_text.find(" ")
    if cmd_end == -1:
        await message.answer("❌ Пример:\n" f"{code('/broadcast Текст рассылки')}", parse_mode="HTML")
        return

    # text_offset is the character position where broadcast text begins (skip leading spaces)
    text_offset = cmd_end + 1
    broadcast_raw = msg_text[text_offset:]
    # Strip leading whitespace and adjust offset accordingly
    lstripped = broadcast_raw.lstrip()
    if not lstripped:
        await message.answer("❌ Пример:\n" f"{code('/broadcast Текст рассылки')}", parse_mode="HTML")
        return
    text_offset += len(broadcast_raw) - len(lstripped)
    broadcast_raw = lstripped

    # Shift and filter entities that belong to broadcast text (after the command)
    shifted_entities = []
    for ent in msg_entities:
        ent_start = ent.offset
        ent_end = ent.offset + ent.length
        if ent_end <= text_offset:
            continue  # entity is part of the command word, skip
        new_offset = max(0, ent_start - text_offset)
        # Clamp length so entity doesn't go out of broadcast_raw bounds
        new_length = min(ent.length, len(broadcast_raw) - new_offset)
        if new_length <= 0:
            continue
        shifted_ent = type(ent)(
            **{**ent.model_dump(), "offset": new_offset, "length": new_length}
        )
        shifted_entities.append(shifted_ent)

    # Convert entities to HTML
    try:
        broadcast_html = html_decoration.unparse(broadcast_raw, shifted_entities)
    except Exception:
        broadcast_html = html_escape(broadcast_raw)

    pending_admin_broadcast[admin_id] = "custom"
    pending_admin_broadcast_text[admin_id] = broadcast_html
    pending_admin_broadcast_source[admin_id] = "cmd"
    users_cnt = len(store.data.get("users", []))
    await message.answer(
        "📣 <b>Подтверждение рассылки</b>\n\n"
        "Тип: <b>Своя рассылка</b>\n"
        f"Получателей: <b>{users_cnt}</b>\n\n"
        "Отправить?",
        parse_mode="HTML",
        reply_markup=admin_broadcast_confirm_kb("custom"),
    )


@dp.message(Command("reminder_message"))
async def reminder_message_cmd(message: Message):
    admin_id = message.from_user.id
    admin_label = await resolve_user_label(message.bot, admin_id)
    store.set_user_label(admin_id, admin_label)

    if not is_admin(admin_id):
        return
    if not await gate_message(message, admin_label):
        return

    pending_admin_broadcast[admin_id] = "reminder"
    pending_admin_broadcast_text.pop(admin_id, None)
    pending_admin_broadcast_source[admin_id] = "cmd"
    users_cnt = len(store.data.get("users", []))
    await message.answer(
        "📣 <b>Подтверждение рассылки</b>\n\n"
        "Тип: <b>Напоминание</b>\n"
        f"Получателей: <b>{users_cnt}</b>\n\n"
        "Отправить?",
        parse_mode="HTML",
        reply_markup=admin_broadcast_confirm_kb("reminder"),
    )


@dp.message(Command("advertisement_message"))
async def advertisement_message_cmd(message: Message):
    admin_id = message.from_user.id
    admin_label = await resolve_user_label(message.bot, admin_id)
    store.set_user_label(admin_id, admin_label)

    if not is_admin(admin_id):
        return
    if not await gate_message(message, admin_label):
        return

    pending_admin_broadcast[admin_id] = "advert"
    pending_admin_broadcast_text.pop(admin_id, None)
    pending_admin_broadcast_source[admin_id] = "cmd"
    users_cnt = len(store.data.get("users", []))
    await message.answer(
        "📣 <b>Подтверждение рассылки</b>\n\n"
        "Тип: <b>Реклама</b>\n"
        f"Получателей: <b>{users_cnt}</b>\n\n"
        "Отправить?",
        parse_mode="HTML",
        reply_markup=admin_broadcast_confirm_kb("advert"),
    )


@dp.message(Command("dblog"))
async def dblog_cmd(message: Message):
    """Отправить текстовый отчёт по БД в лог-канал (файлом .txt)."""
    admin_id = message.from_user.id
    admin_label = await resolve_user_label(message.bot, admin_id)
    store.set_user_label(admin_id, admin_label)

    if not is_admin(admin_id):
        return
    if not await gate_message(message, admin_label):
        return

    from db_report import send_db_report
    log_admin(admin_id, "dblog", "manual db report requested")
    await message.answer("📊 Генерирую отчёт…")
    await send_db_report(message.bot, title="Отчёт БД (ручной запрос)")
    await message.answer("✅ Отчёт-файл отправлен в лог-канал.")


@dp.message(Command("dbfile"))
async def dbfile_cmd(message: Message):
    """Отправить полный JSON-дамп базы данных прямо в этот чат."""
    admin_id = message.from_user.id
    admin_label = await resolve_user_label(message.bot, admin_id)
    store.set_user_label(admin_id, admin_label)

    if not is_admin(admin_id):
        return
    if not await gate_message(message, admin_label):
        return

    from db_report import send_db_json
    log_admin(admin_id, "dbfile", "manual db dump requested")
    await log_admin_action_to_channel(
        message.bot,
        "Запрошен дамп БД",
        [f"👤 Кто: <b>{format_user_for_log(admin_label, admin_id)}</b>"],
    )
    await message.answer("🗄 Формирую дамп БД…")
    await send_db_json(message.bot, admin_id)
    await message.answer("✅ Файл отправлен.")


@dp.message(Command("adminadd"))
async def adminadd_cmd(message: Message):
    admin_id = message.from_user.id
    admin_label = await resolve_user_label(message.bot, admin_id)
    store.set_user_label(admin_id, admin_label)

    if not is_admin(admin_id):
        return
    if not await gate_message(message, admin_label):
        return

    from config import ADMINS
    if admin_id not in ADMINS:
        await message.answer("❌ Только владелец (суперадмин) может добавлять администраторов.", parse_mode="HTML")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().isdigit():
        await message.answer(f"Использование: {code('/adminadd 123456789')}", parse_mode="HTML")
        return

    uid = int(parts[1].strip())
    target_label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, target_label)

    added = store.add_extra_admin(uid)
    log_admin(admin_id, "adminadd", f"target={uid} success={added}")

    if added:
        await log_event(
            message.bot,
            "adminadd",
            [
                "👑 Категория: <b>Назначение администратора</b>",
                f"👤 Кто: <b>{format_user_for_log(admin_label, admin_id)}</b>",
                f"➕ Новый админ: <b>{format_user_for_log(target_label, uid)}</b>",
            ],
        )
        await message.answer(
            f"✅ Администратор добавлен: <b>{format_user_for_log(target_label, uid)}</b>",
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"ℹ️ Уже является администратором: <b>{format_user_for_log(target_label, uid)}</b>",
            parse_mode="HTML",
        )


@dp.message(Command("admindel"))
async def admindel_cmd(message: Message):
    admin_id = message.from_user.id
    admin_label = await resolve_user_label(message.bot, admin_id)
    store.set_user_label(admin_id, admin_label)

    if not is_admin(admin_id):
        return
    if not await gate_message(message, admin_label):
        return

    from config import ADMINS
    if admin_id not in ADMINS:
        await message.answer("❌ Только владелец (суперадмин) может удалять администраторов.", parse_mode="HTML")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().isdigit():
        await message.answer(f"Использование: {code('/admindel 123456789')}", parse_mode="HTML")
        return

    uid = int(parts[1].strip())
    if uid in ADMINS:
        await message.answer("❌ Нельзя удалить суперадмина (прописан в config).", parse_mode="HTML")
        return

    target_label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, target_label)
    removed = store.del_extra_admin(uid)
    log_admin(admin_id, "admindel", f"target={uid} success={removed}")

    if removed:
        await log_event(
            message.bot,
            "admindel",
            [
                "🚫 Категория: <b>Снятие администратора</b>",
                f"👤 Кто: <b>{format_user_for_log(admin_label, admin_id)}</b>",
                f"➖ Снят админ: <b>{format_user_for_log(target_label, uid)}</b>",
            ],
        )
        await message.answer(
            f"✅ Администратор удалён: <b>{format_user_for_log(target_label, uid)}</b>",
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"ℹ️ Не является дополнительным администратором: <b>{format_user_for_log(target_label, uid)}</b>",
            parse_mode="HTML",
        )


@dp.message(Command("adminlist"))
async def adminlist_cmd(message: Message):
    admin_id = message.from_user.id
    admin_label = await resolve_user_label(message.bot, admin_id)
    store.set_user_label(admin_id, admin_label)

    if not is_admin(admin_id):
        return
    if not await gate_message(message, admin_label):
        return

    from config import ADMINS
    lines = ["👑 <b>Список администраторов</b>\n"]

    lines.append("🔒 <b>Суперадмины (config):</b>")
    for uid2 in sorted(ADMINS):
        lbl = store.get_user_label(uid2)
        lines.append(f"  • <b>{format_user_for_log(lbl, uid2)}</b>")

    extra = store.get_extra_admins()
    lines.append(f"\n➕ <b>Дополнительные ({len(extra)}):</b>")
    if extra:
        for uid2 in sorted(extra):
            lbl = store.get_user_label(uid2)
            lines.append(f"  • <b>{format_user_for_log(lbl, uid2)}</b>")
    else:
        lines.append("  <i>нет</i>")

    log_admin(admin_id, "adminlist", f"extra_count={len(extra)}")
    await message.answer("\n".join(lines), parse_mode="HTML")
