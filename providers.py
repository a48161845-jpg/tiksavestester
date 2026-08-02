"""
Провайдеры скачивания медиа из TikTok: основной (tikwm.com API)
и резервный (Apify, опционально), а также логика переключения между ними.
"""
import json
import re
import time
import asyncio
import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Dict, Any, List
from collections import deque

import aiohttp
from aiohttp import ClientPayloadError
from aiogram import Bot

from config import (
    API_URL,
    APIFY_TOKEN,
    APIFY_ACTOR,
    TIKWM_COOLDOWN_SEC,
    API_ERROR_WINDOW_SEC,
    API_ERROR_THRESHOLD,
    API_FALLBACK_COOLDOWN_SEC,
)
from helpers import html_escape, code, clamp_reason, ms_since, exc_type_name, resolve_tiktok_redirect, normalize_tiktok_url, normalize_description as _normalize_description
from storage import store
from logging_channel import log_event


class _FileTooLargeError(Exception):
    """Внутренний сигнал: файл превышает лимит. Не логируется как dlerr."""


# ================== PROVIDERS ==================
@dataclass
class MediaInfo:
    video: Optional[str]
    photos: List[str]
    music: Optional[str]
    description: Optional[str] = None


def _deep_find_str(data: Any, keys: List[str], _depth: int = 0) -> Optional[str]:
    """
    Рекурсивно ищет в JSON (dict/list) первое строковое значение по одному
    из ключей-кандидатов. Нужен для "запасных" провайдеров, у которых точная
    форма ответа может отличаться/меняться — сканируем несколько вариантов
    вложенности вместо жёсткой привязки к одному пути.
    """
    if _depth > 4 or data is None:
        return None
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for v in data.values():
            if isinstance(v, (dict, list)):
                r = _deep_find_str(v, keys, _depth + 1)
                if r:
                    return r
    elif isinstance(data, list):
        for item in data:
            r = _deep_find_str(item, keys, _depth + 1)
            if r:
                return r
    return None


def _deep_find_url(data: Any, keys: List[str], _depth: int = 0) -> Optional[str]:
    v = _deep_find_str(data, keys, _depth)
    return v if v and v.startswith("http") else None


def _deep_find_list(data: Any, keys: List[str], _depth: int = 0) -> List[str]:
    """Ищет список URL-строк (фото/слайды) по ключам-кандидатам."""
    if _depth > 4 or data is None:
        return []
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if isinstance(v, list) and v:
                out: List[str] = []
                for item in v:
                    if isinstance(item, str) and item.startswith("http"):
                        out.append(item)
                    elif isinstance(item, dict):
                        u = item.get("url") or item.get("image") or item.get("urlList")
                        if isinstance(u, list) and u:
                            u = u[0]
                        if isinstance(u, str) and u.startswith("http"):
                            out.append(u)
                if out:
                    return out
        for v in data.values():
            if isinstance(v, (dict, list)):
                r = _deep_find_list(v, keys, _depth + 1)
                if r:
                    return r
    elif isinstance(data, list):
        for item in data:
            r = _deep_find_list(item, keys, _depth + 1)
            if r:
                return r
    return []


class BaseProvider:
    name = "base"
    async def get_media(self, url: str) -> MediaInfo:
        raise NotImplementedError
    async def download_to_file(
        self,
        url: str,
        path: Path,
        max_bytes: int,
        stage: str,
        progress_cb: Optional[Callable] = None,
        cancel_cb: Optional[Callable] = None,
    ) -> int:
        raise NotImplementedError


class _DlErrMixin:
    """Общая логика логирования ошибок скачивания — используется всеми провайдерами."""
    bot: Optional[Bot] = None

    async def _log_dlerr(self, stage: str, src: str, attempt: int, dur_ms: int, err: Exception) -> None:
        # stats error counter
        try:
            store.inc_error(stage, err)
        except Exception:
            pass

        if not self.bot:
            return
        reason = clamp_reason(err)
        await log_event(
            self.bot,
            "dlerr",
            [
                "❌ Категория: <b>Ошибка скачивания</b>",
                f"🧩 Стадия: <b>{html_escape(stage)}</b>",
                f"🧬 Тип: <b>{html_escape(exc_type_name(err))}</b>",
                f"🔁 Попытка: <b>{attempt}</b>",
                f"⏱️ Время: <b>{dur_ms} мс</b>",
                f"🔗 Ссылка: {code(src)}",
                f"🧨 Причина: <b>{html_escape(reason)}</b>",
            ],
        )


class TikWMClient(_DlErrMixin, BaseProvider):
    name = "tikwm"

    # Общий для всех запросов "тормоз" перед вызовом tikwm API — небольшая
    # пауза между запросами, чтобы не словить рейт-лимит бесплатного API
    # (лок и таймстемп на уровне класса — общие для всех инстансов и всех
    # параллельных скачиваний, а не привязаны к конкретному пользователю).
    _cooldown_lock = asyncio.Lock()
    _last_call_ts = 0.0

    def __init__(self, session: aiohttp.ClientSession, bot: Optional[Bot] = None):
        self.session = session
        self.bot = bot

    @classmethod
    async def _respect_cooldown(cls) -> None:
        async with cls._cooldown_lock:
            now = time.monotonic()
            wait = TIKWM_COOLDOWN_SEC - (now - cls._last_call_ts)
            if wait > 0:
                await asyncio.sleep(wait)
            cls._last_call_ts = time.monotonic()

    @staticmethod
    def _media_from_data(data: Dict[str, Any]) -> MediaInfo:
        video = data.get("play") or data.get("wmplay")

        photos: List[str] = []
        for key in ("images", "image", "photos"):
            v = data.get(key)
            if isinstance(v, list) and v:
                if isinstance(v[0], dict):
                    photos = [x for x in ((o.get("url") or o.get("image") or "") for o in v) if x]
                else:
                    photos = [str(x) for x in v if x]
                break

        music = None
        for k in ("music", "music_url", "musicUrl", "playUrl", "music_play", "musicPlay"):
            v = data.get(k)
            if isinstance(v, str) and v.startswith("http"):
                music = v
                break

        if not music:
            mi = data.get("music_info") or data.get("musicInfo") or {}
            if isinstance(mi, dict):
                for k in ("play", "play_url", "playUrl", "url"):
                    v = mi.get(k)
                    if isinstance(v, str) and v.startswith("http"):
                        music = v
                        break

        description = None
        for k in ("title", "desc", "description"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                description = _normalize_description(v.strip())
                break

        return MediaInfo(video=video, photos=photos, music=music, description=description)

    async def get_media(self, url: str) -> MediaInfo:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Connection": "keep-alive",
        }

        last_err: Optional[Exception] = None
        for attempt in range(1, 4):
            t0 = time.perf_counter()
            try:
                await self._respect_cooldown()
                async with self.session.post(API_URL, data={"url": url}, headers=headers) as resp:
                    raw = await resp.read()
                    if not raw:
                        raise RuntimeError("Empty response body from API")
                    js = json.loads(raw.decode("utf-8", "ignore"))

                if js.get("code") != 0 or "data" not in js:
                    raise RuntimeError(f"API error: {js}")

                return self._media_from_data(js["data"])

            except (ClientPayloadError, aiohttp.ClientConnectionError, aiohttp.ServerDisconnectedError,
                    asyncio.TimeoutError, aiohttp.ClientOSError, aiohttp.ClientResponseError) as e:
                last_err = e
                await self._log_dlerr("api", url, attempt, ms_since(t0), e)
                await asyncio.sleep(0.6 * attempt)
                continue
            except Exception as e:
                await self._log_dlerr("api", url, attempt, ms_since(t0), e)
                raise

        raise RuntimeError(f"TikWM fetch failed after retries: {last_err}") from last_err

    async def download_to_file(
        self,
        url: str,
        path: Path,
        max_bytes: int,
        stage: str,
        progress_cb: Optional[Callable] = None,
        cancel_cb: Optional[Callable] = None,
    ) -> int:
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Connection": "keep-alive"}

        last_err: Optional[Exception] = None
        for attempt in range(1, 4):
            t0 = time.perf_counter()
            tmp = path.with_suffix(path.suffix + ".part")
            size = 0
            try:
                async with self.session.get(url, headers=headers, allow_redirects=True) as resp:
                    resp.raise_for_status()
                    total = resp.content_length or 0
                    # Проверяем Content-Length заранее — не тратим трафик
                    if total > max_bytes:
                        raise _FileTooLargeError(f"File too large (> {max_bytes} bytes)")
                    with tmp.open("wb") as f:
                        async for chunk in resp.content.iter_chunked(1024 * 64):
                            if not chunk:
                                continue
                            if cancel_cb and cancel_cb():
                                raise RuntimeError("Cancelled")
                            size += len(chunk)
                            if size > max_bytes:
                                raise _FileTooLargeError(f"File too large (> {max_bytes} bytes)")
                            f.write(chunk)
                            if progress_cb and total > 0:
                                progress = int(size * 100 / total)
                                progress_cb(progress)

                tmp.replace(path)
                return size

            except _FileTooLargeError:
                # Файл слишком большой — не логируем как ошибку, сразу бросаем
                with contextlib.suppress(Exception):
                    tmp.unlink(missing_ok=True)
                raise RuntimeError(f"File too large (> {max_bytes} bytes)")
            except (ClientPayloadError, aiohttp.ClientConnectionError, aiohttp.ServerDisconnectedError,
                    asyncio.TimeoutError, aiohttp.ClientOSError, aiohttp.ClientResponseError) as e:
                last_err = e
                with contextlib.suppress(Exception):
                    tmp.unlink(missing_ok=True)
                await self._log_dlerr(stage, url, attempt, ms_since(t0), e)
                await asyncio.sleep(0.6 * attempt)
                continue
            except Exception as e:
                with contextlib.suppress(Exception):
                    tmp.unlink(missing_ok=True)
                await self._log_dlerr(stage, url, attempt, ms_since(t0), e)
                raise

        raise RuntimeError(f"Download failed after retries: {last_err}") from last_err


class ApifyProvider(_DlErrMixin, BaseProvider):
    """
    Запасной платный источник через Apify (нужен APIFY_TOKEN в .env и
    ALT_PROVIDER=apify). По умолчанию используется актор apilabs/tiktok-downloader
    (см. APIFY_ACTOR в config.py) — парсинг ответа тоже защитный (ищем несколько
    вариантов полей вместо жёсткой привязки к одному пути),
    т.к. точная схема датасета зависит от актора.
    """
    name = "apify"

    def __init__(self, session: aiohttp.ClientSession, bot: Optional[Bot]):
        self.session = session
        self.bot = bot

    async def get_media(self, url: str) -> MediaInfo:
        if not APIFY_TOKEN:
            raise RuntimeError("APIFY_TOKEN not set")

        run_url = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items"
        t0 = time.perf_counter()
        try:
            async with self.session.post(
                run_url,
                params={"token": APIFY_TOKEN},
                json={"postURLs": [url], "shouldDownloadVideos": True, "shouldDownloadCovers": False},
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                raw = await resp.read()
                if resp.status >= 400:
                    raise RuntimeError(f"Apify HTTP {resp.status}: {raw[:300]!r}")
                items = json.loads(raw.decode("utf-8", "ignore"))

            if not items:
                raise RuntimeError("Apify: empty dataset (актор не вернул данных для этой ссылки)")
            item = items[0] if isinstance(items, list) else items

            video = _deep_find_url(item, ["downloadAddr", "play", "video_url", "videoUrl", "noWatermark", "hdplay"])
            photos = _deep_find_list(item, ["images", "imagePost", "photos", "slides"])
            music = _deep_find_url(item, ["musicMeta", "music", "music_url", "musicUrl", "playUrl"])
            description = _normalize_description(_deep_find_str(item, ["text", "title", "desc", "description"]))

            if not video and not photos:
                raise RuntimeError(f"Apify: no video/photo links in dataset item (keys: {list(item.keys()) if isinstance(item, dict) else type(item)})")

            return MediaInfo(video=video, photos=photos, music=music, description=description)

        except Exception as e:
            await self._log_dlerr("api_apify", url, 1, ms_since(t0), e)
            raise

    async def download_to_file(
        self,
        url: str,
        path: Path,
        max_bytes: int,
        stage: str,
        progress_cb: Optional[Callable] = None,
        cancel_cb: Optional[Callable] = None,
    ) -> int:
        client = TikWMClient(self.session, self.bot)
        return await client.download_to_file(
            url,
            path,
            max_bytes,
            stage=stage,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
        )


class ProviderSwitcher:
    """
    Цепочка провайдеров с реальным переключением "на лету": если очередной
    провайдер не смог отдать медиа — тут же (в рамках того же запроса
    пользователя) пробуем следующий в списке, а не ждём.

    providers[0] — основной (tikwm), дальше — запасные по порядку
    (например tiklydown, потом apify, если настроен). Если у провайдера
    подряд накопилось много ошибок за короткое окно — временно (на время
    "остывания") отправляем его в конец очереди, чтобы не долбить
    видимо упавший сервис на каждый запрос.
    """

    def __init__(self, providers: List[BaseProvider], bot: Bot):
        if not providers:
            raise ValueError("ProviderSwitcher needs at least one provider")
        self.providers = providers
        self.bot = bot
        self._errs: Dict[str, deque] = {p.name: deque() for p in providers}

    def _cleanup(self, name: str) -> None:
        now = time.time()
        dq = self._errs.setdefault(name, deque())
        while dq and now - dq[0] > API_ERROR_WINDOW_SEC:
            dq.popleft()

    def mark_error(self, provider: BaseProvider) -> None:
        now = time.time()
        self._errs.setdefault(provider.name, deque()).append(now)
        self._cleanup(provider.name)

    def mark_success(self, provider: BaseProvider) -> None:
        self._errs.setdefault(provider.name, deque()).clear()

    def _order(self) -> List[BaseProvider]:
        primary = self.providers[0]
        self._cleanup(primary.name)
        if len(self._errs.get(primary.name, [])) >= API_ERROR_THRESHOLD:
            return self.providers[1:] + [primary]
        return list(self.providers)

    def choose(self) -> BaseProvider:
        """Оставлено для обратной совместимости — какой провайдер пошёл бы первым."""
        return self._order()[0]

    async def log_switch(self, using: str, reason: str = "") -> None:
        lines = [
            "🔁 Категория: <b>Переключение провайдера</b>",
            f"📡 Сработал запасной: <b>{html_escape(using)}</b>",
        ]
        if reason:
            lines.append(f"🧨 Основной не смог: <b>{html_escape(reason)}</b>")
        await log_event(self.bot, "providerfallback", lines)

    async def get_media(self, url: str, raw_url: Optional[str] = None):
        """
        Пробует провайдеров по очереди, пока кто-то не вернёт медиа.
        Если все не смогли и raw_url похож на короткую ссылку —
        раскрываем редирект и пробуем цепочку ещё раз с реальным URL.
        Возвращает (MediaInfo, использованный_провайдер).
        """
        order = self._order()
        last_err: Optional[Exception] = None
        tried: List[str] = []

        for i, provider in enumerate(order):
            try:
                media = await provider.get_media(url)
                self.mark_success(provider)
                if i > 0:
                    with contextlib.suppress(Exception):
                        await self.log_switch(provider.name, reason=", ".join(tried) or "?")
                return media, provider
            except Exception as e:
                last_err = e
                tried.append(f"{provider.name}: {clamp_reason(e)}")
                self.mark_error(provider)
                continue

        if raw_url:
            sess = getattr(order[0], "session", None)
            if sess:
                with contextlib.suppress(Exception):
                    resolved = normalize_tiktok_url(await resolve_tiktok_redirect(sess, raw_url))
                    if resolved and resolved != raw_url and resolved != url:
                        for provider in order:
                            try:
                                media = await provider.get_media(resolved)
                                self.mark_success(provider)
                                return media, provider
                            except Exception as e:
                                last_err = e
                                self.mark_error(provider)
                                continue

        raise last_err or RuntimeError("All providers failed")
