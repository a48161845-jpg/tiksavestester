"""
Состояние "ожидающих выбора" сущностей между шагами диалога:
- pending: фото-пикер (выбор конкретных фото из слайдшоу);
- pending_video: задел на выбор перед скачиванием видео (см. video choice callbacks);
- last_audio_url / last_video_src: последние известные ссылки на музыку/видео
  пользователя — нужны для кнопки "Музыка" под уже отправленным видео.
"""
import time
from typing import Dict, Any, List

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import PENDING_TTL_SEC, PAGE_SIZE

pending: Dict[int, Dict[str, Any]] = {}
last_audio_url: Dict[int, str] = {}
last_video_src: Dict[int, str] = {}
last_description: Dict[int, str] = {}
pending_video: Dict[int, Dict[str, Any]] = {}


def cleanup_pending() -> None:
    now = time.time()
    dead = [uid for uid, st in pending.items() if now - float(st["ts"]) > PENDING_TTL_SEC]
    for uid in dead:
        pending.pop(uid, None)


def cleanup_pending_video() -> None:
    now = time.time()
    dead = [uid for uid, st in pending_video.items() if now - float(st["ts"]) > PENDING_TTL_SEC]
    for uid in dead:
        pending_video.pop(uid, None)


def picker_kb(uid: int) -> InlineKeyboardMarkup:
    st = pending[uid]
    photos: List[str] = st["photos"]
    selected: set[int] = st["selected"]
    page: int = st["page"]

    total = len(photos)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    st["page"] = page

    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)

    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []

    for idx in range(start, end):
        num = idx + 1
        txt = f"{'✅ ' if idx in selected else ''}{num}"
        row.append(InlineKeyboardButton(text=txt, callback_data=f"pk:t:{idx}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton(text="✅ Выбрать страницу", callback_data="pk:selpage")])
    rows.append([InlineKeyboardButton(text="🔽 Скачать всё", callback_data="pk:sendall")])
    row2: List[InlineKeyboardButton] = [InlineKeyboardButton(text="🎵 Скачать музыку", callback_data="pk:music")]
    if st.get("description"):
        row2.append(InlineKeyboardButton(text="📝 Описание", callback_data="pk:desc"))
    rows.append(row2)
    rows.append([InlineKeyboardButton(text="🧹 Очистить", callback_data="pk:clr")])

    if pages > 1:
        rows.append(
            [
                InlineKeyboardButton(text="⬅️", callback_data="pk:pg:-1"),
                InlineKeyboardButton(text=f"{page+1}/{pages}", callback_data="pk:n"),
                InlineKeyboardButton(text="➡️", callback_data="pk:pg:+1"),
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(text=f"➡️ Продолжить ({len(selected)})", callback_data="pk:go"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
