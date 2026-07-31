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
            # ---- реферальная система ----
            "referrals": {},       # uid_str -> referrer_id (int): кто кого пригласил
            "referrals_log": [],   # [{"user_id":, "referrer_id":, "ts":}, ...] — история
            "ref_stats": {},       # uid_str -> {"referrals_count": int, "ref_points": int}
            # ---- магазин подарков ----
            "gift_requests": {},   # req_id_str -> {"user_id","gift_key","gift_name","gift_price","status","created_at","updated_at"}
            "gift_requests_seq": 0,
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

    def get_users_count(self) -> int:
        """
        Актуальное количество пользователей ПРЯМО СЕЙЧАС — из живого набора
        в памяти, а не из data["users"], который синхронизируется с ним только
        во время сохранения (раз в AUTO_SAVE_INTERVAL_SEC). Использовать это,
        а не len(store.data.get("users", [])), везде где важна точность.
        """
        return len(self._users_set)

    def get_all_user_ids(self) -> List[int]:
        """Актуальный список всех зарегистрированных uid (см. get_users_count)."""
        return sorted(self._users_set)

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

    # ---------- referrals ----------
    def get_referrer(self, uid: int) -> Optional[int]:
        v = self.data.get("referrals", {}).get(str(uid))
        if v is None:
            return None
        return int(v["referrer_id"]) if isinstance(v, dict) else int(v)

    def set_referral(self, uid: int, referrer_id: int) -> bool:
        """
        Фиксирует, что uid пришёл по ссылке referrer_id (баллы пока НЕ начисляются —
        это происходит один раз, при первом успешном скачивании uid, см.
        try_reward_referral). Возвращает True, только если это реально новая,
        валидная запись: сам на себя не считается, повторно один и тот же
        реферал не переписывается.
        """
        if uid == referrer_id:
            return False
        refs = self.data.setdefault("referrals", {})
        if str(uid) in refs:
            return False
        refs[str(uid)] = {"referrer_id": int(referrer_id), "rewarded": False, "ts": int(time.time())}
        self.data.setdefault("referrals_log", []).append(
            {"user_id": uid, "referrer_id": referrer_id, "ts": int(time.time())}
        )
        self._mark_dirty()
        return True

    def try_reward_referral(self, uid: int, points: int) -> Optional[Dict[str, int]]:
        """
        Начисляет баллы пригласившему за uid — но только один раз, при первом
        успешном скачивании этого uid (а не при простом /start). Возвращает
        {"referrer_id","referrals_count","ref_points"} если начисление произошло
        сейчас, иначе None (нет реферала или уже начислено раньше).
        """
        refs = self.data.get("referrals", {})
        rec = refs.get(str(uid))
        if rec is None:
            return None
        if isinstance(rec, dict):
            if rec.get("rewarded"):
                return None
            referrer_id = int(rec["referrer_id"])
            rec["rewarded"] = True
        else:
            # обратная совместимость со старым форматом записи (просто int)
            referrer_id = int(rec)
            refs[str(uid)] = {"referrer_id": referrer_id, "rewarded": True, "ts": int(time.time())}
        rs = self.add_ref_points(referrer_id, points)
        self._mark_dirty()
        return {"referrer_id": referrer_id, **rs}

    def add_ref_points(self, referrer_id: int, points: int) -> Dict[str, int]:
        """Начисляет баллы пригласившему и увеличивает счётчик рефералов."""
        rs = self.data.setdefault("ref_stats", {})
        rec = rs.setdefault(str(referrer_id), {"referrals_count": 0, "ref_points": 0})
        rec["referrals_count"] = int(rec.get("referrals_count", 0)) + 1
        rec["ref_points"] = int(rec.get("ref_points", 0)) + int(points)
        self._mark_dirty()
        return {"referrals_count": int(rec["referrals_count"]), "ref_points": int(rec["ref_points"])}

    def get_ref_stats(self, uid: int) -> Dict[str, int]:
        rec = self.data.get("ref_stats", {}).get(str(uid)) or {}
        return {
            "referrals_count": int(rec.get("referrals_count", 0)),
            "ref_points": int(rec.get("ref_points", 0)),
        }

    def add_ref_points_delta(self, uid: int, delta: int) -> int:
        """Списание/возврат/ручная корректировка баллов админом."""
        rs = self.data.setdefault("ref_stats", {})
        rec = rs.setdefault(str(uid), {"referrals_count": 0, "ref_points": 0})
        rec["ref_points"] = int(rec.get("ref_points", 0)) + int(delta)
        self._mark_dirty()
        return int(rec["ref_points"])

    def add_ref_count_delta(self, uid: int, delta: int) -> int:
        """Ручная корректировка счётчика рефералов админом (не уходит в минус)."""
        rs = self.data.setdefault("ref_stats", {})
        rec = rs.setdefault(str(uid), {"referrals_count": 0, "ref_points": 0})
        rec["referrals_count"] = max(0, int(rec.get("referrals_count", 0)) + int(delta))
        self._mark_dirty()
        return int(rec["referrals_count"])

    def reset_ref_stats(self, uid: int) -> None:
        """Полностью обнуляет баллы и счётчик рефералов пользователя."""
        rs = self.data.setdefault("ref_stats", {})
        rs[str(uid)] = {"referrals_count": 0, "ref_points": 0}
        self._mark_dirty()

    def total_referrals_count(self) -> int:
        """Сколько всего людей пришло по реферальным ссылкам (все записи, не только вознаграждённые)."""
        return len(self.data.get("referrals", {}))

    def referrals_of(self, referrer_id: int) -> List[int]:
        """Список uid всех, кого пригласил referrer_id (по записям в 'referrals')."""
        refs = self.data.get("referrals", {})
        out: List[int] = []
        for uid_str, rec in refs.items():
            rid = rec.get("referrer_id") if isinstance(rec, dict) else rec
            try:
                if int(rid) == int(referrer_id):
                    out.append(int(uid_str))
            except (TypeError, ValueError):
                continue
        return out

    def top_referrers(self, limit: int = 10) -> List[Tuple[int, int]]:
        from helpers import is_admin  # локальный импорт — без цикла на уровне модулей
        rs = self.data.get("ref_stats", {})
        items = [(int(uid), int((rec or {}).get("referrals_count", 0))) for uid, rec in rs.items()]
        items = [x for x in items if x[1] > 0 and not is_admin(x[0])]
        items.sort(key=lambda x: x[1], reverse=True)
        return items[:limit]

    def ref_rank(self, uid: int) -> Optional[int]:
        from helpers import is_admin
        rs = self.data.get("ref_stats", {})
        items = sorted(
            ((int(u), int((r or {}).get("referrals_count", 0))) for u, r in rs.items() if not is_admin(int(u))),
            key=lambda x: x[1],
            reverse=True,
        )
        for i, (u, cnt) in enumerate(items, start=1):
            if u == uid:
                return i if cnt > 0 else None
        return None

    # ---------- gift requests (магазин подарков) ----------
    def new_gift_request(self, uid: int, gift_key: str, gift_name: str, gift_price: int) -> int:
        gr = self.data.setdefault("gift_requests", {})
        seq = int(self.data.get("gift_requests_seq", 0)) + 1
        self.data["gift_requests_seq"] = seq
        now_ts = int(time.time())
        gr[str(seq)] = {
            "user_id": int(uid),
            "gift_key": gift_key,
            "gift_name": gift_name,
            "gift_price": int(gift_price),
            "status": "pending",
            "created_at": now_ts,
            "updated_at": now_ts,
        }
        self._mark_dirty()
        return seq

    def get_gift_request(self, req_id: int) -> Optional[Dict[str, Any]]:
        rec = self.data.get("gift_requests", {}).get(str(req_id))
        return dict(rec) if rec else None

    def set_gift_request_status(self, req_id: int, status: str) -> None:
        gr = self.data.get("gift_requests", {}).get(str(req_id))
        if gr is not None:
            gr["status"] = status
            gr["updated_at"] = int(time.time())
            self._mark_dirty()

    def user_gift_requests(self, uid: int, limit: int = 20) -> List[Dict[str, Any]]:
        gr = self.data.get("gift_requests", {})
        items = [dict(v, id=int(k)) for k, v in gr.items() if int(v.get("user_id", -1)) == int(uid)]
        items.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        return items[:limit]

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
