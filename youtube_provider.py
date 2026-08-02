"""
Скачивание видео с YouTube через yt-dlp.

В отличие от TikTok-провайдеров (providers.py), тут нет отдельного "получить
ссылки" + "скачать по ссылке" — yt-dlp сам качает файл на диск за один вызов,
и делает это синхронно, поэтому каждый вызов заворачиваем в отдельный поток
(asyncio.to_thread), чтобы не блокировать event loop бота.
"""
import asyncio
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yt_dlp

from config import YOUTUBE_MAX_HEIGHT


class YoutubeTooLargeError(Exception):
    """Итоговый файл больше допустимого лимита."""


def _find_ffmpeg() -> Optional[str]:
    """
    Ищет ffmpeg: сначала системный (если вдруг есть), потом — портативный
    бинарник из пакета imageio-ffmpeg (ставится через pip, ничего вручную
    в систему устанавливать не нужно — именно так чинили отсутствие ffmpeg
    на этом сервере).
    """
    system_path = shutil.which("ffmpeg")
    if system_path:
        return system_path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


_FFMPEG_PATH: Optional[str] = _find_ffmpeg()


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

        if _FFMPEG_PATH:
            # ffmpeg есть (системный или портативный из imageio-ffmpeg) —
            # можно склеивать отдельные видео/аудио-потоки, это даёт заметно
            # лучшее качество (особенно для вертикальных Shorts, где хорошие
            # потоки почти всегда раздельные, а не в одном файле).
            fmt = (
                f"bestvideo[height<={max_height}]+bestaudio/"
                f"best[height<={max_height}]/best"
            )
        else:
            # ffmpeg не нашёлся вообще нигде — склейка невозможна, берём
            # только уже готовые (video+audio в одном файле) форматы.
            fmt = f"best[ext=mp4][height<={max_height}]/best[height<={max_height}]/best"

        opts = {
            "format": fmt,
            "outtmpl": out_template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "socket_timeout": 20,
            "retries": 3,
        }
        if _FFMPEG_PATH:
            opts["merge_output_format"] = "mp4"
            opts["ffmpeg_location"] = _FFMPEG_PATH

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


# Эти функции на самом деле не привязаны к YouTube — просто вызывают
# yt-dlp.extract_info(url), который сам определяет площадку. Алиасы с
# нейтральными именами — для использования с Instagram/VK/Pinterest и т.п.
probe_media = probe_youtube
download_media = download_youtube
