"""
Реферальная система + магазин подарков за баллы.

Логика формирования текстов и временное состояние (ожидание подтверждения
покупки) — здесь. Хендлеры команд/колбэков — в handlers/referral_commands.py
и handlers/referral_callbacks.py. Подарки выдаются вручную администрацией —
бот только ведёт учёт баллов, рефералов и заявок.
"""
from typing import Dict, List, Optional

from config import BOT_USERNAME, GIFTS, GIFTS_BY_KEY, REF_POINTS_PER_REFERRAL, REF_TOP_LIMIT
from helpers import html_escape
from storage import store

# uid -> gift_key: ждём подтверждения покупки этого подарка
pending_gift_purchase: Dict[int, str] = {}
PENDING_GIFT_TTL_SEC = 300


def ref_link(uid: int) -> str:
    return f"https://t.me/{BOT_USERNAME}?start={uid}"


def gift_by_key(key: str) -> Optional[dict]:
    return GIFTS_BY_KEY.get(key)


def ref_menu_text(uid: int) -> str:
    rs = store.get_ref_stats(uid)
    return (
        "🎁 <b>Реферальная система Tiksaves</b>\n\n"
        f"👥 Твои рефералы:\n<b>{rs['referrals_count']}</b>\n\n"
        f"🎟 Баланс:\n<b>{rs['ref_points']} баллов</b>\n\n"
        "Приглашай друзей и получай баллы!\n\n"
        f"За каждого активного пользователя:\n+{REF_POINTS_PER_REFERRAL} 🎟\n\n"
        "Твоя ссылка:\n"
        f"🔗 t.me/{BOT_USERNAME}?start={uid}"
    )


def _gifts_grouped_by_price() -> List[tuple]:
    """Группирует каталог подарков по цене, сохраняя порядок появления."""
    order: List[int] = []
    groups: Dict[int, List[dict]] = {}
    for g in GIFTS:
        price = g["price"]
        groups.setdefault(price, []).append(g)
        if price not in order:
            order.append(price)
    return [(price, groups[price]) for price in order]


def gift_shop_text(uid: int) -> str:
    rs = store.get_ref_stats(uid)
    lines = ["🎁 <b>Магазин подарков</b>\n", f"Твой баланс:\n🎟 <b>{rs['ref_points']}</b>\n", "Выбери подарок:\n"]
    for price, gifts in _gifts_grouped_by_price():
        for g in gifts:
            lines.append(f"{g['emoji']} {html_escape(g['name'])}")
        lines.append(f"<b>{price} 🎟</b>\n")
    return "\n".join(lines)


def gift_confirm_text(gift: dict) -> str:
    return (
        "🎁 <b>Подтверждение покупки</b>\n\n"
        f"Подарок:\n{gift['emoji']} {html_escape(gift['name'])}\n\n"
        f"Стоимость:\n🎟 {gift['price']}\n\n"
        "После подтверждения баллы будут списаны.\n"
        "Подарок будет выдан вручную администрацией.\n\n"
        "Продолжить?"
    )


def gift_created_text(gift: dict) -> str:
    return (
        "✅ <b>Заявка создана!</b>\n\n"
        f"🎁 Подарок:\n{gift['emoji']} {html_escape(gift['name'])}\n\n"
        f"🎟 Списано:\n{gift['price']} баллов\n\n"
        "Статус:\n⏳ Ожидает выдачи\n\n"
        "Администратор скоро обработает заявку.\n"
        "Спасибо за развитие Tiksaves ❤️"
    )


_STATUS_LABELS = {
    "pending": "⏳ Ожидает выдачи",
    "completed": "✅ Выдан",
    "rejected": "❌ Отменён",
}


def my_requests_text(uid: int) -> str:
    reqs = store.user_gift_requests(uid)
    if not reqs:
        return "📦 <b>История заявок</b>\n\nПока пусто — загляни в 🎁 Магазин подарков!"
    lines = ["📦 <b>История заявок</b>\n"]
    for i, r in enumerate(reqs, start=1):
        g = gift_by_key(r.get("gift_key", "")) or {}
        emoji = g.get("emoji", "🎁")
        name = r.get("gift_name") or g.get("name", "?")
        status = _STATUS_LABELS.get(r.get("status", ""), str(r.get("status", "?")))
        lines.append(f"{i}.\n{emoji} {html_escape(name)}\n{status}\n")
    return "\n".join(lines)


def top_referrers_text(uid: int) -> str:
    top = store.top_referrers(REF_TOP_LIMIT)
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Топ рефереров Tiksaves</b>\n"]
    if not top:
        lines.append("Пока никто не пригласил ни одного реферала.\n")
    for i, (ref_uid, cnt) in enumerate(top):
        medal = medals[i] if i < 3 else f"{i + 1}."
        label = store.get_user_label(ref_uid)
        lines.append(f"{medal} {html_escape(label)}\n👥 {cnt} рефералов\n")

    rank = store.ref_rank(uid)
    rs = store.get_ref_stats(uid)
    lines.append("Твоё место:")
    if rank:
        lines.append(f"#{rank}\n👥 {rs['referrals_count']} рефералов")
    else:
        lines.append(f"— пока нет рефералов\n👥 {rs['referrals_count']} рефералов")
    return "\n".join(lines)


HOW_IT_WORKS_TEXT = (
    "📖 <b>Реферальная система</b>\n\n"
    "1️⃣ Получи свою ссылку в /ref\n\n"
    "2️⃣ Приглашай друзей\n\n"
    "3️⃣ За каждого активного пользователя получай:\n"
    f"+{REF_POINTS_PER_REFERRAL} 🎟\n\n"
    "4️⃣ Обменивай баллы на Telegram-подарки\n\n"
    "🎁 Выдача подарков производится вручную администрацией."
)


def new_referral_notify_text(new_user_label: str, rs: Dict[str, int]) -> str:
    return (
        "🎉 <b>Новый реферал!</b>\n\n"
        f"👤 Пользователь:\n{html_escape(new_user_label)}\n\n"
        "перешёл по твоей ссылке и запустил Tiksaves!\n\n"
        f"+{REF_POINTS_PER_REFERRAL} 🎟 начислено\n\n"
        "Твой результат:\n\n"
        f"👥 Рефералы: {rs['referrals_count']}\n"
        f"🎟 Баланс: {rs['ref_points']}"
    )
