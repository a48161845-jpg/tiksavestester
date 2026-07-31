"""
Вся логика статистики: агрегация по периодам/диапазонам дат,
формирование текстов для /stats и /top (и для пользователя, и для админа),
а также их отправка с клавиатурой.
"""
import contextlib
from datetime import datetime
from typing import Dict, Any, List, Tuple

from aiogram.types import Message, LinkPreviewOptions

from helpers import html_escape, msk_now, period_keys, week_range_str, iter_day_keys, format_msk
from storage import store
from admin_log_file import log_admin
from logging_channel import log_event, log_admin_action_to_channel, format_user_for_log
from keyboards import stats_kb, top_kb


# ================== RANGE AGGREGATION ==================
def _sum_range_bucket(day_keys: List[str]) -> Dict[str, Any]:
    stats_root = store.data.get("stats", {}) or {}
    total = {
        "users_new": 0,
        "downloads": {"video_ops": 0, "photo_ops": 0, "video_sent": 0, "photos_sent": 0, "audio_sent": 0},
        "errors": {"total": 0, "by_stage": {}, "by_type": {}},
        "bans_total": 0,
        "strikes_total": {"spam": 0, "dl": 0, "photo": 0},
        "stars_total": 0,
    }

    for key in day_keys:
        b = (stats_root.get("d", {}) or {}).get(key, {}) or {}
        total["users_new"] += int(b.get("users_new", 0))

        dls = b.get("downloads", {}) or {}
        total["downloads"]["video_ops"] += int(dls.get("video_ops", 0))
        total["downloads"]["photo_ops"] += int(dls.get("photo_ops", 0))
        total["downloads"]["video_sent"] += int(dls.get("video_sent", 0))
        total["downloads"]["photos_sent"] += int(dls.get("photos_sent", 0))
        total["downloads"]["audio_sent"] += int(dls.get("audio_sent", 0))

        errs = b.get("errors", {}) or {}
        total["errors"]["total"] += int(errs.get("total", 0))
        by_stage = errs.get("by_stage", {}) or {}
        by_type = errs.get("by_type", {}) or {}
        for k, v in by_stage.items():
            total["errors"]["by_stage"][k] = int(total["errors"]["by_stage"].get(k, 0)) + int(v)
        for k, v in by_type.items():
            total["errors"]["by_type"][k] = int(total["errors"]["by_type"].get(k, 0)) + int(v)

        total["bans_total"] += int(b.get("bans_total", 0))

        strikes = b.get("strikes_total", {}) or {}
        for k in ("spam", "dl", "photo"):
            total["strikes_total"][k] = int(total["strikes_total"].get(k, 0)) + int(strikes.get(k, 0))

        total["stars_total"] += int(b.get("stars_total", 0))

    return total

def _range_user_totals(day_keys: List[str]) -> Dict[int, Dict[str, int]]:
    mp = (store.data.get("user_stats_period", {}) or {}).get("d", {}) or {}
    totals: Dict[int, Dict[str, int]] = {}
    for uid_str, rec_by_day in mp.items():
        if not isinstance(rec_by_day, dict):
            continue
        agg = {"video_ops": 0, "photo_ops": 0, "video_sent": 0, "photos_sent": 0, "audio_sent": 0, "stars": 0}
        for day_key in day_keys:
            rec = rec_by_day.get(day_key, {})
            if not rec:
                continue
            agg["video_ops"] += int(rec.get("video_ops", 0))
            agg["photo_ops"] += int(rec.get("photo_ops", 0))
            agg["video_sent"] += int(rec.get("video_sent", 0))
            agg["photos_sent"] += int(rec.get("photos_sent", 0))
            agg["audio_sent"] += int(rec.get("audio_sent", 0))
            agg["stars"] += int(rec.get("stars", 0))
        if sum(agg.values()) > 0:
            totals[int(uid_str)] = agg
    return totals

def _admin_stats_range_text(start_dt: datetime, end_dt: datetime) -> str:
    day_keys = iter_day_keys(start_dt, end_dt)
    bucket = _sum_range_bucket(day_keys)
    users_total = len(store.data.get("users", []))

    users_new = int(bucket.get("users_new", 0))
    dls = bucket.get("downloads", {}) or {}
    video_sent = int(dls.get("video_sent", 0))
    photos_sent = int(dls.get("photos_sent", 0))
    audio_sent = int(dls.get("audio_sent", 0))
    video_ops = int(dls.get("video_ops", 0))
    photo_ops = int(dls.get("photo_ops", 0))

    errs = bucket.get("errors", {}) or {}
    err_total = int(errs.get("total", 0))
    err_stage = errs.get("by_stage", {}) or {}
    err_type = errs.get("by_type", {}) or {}

    bans_total = int(bucket.get("bans_total", 0))
    active_bans = len(store.data.get("bans", {}))
    stars_total = int(bucket.get("stars_total", 0))

    user_totals = _range_user_totals(day_keys)
    active_all = sum(1 for _u, rec in user_totals.items() if int(rec.get("video_ops", 0)) + int(rec.get("photo_ops", 0)) > 0)
    active_video_all = sum(1 for _u, rec in user_totals.items() if int(rec.get("video_ops", 0)) > 0)

    top_downloaders = sorted(
        user_totals.items(),
        key=lambda kv: (
            int(kv[1].get("video_ops", 0)) + int(kv[1].get("photo_ops", 0)),
            int(kv[1].get("video_ops", 0)),
            int(kv[1].get("photo_ops", 0)),
        ),
        reverse=True
    )[:5]

    def fmt_top_downloaders() -> str:
        if not top_downloaders:
            return "-"
        lines = []
        for u, rec in top_downloaders:
            who = store.get_user_label(u)
            lines.append(
                f"• <b>{format_user_for_log(who, u)}</b>: "
                f"🎬 <b>{int(rec.get('video_ops', 0))}</b> шт | "
                f"🖼️ <b>{int(rec.get('photo_ops', 0))}</b> шт"
            )
        return "\n".join(lines)

    top_don = sorted(((u, int(rec.get("stars", 0))) for u, rec in user_totals.items()), key=lambda x: x[1], reverse=True)[:5]

    def fmt_top_don() -> str:
        if not top_don:
            return "-"
        lines = []
        for u, s in top_don:
            who = store.get_user_label(u)
            lines.append(f"• <b>{format_user_for_log(who, u)}</b>: <b>{s} ⭐</b>")
        return "\n".join(lines)

    top_err_types = sorted(err_type.items(), key=lambda kv: int(kv[1]), reverse=True)[:5]
    top_err_stages = sorted(err_stage.items(), key=lambda kv: int(kv[1]), reverse=True)

    def fmt_map(pairs) -> str:
        if not pairs:
            return "-"
        return "\n".join([f"• <b>{html_escape(str(k))}</b>: {int(v)}" for k, v in pairs])

    title = (
        "📊 <b>Статистика: диапазон</b>\n"
        f"<i>{start_dt.strftime('%d.%m.%Y')} — {end_dt.strftime('%d.%m.%Y')}</i>"
    )

    total_dl = video_ops + photo_ops + audio_sent
    pct_active = f"{active_all * 100 / users_total:.1f}%" if users_total else "0%"

    top_err_stages_sorted = sorted(err_stage.items(), key=lambda kv: int(kv[1]), reverse=True)
    top_err_types_sorted = sorted(err_type.items(), key=lambda kv: int(kv[1]), reverse=True)[:5]

    text_parts = [
        f"{title}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Пользователи</b>\n"
        f"├ Всего: <b>{users_total}</b>\n"
        f"└ Новых за период: <b>{users_new}</b>\n\n"
        f"⬇️ <b>Скачивания</b>\n"
        f"├ 🎬 Видео: <b>{video_ops}</b> операций (файлов: <b>{video_sent}</b>)\n"
        f"├ 🖼️ Фото: <b>{photo_ops}</b> операций (фото: <b>{photos_sent}</b>)\n"
        f"├ 🎵 Музыка: <b>{audio_sent}</b> шт\n"
        f"└ 📦 Итого: <b>{total_dl}</b> шт\n\n"
        f"💥 <b>Ошибки</b>\n"
        f"├ Всего: <b>{err_total}</b>\n"
    ]
    if top_err_stages_sorted:
        stage_lines = [f"│  {'└' if i == len(top_err_stages_sorted)-1 else '├'} {html_escape(str(k))}: <b>{int(v)}</b>" for i, (k, v) in enumerate(top_err_stages_sorted)]
        text_parts.append("├ По стадиям:\n" + "\n".join(stage_lines) + "\n")
    if top_err_types_sorted:
        type_lines = [f"   {'└' if i == len(top_err_types_sorted)-1 else '├'} {html_escape(str(k))}: <b>{int(v)}</b>" for i, (k, v) in enumerate(top_err_types_sorted)]
        text_parts.append("└ Топ типов:\n" + "\n".join(type_lines) + "\n")
    text_parts.append(
        f"\n🚫 <b>Баны</b>\n"
        f"├ За период: <b>{bans_total}</b>\n"
        f"└ Активных сейчас: <b>{active_bans}</b>\n\n"
        f"⭐ <b>Звёзды (донаты)</b>\n"
        f"└ За период: <b>{stars_total} ⭐</b>\n\n"
        f"📈 <b>Активность</b>\n"
        f"├ Скачивали: <b>{active_all}</b> / <b>{users_total}</b> ({pct_active})\n"
        f"└ Из них по видео: <b>{active_video_all}</b> / <b>{users_total}</b>\n\n"
        f"🏆 <b>Топ скачиваний</b>\n{fmt_top_downloaders()}\n\n"
        f"🏆 <b>Топ донатеров</b>\n{fmt_top_don()}\n\n"
    )
    return "".join(text_parts)

def _user_stats_range_text(uid: int, start_dt: datetime, end_dt: datetime) -> str:
    day_keys = iter_day_keys(start_dt, end_dt)
    mp = (store.data.get("user_stats_period", {}) or {}).get("d", {}) or {}
    rec_by_day = mp.get(str(uid), {}) or {}

    v_sent = p_sent = v_ops = p_ops = stars = 0
    a_sent = 0
    for day_key in day_keys:
        rec = rec_by_day.get(day_key, {})
        if not rec:
            continue
        v_sent += int(rec.get("video_sent", 0))
        p_sent += int(rec.get("photos_sent", 0))
        v_ops += int(rec.get("video_ops", 0))
        p_ops += int(rec.get("photo_ops", 0))
        a_sent += int(rec.get("audio_sent", 0))
        stars += int(rec.get("stars", 0))


    first_seen_ts = int((store.data.get("first_seen", {}) or {}).get(str(uid), 0))
    if first_seen_ts > 0:
        joined = format_msk(first_seen_ts)
    else:
        last_seen_ts = int((store.data.get("last_seen", {}) or {}).get(str(uid), 0))
        joined = format_msk(last_seen_ts) if last_seen_ts > 0 else "неизвестно"

    return (
        "📊 <b>Твоя статистика (диапазон)</b>\n"
        f"<i>{start_dt.strftime('%d.%m.%Y')} - {end_dt.strftime('%d.%m.%Y')}</i>\n\n"
        f"👤 ID: <b>{uid}</b>\n"
        f"🕒 Первый визит: <b>{joined}</b>\n\n"
        f"🎬 Видео скачано: <b>{v_ops}</b> операций (файлов: <b>{v_sent}</b>)\n"
        f"🖼️ Фото скачано: <b>{p_ops}</b> операций (фото: <b>{p_sent}</b>)\n"
        f"🎵 Музыка скачано: <b>{a_sent}</b>\n"
        f"⭐ Stars пожертвовано: <b>{stars}</b>\n"
    )

def _admin_stats_text(mode: str) -> str:
    stats_root = store.data.get("stats", {}) or {}

    if mode == "all":
        bucket = stats_root.get("all", {}) or {}
        title = "📊 <b>Статистика: всё время</b>"
    else:
        keys = period_keys(msk_now())
        key = keys[mode]
        bucket = (stats_root.get(mode, {}) or {}).get(key, {}) or {}
        nice = {"d": "сегодня", "n": "неделя", "m": "месяц", "y": "год"}[mode]
        if mode == "n":
            key_view = week_range_str(msk_now())
        else:
            key_view = key
        title = f"📊 <b>Статистика: {nice}</b>\n<i>{html_escape(key_view)}</i>"

    users_total = len(store.data.get("users", []))
    users_new = int(bucket.get("users_new", 0))

    dls = bucket.get("downloads", {}) or {}
    video_sent = int(dls.get("video_sent", 0))
    photos_sent = int(dls.get("photos_sent", 0))
    audio_sent = int(dls.get("audio_sent", 0))
    video_ops = int(dls.get("video_ops", 0))
    photo_ops = int(dls.get("photo_ops", 0))

    errs = bucket.get("errors", {}) or {}
    err_total = int(errs.get("total", 0))
    err_stage = errs.get("by_stage", {}) or {}
    err_type = errs.get("by_type", {}) or {}

    bans_total = int(bucket.get("bans_total", 0))
    active_bans = len(store.data.get("bans", {}))

    stars_total = int(bucket.get("stars_total", 0))

    us_dl = (store.data.get("user_stats", {}) or {}).get("downloads", {}) or {}
    active_all = sum(1 for _u, rec in us_dl.items() if int(rec.get("video_ops", 0)) + int(rec.get("photo_ops", 0)) > 0)
    active_video_all = sum(1 for _u, rec in us_dl.items() if int(rec.get("video_ops", 0)) > 0)

    top_downloaders = sorted(
        ((int(k), v) for k, v in us_dl.items()),
        key=lambda kv: (
            int(kv[1].get("video_ops", 0)) + int(kv[1].get("photo_ops", 0)),
            int(kv[1].get("video_ops", 0)),
            int(kv[1].get("photo_ops", 0)),
        ),
        reverse=True
    )[:5]

    def fmt_top_downloaders() -> str:
        if not top_downloaders:
            return "-"
        lines = []
        for u, rec in top_downloaders:
            who = store.get_user_label(u)
            lines.append(
                f"• <b>{format_user_for_log(who, u)}</b>: "
                f"🎬 <b>{int(rec.get('video_ops', 0))}</b> шт | "
                f"🖼️ <b>{int(rec.get('photo_ops', 0))}</b> шт"
            )
        return "\n".join(lines)

    stars_by_user = (store.data.get("user_stats", {}) or {}).get("stars", {}) or {}
    top_don = sorted(((int(k), int(v)) for k, v in stars_by_user.items()), key=lambda x: x[1], reverse=True)[:5]

    def fmt_top_don() -> str:
        if not top_don:
            return "-"
        lines = []
        for u, s in top_don:
            who = store.get_user_label(u)
            lines.append(f"• <b>{format_user_for_log(who, u)}</b>: <b>{s} ⭐</b>")
        return "\n".join(lines)

    top_err_types = sorted(err_type.items(), key=lambda kv: int(kv[1]), reverse=True)[:5]
    top_err_stages = sorted(err_stage.items(), key=lambda kv: int(kv[1]), reverse=True)

    def fmt_map(pairs) -> str:
        if not pairs:
            return "-"
        return "\n".join([f"• <b>{html_escape(str(k))}</b>: {int(v)}" for k, v in pairs])

    if mode == "all":
        keys_all = period_keys(msk_now())
        users_new_day = int((stats_root.get("d", {}) or {}).get(keys_all["d"], {}).get("users_new", 0))
        users_new_week = int((stats_root.get("n", {}) or {}).get(keys_all["n"], {}).get("users_new", 0))
        users_new_month = int((stats_root.get("m", {}) or {}).get(keys_all["m"], {}).get("users_new", 0))
        users_new_year = int((stats_root.get("y", {}) or {}).get(keys_all["y"], {}).get("users_new", 0))
        users_new_all = int((stats_root.get("all", {}) or {}).get("users_new", 0))
        users_new_block = (
            "🆕 <b>Новые пользователи</b>\n"
            f"├ Сегодня: <b>{users_new_day}</b>\n"
            f"├ Неделя: <b>{users_new_week}</b>\n"
            f"├ Месяц: <b>{users_new_month}</b>\n"
            f"├ Год: <b>{users_new_year}</b>\n"
            f"└ Всего: <b>{users_new_all}</b>\n"
        )
    else:
        users_new_block = f"🆕 Новых за период: <b>{users_new}</b>\n"

    total_dl = video_ops + photo_ops + audio_sent
    pct_active = f"{active_all * 100 / users_total:.1f}%" if users_total else "0%"

    text_parts = [
        f"{title}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Пользователи</b>\n"
        f"└ Всего: <b>{users_total}</b>\n\n"
        f"{users_new_block}\n"
        f"⬇️ <b>Скачивания</b>\n"
        f"├ 🎬 Видео: <b>{video_ops}</b> операций (файлов: <b>{video_sent}</b>)\n"
        f"├ 🖼️ Фото: <b>{photo_ops}</b> операций (фото: <b>{photos_sent}</b>)\n"
        f"├ 🎵 Музыка: <b>{audio_sent}</b> шт\n"
        f"└ 📦 Итого: <b>{total_dl}</b> шт\n\n"
        f"💥 <b>Ошибки</b>\n"
        f"├ Всего: <b>{err_total}</b>\n"
    ]
    if top_err_stages:
        stage_lines = [f"│  {'└' if i == len(top_err_stages)-1 else '├'} {html_escape(str(k))}: <b>{int(v)}</b>" for i, (k, v) in enumerate(top_err_stages)]
        text_parts.append("├ По стадиям:\n" + "\n".join(stage_lines) + "\n")
    if top_err_types:
        type_lines = [f"   {'└' if i == len(top_err_types)-1 else '├'} {html_escape(str(k))}: <b>{int(v)}</b>" for i, (k, v) in enumerate(top_err_types)]
        text_parts.append("└ Топ типов:\n" + "\n".join(type_lines) + "\n")
    text_parts.append(
        f"\n🚫 <b>Баны</b>\n"
        f"├ За период: <b>{bans_total}</b>\n"
        f"└ Активных сейчас: <b>{active_bans}</b>\n\n"
        f"⭐ <b>Звёзды (донаты)</b>\n"
        f"└ За период: <b>{stars_total} ⭐</b>\n\n"
        f"🎁 <b>Реферальная система</b>\n"
        f"└ Приглашено всего: <b>{store.total_referrals_count()}</b>\n"
    )
    if mode == "all":
        text_parts.append(
            f"\n📈 <b>Активность</b>\n"
            f"├ Скачивали: <b>{active_all}</b> / <b>{users_total}</b> ({pct_active})\n"
            f"└ Из них по видео: <b>{active_video_all}</b> / <b>{users_total}</b>\n\n"
            f"🏆 <b>Топ скачиваний</b>\n{fmt_top_downloaders()}\n\n"
            f"🏆 <b>Топ донатеров</b>\n{fmt_top_don()}\n\n"
        )
    return "".join(text_parts)

def _top_text_from_totals(title: str, totals: Dict[int, Dict[str, int]]) -> str:
    def top_by(field: str) -> List[Tuple[int, int]]:
        return sorted(
            ((uid, int(rec.get(field, 0))) for uid, rec in totals.items()),
            key=lambda x: x[1],
            reverse=True,
        )[:3]

    def fmt_list(items: List[Tuple[int, int]], icon: str, suffix: str) -> str:
        if not items or all(v <= 0 for _u, v in items):
            return "-"
        lines = []
        for uid, val in items:
            if val <= 0:
                continue
            who = store.get_user_label(uid)
            lines.append(f"• <b>{format_user_for_log(who, uid)}</b>: {icon} <b>{val}</b> {suffix}")
        return "\n".join(lines) if lines else "-"

    top_stars = top_by("stars")
    top_video = top_by("video_ops")
    top_photo = top_by("photo_ops")

    return (
        f"{title}\n\n"
        f"⭐ <b>Топ Stars</b>\n{fmt_list(top_stars, '⭐', '⭐')}\n\n"
        f"🎬 <b>Топ видео</b>\n{fmt_list(top_video, '🎬', 'шт')}\n\n"
        f"🖼️ <b>Топ фото</b>\n{fmt_list(top_photo, '🖼️', 'шт')}\n"
    )

def _top_text_for_mode(mode: str) -> str:
    stats_root = store.data.get("stats", {}) or {}
    if mode == "all":
        title = "🏆 <b>Топ: всё время</b>"
        us_dl = (store.data.get("user_stats", {}) or {}).get("downloads", {}) or {}
        stars_by_user = (store.data.get("user_stats", {}) or {}).get("stars", {}) or {}
        totals: Dict[int, Dict[str, int]] = {}
        for uid_str, rec in us_dl.items():
            try:
                uid = int(uid_str)
            except Exception:
                continue
            totals[uid] = {
                "video_ops": int(rec.get("video_ops", 0)),
                "photo_ops": int(rec.get("photo_ops", 0)),
                "stars": int(stars_by_user.get(str(uid), 0)),
            }
        return _top_text_from_totals(title, totals)

    keys = period_keys(msk_now())
    key = keys[mode]
    mp = (store.data.get("user_stats_period", {}) or {}).get(mode, {}) or {}
    nice = {"d": "сегодня", "n": "неделя", "m": "месяц", "y": "год"}[mode]
    if mode == "n":
        key_view = week_range_str(msk_now())
    else:
        key_view = key
    title = f"🏆 <b>Топ: {nice}</b>\n<i>{html_escape(key_view)}</i>"

    totals = {}
    for uid_str, rec_by_key in mp.items():
        if not isinstance(rec_by_key, dict):
            continue
        rec = rec_by_key.get(key, {}) or {}
        if not rec:
            continue
        try:
            uid = int(uid_str)
        except Exception:
            continue
        totals[uid] = {
            "video_ops": int(rec.get("video_ops", 0)),
            "photo_ops": int(rec.get("photo_ops", 0)),
            "stars": int(rec.get("stars", 0)),
        }
    return _top_text_from_totals(title, totals)

def _top_text_for_range(start_dt: datetime, end_dt: datetime) -> str:
    day_keys = iter_day_keys(start_dt, end_dt)
    totals = _range_user_totals(day_keys)
    title = (
        "🏆 <b>Топ: диапазон</b>\n"
        f"<i>{start_dt.strftime('%d.%m.%Y')} - {end_dt.strftime('%d.%m.%Y')}</i>"
    )
    return _top_text_from_totals(title, totals)

def _user_stats_text(uid: int) -> str:
    us_dl = (store.data.get("user_stats", {}) or {}).get("downloads", {}) or {}
    rec = us_dl.get(str(uid), {}) or {}
    v_sent = int(rec.get("video_sent", 0))
    p_sent = int(rec.get("photos_sent", 0))
    v_ops = int(rec.get("video_ops", 0))
    p_ops = int(rec.get("photo_ops", 0))
    a_sent = int(rec.get("audio_sent", 0))
    stars_by_user = (store.data.get("user_stats", {}) or {}).get("stars", {}) or {}
    stars = int(stars_by_user.get(str(uid), 0))
    first_seen_ts = int((store.data.get("first_seen", {}) or {}).get(str(uid), 0))
    if first_seen_ts > 0:
        joined = format_msk(first_seen_ts)
    else:
        last_seen_ts = int((store.data.get("last_seen", {}) or {}).get(str(uid), 0))
        joined = format_msk(last_seen_ts) if last_seen_ts > 0 else "неизвестно"
    return (
        "📊 <b>Твоя статистика (всё время)</b>\n\n"
        f"👤 ID: <b>{uid}</b>\n"
        f"🕒 Первый визит: <b>{joined}</b>\n\n"
        f"🎬 Видео скачано: <b>{v_ops}</b> операций (файлов: <b>{v_sent}</b>)\n"
        f"🖼️ Фото скачано: <b>{p_ops}</b> операций (фото: <b>{p_sent}</b>)\n"
        f"🎵 Музыка скачано: <b>{a_sent}</b>\n"
        f"⭐ Пожертвовано Stars: <b>{stars}</b>\n"
    )

def _user_stats_period_text(uid: int, mode: str) -> str:
    keys = period_keys(msk_now())
    key = keys.get(mode)
    mp = (store.data.get("user_stats_period", {}) or {}).get(mode, {}) or {}
    rec = (mp.get(str(uid), {}) or {}).get(key, {}) if key else {}
    rec = rec or {}
    v_sent = int(rec.get("video_sent", 0))
    p_sent = int(rec.get("photos_sent", 0))
    v_ops = int(rec.get("video_ops", 0))
    p_ops = int(rec.get("photo_ops", 0))
    a_sent = int(rec.get("audio_sent", 0))
    stars = int(rec.get("stars", 0))
    nice = {"d": "день", "n": "неделя", "m": "месяц", "y": "год"}[mode]
    if mode == "n":
        key_view = week_range_str(msk_now())
    else:
        key_view = key or ""
    return (
        f"📊 <b>Твоя статистика ({nice})</b>\n"
        f"<i>{html_escape(key_view)}</i>\n\n"
        f"🎬 Видео скачано: <b>{v_ops}</b> операций (файлов: <b>{v_sent}</b>)\n"
        f"🖼️ Фото скачано: <b>{p_ops}</b> операций (фото: <b>{p_sent}</b>)\n"
        f"🎵 Музыка скачано: <b>{a_sent}</b>\n"
        f"⭐ Stars пожертвовано: <b>{stars}</b>\n"
    )

def _admin_banlist_text() -> str:
    bans = store.list_bans()
    if not bans:
        return "✅ Активных банов нет."
    lines = ["🚫 <b>Активные баны</b>\n\n"]
    for uid2, until, reason, _by in bans[:100]:
        who_label = store.get_user_label(uid2)
        lines.append(
            f"• <b>{format_user_for_log(who_label, uid2)}</b> - до <b>{format_msk(until)} МСК</b>\n"
            f"  Причина: <i>{html_escape(reason)}</i>\n\n"
        )
    return "".join(lines)


# ================== SEND WRAPPERS ==================
async def send_stats_message(message: Message, uid: int, label: str, mode: str, *, edit: bool = False) -> None:
    log_admin(uid, "stats", f"mode={mode}")
    await log_admin_action_to_channel(
        message.bot,
        "Статистика",
        [f"👤 Кто: <b>{format_user_for_log(label, uid)}</b>", f"🧾 Режим: <b>{html_escape(mode)}</b>"],
    )
    with contextlib.suppress(Exception):
        await message.bot.send_chat_action(message.chat.id, "typing")
    text = _admin_stats_text(mode)
    if edit and message:
        with contextlib.suppress(Exception):
            await message.edit_text(text, parse_mode="HTML", reply_markup=stats_kb(), link_preview_options=LinkPreviewOptions(is_disabled=True))
        return
    await message.answer(
        text,
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
        reply_markup=stats_kb(),
    )

async def send_stats_range_message(message: Message, uid: int, label: str, start_dt: datetime, end_dt: datetime) -> None:
    log_admin(uid, "stats_range", f"from={start_dt.strftime('%Y-%m-%d')} to={end_dt.strftime('%Y-%m-%d')}")
    await log_admin_action_to_channel(
        message.bot,
        "Статистика (диапазон)",
        [
            f"👤 Кто: <b>{format_user_for_log(label, uid)}</b>",
            f"🧾 Период: <b>{start_dt.strftime('%d.%m.%Y')} - {end_dt.strftime('%d.%m.%Y')}</b>",
        ],
    )
    with contextlib.suppress(Exception):
        await message.bot.send_chat_action(message.chat.id, "typing")
    text = _admin_stats_range_text(start_dt, end_dt)
    await message.answer(
        text,
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
        reply_markup=stats_kb(),
    )

async def send_top_message(message: Message, uid: int, label: str, mode: str, *, edit: bool = False) -> None:
    log_admin(uid, "top", f"mode={mode}")
    await log_admin_action_to_channel(
        message.bot,
        "Топ",
        [f"👤 Кто: <b>{format_user_for_log(label, uid)}</b>", f"🧾 Режим: <b>{html_escape(mode)}</b>"],
    )
    with contextlib.suppress(Exception):
        await message.bot.send_chat_action(message.chat.id, "typing")
    text = _top_text_for_mode(mode)
    if edit and message:
        with contextlib.suppress(Exception):
            await message.edit_text(text, parse_mode="HTML", reply_markup=top_kb(), link_preview_options=LinkPreviewOptions(is_disabled=True))
        return
    await message.answer(text, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True), reply_markup=top_kb())

async def send_top_range_message(message: Message, uid: int, label: str, start_dt: datetime, end_dt: datetime) -> None:
    log_admin(uid, "top_range", f"from={start_dt.strftime('%Y-%m-%d')} to={end_dt.strftime('%Y-%m-%d')}")
    await log_admin_action_to_channel(
        message.bot,
        "Топ (диапазон)",
        [
            f"👤 Кто: <b>{format_user_for_log(label, uid)}</b>",
            f"🧾 Период: <b>{start_dt.strftime('%d.%m.%Y')} - {end_dt.strftime('%d.%m.%Y')}</b>",
        ],
    )
    with contextlib.suppress(Exception):
        await message.bot.send_chat_action(message.chat.id, "typing")
    text = _top_text_for_range(start_dt, end_dt)
    await message.answer(text, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True), reply_markup=top_kb())


# ================== COMPACT DAILY SUMMARY (для лог-канала) ==================
def daily_summary_text() -> str:
    """
    Короткая сводка за сегодня — одно сообщение в лог-канал раз в день
    вместо десятков отдельных событий за день.
    """
    keys = period_keys(msk_now())
    bucket = (store.data.get("stats", {}).get("d", {}) or {}).get(keys["d"], {}) or {}

    users_new = int(bucket.get("users_new", 0))
    dls = bucket.get("downloads", {}) or {}
    video_ops = int(dls.get("video_ops", 0))
    photo_ops = int(dls.get("photo_ops", 0))
    audio_sent = int(dls.get("audio_sent", 0))
    errors_total = int((bucket.get("errors", {}) or {}).get("total", 0))
    stars_total = int(bucket.get("stars_total", 0))
    bans_total = int(bucket.get("bans_total", 0))
    users_total = len(store.data.get("users", []))

    return (
        "📊 <b>Итоги дня</b>\n"
        f"<i>{keys['d']}</i>\n\n"
        f"🆕 Новых пользователей: <b>{users_new}</b> (всего: <b>{users_total}</b>)\n"
        f"🎬 Видео: <b>{video_ops}</b>\n"
        f"🖼️ Фото-сессий: <b>{photo_ops}</b>\n"
        f"🎵 Музыки: <b>{audio_sent}</b>\n"
        f"❌ Ошибок: <b>{errors_total}</b>\n"
        f"🚫 Банов: <b>{bans_total}</b>\n"
        f"⭐ Stars: <b>{stars_total}</b>"
    )
