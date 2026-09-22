"""صدور کانفیگ رایگان — مسیر حساس و Atomic.

ترتیب عملیات به‌صورت عمدی این‌گونه است:

    قفل کاربر  ->  بررسی شرایط  ->  ساخت کانفیگ  ->  مصرف سهمیه  ->  ارسال

اگر ساخت کانفیگ شکست بخورد، سهمیه هرگز مصرف نمی‌شود.
اگر ارسال شکست بخورد، کانفیگ باقی می‌ماند (در «کانفیگ‌های من» قابل دریافت مجدد
است) و وضعیت delivered=False ثبت می‌شود تا قابل تشخیص باشد.

مقاومت در برابر Double-Click دو لایه دارد:
  1) store.claim(): رد فوری درخواست تکراری هم‌زمان
  2) asyncio.Lock مخصوص همان کاربر: سریال‌سازی کامل
"""

from __future__ import annotations

import logging

from .. import security, store
from . import channels, configs, quota

logger = logging.getLogger("vodiwalker.botsys.issuing")


class IssueResult:
    """نتیجه‌ی تلاش برای صدور کانفیگ."""

    def __init__(self, ok: bool, reason: str = "", user_config: dict | None = None,
                 missing_channels: list | None = None, busy: bool = False,
                 reused: bool = False):
        self.ok = ok
        self.reason = reason
        self.user_config = user_config
        self.missing_channels = missing_channels or []
        self.busy = busy
        #: True یعنی کانفیگ تازه ساخته نشد و همان کانفیگِ تحویل‌نشده‌ی قبلی
        #: برگردانده شد (سهمیه دوباره مصرف نشده است).
        self.reused = reused


async def issue_free_config(telegram_id: int) -> IssueResult:
    """کل فرایند دریافت کانفیگ رایگان."""
    if store.setting("maintenance_mode", False):
        return IssueResult(False, "ربات موقتاً در حال به‌روزرسانی است.")

    if security.is_blocked(telegram_id):
        return IssueResult(False, "دسترسی شما محدود شده است.")

    # لایه ۱: ضد Double-Click. اگر همین کاربر همین الان درخواست در جریان دارد،
    # فوراً رد می‌کنیم بدون اینکه منتظر قفل بمانیم.
    guard = f"free:{telegram_id}"
    if not store.claim(guard, ttl=60):
        # پیام «در حال پردازش» نباید دلیل واقعی را پنهان کند: اگر سهمیه کاربر
        # تمام شده، همان را می‌گوییم تا کاربر بی‌جهت منتظر نماند.
        allowed, reason = quota.can_receive(telegram_id)
        if not allowed:
            return IssueResult(False, reason)
        return IssueResult(False, "درخواست قبلی شما در حال پردازش است. کمی صبر کنید.", busy=True)

    # در صورت موفقیت، قفل تا پایان Cooldown آزاد *نمی‌شود*؛ بنابراین اسپم‌کلیک روی
    # دکمه نمی‌تواند چند سهمیه را پشت‌سرهم بسوزاند. در صورت شکست فوراً آزاد می‌شود
    # تا کاربر بعد از رفع مشکل (مثلاً عضویت در کانال) بتواند بی‌درنگ تلاش کند.
    issued = False

    try:
        # لایه ۲: سریال‌سازی کامل عملیات سهمیه‌ای این کاربر.
        async with store.user_lock(telegram_id):
            # لایه ۳ (Idempotency): اگر کانفیگ قبلی ساخته شده ولی ارسالش به
            # تلگرام شکست خورده، *همان* را برمی‌گردانیم. بنابراین Retry هیچ‌وقت
            # کانفیگ/ساب دوم نمی‌سازد و سهمیه دوباره مصرف نمی‌شود.
            stale = configs.pending_delivery(telegram_id, source="free")
            if stale is not None:
                logger.info("re-delivering undelivered free config %s to %s",
                            stale.get("id"), telegram_id)
                issued = True
                store.claim(guard, ttl=float(store.setting("free_cooldown_seconds", 15) or 0),
                            refresh=True)
                return IssueResult(True, user_config=stale, reused=True)

            # ۱) بررسی عضویت در کانال‌های اجباری
            ok, missing, _unverifiable = await channels.check_membership(telegram_id)
            if not ok:
                return IssueResult(False, "channels", missing_channels=missing)

            # ۲) بررسی شرایط سهمیه
            allowed, reason = quota.can_receive(telegram_id)
            if not allowed:
                return IssueResult(False, reason)

            conf = quota.effective(telegram_id)

            # ۳) ساخت کانفیگ با سرویس موجود پنل
            try:
                user_config = await configs.create_for_user(
                    telegram_id,
                    {
                        "volume_gb": conf["volume_gb"],
                        "speed_mbps": conf["speed_mbps"],
                        "duration_days": conf["duration_days"],
                        "ip_limit": conf["ip_limit"],
                        "protocol": store.setting("free_protocol", ""),
                        "port": store.setting("free_port", 0),
                    },
                    source="free",
                )
            except configs.ConfigCreationError as exc:
                # سهمیه دست‌نخورده می‌ماند.
                logger.error("free config creation failed for %s: %s", telegram_id, exc)
                store.bump_stat("config_errors")
                return IssueResult(False, "ساخت کانفیگ ناموفق بود. لطفاً بعداً دوباره تلاش کنید.")

            # ۴) مصرف سهمیه — فقط بعد از ساخت موفق
            await quota.consume(telegram_id, 1)

            try:
                from main import save_state
                await save_state()
            except Exception:
                logger.warning("save_state after issuing failed", exc_info=True)

            issued = True
            store.claim(guard, ttl=float(store.setting("free_cooldown_seconds", 15) or 0), refresh=True)
            return IssueResult(True, user_config=user_config)
    finally:
        if not issued:
            store.release(guard)


async def grant_config(actor_id: int, telegram_id: int, *, volume_gb: float | None = None,
                       speed_mbps: float | None = None, duration_days: int | None = None,
                       ip_limit: int | None = None) -> IssueResult:
    """اعطای کانفیگ توسط ادمین (سهمیه‌ی کاربر مصرف نمی‌شود)."""
    from .. import audit
    conf = quota.effective(telegram_id)
    spec = {
        "volume_gb": conf["volume_gb"] if volume_gb is None else volume_gb,
        "speed_mbps": conf["speed_mbps"] if speed_mbps is None else speed_mbps,
        "duration_days": conf["duration_days"] if duration_days is None else duration_days,
        "ip_limit": conf["ip_limit"] if ip_limit is None else ip_limit,
        "protocol": store.setting("free_protocol", ""),
        "port": store.setting("free_port", 0),
    }
    async with store.user_lock(telegram_id):
        try:
            user_config = await configs.create_for_user(telegram_id, spec, source="admin_grant")
        except configs.ConfigCreationError as exc:
            return IssueResult(False, f"ساخت کانفیگ ناموفق بود: {exc}")

    audit.record(actor_id, "config_grant", telegram_id, "config", None,
                 user_config.get("label"), note=f"link={user_config.get('link_uid')}")
    return IssueResult(True, user_config=user_config)
