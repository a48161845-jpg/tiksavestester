"""
Команда /ref — главное меню реферальной системы и магазина подарков.
"""
from aiogram.filters import Command
from aiogram.types import Message

from globals_state import dp
from storage import store
from user_label import resolve_user_label
from gates import gate_message
from referral import ref_menu_text
from keyboards import ref_menu_kb


@dp.message(Command("ref"))
async def ref_cmd(message: Message):
    uid = message.from_user.id
    label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, label)

    if not await gate_message(message, label):
        return

    await message.answer(ref_menu_text(uid), parse_mode="HTML", reply_markup=ref_menu_kb())
