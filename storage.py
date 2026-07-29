"""
Персистентное хранилище данных бота через PostgreSQL (asyncpg).
При первом запуске автоматически создаёт таблицы.
Если рядом лежит data.json — мигрирует данные из него один раз.

Публичный интерфейс максимально совместим со старым JSON-хранилищем,
чтобы минимально менять остальной код.
"""
import json
import time
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import asyncpg

from config import DATABASE_URL, DATA_FILE, log

# Если DATABASE_URL не задан, fallback на JSON-файл (для локальной разработки)
_USE_DB = bool(DATABASE_URL)

# =================== DDL ===================
_DDL = """
CREATE TABLE IF NOT EXISTS bot_kv (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL
);
"""

# =================== POOL ===================
_pool: Optional[asyncpg.Pool] = None


async def init_db() -> None:
    """Вызывается один раз при старте бота."""
    global _pool
    if not _USE_DB:
        log.warning("DATABASE_URL не задан, используется data.json (fallback)")
        return
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, command_timeout=30)
    async with _pool.acquire() as conn:
        await conn.execute(_DDL)
    log.info("DB: подключено к PostgreSQL")
    await _maybe_migrate()


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


# =================== LOW-LEVEL KV ===================
async def _db_get(key: str) -> Any:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM bot_kv WHERE key=$1", key)
        return json.loads(row["value"]) if row else None


async def _db_set(key: str, value: Any) -> None:
    v = json.dumps(value, ensure_ascii=False)
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO bot_kv(key,value) VALUES($1,$2::jsonb) "
            "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
            key, v
        )


async def _db_get_all() -> Dict[str, Any]:
    """Загружает ВСЁ содержимое таблицы в словарь."""
    async with _pool.acquire() as conn:
        rows = await conn.fetch("SELECT key, value FROM bot_kv")
        return {r["key"]: json.loads(r["value"]) for r in rows}


# =================== MIGRATION ===================
async def _maybe_migrate() -> None:
    """Миграция из data.json отключена — используем только PostgreSQL."""
    pass


# =================== STORAGE CLASS ===================
class Storage:
    """
    Синхронная обёртка поверх asyncpg-пула.
    Данные кешируются в памяти; грязный флаг → периодический flush.
    """

    SAVE_MIN_INTERVAL_SEC = 0.6

    def __init__(self):
        self.data: Dict[str, Any] = self._default_data()
        self._dirty = False
        self._last_save_ts = 0.0
        self._users_set: set = set()
        self._loaded = False  # станет True после async load

    # ---------- defaults ----------
    def _default_data(self) -> Dict[str, Any]:
        return {
            "users": [],
            "downloads": 0,  # legacy counter, kept for migration compatibility
            "bans": {},
            "users_map": {},
            "log_seq_map": {},
            "first_seen": {},
            "last_seen": {},
            "admins_extra": [],  # дополнительные админы (кроме ADMINS из config)
            "users_info": {},    # актуальные данные профиля: {uid: {username, first_name, last_name}}
            "stats": {
                "d": {}, "n": {}, "m": {}, "y": {},
                "all": {
                    "users_new": 0,
                    "downloads": {"video_ops": 0, "photo_ops": 0, "video_sent": 0, "photos_sent": 0, "audio_sent": 0},
                    "errors": {"total": 0, "by_stage": {}, "by_type": {}},
                    "bans_total": 0,
                    "stars_total": 0,
                },
            },
            "user_stats": {"downloads": {}, "stars": {}},
            "user_stats_period": {"d": {}, "n": {}, "m": {}, "y": {}},
        }

    # ---------- async load ----------
    async def load_from_db(self) -> None:
        if not _USE_DB:
            # fallback: JSON
            if DATA_FILE.exists():
                try:
                    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
                    self._apply_raw(raw)
                except Exception as e:
                    log.error("Storage: load JSON error: %s", e)
        else:
            rows = await _db_get_all()
            merged: Dict[str, Any] = {}
            for k, v in rows.items():
                if not k.startswith("__"):
                    merged[k] = v
            if merged:
                self._apply_raw(merged)
        self._users_set = set(int(x) for x in self.data.get("users", []) if str(x).isdigit())
        self._loaded = True
        log.info("Storage: загружено %d пользователей", len(self._users_set))

    def _apply_raw(self, d: Dict[str, Any]) -> None:
        base = self._default_data()
        # Merge ALL keys from loaded data (not just known ones), so nothing is lost
        base.update(d)
        # ensure nested structure integrity
        base.setdefault("stats", {})
        for p in ("d", "n", "m", "y"):
            base["stats"].setdefault(p, {})
        base["stats"].setdefault("all", self._default_data()["stats"]["all"])
        base.setdefault("user_stats", {})
        base["user_stats"].setdefault("downloads", {})
        base["user_stats"].setdefault("stars", {})
        base.setdefault("user_stats_period", {})
        for p in ("d", "n", "m", "y"):
            base["user_stats_period"].setdefault(p, {})
        self.data = base

    # ---------- save ----------
    def _mark_dirty(self) -> None:
        self._dirty = True

    def save(self, force: bool = False) -> None:
        """Синхронная заглушка — реальный flush делает save_async."""
        self._dirty = True

    async def save_async(self, force: bool = False) -> None:
        if not self._dirty and not force:
            return
        now = time.time()
        if not force and (now - self._last_save_ts) < self.SAVE_MIN_INTERVAL_SEC:
            return
        await self._flush()

    async def _flush(self) -> None:
        if not self._dirty:
            return
        try:
            self.data["users"] = sorted(self._users_set)
            if _USE_DB:
                for key, value in self.data.items():
                    await _db_set(key, value)
            else:
                DATA_FILE.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            self._dirty = False
            self._last_save_ts = time.time()
        except Exception as e:
            log.error("Storage flush error: %s", e)

    async def save_unthrottled(self) -> None:
        await self._flush()

    # ---------- users ----------
    def set_user_label(self, uid: int, label: str) -> None:
        self.data.setdefault("users_map", {})
        self.data["users_map"][str(uid)] = str(label)
        self._mark_dirty()

    def get_user_label(self, uid: int) -> str:
        return str(self.data.get("users_map", {}).get(str(uid), f"{uid}"))

    # ---------- stats helpers ----------
    def _ensure_bucket(self, mode: str, key: str) -> Dict[str, Any]:
        from helpers import _empty_period_bucket
        stats = self.data.setdefault("stats", {})
        if mode == "all":
            return stats.setdefault("all", {})
        mp = stats.setdefault(mode, {})
        if key not in mp or not isinstance(mp.get(key), dict):
            mp[key] = _empty_period_bucket()
        return mp[key]

    def _touch_seen(self, uid: int) -> None:
        now_ts = int(time.time())
        self.data.setdefault("last_seen", {})
        self.data["last_seen"][str(uid)] = now_ts
        self.data.setdefault("first_seen", {})
        if str(uid) not in self.data["first_seen"]:
            self.data["first_seen"][str(uid)] = now_ts
        self._mark_dirty()

    def _user_period_rec(self, uid: int, mode: str, key: str) -> Dict[str, Any]:
        mp = self.data.setdefault("user_stats_period", {}).setdefault(mode, {})
        rec = mp.setdefault(str(uid), {})
        return rec.setdefault(
            key,
            {"video_ops": 0, "photo_ops": 0, "video_sent": 0, "photos_sent": 0, "audio_sent": 0, "stars": 0},
        )

    def register(self, uid: int) -> bool:
        from helpers import msk_now, period_keys
        is_new = uid not in self._users_set
        if is_new:
            self._users_set.add(uid)
            now_dt = msk_now()
            now_ts = int(time.time())
            self.data.setdefault("first_seen", {})[str(uid)] = now_ts
            self.data.setdefault("last_seen", {})[str(uid)] = now_ts
            keys = period_keys(now_dt)
            for mode, key in keys.items():
                b = self._ensure_bucket(mode, key)
                b["users_new"] = int(b.get("users_new", 0)) + 1
            self.data["stats"]["all"]["users_new"] = int(self.data["stats"]["all"].get("users_new", 0)) + 1
            self._mark_dirty()
            return True
        self._touch_seen(uid)
        return False

    def inc_download(self, uid: int, kind: str, items: int = 1) -> None:
        from helpers import msk_now, period_keys
        now_dt = msk_now()
        keys = period_keys(now_dt)
        items = int(max(0, items))
        if kind == "video":
            items = max(1, items)

        def apply_bucket(b: Dict[str, Any]) -> None:
            d = b.setdefault("downloads", {})
            if kind == "video":
                d["video_ops"] = int(d.get("video_ops", 0)) + 1
                d["video_sent"] = int(d.get("video_sent", 0)) + items
            else:
                d["photo_ops"] = int(d.get("photo_ops", 0)) + 1
                d["photos_sent"] = int(d.get("photos_sent", 0)) + items

        for mode, key in keys.items():
            apply_bucket(self._ensure_bucket(mode, key))
        apply_bucket(self.data["stats"]["all"])

        us = self.data.setdefault("user_stats", {}).setdefault("downloads", {})
        rec = us.setdefault(str(uid), {"video_ops": 0, "photo_ops": 0, "video_sent": 0, "photos_sent": 0, "audio_sent": 0})
        if kind == "video":
            rec["video_ops"] = int(rec.get("video_ops", 0)) + 1
            rec["video_sent"] = int(rec.get("video_sent", 0)) + items
        else:
            rec["photo_ops"] = int(rec.get("photo_ops", 0)) + 1
            rec["photos_sent"] = int(rec.get("photos_sent", 0)) + items

        for mode, key in keys.items():
            bucket = self._user_period_rec(uid, mode, key)
            if kind == "video":
                bucket["video_ops"] = int(bucket.get("video_ops", 0)) + 1
                bucket["video_sent"] = int(bucket.get("video_sent", 0)) + items
            else:
                bucket["photo_ops"] = int(bucket.get("photo_ops", 0)) + 1
                bucket["photos_sent"] = int(bucket.get("photos_sent", 0)) + items

        self._touch_seen(uid)
        self._mark_dirty()

    def inc_error(self, stage: str, err: Exception) -> None:
        from helpers import msk_now, period_keys
        now_dt = msk_now()
        keys = period_keys(now_dt)
        etype = err.__class__.__name__
        stage = (stage or "unknown").strip().lower()

        def apply_bucket(b: Dict[str, Any]) -> None:
            e = b.setdefault("errors", {})
            e["total"] = int(e.get("total", 0)) + 1
            e.setdefault("by_stage", {})[stage] = int(e["by_stage"].get(stage, 0)) + 1
            e.setdefault("by_type", {})[etype] = int(e["by_type"].get(etype, 0)) + 1

        for mode, key in keys.items():
            apply_bucket(self._ensure_bucket(mode, key))
        apply_bucket(self.data["stats"]["all"])
        self._mark_dirty()

    def inc_ban(self) -> None:
        from helpers import msk_now, period_keys
        now_dt = msk_now()
        keys = period_keys(now_dt)

        def apply_bucket(b: Dict[str, Any]) -> None:
            b["bans_total"] = int(b.get("bans_total", 0)) + 1

        for mode, key in keys.items():
            apply_bucket(self._ensure_bucket(mode, key))
        apply_bucket(self.data["stats"]["all"])
        self._mark_dirty()

    def add_stars(self, uid: int, stars: int) -> None:
        from helpers import msk_now, period_keys
        stars = int(max(0, stars))
        if stars <= 0:
            return
        now_dt = msk_now()
        keys = period_keys(now_dt)

        def apply_bucket(b: Dict[str, Any]) -> None:
            b["stars_total"] = int(b.get("stars_total", 0)) + stars

        for mode, key in keys.items():
            apply_bucket(self._ensure_bucket(mode, key))
        apply_bucket(self.data["stats"]["all"])

        us = self.data.setdefault("user_stats", {}).setdefault("stars", {})
        us[str(uid)] = int(us.get(str(uid), 0)) + stars

        for mode, key in keys.items():
            bucket = self._user_period_rec(uid, mode, key)
            bucket["stars"] = int(bucket.get("stars", 0)) + stars

        self._mark_dirty()

    def inc_audio(self, uid: int, items: int = 1) -> None:
        from helpers import msk_now, period_keys
        now_dt = msk_now()
        keys = period_keys(now_dt)
        items = int(max(0, items))
        if items <= 0:
            return

        def apply_bucket(b: Dict[str, Any]) -> None:
            d = b.setdefault("downloads", {})
            d["audio_sent"] = int(d.get("audio_sent", 0)) + items

        for mode, key in keys.items():
            apply_bucket(self._ensure_bucket(mode, key))
        apply_bucket(self.data["stats"]["all"])

        us = self.data.setdefault("user_stats", {}).setdefault("downloads", {})
        rec = us.setdefault(str(uid), {"video_ops": 0, "photo_ops": 0, "video_sent": 0, "photos_sent": 0, "audio_sent": 0})
        rec["audio_sent"] = int(rec.get("audio_sent", 0)) + items

        for mode, key in keys.items():
            bucket = self._user_period_rec(uid, mode, key)
            bucket["audio_sent"] = int(bucket.get("audio_sent", 0)) + items

        self._touch_seen(uid)
        self._mark_dirty()

    # ---------- bans ----------
    def _cleanup_expired_bans(self) -> None:
        bans = self.data.get("bans", {})
        now = int(time.time())
        dead = [k for k, v in bans.items() if int(v.get("until", 0)) <= now]
        if dead:
            for k in dead:
                bans.pop(k, None)
            self._mark_dirty()

    def get_ban(self, uid: int) -> Optional[Dict[str, Any]]:
        self._cleanup_expired_bans()
        return self.data.get("bans", {}).get(str(uid))

    def set_ban(self, uid: int, until: int, reason: str, by: int) -> None:
        self.data.setdefault("bans", {})[str(uid)] = {"until": int(until), "reason": str(reason), "by": int(by)}
        self._mark_dirty()

    def unban(self, uid: int) -> bool:
        self._cleanup_expired_bans()
        bans = self.data.get("bans", {})
        existed = str(uid) in bans
        if existed:
            bans.pop(str(uid), None)
            self._mark_dirty()
        return existed

    def list_bans(self) -> List[Tuple[int, int, str, int]]:
        self._cleanup_expired_bans()
        out: List[Tuple[int, int, str, int]] = []
        for uid_str, rec in self.data.get("bans", {}).items():
            try:
                out.append((int(uid_str), int(rec.get("until", 0)), str(rec.get("reason", "")), int(rec.get("by", 0))))
            except Exception:
                continue
        out.sort(key=lambda x: x[1])
        return out

    # ---------- admins ----------
    def get_extra_admins(self) -> list:
        return list(self.data.get("admins_extra", []))

    def add_extra_admin(self, uid: int) -> bool:
        lst = self.data.setdefault("admins_extra", [])
        if uid in lst:
            return False
        lst.append(uid)
        self._mark_dirty()
        return True

    def del_extra_admin(self, uid: int) -> bool:
        lst = self.data.get("admins_extra", [])
        if uid not in lst:
            return False
        lst.remove(uid)
        self._mark_dirty()
        return True

    # ---------- log seq ----------
    def next_seq(self, category: str) -> int:
        mp = self.data.setdefault("log_seq_map", {})
        mp[category] = int(mp.get(category, 0)) + 1
        self._mark_dirty()
        return int(mp[category])

    # ---- совместимость: убраны strikes, оставлены заглушки ----
    def strikes_count(self, uid: int, kind: str = "spam") -> int:
        return 0

    def add_strikes(self, uid: int, kind: str, n: int = 1, reason: str = "") -> int:
        return 0

    def del_strikes(self, uid: int, kind: str, n: int = 1) -> int:
        return 0

    def clear_strikes(self, uid: int) -> None:
        pass

    def strikes_list(self, uid: int, kind: str) -> List[Dict[str, Any]]:
        return []

    def strikes_total(self, uid: int) -> int:
        return 0

    def inc_strike(self, kind: str, n: int = 1) -> None:
        pass


store = Storage()
