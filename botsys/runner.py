"""چرخه‌ی حیات ربات: Webhook یا Long Polling.

انتخاب حالت (TELEGRAM_UPDATE_MODE):
    auto     -> اگر TELEGRAM_WEBHOOK_URL یا RAILWAY_PUBLIC_DOMAIN موجود باشد Webhook،
                در غیر این صورت Long Polling.
    webhook  -> اجبار به Webhook
    polling  -> اجبار به Long Polling

روی ریلوی Webhook گزینه‌ی بهتری است: همان وب‌سرور FastAPI که پنل را سرو می‌کند
Update ها را هم می‌گیرد، پس نه پورت اضافه‌ای لازم است و نه پراسس جدا. با این حال
Long Polling هم پشتیبانی می‌شود (مثلاً برای اجرای لوکال).

ربات به‌عنوان Task کنار همان Event Loop پنل اجرا می‌شود، پس هیچ تداخلی با
وب‌سرور ندارد.
"""

from __future__ import annotations

import asyncio
import logging

from . import settings as cfg
from . import store
from .handlers import router
from .services import backups, broadcast, membership
from .tgapi import client

logger = logging.getLogger("vodiwalker.botsys.runner")

WEBHOOK_PATH = "/telegram/webhook"

_state: dict = {
    "running": False,
    "mode": None,
    "poll_task": None,
    "membership_task": None,
    "me": None,
    "last_error": "",
}


def is_running() -> bool:
    return bool(_state["running"])


def status() -> dict:
    return {
        "running": is_running(),
        "mode": _state["mode"],
        "username": (_state.get("me") or {}).get("username"),
        "configured": client.configured,
        "last_error": _state["last_error"],
        "schema_version": store.schema_version(),
    }


def resolve_token() -> str:
    """توکن از Environment، و در صورت نبود از تنظیمات ذخیره‌شده‌ی پنل.

    توکن هیچ‌وقت داخل کد نیست.
    """
    token = cfg.env(cfg.BOT_TOKEN_ENV)
    if token:
        return token
    try:
        import main
        return str(main.CONFIG.get("bot_token") or "").strip()
    except Exception:
        return ""


def public_base_url() -> str:
    """آدرس عمومی سرویس برای ثبت Webhook."""
    explicit = cfg.WEBHOOK_URL
    if explicit:
        return explicit.rstrip("/")
    domain = cfg.env("RAILWAY_PUBLIC_DOMAIN")
    if domain:
        return f"https://{domain}"
    try:
        import main
        base = str(main.CONFIG.get("public_base_url") or "").strip()
        if base:
            return base.rstrip("/")
    except Exception:
        pass
    return ""


def webhook_url() -> str:
    base = public_base_url()
    if not base:
        return ""
    if not base.startswith("http"):
        base = f"https://{base}"
    return f"{base}{WEBHOOK_PATH}/{cfg.WEBHOOK_SECRET}"


def _pick_mode() -> str:
    mode = cfg.UPDATE_MODE
    if mode == "webhook":
        return "webhook"
    if mode == "polling":
        return "polling"
    return "webhook" if public_base_url() else "polling"


# ── start / stop ─────────────────────────────────────────────────────────────
async def start() -> dict:
    """ربات را راه می‌اندازد. اگر توکن نباشد بی‌صدا غیرفعال می‌ماند (پنل باید کار کند)."""
    if _state["running"]:
        return status()

    token = resolve_token()
    if not token:
        _state["last_error"] = "TELEGRAM_BOT_TOKEN تنظیم نشده است"
        logger.info("Telegram bot disabled: no token configured")
        return status()

    client.set_token(token)
    await client.start()

    try:
        me = await client.get_me()
        _state["me"] = me or {}
        logger.info("Telegram bot authorized as @%s", (me or {}).get("username"))
    except Exception as exc:
        _state["last_error"] = f"getMe failed: {exc}"
        logger.error("Telegram bot token seems invalid: %s", exc)
        await client.close()
        return status()

    mode = _pick_mode()
    _state["mode"] = mode
    _state["last_error"] = ""

    if mode == "webhook":
        url = webhook_url()
        if not url:
            logger.warning("webhook mode requested but no public URL; falling back to polling")
            mode = _state["mode"] = "polling"
        else:
            try:
                await client.set_webhook(url, secret_token=cfg.WEBHOOK_SECRET)
                logger.info("Telegram webhook registered at %s", WEBHOOK_PATH)
            except Exception as exc:
                _state["last_error"] = f"setWebhook failed: {exc}"
                logger.error("setWebhook failed, falling back to polling: %s", exc)
                mode = _state["mode"] = "polling"

    if mode == "polling":
        # در حالت Polling نباید Webhook فعالی وجود داشته باشد، وگرنه getUpdates خطا می‌دهد.
        await client.delete_webhook(drop_pending=False)
        _state["poll_task"] = asyncio.create_task(_poll_loop())

    # این پایش علاوه بر chat_member update یک fallback دوره‌ای است؛ در نتیجه
    # اگر Telegram Update را از دست بدهد یا کاربر قبل از فعال شدن webhook خارج شده
    # باشد، کانفیگ نهایتاً در یک بازه‌ی کوتاه قطع می‌شود.
    _state["membership_task"] = asyncio.create_task(membership.monitor_loop())

    backups.start_scheduler(client)
    _state["running"] = True
    logger.info("Telegram bot started in %s mode", mode)
    return status()


async def stop() -> dict:
    """توقف تمیز: Task ها لغو و اتصال‌ها بسته می‌شوند."""
    _state["running"] = False

    for task_key in ("poll_task", "membership_task"):
        task = _state.get(task_key)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            _state[task_key] = None

    await backups.stop_scheduler()
    await broadcast.shutdown()
    await client.close()
    logger.info("Telegram bot stopped")
    return status()


async def restart() -> dict:
    await stop()
    return await start()


# ── Long Polling ─────────────────────────────────────────────────────────────
async def _poll_loop() -> None:
    offset = 0
    backoff = 1.0
    logger.info("long polling loop started")
    while True:
        try:
            updates = await client.get_updates(offset=offset, timeout=30)
            if updates is None:
                await asyncio.sleep(min(backoff, 30))
                backoff = min(backoff * 2, 30)
                continue
            backoff = 1.0
            for update in updates:
                offset = int(update.get("update_id", 0)) + 1
                # هر Update مستقل پردازش می‌شود تا یک خطا بقیه را متوقف نکند.
                await router.dispatch(update)
        except asyncio.CancelledError:
            logger.info("long polling loop cancelled")
            raise
        except Exception:
            logger.exception("polling loop error; retrying")
            await asyncio.sleep(min(backoff, 30))
            backoff = min(backoff * 2, 30)


# ── Webhook ──────────────────────────────────────────────────────────────────
async def handle_webhook_update(update: dict) -> None:
    """از روت FastAPI صدا زده می‌شود."""
    await router.dispatch(update)


def verify_webhook_secret(path_secret: str, header_secret: str | None) -> bool:
    """هم Secret موجود در مسیر و هم هدر رسمی تلگرام بررسی می‌شود."""
    import hmac
    expected = cfg.WEBHOOK_SECRET
    if not hmac.compare_digest(str(path_secret or ""), expected):
        return False
    if header_secret is not None and not hmac.compare_digest(str(header_secret), expected):
        return False
    return True
