"""
Генерация и отправка отчёта по базе данных в лог-канал.
По команде /dbfile (только для админов) и автоматически раз в месяц — файлом.
"""
import asyncio
import contextlib
import io
import json
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot
from aiogram.types import BufferedInputFile

from config import LOG_CHANNEL_ID, MSK_TZ, ADMINS, log
from helpers import msk_now, now_msk_str
from storage import store
from logging_channel import send_channel_log


def _build_report_text(title: str) -> str:
    """Формирует текст отчёта по текущим данным из store."""
    d = store.data
    users_total = store.get_users_count()
    bans_active = len(store.list_bans())
    users_map = d.get("users_map", {})

    all_stats = (d.get("stats") or {}).get("all", {})
    dls = all_stats.get("downloads", {})
    video_ops = dls.get("video_ops", 0)
    photo_ops = dls.get("photo_ops", 0)
    audio_sent = dls.get("audio_sent", 0)
    stars_total = all_stats.get("stars_total", 0)
    errors_total = (all_stats.get("errors") or {}).get("total", 0)
    bans_total = all_stats.get("bans_total", 0)

    # Топ-5 пользователей по видео
    us_dl = (d.get("user_stats") or {}).get("downloads", {})
    top5 = sorted(us_dl.items(), key=lambda x: int((x[1] or {}).get("video_ops", 0)), reverse=True)[:5]

    top_lines = []
    for uid_str, rec in top5:
        label = users_map.get(uid_str, uid_str)
        v = int((rec or {}).get("video_ops", 0))
        p = int((rec or {}).get("photo_ops", 0))
        top_lines.append(f"  • {label}: {v} видео | {p} фото")

    # Топ-3 ошибок по типу
    by_type = (all_stats.get("errors") or {}).get("by_type", {}) or {}
    top_errs = sorted(by_type.items(), key=lambda x: x[1], reverse=True)[:3]
    err_lines = [f"  • {k}: {v}" for k, v in top_errs] or ["  • нет"]

    # Статистика по месяцам (последние 3)
    monthly = (d.get("stats") or {}).get("m", {}) or {}
    month_keys = sorted(monthly.keys(), reverse=True)[:3]
    month_lines = []
    for mk in month_keys:
        mb = monthly[mk] or {}
        md = mb.get("downloads", {}) or {}
        month_lines.append(
            f"  • {mk}: {md.get('video_ops', 0)} видео | {md.get('photo_ops', 0)} фото"
            f" | stars:{mb.get('stars_total', 0)} | err:{(mb.get('errors') or {}).get('total', 0)}"
        )

    lines = [
        f"=== {title} ===",
        f"Дата: {now_msk_str()}",
        "",
        f"Пользователей всего: {users_total}",
        f"С именем/username: {len(users_map)}",
        f"Активных банов: {bans_active}",
        f"Банов всего (история): {bans_total}",
        "",
        f"Видео скачано: {video_ops}",
        f"Фото-сессий: {photo_ops}",
        f"Аудио отправлено: {audio_sent}",
        f"Stars получено: {stars_total}",
        f"Ошибок всего: {errors_total}",
        "",
        "Топ-5 по видео:",
        *(top_lines or ["  • нет данных"]),
        "",
        "Топ ошибок:",
        *err_lines,
        "",
        "Последние месяцы:",
        *(month_lines or ["  • нет данных"]),
    ]
    return "\n".join(lines)


def _build_db_json() -> bytes:
    """Сериализует всё содержимое store в JSON-файл."""
    return json.dumps(store.data, ensure_ascii=False, indent=2).encode("utf-8")


async def send_db_report(bot: Bot, title: str = "Отчёт базы данных") -> None:
    """Отправляет отчёт в лог-канал файлом (.txt)."""
    try:
        text = _build_report_text(title)
        now_dt = msk_now()
        filename = f"db_report_{now_dt.strftime('%Y-%m-%d_%H-%M')}.txt"
        file_bytes = text.encode("utf-8")
        input_file = BufferedInputFile(file_bytes, filename=filename)
        caption = f"📊 <b>{title}</b>\n{now_msk_str()} #dbreport"
        await bot.send_document(
            chat_id=LOG_CHANNEL_ID,
            document=input_file,
            caption=caption,
            parse_mode="HTML",
        )
        log.info("db_report: отправлен файл-отчёт в канал")
    except Exception as e:
        log.error("db_report: ошибка: %s", e)


async def send_db_json(bot: Bot, chat_id: int) -> None:
    """Отправляет полный дамп БД как JSON-файл в указанный чат (для /dbfile)."""
    try:
        data_bytes = _build_db_json()
        now_dt = msk_now()
        filename = f"db_dump_{now_dt.strftime('%Y-%m-%d_%H-%M')}.json"
        input_file = BufferedInputFile(data_bytes, filename=filename)
        await bot.send_document(
            chat_id=chat_id,
            document=input_file,
            caption=f"🗄 <b>Дамп базы данных</b>\n{now_msk_str()}",
            parse_mode="HTML",
        )
        log.info("db_report: отправлен JSON-дамп в chat_id=%s", chat_id)
    except Exception as e:
        log.error("db_report: ошибка отправки JSON-дампа: %s", e)


# =================== MONTHLY CRON ===================
_monthly_task: asyncio.Task | None = None


async def monthly_report_loop(bot: Bot) -> None:
    """Запускается один раз при старте; отправляет отчёт в первый день месяца в 09:00 МСК."""
    while True:
        try:
            now = msk_now()
            # Следующий первый день месяца 09:00
            if now.month == 12:
                next_month = now.replace(year=now.year + 1, month=1, day=1, hour=9, minute=0, second=0, microsecond=0)
            else:
                next_month = now.replace(month=now.month + 1, day=1, hour=9, minute=0, second=0, microsecond=0)

            wait = (next_month - now).total_seconds()
            log.info("db_report: следующий отчёт через %.0f сек (%s МСК)", wait, next_month.strftime("%Y-%m-%d %H:%M"))
            await asyncio.sleep(max(60, wait))

            # Проверяем, что действительно первый день месяца (на случай дрейфа sleep)
            now2 = msk_now()
            if now2.day == 1:
                await send_db_report(bot, title=f"Ежемесячный отчёт — {now2.strftime('%B %Y')}")

        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("monthly_report_loop: %s", e)
            await asyncio.sleep(3600)


def start_monthly_report(bot: Bot) -> asyncio.Task:
    global _monthly_task
    _monthly_task = asyncio.create_task(monthly_report_loop(bot))
    return _monthly_task


def stop_monthly_report() -> None:
    global _monthly_task
    if _monthly_task and not _monthly_task.done():
        _monthly_task.cancel()


# =================== PINNED OVERVIEW (лог-канал) ===================
# Вместо ежедневной сводки в 23:55 — одно закреплённое сообщение с общей
# статистикой за всё время, которое периодически обновляется (редактируется),
# а не плодит новые сообщения каждый день.
_pinned_stats_task: Optional[asyncio.Task] = None
PINNED_STATS_INTERVAL_SEC = 900  # обновление раз в 15 минут


def _pinned_overview_text() -> str:
    d = store.data
    users_total = store.get_users_count()
    # list_bans() сначала чистит просроченные баны — иначе тут могли считаться
    # давно истёкшие баны как "активные", если долго не было банов/разбанов
    # (единственное место, где это реально чистилось раньше).
    bans_active = len(store.list_bans())
    admins_total = len(ADMINS) + len(store.get_extra_admins())

    all_stats = (d.get("stats") or {}).get("all", {})
    dls = all_stats.get("downloads", {}) or {}
    video_ops = dls.get("video_ops", 0)
    photo_ops = dls.get("photo_ops", 0)
    photos_sent = dls.get("photos_sent", 0)
    audio_sent = dls.get("audio_sent", 0)
    stars_total = all_stats.get("stars_total", 0)
    errors_total = (all_stats.get("errors") or {}).get("total", 0)
    bans_total = all_stats.get("bans_total", 0)

    return (
        "📌 <b>Общая статистика TikSaves</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Пользователей: <b>{users_total}</b>\n"
        f"👑 Администраторов: <b>{admins_total}</b>\n"
        f"🚫 Активных банов: <b>{bans_active}</b> (всего за всё время: {bans_total})\n\n"
        f"🎬 Видео скачано: <b>{video_ops}</b>\n"
        f"🖼️ Фото-сессий: <b>{photo_ops}</b> (фото отправлено: {photos_sent})\n"
        f"🎵 Музыки отправлено: <b>{audio_sent}</b>\n"
        f"⭐ Stars получено: <b>{stars_total}</b>\n"
        f"❌ Ошибок всего: <b>{errors_total}</b>\n\n"
        f"🕒 Обновлено: {now_msk_str()}"
    )


async def _update_pinned_overview(bot: Bot) -> None:
    text = _pinned_overview_text()
    info = store.data.get("pinned_stats") or {}
    msg_id = info.get("message_id")
    chat_id = info.get("chat_id") or LOG_CHANNEL_ID

    if msg_id:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, parse_mode="HTML")
            return
        except Exception as e:
            if "not modified" in str(e).lower():
                # Текст не изменился с прошлого раза (не с чем сравнивать) — это
                # не ошибка, ничего пересоздавать не нужно.
                return
            log.info("pinned_overview: не смог отредактировать (%s) — пересоздаю сообщение", e)

    try:
        msg = await bot.send_message(chat_id=LOG_CHANNEL_ID, text=text, parse_mode="HTML")
        store.data["pinned_stats"] = {"chat_id": msg.chat.id, "message_id": msg.message_id}
        store._mark_dirty()
        with contextlib.suppress(Exception):
            await bot.pin_chat_message(chat_id=msg.chat.id, message_id=msg.message_id, disable_notification=True)
    except Exception as e:
        log.error("pinned_overview: не удалось создать/закрепить сообщение: %s", e)


async def pinned_overview_loop(bot: Bot) -> None:
    """Обновляет закреплённую общую статистику в лог-канале раз в PINNED_STATS_INTERVAL_SEC."""
    while True:
        try:
            await _update_pinned_overview(bot)
            await asyncio.sleep(PINNED_STATS_INTERVAL_SEC)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("pinned_overview_loop: %s", e)
            await asyncio.sleep(300)


def start_pinned_overview(bot: Bot) -> asyncio.Task:
    global _pinned_stats_task
    _pinned_stats_task = asyncio.create_task(pinned_overview_loop(bot))
    return _pinned_stats_task


def stop_pinned_overview() -> None:
    global _pinned_stats_task
    if _pinned_stats_task and not _pinned_stats_task.done():
        _pinned_stats_task.cancel()
