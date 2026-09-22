"""تنظیمات ربات: Environment Variables + تنظیمات قابل ویرایش از پنل.

قاعده: هیچ مقدار حساسی Hard-code نمی‌شود. توکن فقط از Environment یا از تنظیمات
ذخیره‌شده‌ی پنل خوانده می‌شود، و هیچ‌وقت داخل کد یا Git قرار نمی‌گیرد.
"""

from __future__ import annotations

import os
import secrets

# ── Environment ───────────────────────────────────────────────────────────────

def env(name: str, default: str = "") -> str:
    return (os.environ.get(name, default) or "").strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name) or default)
    except ValueError:
        return default


def parse_id_list(raw: str | None) -> set[int]:
    """'111, 222' -> {111, 222}. مقادیر نامعتبر بی‌صدا نادیده گرفته می‌شوند."""
    out: set[int] = set()
    for chunk in (raw or "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        neg = chunk.startswith("-")
        digits = chunk[1:] if neg else chunk
        if digits.isdigit():
            out.add(int(chunk))
    return out


BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
SUPER_ADMIN_ENV = "TELEGRAM_SUPER_ADMIN_IDS"
ADMIN_ENV = "TELEGRAM_ADMIN_IDS"

#: حالت دریافت آپدیت: webhook | polling | auto
UPDATE_MODE = (env("TELEGRAM_UPDATE_MODE", "auto") or "auto").lower()
WEBHOOK_URL = env("TELEGRAM_WEBHOOK_URL")
#: مسیر Webhook با یک Secret تصادفی محافظت می‌شود تا کسی نتواند Update جعلی بفرستد.
WEBHOOK_SECRET = env("TELEGRAM_WEBHOOK_SECRET") or secrets.token_urlsafe(24)

#: سقف تعداد پیام در ثانیه برای Broadcast (محدودیت واقعی تلگرام ~30/s است).
BROADCAST_RATE = env_int("TELEGRAM_BROADCAST_RATE", 20)

#: کلید امضای Callback Data (برای جلوگیری از دستکاری). اگر ست نشود از SECRET_KEY پنل استفاده می‌شود.
CALLBACK_SIGNING_KEY = env("TELEGRAM_CALLBACK_SECRET")


# ── تنظیمات قابل ویرایش از پنل (پیش‌فرض‌ها) ──────────────────────────────────
#: همه‌ی این‌ها از پنل وب و از داخل ربات قابل تغییرند؛ این‌ها فقط Seed اولیه‌اند.
DEFAULT_BOT_SETTINGS: dict = {
    # ── سهمیه کانفیگ رایگان ──
    "free_quota_total": 1,              # چند کانفیگ رایگان برای هر کاربر
    "free_quota_reset": "never",        # never | daily | weekly | monthly
    "free_quota_renewable": False,      # آیا بعد از Reset دوباره سهمیه می‌گیرد
    "free_daily_limit": 1,              # سقف دریافت روزانه
    "free_weekly_limit": 0,             # 0 = بی‌اثر
    "free_monthly_limit": 0,
    # ── مشخصات کانفیگ رایگان ──
    "free_volume_gb": 10.0,             # حجم هر کانفیگ رایگان (گیگابایت)
    "free_speed_mbps": 0.0,             # 0 = نامحدود
    "free_duration_days": 30,           # مدت اعتبار
    "free_ip_limit": 1,                 # سقف آی‌پی هم‌زمان
    "free_protocol": "",                # خالی = پیش‌فرض پنل
    "free_port": 0,                     # 0 = پیش‌فرض پنل
    # ── رفتار ربات ──
    "require_channels": True,           # اجبار عضویت در کانال‌ها
    "maintenance_mode": False,
    "support_enabled": True,
    "rate_limit_per_minute": 20,        # سقف اکشن در دقیقه برای هر کاربر
    "free_cooldown_seconds": 15,        # فاصله اجباری بین دو دریافت کانفیگ رایگان
    "broadcast_rate": 20,               # سقف پیام در ثانیه برای Broadcast
}

#: متن‌های قابل ویرایش (از پنل). کلیدها با BOT_TEXTS موجود در main.py یکی هستند.
DEFAULT_BOT_TEXTS: dict = {
    "welcome_user": (
        "👋 خوش آمدید!\n\n"
        "از این ربات می‌توانید کانفیگ رایگان دریافت کنید و کانفیگ‌های خود را مدیریت کنید.\n"
        "برای شروع یکی از گزینه‌های زیر را انتخاب کنید."
    ),
    "need_channels": "برای دریافت کانفیگ رایگان لازم است در کانال‌های زیر عضو شوید:",
    "quota_exhausted": "سهمیه کانفیگ رایگان شما تمام شده است.",
    "blocked": "دسترسی شما به ربات محدود شده است. در صورت اشکال با پشتیبانی تماس بگیرید.",
    "maintenance": "ربات موقتاً در حال به‌روزرسانی است. کمی بعد دوباره تلاش کنید.",
    "support_intro": "پیام خود را بنویسید و ارسال کنید. پشتیبانی در اسرع وقت پاسخ می‌دهد.",
    "tutorial_intro": "سیستم‌عامل خود را انتخاب کنید تا آموزش اتصال نمایش داده شود.",
    "tutorial_android": (
        "<b>Android</b>\n\n"
        "1. برنامه v2rayNG را نصب کنید.\n"
        "2. لینک کانفیگ را کپی کنید.\n"
        "3. در برنامه روی + بزنید و «Import from clipboard» را انتخاب کنید.\n"
        "4. کانفیگ را انتخاب و دکمه اتصال را بزنید."
    ),
    "tutorial_ios": (
        "<b>iOS</b>\n\n"
        "1. برنامه Streisand یا V2Box را نصب کنید.\n"
        "2. لینک کانفیگ را کپی کنید.\n"
        "3. در برنامه گزینه Import from clipboard را بزنید.\n"
        "4. کانفیگ را انتخاب و متصل شوید."
    ),
    "tutorial_windows": (
        "<b>Windows</b>\n\n"
        "1. برنامه v2rayN را دانلود و اجرا کنید.\n"
        "2. لینک کانفیگ را کپی کنید.\n"
        "3. با Ctrl+V کانفیگ را Import کنید.\n"
        "4. روی آیکون برنامه راست‌کلیک و System Proxy را فعال کنید."
    ),
    "tutorial_macos": (
        "<b>macOS</b>\n\n"
        "1. برنامه V2Box یا FoXray را نصب کنید.\n"
        "2. لینک کانفیگ را کپی کنید.\n"
        "3. Import from clipboard را بزنید و متصل شوید."
    ),
}
