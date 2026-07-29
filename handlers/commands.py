"""
Базовые пользовательские и общие команды бота.
"""
from aiogram.filters import Command
from aiogram.types import Message, LinkPreviewOptions

from globals_state import dp
from config import log
from helpers import html_escape, is_admin, parse_stats_mode, parse_date_token
from storage import store
from user_label import resolve_user_label
from gates import gate_message
from logging_channel import log_event, log_admin_action_to_channel, format_user_for_log
from admin_log_file import log_admin
from keyboards import (
    START_TEXT,
    HELP_TEXT,
    SUPPORT_TEXT,
    DONATE_TEXT,
    ADMIN_MENU_TEXT,
    help_kb,
    donate_main_kb,
    admin_menu_kb,
)
from stats import (
    send_stats_message,
    send_stats_range_message,
    send_top_message,
    send_top_range_message,
    _user_stats_text,
    _user_stats_period_text,
    _user_stats_range_text,
)


@dp.message(Command("start"))
async def start_cmd(message: Message):
    uid = message.from_user.id
    label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, label)

    is_new = store.register(uid)
    if is_new:
        await log_event(
            message.bot,
            "user",
            [
                "👋 Категория: <b>Приход пользователя</b>",
                f"👤 User/id: <b>{format_user_for_log(label, uid)}</b>",
            ],
        )

    if not await gate_message(message, label):
        return

    await message.answer(START_TEXT, parse_mode="HTML")


@dp.message(Command("help"))
async def help_cmd(message: Message):
    uid = message.from_user.id
    label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, label)
    if not await gate_message(message, label):
        return
    await message.answer(HELP_TEXT, parse_mode="HTML", reply_markup=help_kb())
    log.info("help: uid=%s", uid)


@dp.message(Command("support"))
async def support_cmd(message: Message):
    uid = message.from_user.id
    label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, label)
    if not await gate_message(message, label):
        return
    await message.answer(SUPPORT_TEXT, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
    await log_event(
        message.bot,
        "support",
        [
            "🆘 Категория: <b>Открыта поддержка</b>",
            f"👤 User/id: <b>{format_user_for_log(label, uid)}</b>",
        ],
    )


@dp.message(Command("donate"))
async def donate_cmd(message: Message):
    uid = message.from_user.id
    label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, label)
    if not await gate_message(message, label):
        return
    await message.answer(DONATE_TEXT, parse_mode="HTML", reply_markup=donate_main_kb(), link_preview_options=LinkPreviewOptions(is_disabled=True))
    await log_event(
        message.bot,
        "donate_open",
        [
            "💛 Категория: <b>Открыт донат</b>",
            f"👤 User/id: <b>{format_user_for_log(label, uid)}</b>",
        ],
    )


@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    uid = message.from_user.id
    label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, label)

    if not is_admin(uid):
        return
    if not await gate_message(message, label):
        return

    log_admin(uid, "open_admin_panel")
    await log_admin_action_to_channel(
        message.bot,
        "Открыл админ-панель",
        [f"👤 Кто: <b>{format_user_for_log(label, uid)}</b>"],
    )
    await message.answer(ADMIN_MENU_TEXT, parse_mode="HTML", reply_markup=admin_menu_kb())


@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    uid = message.from_user.id
    label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, label)

    parts_all = (message.text or "").split()
    if len(parts_all) >= 3:
        d1 = parse_date_token(parts_all[1])
        d2 = parse_date_token(parts_all[2])
        if d1 and d2:
            if not is_admin(uid):
                if not await gate_message(message, label):
                    return
                await message.answer(_user_stats_range_text(uid, d1, d2), parse_mode="HTML")
                await log_event(
                    message.bot,
                    "userstats",
                    [
                        "📊 Категория: <b>Статистика пользователя (диапазон)</b>",
                        f"👤 User/id: <b>{format_user_for_log(label, uid)}</b>",
                        f"🧾 Период: <b>{d1.strftime('%d.%m.%Y')} - {d2.strftime('%d.%m.%Y')}</b>",
                    ],
                )
                return
            if not await gate_message(message, label):
                return
            await send_stats_range_message(message, uid, label, d1, d2)
            return

    if not is_admin(uid):
        if not await gate_message(message, label):
            return
        parts = (message.text or "").split(maxsplit=1)
        mode = "all"
        if len(parts) == 2:
            mode = parse_stats_mode(parts[1])
        if mode == "all":
            await message.answer(_user_stats_text(uid), parse_mode="HTML")
        else:
            await message.answer(_user_stats_period_text(uid, mode), parse_mode="HTML")
        await log_event(
            message.bot,
            "userstats",
            [
                "📊 Категория: <b>Статистика пользователя</b>",
                f"👤 User/id: <b>{format_user_for_log(label, uid)}</b>",
                f"🧾 Режим: <b>{html_escape(mode)}</b>",
            ],
        )
        return
    if not await gate_message(message, label):
        return

    parts = (message.text or "").split(maxsplit=1)
    mode = "all"
    if len(parts) == 2:
        mode = parse_stats_mode(parts[1])

    await send_stats_message(message, uid, label, mode)


@dp.message(Command("top"))
async def top_cmd(message: Message):
    uid = message.from_user.id
    label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, label)

    if not is_admin(uid):
        return
    if not await gate_message(message, label):
        return

    parts_all = (message.text or "").split()
    if len(parts_all) >= 3:
        d1 = parse_date_token(parts_all[1])
        d2 = parse_date_token(parts_all[2])
        if d1 and d2:
            await send_top_range_message(message, uid, label, d1, d2)
            return

    parts = (message.text or "").split(maxsplit=1)
    mode = "all"
    if len(parts) == 2:
        mode = parse_stats_mode(parts[1])
    await send_top_message(message, uid, label, mode)
