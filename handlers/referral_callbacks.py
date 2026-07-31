"""
Callback-обработчики реферальной системы и магазина подарков:
- ref:*     — навигация по меню /ref (магазин, рефералы, заявки, топ, помощь, назад);
- gift:*    — выбор/подтверждение/отмена покупки подарка;
- admgift:* — админ выдал/отклонил заявку (кнопки под сообщением в лог-канале).
"""
import contextlib

from aiogram import F
from aiogram.types import CallbackQuery

from globals_state import dp
from config import REFERRAL_LOG_CHANNEL_ID
from helpers import is_admin, code, now_msk_str
from storage import store
from user_label import resolve_user_label
from gates import gate_callback
from logging_channel import format_user_for_log
from referral import (
    pending_gift_purchase,
    ref_menu_text,
    gift_shop_text,
    gift_by_key,
    gift_confirm_text,
    gift_created_text,
    my_requests_text,
    top_referrers_text,
    HOW_IT_WORKS_TEXT,
)
from keyboards import (
    ref_menu_kb,
    ref_back_kb,
    gift_shop_kb,
    gift_confirm_kb,
    gift_admin_kb,
)


async def _safe_edit(call: CallbackQuery, text: str, kb) -> None:
    if not call.message:
        return
    with contextlib.suppress(Exception):
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@dp.callback_query(F.data.startswith("ref:"))
async def ref_cb(call: CallbackQuery):
    uid = call.from_user.id
    label = await resolve_user_label(call.bot, uid)
    store.set_user_label(uid, label)

    if not await gate_callback(call, label):
        return

    action = (call.data or "").split(":", 1)[-1]

    if action == "back":
        await _safe_edit(call, ref_menu_text(uid), ref_menu_kb())
        await call.answer()
        return

    if action == "shop":
        rs = store.get_ref_stats(uid)
        await _safe_edit(call, gift_shop_text(uid), gift_shop_kb(rs["ref_points"]))
        await call.answer()
        return

    if action == "myrequests":
        await _safe_edit(call, my_requests_text(uid), ref_back_kb())
        await call.answer()
        return

    if action == "top":
        await _safe_edit(call, top_referrers_text(uid), ref_back_kb())
        await call.answer()
        return

    if action == "howitworks":
        await _safe_edit(call, HOW_IT_WORKS_TEXT, ref_back_kb())
        await call.answer()
        return

    await call.answer()


@dp.callback_query(F.data.startswith("gift:"))
async def gift_cb(call: CallbackQuery):
    uid = call.from_user.id
    label = await resolve_user_label(call.bot, uid)
    store.set_user_label(uid, label)

    if not await gate_callback(call, label):
        return

    parts = (call.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "buy":
        key = parts[2] if len(parts) > 2 else ""
        gift = gift_by_key(key)
        if not gift:
            await call.answer("❌ Такого подарка нет.", show_alert=True)
            return
        rs = store.get_ref_stats(uid)
        if rs["ref_points"] < gift["price"]:
            await call.answer(
                f"❌ Недостаточно баллов: нужно {gift['price']} 🎟, у тебя {rs['ref_points']} 🎟",
                show_alert=True,
            )
            return
        pending_gift_purchase[uid] = key
        await _safe_edit(call, gift_confirm_text(gift), gift_confirm_kb(key))
        await call.answer()
        return

    if action == "cancel":
        pending_gift_purchase.pop(uid, None)
        await _safe_edit(call, ref_menu_text(uid), ref_menu_kb())
        await call.answer("Отменено.")
        return

    if action == "confirm":
        key = parts[2] if len(parts) > 2 else ""
        waiting_key = pending_gift_purchase.get(uid)
        gift = gift_by_key(key)

        if waiting_key != key or not gift:
            await call.answer("⏱️ Запрос устарел, открой магазин заново.", show_alert=True)
            return

        rs = store.get_ref_stats(uid)
        if rs["ref_points"] < gift["price"]:
            pending_gift_purchase.pop(uid, None)
            await call.answer("❌ Недостаточно баллов.", show_alert=True)
            await _safe_edit(call, ref_menu_text(uid), ref_menu_kb())
            return

        pending_gift_purchase.pop(uid, None)
        store.add_ref_points_delta(uid, -gift["price"])
        req_id = store.new_gift_request(uid, gift["key"], gift["name"], gift["price"])

        await _safe_edit(call, gift_created_text(gift), ref_back_kb())
        await call.answer("✅ Заявка создана!")

        rs_now = store.get_ref_stats(uid)
        admin_text = (
            "🎁 <b>Новая заявка на подарок</b>\n\n"
            f"👤 Пользователь:\n{format_user_for_log(label, uid)}\n\n"
            f"🆔 ID:\n{code(uid)}\n\n"
            f"🎁 Подарок:\n{gift['emoji']} {gift['name']}\n\n"
            f"🎟 Стоимость:\n{gift['price']}\n\n"
            f"👥 Рефералы:\n{rs_now['referrals_count']}\n\n"
            f"📅 Дата:\n{now_msk_str()}"
        )
        with contextlib.suppress(Exception):
            await call.bot.send_message(
                REFERRAL_LOG_CHANNEL_ID,
                admin_text,
                parse_mode="HTML",
                reply_markup=gift_admin_kb(req_id),
            )
        return

    await call.answer()


@dp.callback_query(F.data.startswith("admgift:"))
async def admin_gift_cb(call: CallbackQuery):
    admin_id = call.from_user.id
    admin_label = await resolve_user_label(call.bot, admin_id)
    store.set_user_label(admin_id, admin_label)

    if not is_admin(admin_id):
        await call.answer("Только для администрации.", show_alert=True)
        return

    parts = (call.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""
    req_id_str = parts[2] if len(parts) > 2 else ""
    if not req_id_str.isdigit():
        await call.answer("❌ Ошибка заявки.", show_alert=True)
        return
    req_id = int(req_id_str)

    req = store.get_gift_request(req_id)
    if not req:
        await call.answer("❌ Заявка не найдена.", show_alert=True)
        return
    if req.get("status") != "pending":
        await call.answer("Заявка уже обработана.", show_alert=True)
        return

    target_uid = int(req["user_id"])
    gift = gift_by_key(req.get("gift_key", "")) or {"emoji": "🎁", "name": req.get("gift_name", "?")}
    target_label = store.get_user_label(target_uid)
    base_text = (call.message.html_text or call.message.text or "") if call.message else ""

    if action == "ok":
        store.set_gift_request_status(req_id, "completed")
        with contextlib.suppress(Exception):
            await call.bot.send_message(
                target_uid,
                "🎉 <b>Подарок выдан!</b>\n\n"
                "Твоя заявка обработана.\n\n"
                f"🎁 Получено:\n{gift['emoji']} {gift['name']}\n\n"
                "Спасибо, что помогаешь развивать Tiksaves ❤️",
                parse_mode="HTML",
            )
        with contextlib.suppress(Exception):
            if call.message:
                await call.message.edit_text(base_text + "\n\n✅ <b>ВЫДАНО</b>", parse_mode="HTML", reply_markup=None)
        await call.answer("✅ Отмечено как выдано.")

        log_text = (
            "✅ <b>Подарок выдан</b>\n\n"
            f"👤 {format_user_for_log(target_label, target_uid)}\n\n"
            f"🎁 {gift['emoji']} {gift['name']}\n\n"
            f"Администратор:\n{format_user_for_log(admin_label, admin_id)}"
        )
        with contextlib.suppress(Exception):
            await call.bot.send_message(REFERRAL_LOG_CHANNEL_ID, log_text, parse_mode="HTML")
        return

    if action == "no":
        store.set_gift_request_status(req_id, "rejected")
        price = int(req.get("gift_price", 0))
        new_balance = store.add_ref_points_delta(target_uid, price)
        with contextlib.suppress(Exception):
            await call.bot.send_message(
                target_uid,
                "❌ <b>Заявка отменена</b>\n\n"
                "К сожалению, ваша заявка была отклонена.\n\n"
                f"🎁 Подарок:\n{gift['emoji']} {gift['name']}\n\n"
                f"🎟 Баллы возвращены:\n+{price}\n\n"
                f"Текущий баланс:\n{new_balance} 🎟",
                parse_mode="HTML",
            )
        with contextlib.suppress(Exception):
            if call.message:
                await call.message.edit_text(base_text + "\n\n❌ <b>ОТКЛОНЕНО</b>", parse_mode="HTML", reply_markup=None)
        await call.answer("❌ Заявка отклонена, баллы возвращены.")

        log_text = (
            "❌ <b>Заявка отклонена</b>\n\n"
            f"👤 {format_user_for_log(target_label, target_uid)}\n\n"
            f"🎁 {gift['emoji']} {gift['name']}\n\n"
            f"Возвращено:\n{price} 🎟"
        )
        with contextlib.suppress(Exception):
            await call.bot.send_message(REFERRAL_LOG_CHANNEL_ID, log_text, parse_mode="HTML")
        return

    await call.answer()
