"""
Обработчик ссылок на YouTube (обычные видео и Shorts) — качаем через yt-dlp.

Отдельно от TikTok-пайплайна (main_handler.py): у YouTube другая природа —
нет пикера фото, нет отдельного "музыка из TikTok", видео может быть куда
длиннее и тяжелее. Регистрируется РАНЬШЕ main_handler.py (см. handlers/__init__.py),
поэтому YouTube-ссылки перехватываются здесь и не долетают до TikTok-хендлера.
"""
import contextlib
import time
from pathlib import Path
from typing import Optional

from aiogram import F
from aiogram.types import Message, FSInputFile

from globals_state import dp
from config import (
    MSG_DL,
    YOUTUBE_MAX_VIDEO_BYTES,
    YOUTUBE_MAX_VIDEO_MB,
    YOUTUBE_MAX_DURATION_SEC,
    REF_POINTS_PER_REFERRAL,
)
from helpers import html_escape, code, clamp_reason, exc_type_name, is_youtube, extract_youtube_url, normalize_youtube_url
from storage import store
from user_label import resolve_user_label
from gates import gate_message
from limiters import lim, download_sem
from logging_channel import log_event, format_user_for_log
from strikes import add_download_strike
from youtube_provider import probe_youtube, download_youtube
from referral import new_referral_notify_text


async def _reward_referral_if_first_download(bot, uid: int, label: str) -> None:
    reward = store.try_reward_referral(uid, REF_POINTS_PER_REFERRAL)
    if not reward:
        return
    with contextlib.suppress(Exception):
        await bot.send_message(
            reward["referrer_id"],
            new_referral_notify_text(
                label, {"referrals_count": reward["referrals_count"], "ref_points": reward["ref_points"]}
            ),
            parse_mode="HTML",
        )


async def _log_yt_err(bot, stage: str, uid: int, label: str, url: str, e: Exception) -> None:
    with contextlib.suppress(Exception):
        store.inc_error(f"youtube_{stage}", e)
    await log_event(
        bot,
        "dlerr",
        [
            "❌ Категория: <b>Ошибка скачивания (YouTube)</b>",
            f"👤 User/id: <b>{format_user_for_log(label, uid)}</b>",
            f"🧩 Стадия: <b>{html_escape(stage)}</b>",
            f"🧬 Тип: <b>{html_escape(exc_type_name(e))}</b>",
            f"🔗 Ссылка: {code(url)}",
            f"🧨 Причина: <b>{html_escape(clamp_reason(e))}</b>",
        ],
    )


def _is_youtube_message(message: Message) -> bool:
    text = (message.text or "").strip()
    return bool(text) and not text.startswith("/") and is_youtube(text)


@dp.message(F.text, _is_youtube_message)
async def youtube_handler(message: Message):
    uid = message.from_user.id
    text = (message.text or "").strip()

    label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, label)

    if not await gate_message(message, label):
        return

    store.register(uid)

    url = normalize_youtube_url(extract_youtube_url(text) or text)

    ok_dl, wait_dl = lim.dl_hit(uid)
    if not ok_dl:
        await message.answer(MSG_DL.format(n=wait_dl))
        await add_download_strike(message.bot, uid, label, "Лимит скачиваний", src=url)
        return

    status = await message.answer("⏳ Смотрю видео на YouTube…")
    tmp_path: Optional[Path] = None

    try:
        async with download_sem:
            try:
                info = await probe_youtube(url)
            except Exception as e:
                await _log_yt_err(message.bot, "probe", uid, label, url, e)
                with contextlib.suppress(Exception):
                    await status.edit_text("❌ Не удалось получить это видео. Проверь ссылку — может, оно приватное/удалено.")
                return

            duration = int(info.get("duration") or 0)
            if duration and duration > YOUTUBE_MAX_DURATION_SEC:
                with contextlib.suppress(Exception):
                    await status.edit_text(
                        f"❌ Видео слишком длинное ({duration // 60} мин). "
                        f"Лимит: {YOUTUBE_MAX_DURATION_SEC // 60} мин."
                    )
                return

            with contextlib.suppress(Exception):
                await status.edit_text("⬇️ Скачиваю видео…")

            out_dir = Path(".")
            try:
                tmp_path, dl_info = await download_youtube(url, out_dir)
            except Exception as e:
                await _log_yt_err(message.bot, "download", uid, label, url, e)
                with contextlib.suppress(Exception):
                    await status.edit_text("❌ Не получилось скачать это видео. Попробуй другую ссылку.")
                return

            size = tmp_path.stat().st_size if tmp_path.exists() else 0
            if size <= 0:
                with contextlib.suppress(Exception):
                    await status.edit_text("❌ Скачанный файл пустой. Попробуй ещё раз.")
                return
            if size > YOUTUBE_MAX_VIDEO_BYTES:
                with contextlib.suppress(Exception):
                    await status.edit_text(f"❌ Файл больше лимита ({YOUTUBE_MAX_VIDEO_MB} МБ). Это видео слишком тяжёлое.")
                return

            title = str(dl_info.get("title") or info.get("title") or "YouTube")
            caption = f"🎬 <b>{html_escape(title[:900])}</b>\n\n📥 Скачано в боте @tiksavesbot"

            with contextlib.suppress(Exception):
                await status.edit_text("📤 Отправляю…")

            try:
                await message.answer_video(FSInputFile(tmp_path), caption=caption, parse_mode="HTML")
            except Exception as e:
                await _log_yt_err(message.bot, "send", uid, label, url, e)
                with contextlib.suppress(Exception):
                    await status.edit_text(
                        "❌ Telegram отклонил файл — скорее всего, он слишком большой "
                        "для отправки ботом (обычный лимит Telegram — 50 МБ на файл)."
                    )
                return

            store.inc_download(uid, "video", items=1)
            await _reward_referral_if_first_download(message.bot, uid, label)

            with contextlib.suppress(Exception):
                await status.delete()

    finally:
        if tmp_path:
            with contextlib.suppress(Exception):
                tmp_path.unlink(missing_ok=True)
