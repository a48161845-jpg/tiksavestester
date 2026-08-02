"""
Вспомогательные функции: HTML-разметка, работа с датами/временем,
парсинг и нормализация TikTok-ссылок.
"""
import re
import time
from typing import Optional, Dict, Any, List

import aiohttp
from datetime import datetime, timezone, timedelta

from config import MSK_TZ, TIKTOK_RE, YOUTUBE_RE, INSTAGRAM_RE, VK_RE, PINTEREST_RE, ADMINS

# ================== HTML FORMATTING ==================
def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def plain(s: str) -> str:
    """Убирает HTML-теги — для мест, где разметка не поддерживается (алерты callback.answer)."""
    return re.sub(r"<[^>]+>", "", s or "")

_TAG_GLUE_RE = re.compile(r"(?<=\S)(?=[#@])")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_TRAILING_TAGS_RE = re.compile(r"[ \t]+((?:[#@]\S+[ \t]*){2,})$")

def normalize_description(text: Optional[str]) -> Optional[str]:
    """
    Скрейперы/API (tikwm, yt-dlp и т.п.) иногда отдают описание с хэштегами,
    склеенными друг с другом и с текстом без пробелов — "текст#тег1#тег2"
    вместо "текст #тег1 #тег2". Расклеиваем: вставляем пробел перед каждым
    "#"/"@", если перед ним нет пробела, и схлопываем случайные повторные
    пробелы (переносы строк не трогаем).

    Дополнительно: если в конце описания идёт "хвост" из 2+ хэштегов/упоминаний
    подряд (частый паттерн — основной текст, потом блок тегов), отделяем его
    пустой строкой от текста, как обычно и выглядит в самих приложениях.
    """
    if not text:
        return text
    t = _TAG_GLUE_RE.sub(" ", text)
    t = _MULTI_SPACE_RE.sub(" ", t)
    m = _TRAILING_TAGS_RE.search(t)
    if m:
        t = t[: m.start()] + "\n\n" + m.group(1).strip()
    return t.strip()

def code(s: Any) -> str:
    return f"<code>{html_escape(str(s))}</code>"

def bold(s: Any) -> str:
    """Жирный текст: <b>текст</b>"""
    return f"<b>{html_escape(str(s))}</b>"

def italic(s: Any) -> str:
    """Курсив: <i>текст</i>"""
    return f"<i>{html_escape(str(s))}</i>"

def underline(s: Any) -> str:
    """Подчеркивание: <u>текст</u>"""
    return f"<u>{html_escape(str(s))}</u>"

def strikethrough(s: Any) -> str:
    """Зачеркивание: <s>текст</s>"""
    return f"<s>{html_escape(str(s))}</s>"

def link(text: str, url: str) -> str:
    """Ссылка: <a href='url'>текст</a>"""
    return f'<a href="{html_escape(url)}">{html_escape(text)}</a>'

def spoiler(s: Any) -> str:
    """Спойлер (скрытый текст): <tg-spoiler>текст</tg-spoiler>"""
    return f"<tg-spoiler>{html_escape(str(s))}</tg-spoiler>"

def to_html_simple(markup: str) -> str:
    t = html_escape(markup)
    # Подчеркивание: __текст__
    t = re.sub(r"__([^_]+)__", r"<u>\1</u>", t)
    # Жирный текст: **текст**
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    # Курсив: *текст*
    t = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", t)
    # Моноширинный текст (бэкстики): `код`
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    # Ссылки: [текст](url)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
    # Зачеркивание: ~~текст~~
    t = re.sub(r"~~([^~]+)~~", r"<s>\1</s>", t)
    return t

# ================== TIME / DATE ==================
def now_msk_str() -> str:
    return datetime.now(tz=MSK_TZ).strftime("%d.%m.%Y %H:%M:%S МСК")

def msk_now() -> datetime:
    return datetime.now(tz=MSK_TZ)

def period_keys(dt: datetime) -> Dict[str, str]:
    day = dt.strftime("%Y-%m-%d")
    month = dt.strftime("%Y-%m")
    year = dt.strftime("%Y")
    iso = dt.isocalendar()
    week = f"{iso.year}-W{iso.week:02d}"
    return {"d": day, "n": week, "m": month, "y": year}

def week_range_str(dt: datetime) -> str:
    iso = dt.isocalendar()
    monday = datetime.fromisocalendar(iso.year, iso.week, 1).replace(tzinfo=MSK_TZ)
    sunday = monday + timedelta(days=6)
    return f"{monday.strftime('%d.%m.%Y')} - {sunday.strftime('%d.%m.%Y')}"

def parse_stats_mode(text: str) -> str:
    t = (text or "").strip().lower()
    if t in {"d", "n", "m", "y", "all"}:
        return t
    return "all"

def parse_date_token(text: str) -> Optional[datetime]:
    s = (text or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=MSK_TZ)
        except ValueError:
            continue
    return None

def iter_day_keys(start_dt: datetime, end_dt: datetime) -> List[str]:
    start = start_dt.date()
    end = end_dt.date()
    if end < start:
        start, end = end, start
    out: List[str] = []
    cur = start
    while cur <= end:
        out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out

def format_msk(ts_unix: int) -> str:
    dt = datetime.fromtimestamp(ts_unix, tz=timezone.utc).astimezone(MSK_TZ)
    return dt.strftime("%d.%m.%Y %H:%M")

def ms_since(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)

# ================== MISC / DOMAIN HELPERS ==================
def clamp_reason(e: Exception, limit: int = 220) -> str:
    s = str(e).strip() or e.__class__.__name__
    s = s.replace("\n", " ").replace("\r", " ")
    if len(s) > limit:
        s = s[:limit - 3] + "..."
    return s

def exc_type_name(e: Exception) -> str:
    """Имя класса исключения — для подробных логов ошибок."""
    return e.__class__.__name__

def is_admin(uid: int) -> bool:
    from storage import store
    return uid in ADMINS or uid in store.data.get("admins_extra", [])

def is_tiktok(text: str) -> bool:
    return bool(TIKTOK_RE.search(text or ""))

def extract_tiktok_url(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"https?://\S+", text)
    if m:
        url = m.group(0)
        return url if is_tiktok(url) else None
    # maybe without scheme
    m2 = TIKTOK_RE.search(text)
    if m2:
        url = text[m2.start():].split()[0]
        if not url.startswith("http"):
            url = "https://" + url
        return url
    return None

def normalize_tiktok_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return u
    if u[0] in "<([" and u[-1] in ">)]":
        u = u[1:-1].strip()
    while u and u[-1] in ".,;!?)\"]}":
        u = u[:-1]
    if not u.startswith("http"):
        u = "https://" + u
    if "tiktok.com" in u:
        base = u.split("?", 1)[0]
        return base
    return u

def is_youtube(text: str) -> bool:
    return bool(YOUTUBE_RE.search(text or ""))

def extract_youtube_url(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"https?://\S+", text)
    if m:
        url = m.group(0)
        return url if is_youtube(url) else None
    # maybe without scheme
    m2 = YOUTUBE_RE.search(text)
    if m2:
        url = text[m2.start():].split()[0]
        if not url.startswith("http"):
            url = "https://" + url
        return url
    return None

def normalize_youtube_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return u
    if u[0] in "<([" and u[-1] in ">)]":
        u = u[1:-1].strip()
    while u and u[-1] in ".,;!?)\"]}":
        u = u[:-1]
    if not u.startswith("http"):
        u = "https://" + u
    # В отличие от TikTok, у YouTube query-параметры значимы (?v=ID) —
    # обрезать их нельзя, поэтому просто возвращаем как есть после очистки.
    return u

def is_instagram(text: str) -> bool:
    return bool(INSTAGRAM_RE.search(text or ""))

def is_vk(text: str) -> bool:
    return bool(VK_RE.search(text or ""))

def is_pinterest(text: str) -> bool:
    return bool(PINTEREST_RE.search(text or ""))

def is_other_source(text: str) -> bool:
    """Instagram / VK / Pinterest — всё, что скачивается через тот же движок, что и YouTube."""
    return is_instagram(text) or is_vk(text) or is_pinterest(text)

def extract_other_source_url(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"https?://\S+", text)
    if m:
        url = m.group(0)
        return url if is_other_source(url) else None
    for rx in (INSTAGRAM_RE, VK_RE, PINTEREST_RE):
        m2 = rx.search(text)
        if m2:
            url = text[m2.start():].split()[0]
            if not url.startswith("http"):
                url = "https://" + url
            return url
    return None

async def resolve_tiktok_redirect(session: aiohttp.ClientSession, url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
    try:
        async with session.get(url, headers=headers, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            return str(resp.url)
    except Exception:
        return url

def is_download_message(text: str) -> bool:
    return is_tiktok(text)

def is_chatty_message(text: str) -> bool:
    if not text:
        return False
    if text.startswith("/"):
        return False
    if is_tiktok(text):
        return False
    return True

# ================== DURATION PARSING (для /ban) ==================
_DUR_RE = re.compile(r"(?i)(\d+)\s*([dhm])")

def parse_duration(s: str) -> int:
    if not s:
        raise ValueError("empty duration")
    total = 0
    for num, unit in _DUR_RE.findall(s.replace(" ", "")):
        n = int(num)
        u = unit.lower()
        if u == "d":
            total += n * 86400
        elif u == "h":
            total += n * 3600
        elif u == "m":
            total += n * 60
    if total <= 0:
        raise ValueError("bad duration")
    return total


def _empty_period_bucket() -> dict:
    return {
        "users_new": 0,
        "downloads": {"video_ops": 0, "photo_ops": 0, "video_sent": 0, "photos_sent": 0, "audio_sent": 0},
        "errors": {"total": 0, "by_stage": {}, "by_type": {}},
        "bans_total": 0,
        "stars_total": 0,
    }
