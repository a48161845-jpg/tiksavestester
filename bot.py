"""
Точка входа: поднимает aiohttp-сессию, провайдеров, фоновые задачи
(автосейв, лог-воркер, рассылки, ежемесячный отчёт) и запускает polling.
"""
import asyncio
import contextlib
import time
from typing import Optional

import aiohttp
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN, ALT_PROVIDER, GLOBAL_CONCURRENCY, ADMINS, log
from helpers import now_msk_str, html_escape
from storage import store, init_db, close_db
import globals_state
from globals_state import dp
from providers import TikWMClient, ApifyProvider, BaseProvider, ProviderSwitcher
from logging_channel import autosave_loop, start_log_worker, stop_log_worker, send_channel_log
from broadcast import broadcast_schedule_loop
from db_report import start_monthly_report, stop_monthly_report, start_daily_summary, stop_daily_summary

# Импорт регистрирует все хендлеры (@dp.message/@dp.callback_query) на dp.
import handlers  # noqa: F401

_autosave_task: Optional[asyncio.Task] = None
_broadcast_task: Optional[asyncio.Task] = None
_monthly_task: Optional[asyncio.Task] = None
_daily_summary_task: Optional[asyncio.Task] = None


async def main():
    global _autosave_task, _broadcast_task, _monthly_task, _daily_summary_task

    # 1) Инициализируем БД (создаём таблицы, мигрируем из JSON если нужно)
    await init_db()

    # 2) Загружаем данные в память
    await store.load_from_db()

    timeout = aiohttp.ClientTimeout(total=60, sock_connect=15, sock_read=30)
    connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

        primary = TikWMClient(session, bot=bot)
        secondary: Optional[BaseProvider] = None
        if ALT_PROVIDER == "apify":
            secondary = ApifyProvider(session, bot)

        switcher = ProviderSwitcher(primary, secondary, bot)
        globals_state.set_global_provider(primary)

        await start_log_worker(bot)

        _autosave_task = asyncio.create_task(autosave_loop())
        _broadcast_task = asyncio.create_task(broadcast_schedule_loop(bot))
        _monthly_task = start_monthly_report(bot)
        _daily_summary_task = start_daily_summary(bot)

        start_ts = time.time()
        shutdown_reason = "⏹️ Штатная остановка"

        try:
            me = await bot.get_me()
            bans_active = len(store.list_bans())
            admins_total = len(ADMINS) + len(store.get_extra_admins())
            provider_line = "tikwm (осн.)" + (" + apify (резерв)" if secondary else "")
            await send_channel_log(
                bot,
                "🚀 <b>Бот запущен</b>\n"
                f"🤖 Бот: @{me.username} (<code>{me.id}</code>)\n"
                f"👥 Пользователей в базе: <b>{len(store.data.get('users', []))}</b>\n"
                f"👑 Администраторов: <b>{admins_total}</b>\n"
                f"🚫 Активных банов: <b>{bans_active}</b>\n"
                f"📡 Провайдер(ы): <b>{provider_line}</b>\n"
                f"⚙️ Параллельных скачиваний: <b>{GLOBAL_CONCURRENCY}</b>\n"
                f"🕒 Время запуска: {now_msk_str()}",
            )
            await dp.start_polling(bot, client=primary, switcher=switcher)
        except asyncio.CancelledError:
            shutdown_reason = "⏹️ Штатная остановка (получен сигнал остановки)"
            raise
        except Exception as e:
            shutdown_reason = f"💥 Аварийная остановка: <b>{e.__class__.__name__}</b> — {html_escape(str(e)[:200])}"
            raise
        finally:
            for task in (_autosave_task, _broadcast_task, _monthly_task, _daily_summary_task):
                if task and not task.done():
                    task.cancel()
                    with contextlib.suppress(Exception):
                        await task

            await store.save_unthrottled()

            uptime_sec = int(time.time() - start_ts)
            uptime_str = f"{uptime_sec // 3600}ч {(uptime_sec % 3600) // 60}м {uptime_sec % 60}с"
            with contextlib.suppress(Exception):
                await send_channel_log(
                    bot,
                    "🛑 <b>Бот остановлен</b>\n"
                    f"{shutdown_reason}\n"
                    f"⏳ Время работы: <b>{uptime_str}</b>\n"
                    f"👥 Пользователей в базе: <b>{len(store.data.get('users', []))}</b>\n"
                    f"🕒 Время остановки: {now_msk_str()}",
                )

            await stop_log_worker()
            await close_db()


if __name__ == "__main__":
    asyncio.run(main())
