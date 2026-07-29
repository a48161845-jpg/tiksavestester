"""
Простой текстовый лог админ-действий на диске (admin.log),
отдельно от буферизованных логов в Telegram-канал.
"""
from datetime import datetime

from config import ADMIN_LOG_FILE


def log_admin(admin_id: int, action: str, details: str = "") -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} | admin={admin_id} | {action}"
    if details:
        line += f" | {details}"
    try:
        with ADMIN_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
