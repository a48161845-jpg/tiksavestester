"""
Импорт всех модулей с хендлерами регистрирует их декораторы (@dp.message,
@dp.callback_query и т.д.) на общем Dispatcher из globals_state.

Порядок импорта здесь не важен для регистрации в aiogram (диспетчер сам
сортирует по специфичности фильтров), но main_handler (catch-all по F.text)
импортирован последним для наглядности — он должен ловить только то,
что не подошло под более специфичные команды/колбэки выше.
"""
from . import commands
from . import admin_commands
from . import admin_callbacks
from . import donate_callbacks
from . import help_callbacks
from . import picker_callbacks
from . import video_choice_callbacks
from . import main_handler  # noqa: F401  (catch-all — импортировать последним)
