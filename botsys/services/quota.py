"""سیستم سهمیه (Quota).

طراحی به‌صورت Strategy است: منطق «دوره» (هرگز/روزانه/هفتگی/ماهانه) و منطق
«سقف» از هم جدا هستند، پس بعداً می‌توان سیستم‌های سهمیه‌بندی جدید (مثلاً بر
اساس امتیاز یا اشتراک) را بدون بازنویسی کامل اضافه کرد.

منبع مقادیر:
    1) Override مخصوص کاربر  (TG_QUOTAS)
    2) تنظیمات عمومی پنل     (TG_SETTINGS)
هیچ مقدار Hard-code ای مصرف نمی‌شود.
"""

from __future__ import annotations

import logging
from datetime import datetime

from .. import store

logger = logging.getLogger("vodiwalker.botsys.quota")

RESET_NEVER = "never"
RESET_DAILY = "daily"
RESET_WEEKLY = "weekly"
RESET_MONTHLY = "monthly"

RESET_LABELS = {
    RESET_NEVER: "هرگز",
    RESET_DAILY: "روزانه",
    RESET_WEEKLY: "هفتگی",
    RESET_MONTHLY: "ماهانه",
}


def period_key(mode: str, now: datetime | None = None) -> str:
    """کلید دوره‌ی جاری. با تغییر دوره، مصرف کاربر صفر می‌شود."""
    now = now or datetime.now()
    if mode == RESET_DAILY:
        return now.strftime("d%Y-%m-%d")
    if mode == RESET_WEEKLY:
        year, week, _ = now.isocalendar()
        return f"w{year}-{week:02d}"
    if mode == RESET_MONTHLY:
        return now.strftime("m%Y-%m")
    return "static"


def override(telegram_id: int) -> dict:
    return store.TG_QUOTAS.get(str(telegram_id)) or {}


def effective(telegram_id: int) -> dict:
    """مقادیر مؤثر سهمیه برای یک کاربر (تنظیمات عمومی + Override کاربر)."""
    ov = override(telegram_id)

    def pick(key: str, setting_key: str, cast=float):
        if key in ov and ov[key] is not None:
            try:
                return cast(ov[key])
            except (TypeError, ValueError):
                pass
        try:
            return cast(store.setting(setting_key))
        except (TypeError, ValueError):
            return cast(0)

    return {
        "total": int(pick("total", "free_quota_total", float)),
        "reset": str(ov.get("reset") or store.setting("free_quota_reset", RESET_NEVER)),
        "renewable": bool(ov["renewable"]) if "renewable" in ov and ov["renewable"] is not None
                     else bool(store.setting("free_quota_renewable", False)),
        "daily_limit": int(pick("daily_limit", "free_daily_limit", float)),
        "weekly_limit": int(pick("weekly_limit", "free_weekly_limit", float)),
        "monthly_limit": int(pick("monthly_limit", "free_monthly_limit", float)),
        "volume_gb": float(pick("volume_gb", "free_volume_gb", float)),
        "speed_mbps": float(pick("speed_mbps", "free_speed_mbps", float)),
        "duration_days": int(pick("duration_days", "free_duration_days", float)),
        "ip_limit": int(pick("ip_limit", "free_ip_limit", float)),
    }


def _sync_period(rec: dict, mode: str) -> None:
    """اگر دوره عوض شده باشد، مصرف را Reset می‌کند."""
    current = period_key(mode)
    if rec.get("quota_period_key") != current:
        if rec.get("quota_period_key"):
            logger.info("quota period rollover for %s -> %s", rec.get("telegram_id"), current)
        rec["quota_period_key"] = current
        rec["quota_used"] = 0


def _window_count(telegram_id: int, days: int) -> int:
    """تعداد کانفیگ رایگان دریافت‌شده در N روز گذشته."""
    from datetime import timedelta
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    count = 0
    for rec in store.TG_USER_CONFIGS.values():
        if str(rec.get("telegram_id")) != str(telegram_id):
            continue
        if rec.get("source") != "free":
            continue
        try:
            created = datetime.fromisoformat(str(rec.get("created_at")))
        except (TypeError, ValueError):
            continue
        if created.tzinfo is None:
            created = created.astimezone()
        if created >= cutoff:
            count += 1
    return count


def status(telegram_id: int) -> dict:
    """وضعیت کامل سهمیه‌ی کاربر برای نمایش و تصمیم‌گیری."""
    conf = effective(telegram_id)
    rec = store.TG_USERS.get(str(telegram_id))
    used = 0
    if rec is not None:
        _sync_period(rec, conf["reset"])
        used = int(rec.get("quota_used") or 0)

    remaining = max(0, conf["total"] - used)
    return {
        **conf,
        "used": used,
        "remaining": remaining,
        "period": period_key(conf["reset"]),
        "reset_label": RESET_LABELS.get(conf["reset"], conf["reset"]),
    }


def can_receive(telegram_id: int) -> tuple[bool, str]:
    """آیا کاربر می‌تواند الان کانفیگ رایگان بگیرد؟ (مجاز, دلیل_رد)"""
    conf = effective(telegram_id)
    rec = store.TG_USERS.get(str(telegram_id))
    if rec is None:
        return False, "کاربر یافت نشد."
    if rec.get("is_blocked"):
        return False, "دسترسی شما محدود شده است."

    _sync_period(rec, conf["reset"])
    used = int(rec.get("quota_used") or 0)

    if conf["total"] <= 0:
        return False, "در حال حاضر کانفیگ رایگان فعال نیست."
    if used >= conf["total"]:
        if conf["reset"] == RESET_NEVER or not conf["renewable"]:
            return False, "سهمیه کانفیگ رایگان شما تمام شده است."
        return False, f"سهمیه این دوره تمام شده است. بازنشانی: {RESET_LABELS.get(conf['reset'])}"

    for limit_key, days, label in (
        ("daily_limit", 1, "روزانه"),
        ("weekly_limit", 7, "هفتگی"),
        ("monthly_limit", 30, "ماهانه"),
    ):
        limit = int(conf.get(limit_key) or 0)
        if limit > 0 and _window_count(telegram_id, days) >= limit:
            return False, f"به سقف دریافت {label} رسیده‌اید. بعداً دوباره تلاش کنید."

    return True, ""


async def consume(telegram_id: int, amount: int = 1) -> bool:
    """مصرف سهمیه.

    باید *فقط بعد از* ساخت موفق کانفیگ صدا زده شود، و همیشه داخل قفل کاربر،
    تا دو درخواست هم‌زمان سهمیه را دو بار مصرف نکنند.
    """
    rec = store.TG_USERS.get(str(telegram_id))
    if rec is None:
        return False
    conf = effective(telegram_id)
    _sync_period(rec, conf["reset"])
    rec["quota_used"] = int(rec.get("quota_used") or 0) + int(amount)
    rec["configs_received"] = int(rec.get("configs_received") or 0) + int(amount)
    store.bump_stat("quota_consumed", int(amount))
    return True


async def refund(telegram_id: int, amount: int = 1) -> None:
    """برگرداندن سهمیه در صورت شکست ساخت/ارسال کانفیگ."""
    rec = store.TG_USERS.get(str(telegram_id))
    if rec is None:
        return
    rec["quota_used"] = max(0, int(rec.get("quota_used") or 0) - int(amount))
    rec["configs_received"] = max(0, int(rec.get("configs_received") or 0) - int(amount))
    logger.info("quota refunded for %s (%s)", telegram_id, amount)


async def set_user_quota(actor_id: int, telegram_id: int, **fields) -> dict:
    """Override سهمیه برای یک کاربر خاص + Audit."""
    from .. import audit
    key = str(telegram_id)
    rec = store.TG_QUOTAS.setdefault(key, {"telegram_id": telegram_id})
    allowed = {"total", "reset", "renewable", "daily_limit", "weekly_limit",
               "monthly_limit", "volume_gb", "speed_mbps", "duration_days", "ip_limit"}
    for field, value in fields.items():
        if field not in allowed:
            continue
        before = rec.get(field)
        rec[field] = value
        audit.record(actor_id, "quota_override", telegram_id, field, before, value)
    return rec


async def reset_user(actor_id: int, telegram_id: int) -> bool:
    """Reset کردن مصرف سهمیه‌ی کاربر."""
    from .. import audit
    rec = store.TG_USERS.get(str(telegram_id))
    if rec is None:
        return False
    before = int(rec.get("quota_used") or 0)
    rec["quota_used"] = 0
    rec["quota_period_key"] = period_key(effective(telegram_id)["reset"])
    audit.record(actor_id, "quota_reset", telegram_id, "quota_used", before, 0)
    return True


async def adjust(actor_id: int, telegram_id: int, delta: int) -> int | None:
    """افزایش/کاهش سقف سهمیه‌ی کاربر."""
    conf = effective(telegram_id)
    new_total = max(0, int(conf["total"]) + int(delta))
    await set_user_quota(actor_id, telegram_id, total=new_total)
    return new_total
