"""
Callback-обработчик фото-пикера: пагинация, выбор отдельных фото,
музыка/описание (галочки-переключатели), скачивание выбранного/всего.

callback_data начинается на "pk:" и всегда содержит req_id — уникальный
идентификатор конкретного сообщения-пикера (НЕ uid!). Это важно: если
пользователь пришлёт несколько ссылок на слайдшоу подряд, у него может быть
открыто сразу несколько пикеров одновременно — раньше состояние хранилось
по uid, и второй пикер перезаписывал данные первого, отчего кнопки под
старым сообщением путали/сбрасывали чужой выбор.
"""
import contextlib
import time
from pathlib import Path

from aiogram import F
from aiogram.types import CallbackQuery, FSInputFile

from globals_state import dp
import globals_state
from config import PAGE_SIZE, MSG_DL, MSG_PHOTO, CAPTION_PHOTO, CAPTION_VIDEO
from helpers import code, exc_type_name, clamp_reason, html_escape
from storage import store
from user_label import resolve_user_label
from limiters import lim
from logging_channel import log_event, format_user_for_log
from strikes import add_download_strike
from send_helpers import send_photos, send_music_if_any, send_description_if_any
from referral import after_download_hooks
from picker_state import pending, cleanup_pending, picker_kb
from keyboards import post_download_kb
from photo_video import build_photo_slideshow_video, SlideshowBuildError


async def _maybe_send_as_video(call: CallbackQuery, st: dict, photo_urls: list, uid: int, label: str) -> None:
    """Если включена галочка "Собрать видео" — качает фото+музыку и склеивает в MP4 через ffmpeg."""
    if not st.get("want_as_video") or not photo_urls:
        return
    session = getattr(globals_state.g_provider, "session", None)
    if not session:
        return

    name_prefix = f"slideshow_{uid}_{int(time.time())}"
    out_path: Path = None
    try:
        out_path = await build_photo_slideshow_video(
            session, photo_urls, st.get("music"), Path("."), name_prefix
        )
        await call.message.answer_video(FSInputFile(out_path), caption=CAPTION_VIDEO, parse_mode="HTML")
        store.inc_download(uid, "video", items=1, source="tiktok")
    except SlideshowBuildError as e:
        with contextlib.suppress(Exception):
            await call.message.answer("❌ Не получилось собрать видео из фото. Попробуй ещё раз позже.")
        with contextlib.suppress(Exception):
            await log_event(
                call.bot,
                "dlerr",
                [
                    "❌ Категория: <b>Ошибка сборки фото-видео</b>",
                    f"👤 User/id: <b>{format_user_for_log(label, uid)}</b>",
                    f"🧬 Тип: <b>{html_escape(exc_type_name(e))}</b>",
                    f"🧨 Причина: <b>{html_escape(clamp_reason(e))}</b>",
                ],
            )
    finally:
        if out_path:
            with contextlib.suppress(Exception):
                out_path.unlink(missing_ok=True)


@dp.callback_query(F.data.startswith("pk:"))
async def picker_cb(call: CallbackQuery):
    uid = call.from_user.id
    label = await resolve_user_label(call.bot, uid)
    store.set_user_label(uid, label)

    ban = store.get_ban(uid)
    if ban:
        await call.answer("Вы в бане.", show_alert=True)
        return

    cleanup_pending()

    parts = (call.data or "").split(":")
    act = parts[1] if len(parts) > 1 else ""
    req_id = parts[2] if len(parts) > 2 else ""

    st = pending.get(req_id)
    if not st:
        await call.answer("⏱️ Выбор устарел. Скинь ссылку ещё раз.", show_alert=True)
        with contextlib.suppress(Exception):
            if call.message:
                await call.message.delete()
        return

    if st.get("uid") != uid:
        await call.answer("Это не твой выбор фото.", show_alert=True)
        return

    if not call.message:
        await call.answer()
        return

    async def gate_download() -> bool:
        ok, wait = lim.dl_hit(uid)
        if ok:
            return True
        await call.message.answer(MSG_DL.format(n=wait), parse_mode="HTML")
        await add_download_strike(
            call.bot,
            uid,
            label,
            "Лимит скачиваний",
            src=st.get("src"),
        )
        return False

    async def gate_photo_volume(photos_cnt: int, src: str) -> bool:
        ok, wait = lim.photo_hit(uid, photos_cnt)
        if ok:
            return True

        await call.message.answer(MSG_PHOTO.format(n=wait), parse_mode="HTML")
        await add_download_strike(
            call.bot,
            uid,
            label,
            "Лимит фото",
            src=src,
        )
        return False

    if act == "n":
        await call.answer()
        return

    if act == "cn":
        pending.pop(req_id, None)
        await call.answer("Ок.")
        with contextlib.suppress(Exception):
            await call.message.delete()
        return

    if act == "pg":
        step = parts[3] if len(parts) > 3 else "+1"
        total = len(st["photos"])
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        st["page"] = (st["page"] + (-1 if step == "-1" else 1)) % pages
        await call.answer()
        await call.message.edit_reply_markup(reply_markup=picker_kb(req_id))
        return

    if act == "t":
        idx = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else -1
        if 0 <= idx < len(st["photos"]):
            sel: set[int] = st["selected"]
            if idx in sel:
                sel.remove(idx)
            else:
                sel.add(idx)
        await call.answer()
        await call.message.edit_reply_markup(reply_markup=picker_kb(req_id))
        return

    if act == "clr":
        st["selected"].clear()
        st["want_music"] = False
        st["want_description"] = False
        st["want_as_video"] = False
        await call.answer("🧹 Очищено")
        await call.message.edit_reply_markup(reply_markup=picker_kb(req_id))
        return

    if act == "selpage":
        page = st["page"]
        total = len(st["photos"])
        start = page * PAGE_SIZE
        end = min(start + PAGE_SIZE, total)
        sel: set[int] = st["selected"]
        for i in range(start, end):
            sel.add(i)
        await call.answer("✅ Страница выбрана!")
        await call.message.edit_reply_markup(reply_markup=picker_kb(req_id))
        return

    if act == "togmusic":
        if st.get("music"):
            st["want_music"] = not st.get("want_music", False)
        await call.answer("🎵 Музыка добавлена" if st.get("want_music") else "🎵 Музыка убрана")
        await call.message.edit_reply_markup(reply_markup=picker_kb(req_id))
        return

    if act == "togdesc":
        if st.get("description"):
            st["want_description"] = not st.get("want_description", False)
        await call.answer("📝 Описание добавлено" if st.get("want_description") else "📝 Описание убрано")
        await call.message.edit_reply_markup(reply_markup=picker_kb(req_id))
        return

    if act == "togvideo":
        if st.get("photos"):
            st["want_as_video"] = not st.get("want_as_video", False)
        await call.answer("🎬 Соберу видео со звуком" if st.get("want_as_video") else "🎬 Видео убрано")
        await call.message.edit_reply_markup(reply_markup=picker_kb(req_id))
        return

    if act == "sendall":
        src = str(st.get("src", ""))
        photos_all = list(st["photos"])
        want_music = bool(st.get("want_music") and st.get("music"))
        want_desc = bool(st.get("want_description") and st.get("description"))

        if not await gate_download():
            await call.answer()
            return
        if not await gate_photo_volume(len(photos_all), src):
            await call.answer()
            return

        pending.pop(req_id, None)
        with contextlib.suppress(Exception):
            await call.message.delete()

        await call.answer("Отправляю всё…")
        cnt = await send_photos(call.message, photos_all, caption_html=CAPTION_PHOTO)
        store.inc_download(uid, "photo", items=cnt, source="tiktok")

        if want_music and globals_state.g_provider:
            await send_music_if_any(call.message, globals_state.g_provider, st.get("music"), uid=uid, label=label, src=src)
        if want_desc:
            await send_description_if_any(call.message, st.get("description"))
        await _maybe_send_as_video(call, st, photos_all, uid, label)

        if cnt:
            await after_download_hooks(call.bot, uid, label)

        await log_event(
            call.bot,
            "photodl",
            [
                "🖼️ Категория: <b>Скачивание фото (всё)</b>",
                f"👤 User/id: <b>{format_user_for_log(label, uid)}</b>",
                f"🔗 Ссылка: {code(src)}",
                f"📦 Кол-во фото: <b>{cnt}</b>",
            ],
        )
        chat_id = call.message.chat.id if call.message else uid
        await call.bot.send_message(chat_id, "👇", reply_markup=post_download_kb())
        return

    if act == "go":
        sel: set[int] = st["selected"]
        want_music = bool(st.get("want_music") and st.get("music"))
        want_desc = bool(st.get("want_description") and st.get("description"))
        want_video = bool(st.get("want_as_video"))
        if not sel and not want_music and not want_desc and not want_video:
            await call.answer("Выбери хотя бы одно фото (или галочку музыки/описания/видео).", show_alert=True)
            return

        src = str(st.get("src", ""))
        chosen = [st["photos"][i] for i in sorted(sel)]
        # Если фото по номерам не выбирали, но включили "Собрать видео" —
        # используем все фото слайдшоу для сборки видео.
        video_source_photos = chosen if chosen else (list(st["photos"]) if want_video else [])

        if not await gate_download():
            await call.answer()
            return
        if chosen and not await gate_photo_volume(len(chosen), src):
            await call.answer()
            return

        pending.pop(req_id, None)
        with contextlib.suppress(Exception):
            await call.message.delete()

        await call.answer("Отправляю…")
        cnt = 0
        if chosen:
            cnt = await send_photos(call.message, chosen, caption_html=CAPTION_PHOTO)
            store.inc_download(uid, "photo", items=cnt, source="tiktok")

        if want_music and globals_state.g_provider:
            await send_music_if_any(call.message, globals_state.g_provider, st.get("music"), uid=uid, label=label, src=src)
        if want_desc:
            await send_description_if_any(call.message, st.get("description"))
        if video_source_photos:
            await _maybe_send_as_video(call, st, video_source_photos, uid, label)

        if cnt or video_source_photos:
            await after_download_hooks(call.bot, uid, label)
        if cnt:
            await log_event(
                call.bot,
                "photodl",
                [
                    "🖼️ Категория: <b>Скачивание фото (выбор)</b>",
                    f"👤 User/id: <b>{format_user_for_log(label, uid)}</b>",
                    f"🔗 Ссылка: {code(src)}",
                    f"📦 Кол-во фото: <b>{cnt}</b>",
                ],
            )
        chat_id = call.message.chat.id if call.message else uid
        await call.bot.send_message(chat_id, "👇", reply_markup=post_download_kb())
        return

    await call.answer()
