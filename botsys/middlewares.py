"""Middleware های ورودی: همه‌ی Update ها قبل از رسیدن به Handler از اینجا می‌گذرند.

ترتیب: اعتبارسنجی -> ثبت/به‌روزرسانی کاربر -> بررسی مسدودی -> Rate Limit -> حالت تعمیر
"""

from __future__ import annotations

import logging

from . import keyboards as k
from . import security, store
from .services import users

logger = logging.getLogger("vodiwalker.botsys.middlewares")


class Rejected(Exception):
    """Update باید متوقف شود. reply در صورت وجود برای کاربر فرستاده می‌شود."""

    def __init__(self, reply: str = "", alert: bool = False):
        super().__init__(reply)
        self.reply = reply
        self.alert = alert


async def process(tg_user: dict, is_callback: bool = False) -> dict:
    """کاربر را آماده می‌کند یا Rejected پرتاب می‌کند."""
    telegram_id = security.valid_telegram_id((tg_user or {}).get("id"))
    if telegram_id is None:
        raise Rejected()

    # ربات‌ها اجازه‌ی استفاده ندارند.
    if (tg_user or {}).get("is_bot"):
        raise Rejected()

    rec = await users.touch(tg_user)

    if rec.get("is_blocked"):
        raise Rejected(store.text("blocked"), alert=True)

    # Rate Limit عمومی: ضد اسپم /start و کلیک پشت‌سرهم.
    if not security.allow_action(telegram_id):
        raise Rejected("درخواست‌های بیش از حد. چند لحظه صبر کنید.", alert=True)

    # حالت تعمیر: ادمین‌ها مستثنا هستند تا بتوانند تنظیمات را درست کنند.
    if store.setting("maintenance_mode", False) and not security.is_admin(telegram_id):
        raise Rejected(store.text("maintenance"), alert=True)

    return rec


def maintenance_kb() -> dict:
    return k.kb([[k.back("u:menu", "🔄 تلاش دوباره")]])
