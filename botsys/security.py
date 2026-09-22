"""امنیت زیرسیستم ربات: Role/Permission، امضای Callback، Rate Limit.

اصول:
  * Super Admin از Admin معمولی جداست و Permission کامل (*) دارد.
  * Permission ها دانه‌درشت‌اند تا بعداً بدون بازنویسی قابل تفکیک باشند.
  * هر Callback Data امضای HMAC کوتاه دارد، پس دستکاری آن قابل تشخیص است.
  * Rate Limit روی هر کاربر و روی دستورات حساس اعمال می‌شود.
  * دسترسی به منابع همیشه با مالکیت بررسی می‌شود (جلوگیری از IDOR).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import defaultdict, deque

from . import settings as cfg
from . import store

# ── Permissions ───────────────────────────────────────────────────────────────
MANAGE_USERS = "MANAGE_USERS"
MANAGE_CONFIGS = "MANAGE_CONFIGS"
MANAGE_QUOTA = "MANAGE_QUOTA"
MANAGE_CHANNELS = "MANAGE_CHANNELS"
VIEW_ANALYTICS = "VIEW_ANALYTICS"
SEND_BROADCAST = "SEND_BROADCAST"
MANAGE_SETTINGS = "MANAGE_SETTINGS"
MANAGE_ADMINS = "MANAGE_ADMINS"
MANAGE_SUPPORT = "MANAGE_SUPPORT"

ALL_BOT_PERMISSIONS = (
    MANAGE_USERS, MANAGE_CONFIGS, MANAGE_QUOTA, MANAGE_CHANNELS,
    VIEW_ANALYTICS, SEND_BROADCAST, MANAGE_SETTINGS, MANAGE_ADMINS,
    MANAGE_SUPPORT,
)

PERMISSION_LABELS = {
    MANAGE_USERS: "مدیریت کاربران",
    MANAGE_CONFIGS: "مدیریت کانفیگ‌ها",
    MANAGE_QUOTA: "مدیریت سهمیه",
    MANAGE_CHANNELS: "مدیریت کانال‌ها",
    VIEW_ANALYTICS: "مشاهده آمار",
    SEND_BROADCAST: "ارسال پیام همگانی",
    MANAGE_SETTINGS: "مدیریت تنظیمات",
    MANAGE_ADMINS: "مدیریت ادمین‌ها",
    MANAGE_SUPPORT: "پاسخ به پشتیبانی",
}

ROLE_USER = "user"
ROLE_ADMIN = "admin"
ROLE_SUPER_ADMIN = "super_admin"


# ── Super admin / admin resolution ────────────────────────────────────────────
def env_super_admins() -> set[int]:
    """Super Admin های تعیین‌شده از Environment.

    این‌ها Bootstrap هستند: همیشه دسترسی کامل دارند و از داخل ربات قابل حذف
    نیستند، تا هیچ‌وقت قفل‌شدگی کامل پنل رخ ندهد.
    """
    ids = cfg.parse_id_list(cfg.env(cfg.SUPER_ADMIN_ENV))
    if not ids:
        # سازگاری با نسخه‌ی قبلی پنل که فقط TELEGRAM_ADMIN_IDS داشت.
        ids = cfg.parse_id_list(cfg.env(cfg.ADMIN_ENV))
    return ids


def is_super_admin(telegram_id: int) -> bool:
    if telegram_id in env_super_admins():
        return True
    rec = store.TG_USERS.get(str(telegram_id))
    return bool(rec and rec.get("role") == ROLE_SUPER_ADMIN)


def is_admin(telegram_id: int) -> bool:
    if is_super_admin(telegram_id):
        return True
    rec = store.TG_USERS.get(str(telegram_id))
    return bool(rec and rec.get("role") in (ROLE_ADMIN, ROLE_SUPER_ADMIN))


def role_of(telegram_id: int) -> str:
    if is_super_admin(telegram_id):
        return ROLE_SUPER_ADMIN
    rec = store.TG_USERS.get(str(telegram_id))
    return (rec or {}).get("role") or ROLE_USER


def permissions_of(telegram_id: int) -> set[str]:
    """Permission های مؤثر یک کاربر."""
    if is_super_admin(telegram_id):
        return set(ALL_BOT_PERMISSIONS)
    rec = store.TG_USERS.get(str(telegram_id)) or {}

    # Override مستقیم روی کاربر مقدم است.
    # اگر کلید permissions وجود داشته باشد، حتی لیست خالی هم عمداً یعنی
    # «هیچ دسترسی»؛ این امکان برای مدیریت دقیق دسترسی‌های هر ادمین لازم است.
    if rec.get("permissions_override") is True and isinstance(rec.get("permissions"), list):
        explicit = rec.get("permissions") or []
        if "*" in explicit:
            return set(ALL_BOT_PERMISSIONS)
        return {p for p in explicit if p in ALL_BOT_PERMISSIONS}

    role = store.TG_ROLES.get(rec.get("role") or ROLE_USER) or {}
    perms = role.get("permissions") or []
    if "*" in perms:
        return set(ALL_BOT_PERMISSIONS)
    return {p for p in perms if p in ALL_BOT_PERMISSIONS}


def has_permission(telegram_id: int, permission: str) -> bool:
    return permission in permissions_of(telegram_id)


def is_blocked(telegram_id: int) -> bool:
    rec = store.TG_USERS.get(str(telegram_id))
    return bool(rec and rec.get("is_blocked"))


# ── Telegram ID validation ────────────────────────────────────────────────────
def valid_telegram_id(value) -> int | None:
    """Telegram User ID را اعتبارسنجی می‌کند. شناسه‌های کاربر همیشه مثبت‌اند."""
    try:
        num = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if num <= 0 or num > 1 << 53:
        return None
    return num


# ── Callback signing ──────────────────────────────────────────────────────────
def _signing_key() -> bytes:
    key = cfg.CALLBACK_SIGNING_KEY
    if not key:
        try:  # کلید پنل؛ روی Volume پایدار است و با Restart عوض نمی‌شود.
            from main import SECRET_KEY as panel_secret
            key = panel_secret
        except Exception:
            key = "vodiwalker-fallback"
    return str(key).encode("utf-8")


def sign(payload: str) -> str:
    """امضای کوتاه (۸ کاراکتر) برای Callback Data.

    محدودیت ۶۴ بایتی Callback Data اجازه‌ی امضای بلند نمی‌دهد؛ ۸ کاراکتر hex
    برای جلوگیری از دستکاری دستی کاملاً کافی است چون کلید سمت سرور مخفی است.
    """
    digest = hmac.new(_signing_key(), payload.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:8]


def sign_callback(payload: str) -> str:
    """payload -> 'payload~sig' (برای عملیات حساس)."""
    return f"{payload}~{sign(payload)}"


def verify_callback(data: str) -> str | None:
    """امضا را بررسی می‌کند و payload را برمی‌گرداند؛ در صورت دستکاری None."""
    if "~" not in data:
        return None
    payload, _, provided = data.rpartition("~")
    if not payload or not hmac.compare_digest(provided, sign(payload)):
        return None
    return payload


# ── Rate limiting ─────────────────────────────────────────────────────────────
_HITS: dict[str, deque] = defaultdict(deque)


def rate_limit(key: str, limit: int, window: float) -> bool:
    """True یعنی مجاز است، False یعنی از سقف گذشته.

    پنجره‌ی لغزان ساده و بدون وابستگی خارجی.
    """
    if limit <= 0:
        return True
    now = time.monotonic()
    bucket = _HITS[key]
    while bucket and bucket[0] <= now - window:
        bucket.popleft()
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    if len(_HITS) > 20000:  # جلوگیری از رشد بی‌نهایت حافظه
        for stale in list(_HITS)[:5000]:
            if not _HITS[stale]:
                _HITS.pop(stale, None)
    return True


#: عملیات حساسی که حتی برای ادمین هم سقف دارد.
#: دلیل: این‌ها ارسال انبوه واقعی به سمت تلگرام‌اند و سقف‌شان محافظت از خود
#: ادمین در برابر 429 رسمی تلگرام است، نه محدودسازی پنل. بقیه‌ی اکشن‌های
#: مدیریتی (Navigation، Toggle، Back، ساخت/ویرایش) برای ادمین آزادند.
ADMIN_LIMITED_ACTIONS = frozenset({"broadcast"})


def rate_limit_exempt(telegram_id: int) -> bool:
    """آیا این کاربر از Rate Limit *داخلی* پروژه مستثنا است؟

    Admin و Super Admin باید بتوانند با هر سرعتی بین منوها (Back / Settings /
    Users / Configs / Channels / ...) جابه‌جا شوند، پس هیچ سقف داخلی‌ای روی
    آن‌ها اعمال نمی‌شود. این هیچ ربطی به محدودیت رسمی Telegram API ندارد؛ آن
    همچنان در tgapi (throttle سراسری + Retry روی 429) رعایت می‌شود.
    """
    return is_admin(telegram_id)


def allow_action(telegram_id: int) -> bool:
    """Rate Limit عمومی هر کاربر (ضد اسپم /start و کلیک پشت‌سرهم).

    ادمین‌ها مستثنا هستند؛ سقف برای کاربران عادی دست‌نخورده باقی می‌ماند.
    """
    if rate_limit_exempt(telegram_id):
        return True
    limit = int(store.setting("rate_limit_per_minute", 20) or 0)
    return rate_limit(f"act:{telegram_id}", limit, 60.0)


def allow_sensitive(telegram_id: int, action: str, limit: int = 5, window: float = 60.0) -> bool:
    """Rate Limit سخت‌گیرانه برای دستورات حساس (دریافت کانفیگ، حذف، Broadcast).

    برای ادمین‌ها فقط ADMIN_LIMITED_ACTIONS سقف دارد؛ باقی اکشن‌ها آزادند.
    """
    if rate_limit_exempt(telegram_id) and action not in ADMIN_LIMITED_ACTIONS:
        return True
    return rate_limit(f"sens:{telegram_id}:{action}", limit, window)


# ── Ownership / IDOR guards ───────────────────────────────────────────────────
def owns_config(telegram_id: int, user_config_id: str) -> bool:
    """بررسی مالکیت کانفیگ. جلوگیری از IDOR: کاربر عادی حتی با ساختن Callback
    دستی هم نمی‌تواند کانفیگ کاربر دیگری را ببیند یا بفرستد."""
    rec = store.TG_USER_CONFIGS.get(str(user_config_id))
    if not rec:
        return False
    return str(rec.get("telegram_id")) == str(telegram_id)


def owns_ticket(telegram_id: int, ticket_id: str) -> bool:
    rec = store.TG_TICKETS.get(str(ticket_id))
    if not rec:
        return False
    return str(rec.get("telegram_id")) == str(telegram_id)
