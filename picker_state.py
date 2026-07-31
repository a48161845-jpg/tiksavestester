"""
Состояние "ожидающих выбора" сущностей между шагами диалога:
- pending: фото-пикер (выбор фото из слайдшоу, галочки музыки/описания).
  Ключ — req_id (не uid!): у одного пользователя может быть открыто сразу
  несколько сообщений-пикеров (если он прислал несколько ссылок подряд, а
  бот теперь обрабатывает несколько скачиваний параллельно) — раньше, когда
  состояние хранилось по uid, второй пикер перезаписывал данные первого,
  и кнопки под старым сообщением начинали путать/сбрасывать чужой выбор
  ("жмёшь на одно — показывает одно, потом другое пропадает"). Теперь у
  каждого сообщения-пикера свой req_id, зашитый прямо в его кнопки.
- pending_video: задел на выбор перед скачиванием видео (video_choice, legacy);
- video_extras: музыка/описание/источник для кнопок под конкретным видео —
  тоже по req_id (та же гонка была исправлена раньше).
"""
import time
import uuid
from typing import Dict, Any, List, Optional

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import PENDING_TTL_SEC, PAGE_SIZE

pending: Dict[str, Dict[str, Any]] = {}
pending_video: Dict[int, Dict[str, Any]] = {}

video_extras: Dict[str, Dict[str, Any]] = {}
VIDEO_EXTRAS_TTL_SEC = 1800  # 30 минут на то, чтобы нажать "Музыка"/"Описание" под видео


def new_req_id() -> str:
    return uuid.uuid4().hex[:12]


def cleanup_video_extras() -> None:
    now = time.time()
    dead = [k for k, v in video_extras.items() if now - float(v.get("ts", 0)) > VIDEO_EXTRAS_TTL_SEC]
    for k in dead:
        video_extras.pop(k, None)


def cleanup_pending() -> None:
    now = time.time()
    dead = [rid for rid, st in pending.items() if now - float(st["ts"]) > PENDING_TTL_SEC]
    for rid in dead:
        pending.pop(rid, None)


def cleanup_pending_video() -> None:
    now = time.time()
    dead = [uid for uid, st in pending_video.items() if now - float(st["ts"]) > PENDING_TTL_SEC]
    for uid in dead:
        pending_video.pop(uid, None)


def picker_kb(req_id: str) -> InlineKeyboardMarkup:
    st = pending[req_id]
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
        row.append(InlineKeyboardButton(text=txt, callback_data=f"pk:t:{req_id}:{idx}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton(text="✅ Выбрать страницу", callback_data=f"pk:selpage:{req_id}")])
    rows.append([InlineKeyboardButton(text="🔽 Скачать всё", callback_data=f"pk:sendall:{req_id}")])

    # Музыка/описание — тоже галочки (переключатели), а не мгновенная отправка:
    # выбираешь, что нужно, и жмёшь "Продолжить"/"Скачать всё" — всё уходит вместе.
    row2: List[InlineKeyboardButton] = []
    if st.get("music"):
        checked = "✅ " if st.get("want_music") else ""
        row2.append(InlineKeyboardButton(text=f"{checked}🎵 Музыка", callback_data=f"pk:togmusic:{req_id}"))
    if st.get("description"):
        checked = "✅ " if st.get("want_description") else ""
        row2.append(InlineKeyboardButton(text=f"{checked}📝 Описание", callback_data=f"pk:togdesc:{req_id}"))
    if row2:
        rows.append(row2)

    rows.append([InlineKeyboardButton(text="🧹 Очистить", callback_data=f"pk:clr:{req_id}")])

    if pages > 1:
        rows.append(
            [
                InlineKeyboardButton(text="⬅️", callback_data=f"pk:pg:{req_id}:-1"),
                InlineKeyboardButton(text=f"{page+1}/{pages}", callback_data=f"pk:n:{req_id}"),
                InlineKeyboardButton(text="➡️", callback_data=f"pk:pg:{req_id}:+1"),
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(text=f"➡️ Продолжить ({len(selected)})", callback_data=f"pk:go:{req_id}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
