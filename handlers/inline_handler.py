"""
Inline-режим: @tiksavesbot <ссылка> прямо в любом чате.

Оптимизация (то, ради чего вообще есть смысл делать инлайн для видео):
- Если ссылка уже когда-то скачивалась через инлайн — результат подставляется
  МГНОВЕННО через кэш file_id (Telegram file_id у бота не протухают),
  без повторного скачивания вообще.
- Для НОВОЙ ссылки инлайн-запрос не может ждать полное скачивание (Telegram
  ждёт ответ на inline_query доли секунды) — поэтому сразу отдаём лёгкий
  плейсхолдер ("⏳ Скачиваю..."), а реальное скачивание и подмена на видео
  происходят в chosen_inline_result: Telegram даёт inline_message_id, который
  позволяет отредактировать уже отправленное сообщение даже в чужом чате.

⚠️ Чтобы инлайн-режим вообще заработал, нужно один раз включить его для бота
через @BotFather → /setinline (это ручная настройка бота, не делается кодом).
"""
import contextlib
import hashlib
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from aiogram import F, Bot
from aiogram.types import (
    InlineQuery,
    ChosenInlineResult,
    InlineQueryResultCachedVideo,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InputMediaVideo,
    FSInputFile,
)

from globals_state import dp
import globals_state
from config import LOG_CHANNEL_ID, MAX_VIDEO_BYTES, MAX_VIDEO_MB, CAPTION_VIDEO, YOUTUBE_MAX_DURATION_SEC
from helpers import (
    is_tiktok, extract_tiktok_url, normalize_tiktok_url,
    is_youtube, extract_youtube_url, normalize_youtube_url,
    is_instagram, is_vk, is_pinterest, extract_other_source_url,
    exc_type_name, clamp_reason,
)
from storage import store
from user_label import resolve_user_label
from limiters import lim
from logging_channel import log_event, format_user_for_log
from youtube_provider import probe_media, download_media
from referral import after_download_hooks

# result_id -> {"url","platform","uid","ts"} — живёт недолго, между показом
# инлайн-результата и моментом, когда его реально выбрали (chosen_inline_result).
_pending_inline: Dict[str, Dict[str, Any]] = {}
_PENDING_TTL_SEC = 600


def _cleanup_pending() -> None:
    now = time.time()
    dead = [k for k, v in _pending_inline.items() if now - v.get("ts", 0) > _PENDING_TTL_SEC]
    for k in dead:
        _pending_inline.pop(k, None)


def _cache_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:24]


def _detect(text: str):
    """Возвращает (platform, normalized_url) или (None, None)."""
    if is_tiktok(text):
        u = extract_tiktok_url(text)
        if u:
            return "tiktok", normalize_tiktok_url(u)
    if is_youtube(text):
        u = extract_youtube_url(text)
        if u:
            return "youtube", normalize_youtube_url(u)
    if is_instagram(text) or is_vk(text) or is_pinterest(text):
        u = extract_other_source_url(text)
        if u:
            platform = "instagram" if is_instagram(u) else ("vk" if is_vk(u) else "pinterest")
            return platform, u
    return None, None


@dp.inline_query()
async def inline_handler(inline_query: InlineQuery):
    uid = inline_query.from_user.id
    query = (inline_query.query or "").strip()

    ban = store.get_ban(uid)
    if ban:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return

    if not query:
        hint = InlineQueryResultArticle(
            id="hint",
            title="Вставь ссылку на TikTok / YouTube / Instagram / VK / Pinterest",
            description="Начни печатать — бот покажет результат прямо тут",
            input_message_content=InputTextMessageContent(
                message_text="📎 Пришли мне ссылку на TikTok, YouTube, Instagram, VK или Pinterest — скачаю видео."
            ),
        )
        await inline_query.answer([hint], cache_time=1, is_personal=True)
        return

    platform, url = _detect(query)
    if not url:
        not_found = InlineQueryResultArticle(
            id="notfound",
            title="Ссылка не распознана",
            description="Поддерживаются TikTok, YouTube, Instagram, VK, Pinterest",
            input_message_content=InputTextMessageContent(message_text="❌ Не нашёл поддерживаемую ссылку в запросе."),
        )
        await inline_query.answer([not_found], cache_time=1, is_personal=True)
        return

    key = _cache_key(url)
    cached = store.get_inline_cache(key)
    if cached and cached.get("file_id"):
        result = InlineQueryResultCachedVideo(
            id=f"c:{key}",
            video_file_id=cached["file_id"],
            title="✅ Готово — отправить видео",
            caption=CAPTION_VIDEO,
            parse_mode="HTML",
        )
        await inline_query.answer([result], cache_time=300, is_personal=False)
        return

    _cleanup_pending()
    result_id = uuid.uuid4().hex[:20]
    _pending_inline[result_id] = {"url": url, "platform": platform, "uid": uid, "ts": time.time()}

    placeholder = InlineQueryResultArticle(
        id=result_id,
        title="⏳ Скачиваю видео…",
        description="Нажми — видео придёт сюда через несколько секунд",
        input_message_content=InputTextMessageContent(message_text="⏳ Скачиваю видео, подожди немного…"),
    )
    await inline_query.answer([placeholder], cache_time=1, is_personal=True)


async def _resolve_and_download(platform: str, url: str, out_dir: Path) -> Path:
    """Возвращает путь к скачанному видео. Бросает исключение при неудаче."""
    if platform == "tiktok":
        switcher = globals_state.g_switcher
        if not switcher:
            raise RuntimeError("provider switcher не инициализирован")
        media, provider = await switcher.get_media(url, raw_url=url)
        if not media.video:
            raise RuntimeError("Это фото-слайдшоу без видео — инлайн поддерживает только видео")
        tmp_path = out_dir / f"inline_{uuid.uuid4().hex[:10]}.mp4"
        await provider.download_to_file(media.video, tmp_path, MAX_VIDEO_BYTES, stage="inline_video")
        return tmp_path

    info = await probe_media(url)
    duration = int(info.get("duration") or 0)
    if duration and duration > YOUTUBE_MAX_DURATION_SEC:
        raise RuntimeError(f"Видео длиннее {YOUTUBE_MAX_DURATION_SEC // 60} мин — не поддерживается в инлайне")
    path, _dl_info = await download_media(url, out_dir)
    return path


@dp.chosen_inline_result()
async def chosen_inline_result_handler(chosen: ChosenInlineResult, bot: Bot):
    result_id = chosen.result_id
    info = _pending_inline.pop(result_id, None)
    if not info or not chosen.inline_message_id:
        return  # плейсхолдер устарел/бот перезапускался — молча ничего не делаем

    uid = int(info["uid"])
    platform = info["platform"]
    url = info["url"]
    label = await resolve_user_label(bot, uid)
    store.set_user_label(uid, label)

    ok_dl, _wait = lim.dl_hit(uid)
    if not ok_dl:
        with contextlib.suppress(Exception):
            await bot.edit_message_text(
                inline_message_id=chosen.inline_message_id,
                text="⏳ Слишком много запросов подряд — попробуй через минуту.",
            )
        return

    tmp_path: Optional[Path] = None
    try:
        tmp_path = await _resolve_and_download(platform, url, Path("."))

        size = tmp_path.stat().st_size if tmp_path.exists() else 0
        if size <= 0:
            raise RuntimeError("Скачанный файл пустой")
        if size > MAX_VIDEO_BYTES:
            raise RuntimeError(f"Файл больше лимита ({MAX_VIDEO_MB} МБ)")

        # Отправляем в служебный канал, чтобы получить постоянный file_id —
        # его же используем для мгновенной подмены плейсхолдера и для кэша
        # (повторные запросы этой ссылки больше не будут качать заново).
        cache_msg = await bot.send_video(LOG_CHANNEL_ID, FSInputFile(tmp_path))
        file_id = cache_msg.video.file_id

        await bot.edit_message_media(
            inline_message_id=chosen.inline_message_id,
            media=InputMediaVideo(media=file_id, caption=CAPTION_VIDEO, parse_mode="HTML"),
        )

        store.set_inline_cache(_cache_key(url), file_id, kind="video")
        store.inc_download(uid, "video", items=1, source=platform)
        await after_download_hooks(bot, uid, label)

    except Exception as e:
        with contextlib.suppress(Exception):
            await bot.edit_message_text(
                inline_message_id=chosen.inline_message_id,
                text="❌ Не получилось скачать это видео. Попробуй ещё раз или пришли ссылку боту напрямую.",
            )
        with contextlib.suppress(Exception):
            store.inc_error(f"inline_{platform}", e)
        with contextlib.suppress(Exception):
            await log_event(
                bot,
                "dlerr",
                [
                    "❌ Категория: <b>Ошибка скачивания (inline)</b>",
                    f"👤 User/id: <b>{format_user_for_log(label, uid)}</b>",
                    f"🧩 Платформа: <b>{platform}</b>",
                    f"🧬 Тип: <b>{exc_type_name(e)}</b>",
                    f"🧨 Причина: <b>{clamp_reason(e)}</b>",
                ],
            )
    finally:
        if tmp_path:
            with contextlib.suppress(Exception):
                tmp_path.unlink(missing_ok=True)
