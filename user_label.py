"""
Резолвер отображаемого имени пользователя (username / имя+фамилия / id)
для логов и админ-команд.

При каждом вызове обновляет данные пользователя в хранилище —
так ники/имена всегда актуальны даже если пользователь их менял.
"""
from typing import Optional

from aiogram import Bot


async def resolve_user_label(bot: Bot, uid: int) -> str:
    """
    Получает актуальные данные пользователя через Telegram API
    и сразу сохраняет их в store, чтобы данные были свежими.
    Возвращает строку-метку вида '@username (uid)' или 'Имя Фамилия (uid)'.
    """
    try:
        chat = await bot.get_chat(uid)
        username = getattr(chat, "username", None)
        first = getattr(chat, "first_name", None) or ""
        last = getattr(chat, "last_name", None) or ""

        # Обновляем хранилище свежими данными
        try:
            from storage import store
            info = {"username": username, "first_name": first, "last_name": last}
            store.data.setdefault("users_info", {})[str(uid)] = info
            store._mark_dirty()
        except Exception:
            pass

        if username:
            return f"@{username} ({uid})"
        name = " ".join([x for x in [first, last] if x]).strip()
        if name:
            return f"{name} ({uid})"
    except Exception:
        # Если API недоступен — пробуем вернуть кешированные данные из store
        try:
            from storage import store
            info = store.data.get("users_info", {}).get(str(uid))
            if info:
                username = info.get("username")
                first = info.get("first_name", "")
                last = info.get("last_name", "")
                if username:
                    return f"@{username} ({uid})"
                name = " ".join([x for x in [first, last] if x]).strip()
                if name:
                    return f"{name} ({uid})"
        except Exception:
            pass
    return f"{uid}"


async def resolve_uid_from_arg(bot: Bot, raw: str) -> Optional[int]:
    """
    Резолвит uid из аргумента админ-команды: числовой ID, @username или
    просто username без @. Сначала пробует Telegram API (get_chat), если
    не вышло — ищет по сохранённым меткам пользователей в store.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.lstrip("-").isdigit():
        return int(raw)

    from storage import store

    username = raw[1:] if raw.startswith("@") else raw
    try:
        chat = await bot.get_chat(username)
        return int(chat.id)
    except Exception:
        for uid_str, label in (store.data.get("users_map", {}) or {}).items():
            if f"@{username}".lower() in str(label).lower():
                return int(uid_str)
        return None
