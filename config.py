"""
Конфигурация бота: переменные окружения, константы, логгер.
"""
import os
import re
import logging
from pathlib import Path
from datetime import timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# ================== CONFIG ==================
ENV_PATH = Path(__file__).with_name(".env")
load_dotenv(dotenv_path=ENV_PATH, override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN не найден. Добавь BOT_TOKEN в .env рядом с bot.py")
if not re.match(r"^\d+:[A-Za-z0-9_-]{30,}$", BOT_TOKEN):
    raise RuntimeError("❌ BOT_TOKEN имеет неверный формат. Проверь токен в .env")

# База данных (PostgreSQL на Render)
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
# Render даёт postgres://, asyncpg требует postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]

# Путь к JSON для первичной миграции данных (если файл существует — мигрируем)
DATA_FILE = Path("data.json")

API_URL = "https://tikwm.com/api/"
ADMINS = {7233257134}  # <-- твой Telegram ID

ADMIN_LOG_FILE = Path("admin.log")

SUPPORT_USERNAME = "@tiksavesbotsupport"
try:
    MSK_TZ = ZoneInfo("Europe/Moscow")
except Exception:
    MSK_TZ = timezone.utc

# Канал для логов (бот должен быть админом канала)
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "-1003763229922"))

TIKTOK_RE = re.compile(r"(https?://)?(www\.)?(tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)/", re.I)

# ========= YOUTUBE =========
YOUTUBE_RE = re.compile(
    r"(https?://)?(www\.|m\.)?(youtube\.com/(watch\?|shorts/|live/)|youtu\.be/)", re.I
)

# ========= ДРУГИЕ ИСТОЧНИКИ (через тот же движок yt-dlp, что и YouTube) =========
# Работают только с ПУБЛИЧНЫМ контентом без логина — это ограничение самих
# площадок (закрытые профили/приватные посты без авторизации не скачать),
# а не бота.
INSTAGRAM_RE = re.compile(r"(https?://)?(www\.)?instagram\.com/(reel|reels|p|tv)/", re.I)
VK_RE = re.compile(r"(https?://)?(www\.|m\.)?(vk\.com|vk\.ru|vkvideo\.ru)/(video|clip)", re.I)
PINTEREST_RE = re.compile(r"(https?://)?(www\.)?(pinterest\.[a-z.]+/pin/|pin\.it/)", re.I)

YOUTUBE_MAX_DURATION_SEC = int(os.getenv("YOUTUBE_MAX_DURATION_SEC", "1800"))  # 30 минут
# Для вертикальных Shorts yt-dlp репортит "height" как реальную высоту в
# пикселях (у "1080p"-шортса это 1920, а не 1080!) — если тут стоит 720,
# такие шортсы срезаются до огрызка качества. Ставим с запасом, чтобы
# доставало и обычным горизонтальным видео (720/1080p), и вертикальным Shorts.
YOUTUBE_MAX_HEIGHT = int(os.getenv("YOUTUBE_MAX_HEIGHT", "1920"))

# Telegram (обычный облачный Bot API) не даёт боту отправлять файлы больше
# 50 МБ — берём с небольшим запасом снизу.
YOUTUBE_MAX_VIDEO_MB = int(os.getenv("YOUTUBE_MAX_VIDEO_MB", "49"))
YOUTUBE_MAX_VIDEO_BYTES = YOUTUBE_MAX_VIDEO_MB * 1024 * 1024

MEDIA_GROUP_LIMIT = 10
PAGE_SIZE = 10
PENDING_TTL_SEC = 300

# ========= DONATE =========
CRYPTO_DONATE_URL = os.getenv("CRYPTO_DONATE_URL", "").strip() or "https://t.me/send?start=IVba6SXTH9iy"
BOT_SHARE_URL = os.getenv("BOT_SHARE_URL", "").strip() or "https://t.me/tiksavesbot"
STARS_MIN = int(os.getenv("STARS_MIN", "1"))
STARS_MAX = int(os.getenv("STARS_MAX", "1000"))
WAITING_STARS_TTL_SEC = 120

# ========= GLOBAL LIMITS =========
# Сколько скачиваний могут обрабатываться параллельно (а не одно за другим).
# Раньше было = 1 (строгая очередь "один за раз, ~раз в минуту").
GLOBAL_CONCURRENCY = int(os.getenv("GLOBAL_CONCURRENCY", "8"))

# ========= SPAM LIMIT (тихий cooldown, без страйков) =========
EVENT_WINDOW_SEC = 15
EVENT_MAX = 8
SPAM_COOLDOWN_SEC = 60

# ========= DOWNLOAD LIMIT =========
DL_WINDOW_SEC = 60
DL_MAX_ACTIONS = 6

# ========= PHOTO VOLUME LIMIT =========
PHOTO_WINDOW_SEC = 60
PHOTO_LIMIT_PER_MIN = 120

# ========= AUTOSAVE =========
AUTO_SAVE_INTERVAL_SEC = 5  # автосинхронизация раз в N сек

# ========= DESCRIPTION (CAPTION TEXT) =========
# Если описание видео влезает в это ограничение — шлём сообщением,
# иначе — файлом (.txt), чтобы не обрезать текст.
DESCRIPTION_TG_LIMIT = 3500

# ========= VIDEO/AUDIO FALLBACK DOWNLOAD =========
# Telegram (обычный облачный Bot API) не даёт боту отправлять файлы больше
# 50 МБ — это ограничение платформы, не бота.
MAX_VIDEO_MB = int(os.getenv("MAX_VIDEO_MB", "49"))
MAX_VIDEO_BYTES = MAX_VIDEO_MB * 1024 * 1024
MAX_AUDIO_MB = int(os.getenv("MAX_AUDIO_MB", "25"))
MAX_AUDIO_BYTES = MAX_AUDIO_MB * 1024 * 1024

# ========= API FALLBACK / HEALTH =========
API_ERROR_WINDOW_SEC = 120
API_ERROR_THRESHOLD = 6
API_FALLBACK_COOLDOWN_SEC = 180

# Варианты fallback: "none" | "apify"
ALT_PROVIDER = os.getenv("ALT_PROVIDER", "none").strip().lower()
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "").strip()
APIFY_ACTOR = os.getenv("APIFY_ACTOR", "apilabs/tiktok-downloader").strip()

# Небольшая задержка между запросами к бесплатному tikwm API — чтобы не
# словить рейт-лимит/бан на их стороне при частых запросах.
TIKWM_COOLDOWN_SEC = float(os.getenv("TIKWM_COOLDOWN_SEC", "1.2"))

BAN_DURATION_SEC = int(os.getenv("BAN_DURATION_SEC", str(24 * 3600)))  # 24 часа по умолчанию
BAN_REASON_SPAM = "Авто-бан: спам/флуд"
BAN_REASON_DL = "Лимит скачиваний"
BAN_REASON_PHOTO = "Лимит фото"

# Подпись с указанием бота
CAPTION_PHOTO = (
    "✨ <b>Готово!</b> 🖼️\n"
    "Забирай — и приятного просмотра 😎\n\n"
    "📥 <i>Скачано в</i> @tiksavesbot"
)
CAPTION_VIDEO = (
    "✨ <b>Готово!</b> 🎬\n"
    "Без водяных знаков, как и должно быть 😉\n\n"
    "📥 <i>Скачано в</i> @tiksavesbot"
)
CAPTION_AUDIO = (
    "🎵 <b>Твой звук готов!</b>\n"
    "Сохраняй и слушай 🎧\n\n"
    "📥 <i>Скачано в</i> @tiksavesbot"
)

ALBUM_PAUSE_MIN = 0.4
ALBUM_PAUSE_MAX = 0.8

BROADCAST_DELAY_SEC = 0.35
BROADCAST_MAX_USERS = 5000

PHOTO_WARNING_TEXT = (
    "⚠️ <b>Прежде чем скачать</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    "Скачивай только свой контент или тот, на который у тебя есть разрешение автора.\n"
    "Уважай чужой труд 🙏"
)

MSG_SPAM = "🛡 <b>Слишком быстро!</b>\nПереведи дух ~{n} сек. и пробуй снова."
MSG_DL = "⏳ <b>Лимит скачиваний</b>\nПодожди ~{n} сек. — и продолжим."
MSG_PHOTO = "📸 <b>Лимит по фото</b>\nПодожди ~{n} сек. — и продолжим."

# ========= РЕФЕРАЛЬНАЯ СИСТЕМА / МАГАЗИН ПОДАРКОВ =========
BOT_USERNAME = os.getenv("BOT_USERNAME", "tiksavesbot").strip().lstrip("@")
REF_POINTS_PER_REFERRAL = int(os.getenv("REF_POINTS_PER_REFERRAL", "10"))
REF_TOP_LIMIT = int(os.getenv("REF_TOP_LIMIT", "10"))

# Каталог подарков: ключ, эмодзи, название, цена в баллах.
# Выдаются вручную администрацией — тут только учёт заявок/баланса.
GIFTS = [
    {"key": "heart",     "emoji": "❤️", "name": "Сердечко",   "price": 100},
    {"key": "bear",      "emoji": "🧸", "name": "Мишка",      "price": 100},
    {"key": "rose",      "emoji": "🌹", "name": "Роза",       "price": 200},
    {"key": "giftbox",   "emoji": "🎁", "name": "Подарок",    "price": 200},
    {"key": "champagne", "emoji": "🥂", "name": "Шампанское", "price": 500},
    {"key": "cake",      "emoji": "🍰", "name": "Тортик",     "price": 500},
    {"key": "rocket",    "emoji": "🚀", "name": "Ракета",     "price": 500},
    {"key": "diamond",   "emoji": "💎", "name": "Алмаз",      "price": 1000},
    {"key": "ring",      "emoji": "💍", "name": "Кольцо",     "price": 1000},
    {"key": "trophy",    "emoji": "🏆", "name": "Кубок",      "price": 1000},
]
GIFTS_BY_KEY = {g["key"]: g for g in GIFTS}

# Отдельный канал для заявок на подарки / выдачи призов реферальной системы
# (не мешаем с общим лог-каналом бота).
REFERRAL_LOG_CHANNEL_ID = int(os.getenv("REFERRAL_LOG_CHANNEL_ID", "-1004333103786"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("tiktok_bot")
