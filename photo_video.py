"""
Сборка фото-слайдшоу TikTok (фото + музыка) в одно MP4-видео — опция
"Собрать видео" в фото-пикере, аналог того, что иногда сам TikTok предлагает
для постов-каруселей ("посмотреть как видео").
"""
import asyncio
import subprocess
from pathlib import Path
from typing import List, Optional

import aiohttp

from youtube_provider import _FFMPEG_PATH

PHOTO_DURATION_SEC = 2.5  # сколько секунд показывается каждое фото


class SlideshowBuildError(Exception):
    pass


async def _download_file(session: aiohttp.ClientSession, url: str, dest: Path) -> None:
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        if resp.status >= 400:
            raise SlideshowBuildError(f"HTTP {resp.status} при скачивании {url}")
        with dest.open("wb") as f:
            async for chunk in resp.content.iter_chunked(65536):
                f.write(chunk)


def _build_sync(photo_paths: List[Path], audio_path: Optional[Path], out_path: Path) -> None:
    if not _FFMPEG_PATH:
        raise SlideshowBuildError("ffmpeg недоступен")
    if not photo_paths:
        raise SlideshowBuildError("нет фото для сборки")

    list_path = out_path.with_suffix(".txt")
    lines = []
    for p in photo_paths:
        lines.append(f"file '{p.resolve().as_posix()}'")
        lines.append(f"duration {PHOTO_DURATION_SEC}")
    # Особенность concat-демуксера ffmpeg: последний файл нужно продублировать
    # без строки duration, иначе он не покажется полный положенный срок.
    lines.append(f"file '{photo_paths[-1].resolve().as_posix()}'")
    list_path.write_text("\n".join(lines), encoding="utf-8")

    cmd = [
        _FFMPEG_PATH, "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_path),
    ]
    if audio_path:
        # Зацикливаем звук на всю длину слайдшоу; -shortest потом обрежет
        # по видео (оно короче бесконечного луп-аудио).
        cmd += ["-stream_loop", "-1", "-i", str(audio_path)]
    cmd += [
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
        "-r", "25",
        "-c:v", "libx264",
        "-preset", "veryfast",
    ]
    if audio_path:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += ["-movflags", "+faststart", str(out_path)]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=180)
    finally:
        list_path.unlink(missing_ok=True)

    if result.returncode != 0 or not out_path.exists():
        raise SlideshowBuildError(result.stderr.decode("utf-8", "ignore")[-500:])


async def build_photo_slideshow_video(
    session: aiohttp.ClientSession,
    photo_urls: List[str],
    music_url: Optional[str],
    out_dir: Path,
    name_prefix: str,
) -> Path:
    """Скачивает фото (+ музыку, если есть) и собирает их в одно MP4-видео через ffmpeg."""
    if not _FFMPEG_PATH:
        raise SlideshowBuildError("ffmpeg недоступен на сервере")

    photo_paths: List[Path] = []
    audio_path: Optional[Path] = None
    try:
        for i, url in enumerate(photo_urls):
            p = out_dir / f"{name_prefix}_p{i}.jpg"
            await _download_file(session, url, p)
            photo_paths.append(p)

        if music_url:
            audio_path = out_dir / f"{name_prefix}_a.m4a"
            try:
                await _download_file(session, music_url, audio_path)
            except Exception:
                audio_path = None  # без музыки тоже нормально, просто продолжаем без звука

        out_path = out_dir / f"{name_prefix}_slideshow.mp4"
        await asyncio.to_thread(_build_sync, photo_paths, audio_path, out_path)
        return out_path
    finally:
        for p in photo_paths:
            p.unlink(missing_ok=True)
        if audio_path:
            audio_path.unlink(missing_ok=True)
