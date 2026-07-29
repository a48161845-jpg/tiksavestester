"""
Провайдеры скачивания медиа из TikTok: основной (tikwm.com API)
и резервный (Apify, опционально), а также логика переключения между ними.
"""
import json
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

from config import API_URL, APIFY_TOKEN, API_ERROR_WINDOW_SEC, API_ERROR_THRESHOLD, API_FALLBACK_COOLDOWN_SEC
from helpers import html_escape, code, clamp_reason, ms_since, exc_type_name
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


class TikWMClient(BaseProvider):
    name = "tikwm"

    def __init__(self, session: aiohttp.ClientSession, bot: Optional[Bot] = None):
        self.session = session
        self.bot = bot

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
                description = v.strip()
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


class ApifyProvider(BaseProvider):
    name = "apify"
    def __init__(self, session: aiohttp.ClientSession, bot: Optional[Bot]):
        self.session = session
        self.bot = bot

    async def get_media(self, url: str) -> MediaInfo:
        if not APIFY_TOKEN:
            raise RuntimeError("APIFY_TOKEN not set")
        raise RuntimeError("ApifyProvider not configured (choose actor + map fields once)")

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
    def __init__(self, primary: BaseProvider, secondary: Optional[BaseProvider], bot: Bot):
        self.primary = primary
        self.secondary = secondary
        self.bot = bot
        self._errs = deque()
        self._use_secondary_until = 0.0

    def _cleanup(self) -> None:
        now = time.time()
        while self._errs and now - self._errs[0] > API_ERROR_WINDOW_SEC:
            self._errs.popleft()

    def mark_error(self) -> None:
        now = time.time()
        self._errs.append(now)
        self._cleanup()
        if self.secondary and len(self._errs) >= API_ERROR_THRESHOLD:
            self._use_secondary_until = now + API_FALLBACK_COOLDOWN_SEC
            self._errs.clear()

    def choose(self) -> BaseProvider:
        if self.secondary and time.time() < self._use_secondary_until:
            return self.secondary
        return self.primary

    async def log_switch(self, using: str) -> None:
        await log_event(
            self.bot,
            "dlerr",
            [
                "🔁 Категория: <b>Переключение провайдера</b>",
                f"📡 Активный: <b>{html_escape(using)}</b>",
            ],
        )
