"""
Inline-режим: @tiksavesbot <ссылка> прямо в любом чате.

Важно про архитектуру: изначально это было сделано через плейсхолдер +
chosen_inline_result (Telegram даёт inline_message_id, которым можно
отредактировать уже отправленное сообщение). НО chosen_inline_result
на практике ненадёжен: Telegram присылает эти апдейты только если явно
включить "Inline Feedback" через @BotFather (/setinlinefeedback), и даже
тогда — не гарантированно на каждый выбор, а с какой-то вероятностью.
Из-за этого плейсхолдер "⏳ Скачиваю..." мог просто никогда не замениться
на видео. Поэтому теперь всё качается СРАЗУ, синхронно, внутри самого
inline_query, с жёстким таймаутом — если не успели, аккуратно сообщаем
об этом вместо того, чтобы врать зависшим "загружаю".

Оптимизация: если ссылка уже когда-то скачивалась через инлайн — результат
подставляется МГНОВЕННО через кэш file_id (Telegram file_id у бота не
протухают), без повторного скачивания вообще — именно это и делает инлайн
режим практичным при не-мгновенном скачивании новых ссылок.

⚠️ Чтобы инлайн-режим вообще заработал, нужно один раз включить его для бота
через @BotFather → /setinline (это ручная настройка бота, не делается кодом).
"""
import asyncio
import contextlib
import hashlib
import uuid
from pathlib import Path
from typing import Optional

from aiogram.types import (
    InlineQuery,
    InlineQueryResultCachedVideo,
    InlineQueryResultArticle,
    InputTextMessageContent,
    FSInputFile,
)

from globals_state import dp
import globals_state
from config import LOG_CHANNEL_ID, MAX_VIDEO_BYTES, CAPTION_VIDEO, YOUTUBE_MAX_DURATION_SEC
from helpers import (
    is_tiktok, extract_tiktok_url, normalize_tiktok_url,
    is_youtube, extract_youtube_url, normalize_youtube_url,
    is_instagram, is_vk, is_pinterest, extract_other_source_url,
)
from storage import store
from user_label import resolve_user_label
from limiters import lim
from youtube_provider import probe_media, download_media
from referral import after_download_hooks

# Сколько максимум ждём скачивание НОВОЙ (некэшированной) ссылки внутри
# самого inline-запроса, пока Telegram-клиент ждёт ответа.
INLINE_TIMEOUT_SEC = 8


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


async def _download_and_cache(platform: str, url: str, uid: int) -> str:
    """Качает видео, кладёт в служебный канал (чтобы получить file_id) и кэширует. Возвращает file_id."""
    tmp_path: Optional[Path] = None
    try:
        tmp_path = await _resolve_and_download(platform, url, Path("."))

        size = tmp_path.stat().st_size if tmp_path.exists() else 0
        if size <= 0:
            raise RuntimeError("Скачанный файл пустой")
        if size > MAX_VIDEO_BYTES:
            raise RuntimeError("Файл больше лимита")

        bot = globals_state.g_provider.bot if globals_state.g_provider else None
        if not bot:
            raise RuntimeError("bot недоступен")

        # Кладём в служебный канал, чтобы получить постоянный file_id — его
        # же используем и для ответа сейчас, и для кэша на будущее (повторные
        # запросы этой ссылки больше не будут качать заново).
        cache_msg = await bot.send_video(LOG_CHANNEL_ID, FSInputFile(tmp_path))
        file_id = cache_msg.video.file_id
        store.set_inline_cache(_cache_key(url), file_id, kind="video")

        label = await resolve_user_label(bot, uid)
        store.set_user_label(uid, label)
        store.inc_download(uid, "video", items=1, source=platform)
        with contextlib.suppress(Exception):
            await after_download_hooks(bot, uid, label)

        return file_id
    finally:
        if tmp_path:
            with contextlib.suppress(Exception):
                tmp_path.unlink(missing_ok=True)


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

    ok_dl, _wait = lim.dl_hit(uid)
    if not ok_dl:
        limited = InlineQueryResultArticle(
            id="ratelimited",
            title="⏳ Слишком много запросов подряд",
            description="Попробуй через минуту",
            input_message_content=InputTextMessageContent(message_text="⏳ Слишком много запросов подряд — попробуй через минуту."),
        )
        await inline_query.answer([limited], cache_time=1, is_personal=True)
        return

    # Новая ссылка — качаем сразу, но не дольше INLINE_TIMEOUT_SEC, чтобы
    # уложиться в то время, что Telegram-клиент готов ждать ответ.
    try:
        file_id = await asyncio.wait_for(_download_and_cache(platform, url, uid), timeout=INLINE_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        slow = InlineQueryResultArticle(
            id="slow",
            title="⏳ Видео качается дольше обычного",
            description="Открой бота напрямую и пришли эту ссылку в чат",
            input_message_content=InputTextMessageContent(
                message_text="⏳ Это видео качается дольше, чем позволяет инлайн-режим. "
                "Пришли ссылку боту напрямую в чат — там лимита по времени нет."
            ),
        )
        await inline_query.answer([slow], cache_time=1, is_personal=True)
        return
    except Exception:
        failed = InlineQueryResultArticle(
            id="failed",
            title="❌ Не получилось скачать это видео",
            description="Попробуй прислать ссылку боту напрямую в чат",
            input_message_content=InputTextMessageContent(
                message_text="❌ Не получилось скачать это видео. Попробуй прислать ссылку боту напрямую в чат."
            ),
        )
        await inline_query.answer([failed], cache_time=1, is_personal=True)
        return

    result = InlineQueryResultCachedVideo(
        id=f"n:{key}",
        video_file_id=file_id,
        title="✅ Готово — отправить видео",
        caption=CAPTION_VIDEO,
        parse_mode="HTML",
    )
    await inline_query.answer([result], cache_time=300, is_personal=False)
