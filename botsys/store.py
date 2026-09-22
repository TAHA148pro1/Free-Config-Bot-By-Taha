"""State store ربات + سیستم Migration.

پروژه‌ی فعلی دیتابیس SQL و ORM ندارد؛ کل State داخل dict های in-memory است که
به‌صورت atomic در یک فایل JSON روی Volume ریلوی ذخیره می‌شود (main.save_state).
بنابراین «Migration» اینجا به شکل Schema-Version روی همان Store پیاده شده است:
هر نسخه یک تابع upgrade دارد و ترتیبی اجرا می‌شود. این کار داده‌ی موجود را
حفظ می‌کند و با Restart شدن ریلوی چیزی از دست نمی‌رود.

موجودیت‌ها (معادل جداول):
    TG_USERS        : User   (کلید اصلی = Telegram User ID)
    TG_ROLES        : Role   (نقش‌های سفارشی + Permission)
    TG_USER_CONFIGS : UserConfig (اتصال کاربر تلگرام به کانفیگ واقعی پنل/LINKS)
    TG_QUOTAS       : Quota  (Override سهمیه برای کاربر خاص)
    TG_CHANNELS     : Channel (کانال‌های اجباری)
    TG_TICKETS      : SupportTicket
    TG_MESSAGES     : SupportMessage
    TG_BROADCASTS   : Broadcast
    TG_AUDIT        : AuditLog
    TG_STATS        : آمار روزانه
    TG_SETTINGS     : تنظیمات قابل ویرایش
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone

from .settings import DEFAULT_BOT_SETTINGS, DEFAULT_BOT_TEXTS

SCHEMA_VERSION = 6

# ── Collections ───────────────────────────────────────────────────────────────
TG_USERS: dict[str, dict] = {}
TG_ROLES: dict[str, dict] = {}
TG_USER_CONFIGS: dict[str, dict] = {}
TG_QUOTAS: dict[str, dict] = {}
TG_CHANNELS: dict[str, dict] = {}
TG_TICKETS: dict[str, dict] = {}
TG_MESSAGES: dict[str, dict] = {}
TG_BROADCASTS: dict[str, dict] = {}
TG_AUDIT: deque = deque(maxlen=5000)
TG_STATS: dict[str, dict] = {}
TG_SETTINGS: dict = dict(DEFAULT_BOT_SETTINGS)
TG_TEXTS: dict = dict(DEFAULT_BOT_TEXTS)

_meta: dict = {"schema_version": 0}

# قفل‌ها: هر کاربر یک قفل مستقل دارد تا عملیات سهمیه‌ای atomic بمانند و
# Double-Click سهمیه را دو بار مصرف نکند.
_USER_LOCKS: dict[int, asyncio.Lock] = {}
STORE_LOCK = asyncio.Lock()


def user_lock(telegram_id: int) -> asyncio.Lock:
    lock = _USER_LOCKS.get(telegram_id)
    if lock is None:
        lock = asyncio.Lock()
        _USER_LOCKS[telegram_id] = lock
    return lock


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def next_id(collection: dict) -> str:
    """شناسه‌ی عددی یکتا و پایدار برای رکوردهای جدید."""
    top = 0
    for key in collection:
        try:
            top = max(top, int(key))
        except (TypeError, ValueError):
            continue
    return str(top + 1)


# ── Settings accessors ────────────────────────────────────────────────────────
def setting(key: str, default=None):
    if key in TG_SETTINGS:
        return TG_SETTINGS[key]
    return DEFAULT_BOT_SETTINGS.get(key, default)


def set_setting(key: str, value) -> None:
    TG_SETTINGS[key] = value


def text(key: str, fallback: str = "") -> str:
    """متن قابل ویرایش ربات. اولویت: مقدار ذخیره‌شده -> پیش‌فرض کد -> fallback."""
    value = TG_TEXTS.get(key)
    if value:
        return str(value)
    return str(DEFAULT_BOT_TEXTS.get(key) or fallback)


def set_text(key: str, value: str) -> None:
    TG_TEXTS[key] = str(value or "")


# ── Migrations ────────────────────────────────────────────────────────────────
def _mig_1_bootstrap() -> None:
    """نسخه ۱: ساخت نقش‌های پایه."""
    TG_ROLES.setdefault("super_admin", {
        "id": "super_admin",
        "name": "Super Admin",
        "permissions": ["*"],
        "builtin": True,
    })
    TG_ROLES.setdefault("admin", {
        "id": "admin",
        "name": "Admin",
        "permissions": [
            "MANAGE_USERS", "MANAGE_CONFIGS", "MANAGE_QUOTA",
            "VIEW_ANALYTICS", "MANAGE_CHANNELS", "MANAGE_SUPPORT",
        ],
        "builtin": True,
    })
    TG_ROLES.setdefault("user", {
        "id": "user", "name": "User", "permissions": [], "builtin": True,
    })


def _mig_2_user_fields() -> None:
    """نسخه ۲: تکمیل فیلدهای کاربر برای رکوردهای قدیمی."""
    for rec in TG_USERS.values():
        rec.setdefault("role", "user")
        rec.setdefault("is_blocked", False)
        rec.setdefault("configs_received", 0)
        rec.setdefault("quota_used", 0)
        rec.setdefault("first_seen", rec.get("created_at") or now_iso())
        rec.setdefault("last_seen", rec.get("first_seen"))
        rec.setdefault("channels_ok", False)
        rec.setdefault("channels_checked_at", None)


def _mig_3_channel_order() -> None:
    """نسخه ۳: تضمین وجود ترتیب نمایش برای کانال‌ها."""
    for idx, rec in enumerate(TG_CHANNELS.values()):
        rec.setdefault("sort_order", idx)
        rec.setdefault("is_active", True)


def _mig_4_admin_support_permission() -> None:
    """نسخه ۴: پاسخ به تیکت بخشی از کار ادمین معمولی است.

    بدون این دسترسی، ادمین‌ها اعلان تیکت جدید دریافت نمی‌کردند.
    """
    role = TG_ROLES.get("admin")
    if role and "*" not in (role.get("permissions") or []):
        perms = list(role.get("permissions") or [])
        if "MANAGE_SUPPORT" not in perms:
            perms.append("MANAGE_SUPPORT")
            role["permissions"] = perms


def _mig_5_membership_state() -> None:
    """نسخه ۵: فیلدهای پایش عضویت و علامت‌گذاری قطع خودکار کانفیگ‌ها."""
    for rec in TG_USERS.values():
        rec.setdefault("membership_blocked", False)
    for rec in TG_USER_CONFIGS.values():
        rec.setdefault("auto_disabled_by_membership", False)



def _mig_6_admin_permission_overrides() -> None:
    """نسخه ۶: دسترسی ادمین‌ها به‌صورت Override صریح ذخیره می‌شود.

    ادمین‌های قدیمی که permissions خالی داشتند از نقش Admin ارث می‌بردند؛
    برای اینکه «خالی» از این به بعد واقعاً به معنی بدون دسترسی باشد، مقدار
    خالی قدیمی به دسترسی‌های پیش‌فرض نقش Admin تبدیل می‌شود.
    """
    admin_role = TG_ROLES.get("admin") or {}
    defaults = list(admin_role.get("permissions") or [])
    for rec in TG_USERS.values():
        if rec.get("role") == "admin" and rec.get("permissions") == []:
            rec["permissions"] = list(defaults)
            rec["permissions_override"] = False


MIGRATIONS = {
    1: _mig_1_bootstrap,
    2: _mig_2_user_fields,
    3: _mig_3_channel_order,
    4: _mig_4_admin_support_permission,
    5: _mig_5_membership_state,
    6: _mig_6_admin_permission_overrides,
}


def run_migrations(logger=None) -> int:
    """Migration های اجرانشده را به ترتیب اجرا می‌کند. Idempotent است."""
    current = int(_meta.get("schema_version") or 0)
    applied = 0
    for version in sorted(MIGRATIONS):
        if version <= current:
            continue
        MIGRATIONS[version]()
        _meta["schema_version"] = version
        applied += 1
        if logger:
            logger.info("botsys migration %s applied", version)
    return applied


# ── Persistence (hooks into main.load_state / main.save_state) ────────────────
_EXPORT_MAP = {
    "tg_users": lambda: TG_USERS,
    "tg_roles": lambda: TG_ROLES,
    "tg_user_configs": lambda: TG_USER_CONFIGS,
    "tg_quotas": lambda: TG_QUOTAS,
    "tg_channels": lambda: TG_CHANNELS,
    "tg_tickets": lambda: TG_TICKETS,
    "tg_messages": lambda: TG_MESSAGES,
    "tg_broadcasts": lambda: TG_BROADCASTS,
    "tg_stats": lambda: TG_STATS,
}


def export_state() -> dict:
    """بخش ربات را برای نوشتن در فایل State پنل برمی‌گرداند."""
    payload = {key: dict(getter()) for key, getter in _EXPORT_MAP.items()}
    payload["tg_settings"] = dict(TG_SETTINGS)
    payload["tg_texts"] = dict(TG_TEXTS)
    payload["tg_audit"] = list(TG_AUDIT)
    payload["tg_meta"] = dict(_meta)
    return payload


def import_state(data: dict, logger=None) -> None:
    """بخش ربات را از فایل State پنل می‌خواند و سپس Migration ها را اجرا می‌کند."""
    for key, getter in _EXPORT_MAP.items():
        loaded = data.get(key) or {}
        if isinstance(loaded, dict):
            getter().update(loaded)

    loaded_settings = data.get("tg_settings") or {}
    if isinstance(loaded_settings, dict):
        # پیش‌فرض‌های جدید کد باید همیشه وجود داشته باشند، ولی مقدار ذخیره‌شده برنده است.
        merged = dict(DEFAULT_BOT_SETTINGS)
        merged.update(loaded_settings)
        TG_SETTINGS.clear()
        TG_SETTINGS.update(merged)

    loaded_texts = data.get("tg_texts") or {}
    if isinstance(loaded_texts, dict):
        merged_texts = dict(DEFAULT_BOT_TEXTS)
        merged_texts.update({k: v for k, v in loaded_texts.items() if isinstance(v, str)})
        TG_TEXTS.clear()
        TG_TEXTS.update(merged_texts)

    for row in (data.get("tg_audit") or []):
        TG_AUDIT.append(row)

    meta = data.get("tg_meta") or {}
    if isinstance(meta, dict):
        _meta.update(meta)

    run_migrations(logger)


def schema_version() -> int:
    return int(_meta.get("schema_version") or 0)


# ── Daily stats ───────────────────────────────────────────────────────────────
def bump_stat(field: str, amount: int = 1) -> None:
    """آمار روزانه‌ی ربات. جدا از DAILY_STATS پنل نگه داشته می‌شود تا با آن تداخل نکند."""
    day = TG_STATS.setdefault(today_key(), {})
    day[field] = int(day.get(field, 0) or 0) + amount
    # نگه‌داشتن ۱۸۰ روز اخیر تا فایل State بی‌نهایت بزرگ نشود.
    if len(TG_STATS) > 180:
        for stale in sorted(TG_STATS)[:-180]:
            TG_STATS.pop(stale, None)


def stats_range(days: int = 7) -> dict:
    keys = sorted(TG_STATS)[-days:]
    out: dict[str, int] = {}
    for key in keys:
        for field, value in (TG_STATS.get(key) or {}).items():
            out[field] = out.get(field, 0) + int(value or 0)
    return out


# ── In-process idempotency guard (anti double-click) ──────────────────────────
_INFLIGHT: dict[str, float] = {}
_INFLIGHT_TTL = 30.0


def claim(key: str, ttl: float = _INFLIGHT_TTL, refresh: bool = False) -> bool:
    """اگر همین عملیات همین الان در جریان باشد False برمی‌گرداند.

    برای مقاوم‌سازی در برابر Double-Click و درخواست‌های هم‌زمان استفاده می‌شود.
    با refresh=True مهلت یک قفلِ در اختیار خودمان تمدید می‌شود (برای Cooldown).
    """
    now = time.monotonic()
    for stale, expires in list(_INFLIGHT.items()):
        if expires <= now:
            _INFLIGHT.pop(stale, None)
    if not refresh and _INFLIGHT.get(key, 0) > now:
        return False
    if ttl <= 0:
        _INFLIGHT.pop(key, None)
        return True
    _INFLIGHT[key] = now + ttl
    return True


def release(key: str) -> None:
    _INFLIGHT.pop(key, None)
