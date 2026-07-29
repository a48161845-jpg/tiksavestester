"""
Хелперы отправки контента пользователю: альбомы фото, видео
(со скачиванием файлом, если прямая ссылка не сработала), музыка.
"""
import time
import random
import asyncio
import contextlib
from pathlib import Path
from typing import Callable, Optional, List

from aiogram.types import Message, InlineKeyboardMarkup, InputMediaPhoto, FSInputFile
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest

from config import (
    CAPTION_PHOTO,
    CAPTION_AUDIO,
    MEDIA_GROUP_LIMIT,
    ALBUM_PAUSE_MIN,
    ALBUM_PAUSE_MAX,
    MAX_VIDEO_BYTES,
    MAX_AUDIO_BYTES,
    DESCRIPTION_TG_LIMIT,
)
from helpers import html_escape, code, clamp_reason, exc_type_name
from storage import store
from providers import BaseProvider
from logging_channel import log_event, format_user_for_log


def chunk(items: List[str], size: int) -> List[List[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


async def send_photos(message: Message, urls: List[str], caption_html: str = CAPTION_PHOTO) -> int:
    packs = chunk(urls, MEDIA_GROUP_LIMIT)
    total = len(urls)
    sent = 0
    for pack in packs:
        media: List[InputMediaPhoto] = []
        for i, u in enumerate(pack):
            global_idx = sent + i
            if global_idx == total - 1:
                media.append(InputMediaPhoto(media=u, caption=caption_html, parse_mode="HTML"))
            else:
                media.append(InputMediaPhoto(media=u))

        try:
            await message.answer_media_group(media)
        except TelegramRetryAfter as e:
            wait = int(getattr(e, "retry_after", 2)) + 1
            await asyncio.sleep(wait)
            await message.answer_media_group(media)

        await asyncio.sleep(random.uniform(ALBUM_PAUSE_MIN, ALBUM_PAUSE_MAX))
        sent += len(pack)

    return len(urls)


async def send_video_smart(
    message: Message,
    provider: BaseProvider,
    video_url: str,
    caption: str,
    status_msg: Optional[Message] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    cancel_cb: Optional[Callable] = None,
) -> None:
    try:
        await message.answer_video(video_url, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
        return
    except TelegramBadRequest as e:
        low = str(e).lower()
        if "failed to get http url content" not in low:
            raise

        tmp = Path(f"tmp_video_{message.from_user.id}_{int(time.time())}.mp4")
        progress_msg: Optional[Message] = status_msg
        try:
            if progress_msg:
                with contextlib.suppress(Exception):
                    await progress_msg.edit_text(
                        "⏳ <b>Скачиваю… понадобится немного больше времени...</b>",
                        parse_mode="HTML",
                    )
            else:
                progress_msg = await message.answer(
                    "⏳ <b>Скачиваю… понадобится немного больше времени...</b>",
                    parse_mode="HTML",
                )

            await provider.download_to_file(
                video_url,
                tmp,
                MAX_VIDEO_BYTES,
                stage="video",
                cancel_cb=cancel_cb,
            )
            await message.answer_video(FSInputFile(tmp), caption=caption, parse_mode="HTML", reply_markup=reply_markup)
        except RuntimeError as e:
            if "file too large" in str(e).lower():
                with contextlib.suppress(Exception):
                    if progress_msg and progress_msg != status_msg:
                        await progress_msg.delete()
                raise  # перебрасываем — main_handler тихо обработает
            raise
        finally:
            with contextlib.suppress(Exception):
                tmp.unlink(missing_ok=True)


def _audio_user_id(message: Message, uid: Optional[int]) -> int:
    """ID пользователя для логов/файлов: при вызове из callback message.from_user — бот."""
    if uid is not None:
        return uid
    return message.chat.id if message.chat else 0


async def send_music_if_any(
    message: Message,
    provider: BaseProvider,
    music_url: Optional[str],
    *,
    uid: Optional[int] = None,
    label: Optional[str] = None,
    src: Optional[str] = None,
) -> None:
    if not music_url:
        return
    user_id = _audio_user_id(message, uid)
    try:
        await message.answer_audio(music_url, caption=CAPTION_AUDIO, parse_mode="HTML")
        if uid is not None:
            store.inc_audio(uid, 1)
        if label is not None:
            await log_event(
                message.bot,
                "audiodl",
                [
                    "🎵 Категория: <b>Скачивание музыки</b>",
                    f"👤 User/id: <b>{format_user_for_log(label, user_id)}</b>",
                    f"🔗 Ссылка: {code(src or '')}" if src else "🔗 Ссылка: -",
                ],
            )
        return
    except TelegramBadRequest as e:
        store.inc_error("audio", e)
        tmp = Path(f"tmp_audio_{user_id}_{int(time.time())}.mp3")
        try:
            await provider.download_to_file(music_url, tmp, MAX_AUDIO_BYTES, stage="audio")
            await message.answer_audio(FSInputFile(tmp), caption=CAPTION_AUDIO, parse_mode="HTML")
            if uid is not None:
                store.inc_audio(uid, 1)
            if label is not None:
                await log_event(
                    message.bot,
                    "audiodl",
                    [
                        "🎵 Категория: <b>Скачивание музыки</b>",
                        f"👤 User/id: <b>{format_user_for_log(label, user_id)}</b>",
                        f"🔗 Ссылка: {code(src or '')}" if src else "🔗 Ссылка: -",
                    ],
                )
        except Exception as fallback_err:
            if label is not None:
                await log_event(
                    message.bot,
                    "dlerr",
                    [
                        "❌ Категория: <b>Ошибка скачивания</b>",
                        f"👤 User/id: <b>{format_user_for_log(label, user_id)}</b>",
                        "🧩 Стадия: <b>audio</b>",
                        f"🧬 Тип: <b>{html_escape(exc_type_name(fallback_err))}</b>",
                        f"🔗 Ссылка: {code(src or '')}" if src else "🔗 Ссылка: -",
                        f"🧨 Причина: <b>{html_escape(clamp_reason(fallback_err))}</b>",
                    ],
                )
            raise
        finally:
            with contextlib.suppress(Exception):
                tmp.unlink(missing_ok=True)


async def send_description_if_any(message: Message, description: Optional[str]) -> None:
    """
    Отправляет описание (подпись/caption) TikTok-видео так же, как музыку:
    - если текст помещается в сообщение Telegram — шлём обычным сообщением,
      без изменения формата (как есть, без парсинга разметки);
    - если текст слишком большой — шлём файлом (.txt), тоже без изменений.
    """
    if not description:
        return
    text = description.strip()
    if not text:
        return

    try:
        if len(text) <= DESCRIPTION_TG_LIMIT:
            await message.answer(text)
            return

        tmp = Path(f"tmp_description_{message.chat.id}_{int(time.time())}.txt")
        try:
            tmp.write_text(text, encoding="utf-8")
            await message.answer_document(FSInputFile(tmp, filename="description.txt"))
        finally:
            with contextlib.suppress(Exception):
                tmp.unlink(missing_ok=True)
    except Exception:
        pass
