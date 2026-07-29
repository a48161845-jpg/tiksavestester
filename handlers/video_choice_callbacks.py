"""
Callback-обработчик выбора перед скачиванием видео (callback_data, начинающийся
на "vd:"). Примечание: в текущей логике main_handler видео отправляется сразу,
без промежуточного экрана выбора, поэтому pending_video никогда не заполняется
и эти кнопки не появляются у пользователя. Код сохранён как есть на случай,
если экран выбора будет включён повторно.
"""
import contextlib

from aiogram import F
from aiogram.types import CallbackQuery

from globals_state import dp
import globals_state
from config import CAPTION_VIDEO
from helpers import code
from storage import store
from user_label import resolve_user_label
from gates import gate_callback
from logging_channel import log_event, format_user_for_log
from send_helpers import send_video_smart, send_music_if_any
from picker_state import pending_video, cleanup_pending_video, last_audio_url
from keyboards import under_video_kb


@dp.callback_query(F.data.startswith("vd:"))
async def video_choice_cb(call: CallbackQuery):
    uid = call.from_user.id
    label = await resolve_user_label(call.bot, uid)
    store.set_user_label(uid, label)

    if not await gate_callback(call, label):
        return

    cleanup_pending_video()
    st = pending_video.get(uid)
    if not st:
        await call.answer("⏱️ Выбор устарел. Скинь ссылку ещё раз.", show_alert=True)
        with contextlib.suppress(Exception):
            if call.message:
                await call.message.delete()
        return

    action = (call.data or "").split(":", 1)[-1]
    if action == "cancel":
        pending_video.pop(uid, None)
        await call.answer("Ок.")
        with contextlib.suppress(Exception):
            if call.message:
                await call.message.delete()
        return

    if not call.message:
        await call.answer()
        return

    if action == "music":
        pending_video.pop(uid, None)
        await call.answer("Отправляю музыку…")
        if globals_state.g_provider:
            await send_music_if_any(call.message, globals_state.g_provider, st.get("music"), uid=uid, label=label, src=st.get("src"))
        with contextlib.suppress(Exception):
            await call.message.delete()
        return

    if action == "video":
        pending_video.pop(uid, None)
        await call.answer("Отправляю видео…")
        if globals_state.g_provider:
            has_music = bool(st.get("music"))
            if has_music:
                last_audio_url[uid] = st.get("music", "")
            await send_video_smart(
                call.message,
                globals_state.g_provider,
                st.get("video"),
                CAPTION_VIDEO,
                reply_markup=under_video_kb(has_music=has_music),
            )
            store.inc_download(uid, "video", items=1)
            await log_event(
                call.bot,
                "videodl",
                [
                    "🎬 Категория: <b>Скачивание видео</b>",
                    f"👤 User/id: <b>{format_user_for_log(label, uid)}</b>",
                    f"🔗 Ссылка: {code(st.get('src') or '')}" if st.get("src") else "🔗 Ссылка: -",
                ],
            )
        with contextlib.suppress(Exception):
            await call.message.delete()
        return

    await call.answer()
