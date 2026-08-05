"""
Общий изменяемый стейт, используемый из разных модулей хендлеров:
- dp: единый Dispatcher, на который регистрируются все роутеры/хендлеры;
- g_provider: "глобальный" провайдер медиа, нужен для скачивания музыки
  отдельно от основного потока скачивания видео/фото;
- g_switcher: полная цепочка провайдеров (с фолбэком) — нужна там, где
  скачивание идёт не из основного хендлера с гейтами (например, inline-режим).
"""
from typing import Optional

from aiogram import Dispatcher

from providers import BaseProvider, ProviderSwitcher

dp = Dispatcher()

# глобальный provider (нужно для музыки после фото)
g_provider: Optional[BaseProvider] = None
g_switcher: Optional[ProviderSwitcher] = None


def set_global_provider(provider: BaseProvider) -> None:
    global g_provider
    g_provider = provider


def set_global_switcher(switcher: ProviderSwitcher) -> None:
    global g_switcher
    g_switcher = switcher
