"""پل سازگاری با telegram_bot.py موجود.

مسئله: یک توکن نمی‌تواند دو مصرف‌کننده‌ی Update داشته باشد. اگر حلقه‌ی
getUpdates قدیمی همراه زیرسیستم جدید اجرا شود، تلگرام خطای 409 Conflict می‌دهد
و *هر دو* ربات از کار می‌افتند.

راه‌حل بدون حذف قابلیت: زیرسیستم جدید تنها مالک اتصال است، و ماژول قدیمی
به‌عنوان «ابزارهای پیشرفته‌ی ادمین» حفظ می‌شود:

  * تمام Handler های قدیمی (ویزارد ساخت کانفیگ، گروه‌های ساب، کاربران اینباند)
    دست‌نخورده باقی می‌مانند و از پنل ادمین جدید قابل دسترسی‌اند.
  * فقط حلقه‌ی Polling قدیمی اجرا نمی‌شود.
  * کلاینت HTTP و بررسی دسترسی ماژول قدیمی به زیرسیستم جدید وصل می‌شود، پس
    Rate Limit و مدل Permission یکسان اعمال می‌شود.

بنابراین هیچ قابلیت فعلی پنل یا ربات از بین نمی‌رود.
"""

from __future__ import annotations

import logging

from . import security
from .tgapi import client

logger = logging.getLogger("vodiwalker.botsys.legacy")

#: پیشوندهای Callback ماژول قدیمی (هیچ‌کدام با 'u:' و 'a:' تداخل ندارند).
LEGACY_PREFIXES = (
    "menu", "stats", "list:", "newcfg", "view:", "link:", "toggle:", "del:", "delok:",
    "clients:", "addclient:", "delclient:", "delclientok:",
    "subs:", "newsub", "subview:", "subdel:", "subdelok:", "subaddlink:", "subaddlinkdo:",
    "cfggroup:", "cfgaddgroup:", "cfgungroup:", "cfgnewgroup:",
    "w:", "wc:",
)

_attached = False


def _module():
    import telegram_bot
    return telegram_bot


def attach() -> bool:
    """ماژول قدیمی را به کلاینت و مدل دسترسی جدید وصل می‌کند (بدون اجرای Polling)."""
    global _attached
    try:
        legacy = _module()
    except Exception as exc:
        logger.warning("legacy telegram_bot module unavailable: %s", exc)
        return False

    try:
        # کلاینت HTTP مشترک: ماژول قدیمی از طریق همان اتصال و همان Rate Limit کار می‌کند.
        legacy._client = _LegacyClientProxy()
        legacy.BOT_TOKEN = client.token
        legacy.API_BASE = client.api_base
        legacy._running = True

        # بررسی دسترسی قدیمی (بر اساس ENV) با مدل Permission جدید جایگزین می‌شود.
        legacy._is_admin = lambda chat_id: security.is_admin(int(chat_id)) and \
            security.has_permission(int(chat_id), security.MANAGE_CONFIGS)
    except Exception as exc:
        logger.warning("could not attach legacy module: %s", exc)
        return False

    _attached = True
    logger.info("legacy admin tools attached (polling disabled, handlers preserved)")
    return True


def detach() -> None:
    global _attached
    if not _attached:
        return
    try:
        legacy = _module()
        legacy._running = False
        legacy._client = None
    except Exception:
        pass
    _attached = False


def is_attached() -> bool:
    return _attached


def owns_callback(data: str) -> bool:
    return bool(data) and data.startswith(LEGACY_PREFIXES)


def has_pending(chat_id: int) -> bool:
    """آیا ویزارد قدیمی منتظر ورودی متنی این ادمین است؟"""
    try:
        return int(chat_id) in _module()._pending
    except Exception:
        return False


async def handle_callback(callback: dict) -> None:
    await _module()._handle_callback(callback)


async def handle_message(message: dict) -> None:
    await _module()._handle_message(message)


class _LegacyClientProxy:
    """ماژول قدیمی مستقیماً `_client.post(url, json=..., timeout=...)` صدا می‌زند.

    این Proxy همان امضا را می‌پذیرد ولی درخواست را از کلاینت مرکزی (با Rate Limit
    و Retry) عبور می‌دهد، تا دو مسیر شبکه‌ی جدا و دو الگوی خطا نداشته باشیم.
    """

    async def post(self, url: str, json: dict | None = None, timeout=None):
        method = str(url).rsplit("/", 1)[-1]
        payload = dict(json or {})
        try:
            result = await client.call_strict(method, **payload)
            return _LegacyResponse({"ok": True, "result": result})
        except Exception as exc:
            description = getattr(exc, "description", str(exc))
            return _LegacyResponse({"ok": False, "description": description})


class _LegacyResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self) -> dict:
        return self._payload
