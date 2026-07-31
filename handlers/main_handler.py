"""
Основной обработчик текстовых сообщений: распознаёт TikTok-ссылку,
скачивает медиа и отправляет видео/фото-пикер/музыку. Также обрабатывает
ввод кастомной суммы донат-Stars, если пользователь её ожидает.

client и switcher приходят через aiogram workflow_data (см. dp.start_polling
в bot.py: client=primary, switcher=switcher) — aiogram сам инжектирует их
в хендлер по совпадению имени параметра.
"""
import time
import contextlib

import aiohttp
from aiogram import F
from aiogram.types import Message, LinkPreviewOptions

from globals_state import dp
from config import (
    STARS_MIN,
    STARS_MAX,
    WAITING_STARS_TTL_SEC,
    MSG_DL,
    CAPTION_VIDEO,
    PHOTO_WARNING_TEXT,
    REF_POINTS_PER_REFERRAL,
)
from helpers import (
    html_escape,
    code,
    clamp_reason,
    exc_type_name,
    extract_tiktok_url,
    normalize_tiktok_url,
)
from storage import store
from user_label import resolve_user_label
from gates import gate_message
from limiters import lim, download_sem
from logging_channel import log_event, format_user_for_log
from strikes import add_download_strike
from providers import TikWMClient, ProviderSwitcher
from send_helpers import send_video_smart
from picker_state import pending, cleanup_pending, video_extras, new_req_id, cleanup_video_extras, picker_kb
from keyboards import under_video_kb
from donate import waiting_stars_amount, send_stars_invoice
from referral import new_referral_notify_text


async def _reward_referral_if_first_download(bot, uid: int, label: str) -> None:
    """Начисляет баллы пригласившему — только один раз, при первом успешном скачивании uid."""
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


@dp.message(F.text)
async def main_handler(message: Message, client: TikWMClient, switcher: ProviderSwitcher):
    uid = message.from_user.id
    text = (message.text or "").strip()
    if not text:
        return

    label = await resolve_user_label(message.bot, uid)
    store.set_user_label(uid, label)

    if not await gate_message(message, label):
        return

    # custom stars amount
    ts_wait = waiting_stars_amount.get(uid)
    if ts_wait:
        if time.time() - ts_wait > WAITING_STARS_TTL_SEC:
            waiting_stars_amount.pop(uid, None)
        else:
            if text.isdigit():
                stars = int(text)
                if not (STARS_MIN <= stars <= STARS_MAX):
                    await message.answer(f"❌ Сумма должна быть {STARS_MIN}–{STARS_MAX} ⭐")
                    return
                waiting_stars_amount.pop(uid, None)
                await send_stars_invoice(message.bot, uid, stars)
                return

    store.register(uid)

    url = extract_tiktok_url(text)
    if url:
        url = normalize_tiktok_url(url)
    if not url and not text.startswith("/"):
        await message.answer("📎 Пришли ссылку на TikTok.")
        return

    if text.startswith("/"):
        return

    ok_dl, wait_dl = lim.dl_hit(uid)
    if not ok_dl:
        await message.answer(MSG_DL.format(n=wait_dl))
        await add_download_strike(
            message.bot,
            uid,
            label,
            "Лимит скачиваний",
            src=url or text,
        )
        return

    status = await message.answer("⏳ Скачиваю…")

    try:
        async with download_sem:
            with contextlib.suppress(Exception):
                await status.edit_text("⏳ Скачиваю…")
            media, provider = await switcher.get_media(url or text, raw_url=url)

            video, photos, music = media.video, media.photos, media.music
            description = media.description
            req_id = new_req_id()
            cleanup_video_extras()
            if music or description:
                video_extras[req_id] = {
                    "music": music,
                    "description": description,
                    "src": url or text,
                    "uid": uid,
                    "ts": time.time(),
                }

            if photos:
                await message.answer(PHOTO_WARNING_TEXT, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))

                cleanup_pending()
                pending[req_id] = {
                    "uid": uid,
                    "photos": photos,
                    "music": music,
                    "description": description,
                    "want_music": False,
                    "want_description": False,
                    "selected": set(),
                    "page": 0,
                    "ts": time.time(),
                    "src": url or text,
                }
                with contextlib.suppress(Exception):
                    await status.edit_text("🖼️ Выбери фото по номерам или выдели страницу 👇", reply_markup=picker_kb(req_id))
                return

            if not video:
                raise RuntimeError("No media links (video/photo missing)")

            # Сразу отправляем видео; кнопка «Музыка» — под видео (если есть звук)
            await send_video_smart(
                message,
                provider,
                video,
                CAPTION_VIDEO,
                status_msg=status,
                reply_markup=under_video_kb(has_music=bool(music), has_description=bool(description), req_id=req_id),
            )
            store.inc_download(uid, "video", items=1)
            await _reward_referral_if_first_download(message.bot, uid, label)
            with contextlib.suppress(Exception):
                await status.delete()
            await log_event(
                message.bot,
                "videodl",
                [
                    "🎬 Категория: <b>Скачивание видео</b>",
                    f"👤 User/id: <b>{format_user_for_log(label, uid)}</b>",
                    f"🔗 Ссылка: {code(url or text)}",
                ],
            )

    except aiohttp.ClientError as e:
        reason = clamp_reason(e)
        store.inc_error("handler", e)
        with contextlib.suppress(Exception):
            await status.edit_text("❌ Проблема с сетью/сервисом. Попробуй позже.")

        await log_event(
            message.bot,
            "dlerr",
            [
                "❌ Категория: <b>Ошибка скачивания</b>",
                f"👤 User/id: <b>{format_user_for_log(label, uid)}</b>",
                "🧩 Стадия: <b>handler</b>",
                f"🧬 Тип: <b>{html_escape(exc_type_name(e))}</b>",
                f"🔗 Ссылка: {code(text)}",
                f"🧨 Причина: <b>{html_escape(reason)}</b>",
            ],
        )

    except Exception as e:
        reason = clamp_reason(e)
        low = reason.lower()

        # Видео слишком большое — тихая ошибка, не логируем в канал
        if "file too large" in low:
            with contextlib.suppress(Exception):
                await status.edit_text("❌ Видео слишком большое для отправки через Telegram (лимит 60 МБ).")
            return

        store.inc_error("handler", e)
        msg = "❌ Не удалось скачать. Попробуй позже."
        if any(x in low for x in ["private", "приват", "недоступ", "unavailable"]):
            msg = "❌ Видео приватное или недоступно."
        elif any(x in low for x in ["deleted", "удален", "removed", "not found"]):
            msg = "❌ Видео удалено или не найдено."
        elif "url parsing" in low:
            msg = "❌ Не удалось разобрать ссылку. Проверь ссылку и попробуй ещё раз."
        with contextlib.suppress(Exception):
            await status.edit_text(msg)

        await log_event(
            message.bot,
            "dlerr",
            [
                "❌ Категория: <b>Ошибка скачивания</b>",
                f"👤 User/id: <b>{format_user_for_log(label, uid)}</b>",
                "🧩 Стадия: <b>handler</b>",
                f"🧬 Тип: <b>{html_escape(exc_type_name(e))}</b>",
                f"🔗 Ссылка: {code(text)}",
                f"🧨 Причина: <b>{html_escape(reason)}</b>",
            ],
        )
