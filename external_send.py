"""
Общая логика отправки видео из "внешних" источников — YouTube, Instagram,
VK, Pinterest. Все они идут через yt-dlp (youtube_provider.py) и после
скачивания ведут себя так же, как TikTok: видео + кнопки "🎵 Музыка" /
"📝 Описание" (по требованию, через video_extras/req_id), плюс
Донат/Поделиться из той же клавиатуры under_video_kb.
"""
import time
from pathlib import Path
from typing import Any, Dict, Optional

from aiogram.types import Message, FSInputFile

from helpers import html_escape, normalize_description
from storage import store
from picker_state import video_extras, new_req_id, cleanup_video_extras
from keyboards import under_video_kb
from referral import reward_referral_if_first_download


def best_audio_url(info: Dict[str, Any]) -> Optional[str]:
    """
    Достаёт прямую ссылку на лучший чисто-аудио поток из info-dict yt-dlp —
    чтобы кнопка "🎵 Музыка" могла скачать и прислать звук отдельно, как и
    у TikTok (там это оригинальный "sound", здесь — звуковая дорожка самого
    видео). Ссылки от yt-dlp часто временные/подписанные — используем сразу
    после скачивания, пока они ещё точно живы.
    """
    req = info.get("requested_formats")
    if req:
        for f in req:
            if f.get("vcodec") in (None, "none") and f.get("url"):
                return f["url"]

    formats = info.get("formats") or []
    audio_only = [
        f for f in formats
        if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none") and f.get("url")
    ]
    if not audio_only:
        return None
    audio_only.sort(key=lambda f: (f.get("abr") or 0, f.get("filesize") or f.get("filesize_approx") or 0), reverse=True)
    return audio_only[0]["url"]


async def send_external_video(
    message: Message,
    uid: int,
    label: str,
    tmp_path: Path,
    info: Dict[str, Any],
    dl_info: Dict[str, Any],
    emoji: str,
) -> None:
    """Отправляет скачанное видео с кнопками Музыка/Описание/Донат и учитывает статистику/рефералку."""
    title = str(dl_info.get("title") or info.get("title") or "").strip()
    description = normalize_description(dl_info.get("description") or info.get("description"))
    music_url = best_audio_url(dl_info) or best_audio_url(info)
    src = dl_info.get("webpage_url") or info.get("webpage_url") or ""

    req_id = new_req_id()
    cleanup_video_extras()
    if music_url or description:
        video_extras[req_id] = {
            "music": music_url,
            "description": description,
            "src": src,
            "uid": uid,
            "ts": time.time(),
        }

    if title:
        caption = f"{emoji} <b>{html_escape(title[:900])}</b>\n\n📥 Скачано в @tiksavesbot"
    else:
        caption = f"{emoji} <b>Готово!</b>\n\n📥 Скачано в @tiksavesbot"

    kb = under_video_kb(has_music=bool(music_url), has_description=bool(description), req_id=req_id)

    try:
        await message.answer_video(FSInputFile(tmp_path), caption=caption, parse_mode="HTML", reply_markup=kb)
    except Exception:
        # Иногда попадается кодек/контейнер, который Telegram не принимает
        # как "видео" — на такой случай шлём документом, чтобы человек
        # всё равно получил файл (кнопки при этом сохраняются).
        await message.answer_document(FSInputFile(tmp_path), caption=caption, parse_mode="HTML", reply_markup=kb)

    store.inc_download(uid, "video", items=1)
    await reward_referral_if_first_download(message.bot, uid, label)
