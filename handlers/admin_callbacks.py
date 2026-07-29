"""
Обработка callback-кнопок админ-панели (callback_data, начинающийся на "ad:").
"""
import contextlib

from aiogram import F
from aiogram.types import CallbackQuery

from globals_state import dp
from helpers import is_admin, parse_stats_mode
from storage import store
from user_label import resolve_user_label
from gates import gate_callback
from logging_channel import log_admin_action_to_channel, format_user_for_log
from keyboards import (
    ADMIN_MENU_TEXT,
    ADMIN_HELP_TEXT,
    admin_menu_kb,
    admin_back_kb,
    admin_broadcast_confirm_kb,
)
from stats import (
    send_stats_message,
    send_top_message,
    _admin_banlist_text,
)
from broadcast import (
    REMINDER_MSG,
    ADVERTISEMENT_MSG,
    do_broadcast,
    pending_admin_broadcast,
    pending_admin_broadcast_text,
    pending_admin_broadcast_source,
    pending_admin_broadcast_cancel,
)


@dp.callback_query(F.data.startswith("ad:"))
async def admin_cb(call: CallbackQuery):
    uid = call.from_user.id
    label = await resolve_user_label(call.bot, uid)
    store.set_user_label(uid, label)

    if not is_admin(uid):
        await call.answer("Нет доступа.", show_alert=True)
        return

    if not await gate_callback(call, label):
        return

    if not call.message:
        await call.answer()
        return

    parts = (call.data or "").split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""
    if cmd == "stats":
        await call.answer()
        if call.message:
            mode = parse_stats_mode(arg) if arg else "all"
            await send_stats_message(call.message, uid, label, mode, edit=True)
        return

    if cmd == "top":
        await call.answer()
        if call.message:
            mode = parse_stats_mode(arg) if arg else "all"
            await send_top_message(call.message, uid, label, mode, edit=True)
        return

    if cmd == "send":
        kind = arg
        if pending_admin_broadcast.get(uid) != kind:
            await call.answer("Нет подтверждения.", show_alert=True)
            return
        pending_admin_broadcast.pop(uid, None)
        pending_admin_broadcast_source.pop(uid, None)
        pending_admin_broadcast_cancel.pop(uid, None)
        await call.answer("Ок")
        if call.message:
            with contextlib.suppress(Exception):
                await call.message.delete()
        if not call.message:
            return
        if kind == "reminder":
            pending_admin_broadcast_text.pop(uid, None)
            await do_broadcast(call.message, uid, label, REMINDER_MSG)
            return
        if kind == "advert":
            pending_admin_broadcast_text.pop(uid, None)
            await do_broadcast(call.message, uid, label, ADVERTISEMENT_MSG)
            return
        if kind == "custom":
            raw = pending_admin_broadcast_text.get(uid, "")
            pending_admin_broadcast_text.pop(uid, None)
            if raw:
                await do_broadcast(call.message, uid, label, raw, already_html=True)
            return
        pending_admin_broadcast_text.pop(uid, None)
        return

    if cmd == "banlist":
        bans = store.list_bans()
        await log_admin_action_to_channel(call.bot, "Бан-лист (кнопка)", [f"👤 Кто: <b>{format_user_for_log(label, uid)}</b>", f"🚫 Кол-во: <b>{len(bans)}</b>"])
        if call.message:
            with contextlib.suppress(Exception):
                await call.message.edit_text(_admin_banlist_text(), parse_mode="HTML", reply_markup=admin_back_kb())
        await call.answer("Ок")
        return

    if cmd == "adminlist":
        from config import ADMINS
        lines = ["👑 <b>Администраторы</b>\n━━━━━━━━━━━━━━━━━━━━\n"]
        lines.append("🔒 <b>Суперадмины:</b>")
        for aid in sorted(ADMINS):
            lbl = store.get_user_label(aid)
            lines.append(f"  └ <b>{format_user_for_log(lbl, aid)}</b>")
        extra = store.get_extra_admins()
        lines.append(f"\n➕ <b>Дополнительные ({len(extra)}):</b>")
        if extra:
            for aid in sorted(extra):
                lbl = store.get_user_label(aid)
                lines.append(f"  └ <b>{format_user_for_log(lbl, aid)}</b>")
        else:
            lines.append("  <i>нет</i>")
        lines.append(f"\n<i>Управление: /adminadd ID · /admindel ID</i>")
        if call.message:
            with contextlib.suppress(Exception):
                await call.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=admin_back_kb())
        await call.answer("Ок")
        return

    if cmd == "dbfile":
        await log_admin_action_to_channel(call.bot, "Дамп БД (кнопка)", [f"👤 Кто: <b>{format_user_for_log(label, uid)}</b>"])
        await call.answer("Формирую дамп…")
        from db_report import send_db_json
        await send_db_json(call.bot, uid)
        return

    if cmd == "help":
        await log_admin_action_to_channel(call.bot, "Подсказка админ-панели (кнопка)", [f"👤 Кто: <b>{format_user_for_log(label, uid)}</b>"])
        if call.message:
            with contextlib.suppress(Exception):
                await call.message.edit_text(ADMIN_HELP_TEXT, parse_mode="HTML", reply_markup=admin_back_kb())
        await call.answer("Ок")
        return

    if cmd == "reminder":
        pending_admin_broadcast[uid] = "reminder"
        pending_admin_broadcast_text.pop(uid, None)
        pending_admin_broadcast_source[uid] = "panel"
        users_cnt = len(store.data.get("users", []))
        if not call.message:
            await call.answer("Ошибка: сообщение недоступно.", show_alert=True)
            return
        with contextlib.suppress(Exception):
            await call.message.delete()
        await call.message.answer(
            "📣 <b>Подтверждение рассылки</b>\n\n"
            "Тип: <b>Напоминание</b>\n"
            f"Получателей: <b>{users_cnt}</b>\n\n"
            "Отправить?",
            parse_mode="HTML",
            reply_markup=admin_broadcast_confirm_kb("reminder"),
        )
        await call.answer()
        return

    if cmd == "advert":
        pending_admin_broadcast[uid] = "advert"
        pending_admin_broadcast_text.pop(uid, None)
        pending_admin_broadcast_source[uid] = "panel"
        users_cnt = len(store.data.get("users", []))
        if not call.message:
            await call.answer("Ошибка: сообщение недоступно.", show_alert=True)
            return
        with contextlib.suppress(Exception):
            await call.message.delete()
        await call.message.answer(
            "📣 <b>Подтверждение рассылки</b>\n\n"
            "Тип: <b>Реклама</b>\n"
            f"Получателей: <b>{users_cnt}</b>\n\n"
            "Отправить?",
            parse_mode="HTML",
            reply_markup=admin_broadcast_confirm_kb("advert"),
        )
        await call.answer()
        return

    if cmd == "cancel":
        pending_admin_broadcast.pop(uid, None)
        pending_admin_broadcast_text.pop(uid, None)
        pending_admin_broadcast_source.pop(uid, None)
        pending_admin_broadcast_cancel.pop(uid, None)
        if call.message:
            with contextlib.suppress(Exception):
                await call.message.delete()
        await call.answer()
        return

    if cmd == "bcancel":
        if not is_admin(uid):
            await call.answer("Нет доступа.", show_alert=True)
            return
        pending_admin_broadcast_cancel[uid] = True
        await call.answer("Останавливаю…")
        if call.message:
            with contextlib.suppress(Exception):
                await call.message.edit_reply_markup(reply_markup=None)
        return

    if cmd == "back":
        if call.message:
            with contextlib.suppress(Exception):
                await call.message.edit_text(ADMIN_MENU_TEXT, parse_mode="HTML", reply_markup=admin_menu_kb())
        await call.answer()
        return

    if cmd == "close":
        if pending_admin_broadcast_source.get(uid) == "panel":
            pending_admin_broadcast.pop(uid, None)
            pending_admin_broadcast_text.pop(uid, None)
            pending_admin_broadcast_source.pop(uid, None)
            if call.message:
                with contextlib.suppress(Exception):
                    await call.message.edit_text(ADMIN_MENU_TEXT, parse_mode="HTML", reply_markup=admin_menu_kb())
            await call.answer()
            return
        with contextlib.suppress(Exception):
            await call.message.delete()
        await call.answer()
        return

    await call.answer()
