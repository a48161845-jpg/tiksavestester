"""
Админ-команды реферальной системы:
- /refpoints <id/username> <±N>  — начислить/списать баллы вручную
- /refcount  <id/username> <±N>  — скорректировать счётчик рефералов вручную
- /refreset  <id/username>       — обнулить баллы и рефералов
- /refid     <id/username>       — кто пригласил этого пользователя
- /refinfo   <id/username>       — список всех, кого пригласил пользователь
                                     (если больше 20 — отправляется HTML-файлом)
"""
import contextlib
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from aiogram.filters import Command
from aiogram.types import Message, FSInputFile

from globals_state import dp
from config import MSK_TZ
from helpers import is_admin, code, html_escape
from storage import store
from user_label import resolve_user_label, resolve_uid_from_arg
from gates import gate_message
from logging_channel import format_user_for_log
from admin_log_file import log_admin


def _parse_signed_int(raw: str) -> Optional[int]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@dp.message(Command("refpoints"))
async def refpoints_cmd(message: Message):
    admin_id = message.from_user.id
    admin_label = await resolve_user_label(message.bot, admin_id)
    store.set_user_label(admin_id, admin_label)

    if not is_admin(admin_id):
        return
    if not await gate_message(message, admin_label):
        return

    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer(
            "Использование:\n"
            f"{code('/refpoints id/username ±N')}\n\n"
            f"Пример: {code('/refpoints 123456 +50')}\n"
            f"Пример: {code('/refpoints @user -20')}",
            parse_mode="HTML",
        )
        return

    uid = await resolve_uid_from_arg(message.bot, parts[1])
    if uid is None:
        await message.answer("❌ Пользователь не найден. Проверь ID или username.", parse_mode="HTML")
        return

    delta = _parse_signed_int(parts[2])
    if delta is None:
        await message.answer("❌ Число баллов указано неверно (пример: +50 или -20).", parse_mode="HTML")
        return

    who_label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, who_label)
    new_balance = store.add_ref_points_delta(uid, delta)

    sign = "➕" if delta >= 0 else "➖"
    await message.answer(
        f"{sign} <b>Баллы скорректированы</b>\n\n"
        f"👤 {format_user_for_log(who_label, uid)}\n"
        f"Изменение: <b>{delta:+d} 🎟</b>\n"
        f"Новый баланс: <b>{new_balance} 🎟</b>",
        parse_mode="HTML",
    )
    log_admin(admin_id, "refpoints", f"target={uid} delta={delta} new_balance={new_balance}")

    with contextlib.suppress(Exception):
        await message.bot.send_message(
            uid,
            f"🎟 Администратор изменил твой баланс баллов: <b>{delta:+d}</b>\n"
            f"Текущий баланс: <b>{new_balance} 🎟</b>",
            parse_mode="HTML",
        )


@dp.message(Command("refcount"))
async def refcount_cmd(message: Message):
    admin_id = message.from_user.id
    admin_label = await resolve_user_label(message.bot, admin_id)
    store.set_user_label(admin_id, admin_label)

    if not is_admin(admin_id):
        return
    if not await gate_message(message, admin_label):
        return

    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer(
            "Использование:\n"
            f"{code('/refcount id/username ±N')}\n\n"
            f"Пример: {code('/refcount 123456 +3')}\n"
            f"Пример: {code('/refcount @user -1')}",
            parse_mode="HTML",
        )
        return

    uid = await resolve_uid_from_arg(message.bot, parts[1])
    if uid is None:
        await message.answer("❌ Пользователь не найден. Проверь ID или username.", parse_mode="HTML")
        return

    delta = _parse_signed_int(parts[2])
    if delta is None:
        await message.answer("❌ Число указано неверно (пример: +3 или -1).", parse_mode="HTML")
        return

    who_label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, who_label)
    new_count = store.add_ref_count_delta(uid, delta)

    sign = "➕" if delta >= 0 else "➖"
    await message.answer(
        f"{sign} <b>Счётчик рефералов скорректирован</b>\n\n"
        f"👤 {format_user_for_log(who_label, uid)}\n"
        f"Изменение: <b>{delta:+d}</b>\n"
        f"Новое значение: <b>{new_count}</b> рефералов",
        parse_mode="HTML",
    )
    log_admin(admin_id, "refcount", f"target={uid} delta={delta} new_count={new_count}")


@dp.message(Command("refreset"))
async def refreset_cmd(message: Message):
    admin_id = message.from_user.id
    admin_label = await resolve_user_label(message.bot, admin_id)
    store.set_user_label(admin_id, admin_label)

    if not is_admin(admin_id):
        return
    if not await gate_message(message, admin_label):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer(f"Использование: {code('/refreset id/username')}", parse_mode="HTML")
        return

    uid = await resolve_uid_from_arg(message.bot, parts[1].strip())
    if uid is None:
        await message.answer("❌ Пользователь не найден. Проверь ID или username.", parse_mode="HTML")
        return

    who_label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, who_label)
    store.reset_ref_stats(uid)

    await message.answer(
        f"🧹 <b>Обнулено</b>\n\n👤 {format_user_for_log(who_label, uid)}\n"
        f"Баллы и счётчик рефералов сброшены в 0.",
        parse_mode="HTML",
    )
    log_admin(admin_id, "refreset", f"target={uid}")


@dp.message(Command("refid"))
async def refid_cmd(message: Message):
    admin_id = message.from_user.id
    admin_label = await resolve_user_label(message.bot, admin_id)
    store.set_user_label(admin_id, admin_label)

    if not is_admin(admin_id):
        return
    if not await gate_message(message, admin_label):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer(f"Использование: {code('/refid id/username')}", parse_mode="HTML")
        return

    uid = await resolve_uid_from_arg(message.bot, parts[1].strip())
    if uid is None:
        await message.answer("❌ Пользователь не найден. Проверь ID или username.", parse_mode="HTML")
        return

    who_label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, who_label)

    referrer_id = store.get_referrer(uid)
    if referrer_id is None:
        await message.answer(
            f"👤 {format_user_for_log(who_label, uid)}\n\n"
            f"❌ Пришёл не по реферальной ссылке.",
            parse_mode="HTML",
        )
        return

    referrer_label = store.get_user_label(referrer_id)
    await message.answer(
        f"👤 {format_user_for_log(who_label, uid)}\n\n"
        f"🔗 Приглашён пользователем:\n{format_user_for_log(referrer_label, referrer_id)}",
        parse_mode="HTML",
    )


def _refinfo_html(owner_label: str, owner_uid: int, rows: list) -> str:
    body_rows = []
    for i, (uid, label, rewarded) in enumerate(rows, start=1):
        badge = '<span class="badge yes">начислено</span>' if rewarded else '<span class="badge no">ожидает</span>'
        body_rows.append(
            f"<tr><td>{i}</td><td>{html_escape(label)}</td><td>{uid}</td><td>{badge}</td></tr>"
        )
    ts = datetime.now(MSK_TZ).strftime("%d.%m.%Y %H:%M МСК")
    return (
        "<!DOCTYPE html><html lang='ru'><head><meta charset='UTF-8'>"
        f"<title>Рефералы {html_escape(owner_label)}</title>"
        "<style>"
        "body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#0f1115;color:#e8e8e8;padding:24px;}"
        "h1{font-size:20px;margin-bottom:4px;}"
        ".muted{color:#767c88;font-size:13px;}"
        "table{border-collapse:collapse;width:100%;margin-top:18px;}"
        "th,td{padding:10px 12px;border-bottom:1px solid #2a2d34;text-align:left;font-size:14px;}"
        "th{background:#1b1e24;color:#9aa0aa;text-transform:uppercase;font-size:11px;letter-spacing:.04em;}"
        "tr:hover{background:#171a20;}"
        ".badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;}"
        ".yes{background:#16321f;color:#57d778;}"
        ".no{background:#3a2222;color:#ff8080;}"
        "</style></head><body>"
        f"<h1>🎁 Рефералы: {html_escape(owner_label)} (ID {owner_uid})</h1>"
        f"<p class='muted'>Всего приглашено: {len(rows)} • Сформировано: {ts}</p>"
        "<table><tr><th>#</th><th>Пользователь</th><th>ID</th><th>Баллы</th></tr>"
        + "".join(body_rows) +
        "</table></body></html>"
    )


@dp.message(Command("refinfo"))
async def refinfo_cmd(message: Message):
    admin_id = message.from_user.id
    admin_label = await resolve_user_label(message.bot, admin_id)
    store.set_user_label(admin_id, admin_label)

    if not is_admin(admin_id):
        return
    if not await gate_message(message, admin_label):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer(f"Использование: {code('/refinfo id/username')}", parse_mode="HTML")
        return

    uid = await resolve_uid_from_arg(message.bot, parts[1].strip())
    if uid is None:
        await message.answer("❌ Пользователь не найден. Проверь ID или username.", parse_mode="HTML")
        return

    who_label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, who_label)

    ref_uids = store.referrals_of(uid)
    if not ref_uids:
        await message.answer(
            f"👤 {format_user_for_log(who_label, uid)}\n\n📦 У этого пользователя пока нет рефералов.",
            parse_mode="HTML",
        )
        return

    refs_data = store.data.get("referrals", {})
    rows = []
    for r_uid in ref_uids:
        rec = refs_data.get(str(r_uid))
        rewarded = bool(rec.get("rewarded")) if isinstance(rec, dict) else True
        rows.append((r_uid, store.get_user_label(r_uid), rewarded))

    if len(rows) <= 20:
        lines = [f"👤 {format_user_for_log(who_label, uid)}\n", f"📦 <b>Рефералы ({len(rows)}):</b>\n"]
        for i, (r_uid, r_label, rewarded) in enumerate(rows, start=1):
            mark = "✅" if rewarded else "⏳"
            lines.append(f"{i}. {mark} {format_user_for_log(r_label, r_uid)}")
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    html_text = _refinfo_html(who_label, uid, rows)
    tmp = Path(f"tmp_refinfo_{uid}_{int(time.time())}.html")
    try:
        tmp.write_text(html_text, encoding="utf-8")
        await message.answer_document(
            FSInputFile(tmp, filename=f"referrals_{uid}.html"),
            caption=f"🎁 Рефералы {who_label} — всего {len(rows)}",
        )
    finally:
        with contextlib.suppress(Exception):
            tmp.unlink(missing_ok=True)
