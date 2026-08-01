"""
Скачивание видео с YouTube через yt-dlp.

В отличие от TikTok-провайдеров (providers.py), тут нет отдельного "получить
ссылки" + "скачать по ссылке" — yt-dlp сам качает файл на диск за один вызов,
и делает это синхронно, поэтому каждый вызов заворачиваем в отдельный поток
(asyncio.to_thread), чтобы не блокировать event loop бота.
"""
import asyncio
from pathlib import Path
from typing import Any, Dict, Tuple

import yt_dlp

from config import YOUTUBE_MAX_HEIGHT


class YoutubeTooLargeError(Exception):
    """Итоговый файл больше допустимого лимита."""


def _probe_sync(url: str) -> Dict[str, Any]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 20,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if info is None:
            raise RuntimeError("yt-dlp: пустой ответ при получении информации о видео")
        return info


async def probe_youtube(url: str) -> Dict[str, Any]:
    """Узнаёт длительность/название и т.п. БЕЗ скачивания — чтобы отсечь слишком длинные видео заранее."""
    return await asyncio.to_thread(_probe_sync, url)


async def download_youtube(
    url: str, out_dir: Path, max_height: int = YOUTUBE_MAX_HEIGHT
) -> Tuple[Path, Dict[str, Any]]:
    """Качает видео на диск, возвращает путь к файлу и распарсенный info-dict yt-dlp."""
    info_holder: Dict[str, Any] = {}

    def _run() -> Path:
        out_template = str(out_dir / "%(id)s.%(ext)s")
        opts = {
            # ВАЖНО: никаких "bestvideo+bestaudio" — склейка отдельных
            # видео/аудио-потоков требует ffmpeg, а его на сервере нет.
            # Берём только уже смешанные (video+audio в одном файле) форматы —
            # yt-dlp отдаёт их как есть, без пост-обработки.
            "format": f"best[ext=mp4][height<={max_height}]/best[height<={max_height}]/best",
            "outtmpl": out_template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "socket_timeout": 20,
            "retries": 3,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            info_holder.update(info or {})
            filename = ydl.prepare_filename(info)
            p = Path(filename)
            if not p.exists():
                candidate = p.with_suffix(".mp4")
                if candidate.exists():
                    p = candidate
            if not p.exists():
                raise RuntimeError(f"yt-dlp: итоговый файл не найден ({filename})")
            return p

    path = await asyncio.to_thread(_run)
    return path, info_holder
