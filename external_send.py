"""
Общая логика отправки видео из "внешних" источников — YouTube, Instagram,
VK, Pinterest. Все они идут через yt-dlp (youtube_provider.py) и после
скачивания ведут себя так же, как TikTok: видео с нейтральной подписью
(без имени/названия из исходного поста) + кнопки "🎵 Музыка" / "📝 Описание"
(по требованию, через video_extras/req_id), плюс Донат/Поделиться из той же
клавиатуры under_video_kb.
"""
import time
from pathlib import Path
from typing import Any, Dict

from aiogram.types import Message, FSInputFile

from config import CAPTION_VIDEO
from helpers import normalize_description
from storage import store
from picker_state import video_extras, new_req_id, cleanup_video_extras
from keyboards import under_video_kb
from youtube_provider import has_audio_track
from referral import after_download_hooks


async def send_external_video(
    message: Message,
    uid: int,
    label: str,
    tmp_path: Path,
    info: Dict[str, Any],
    dl_info: Dict[str, Any],
    emoji: str,
    source: str = "youtube",
) -> None:
    """Отправляет скачанное видео с кнопками Музыка/Описание/Донат и учитывает статистику/рефералку."""
    description = normalize_description(dl_info.get("description") or info.get("description"))
    has_music = has_audio_track(dl_info) or has_audio_track(info)
    src = dl_info.get("webpage_url") or info.get("webpage_url") or ""

    req_id = new_req_id()
    cleanup_video_extras()
    if has_music or description:
        video_extras[req_id] = {
            # Для внешних источников звук не берём прямой CDN-ссылкой (у многих
            # площадок, особенно VK, она требует спец-заголовков, без которых
            # скачивание падает или приходит битым) — вместо этого при нажатии
            # кнопки качаем звук заново через yt-dlp (см. music_mode).
            "music": src if has_music else None,
            "music_mode": "ytdlp_external",
            "description": description,
            "src": src,
            "uid": uid,
            "ts": time.time(),
        }

    # Без имени/названия исходного поста — единый вид подписи, как у TikTok.
    caption = CAPTION_VIDEO
    kb = under_video_kb(has_music=has_music, has_description=bool(description), req_id=req_id)

    try:
        await message.answer_video(FSInputFile(tmp_path), caption=caption, parse_mode="HTML", reply_markup=kb)
    except Exception:
        # Иногда попадается кодек/контейнер, который Telegram не принимает
        # как "видео" — на такой случай шлём документом, чтобы человек
        # всё равно получил файл (кнопки при этом сохраняются).
        await message.answer_document(FSInputFile(tmp_path), caption=caption, parse_mode="HTML", reply_markup=kb)

    store.inc_download(uid, "video", items=1, source=source)
    await after_download_hooks(message.bot, uid, label)
