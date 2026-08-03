"""
Команды управления страйками — оставлены для совместимости,
но страйки удалены из системы. Команды выводят соответствующее сообщение,
чтобы админ, набравший команду по старой памяти, не оставался без ответа.
"""
from aiogram.filters import Command
from aiogram.types import Message

from globals_state import dp
from helpers import is_admin
from gates import gate_message
from user_label import resolve_user_label
from storage import store

_STRIKES_OFF_TEXT = (
    "ℹ️ <b>Система страйков отключена</b>\n\n"
    "Используется тихий анти-спам лимит без накопления страйков.\n"
    "Для управления банами используй /ban и /unban."
)


@dp.message(Command("strikes", "strike", "strikeadd", "strikedel", "strikeclear"))
async def strikes_cmd(message: Message):
    admin_id = message.from_user.id
    label = await resolve_user_label(message.bot, admin_id)
    store.set_user_label(admin_id, label)

    if not is_admin(admin_id):
        return
    if not await gate_message(message, label):
        return

    await message.answer(_STRIKES_OFF_TEXT, parse_mode="HTML")
