"""ساخت کیبوردها با استفاده از قابلیت رسمی `style` در Telegram Bot API.

مطابق مستندات رسمی، InlineKeyboardButton و KeyboardButton فیلد اختیاری `style`
دارند که یکی از مقادیر زیر را می‌پذیرد:

    primary  -> آبی
    success  -> سبز
    danger   -> قرمز

هیچ ایموجی‌ای به‌عنوان جایگزین رنگ استفاده نمی‌شود و هیچ روش غیررسمی‌ای به کار
نمی‌رود. ایموجی‌ها فقط نقش آیکون معنایی دارند.

نکته‌ی سازگاری: `style` یک فیلد اختیاری است، پس کلاینت‌های قدیمی‌تر فقط رنگ را
نادیده می‌گیرند و دکمه کاملاً سالم کار می‌کند.
"""

from __future__ import annotations

PRIMARY = "primary"
SUCCESS = "success"
DANGER = "danger"

_VALID_STYLES = {PRIMARY, SUCCESS, DANGER}


def btn(text: str, callback_data: str, style: str | None = None, **extra) -> dict:
    """یک InlineKeyboardButton می‌سازد.

    style فقط وقتی به payload اضافه می‌شود که مقدار معتبری داشته باشد، تا هیچ‌وقت
    فیلد نامعتبر به API فرستاده نشود.
    """
    button: dict = {"text": text, "callback_data": callback_data}
    if style in _VALID_STYLES:
        button["style"] = style
    button.update(extra)
    return button


def url_btn(text: str, url: str, style: str | None = None) -> dict:
    button: dict = {"text": text, "url": url}
    if style in _VALID_STYLES:
        button["style"] = style
    return button


#: سقف رسمی فیلد text در CopyTextButton مطابق مستندات Bot API.
COPY_TEXT_MAX = 256


def copy_fits(payload: str) -> bool:
    """آیا این مقدار در دکمه‌ی کپی رسمی جا می‌شود؟"""
    return 0 < len(str(payload or "")) <= COPY_TEXT_MAX


def copy_btn(text: str, payload: str, style: str | None = None) -> dict | None:
    """دکمه‌ی کپی رسمی تلگرام (CopyTextButton).

    کلیک روی آن *فقط* متن را در Clipboard می‌گذارد: هیچ Callback ای نمی‌فرستد،
    هیچ پیام جدیدی نمی‌سازد و Keyboard را عوض نمی‌کند.

    مهم: قبلاً payload با `[:256]` بریده می‌شد. برای یک لینک ساب مشکلی نبود، ولی
    یک کانفیگ خام (vless/vmess/trojan) معمولاً بلندتر از ۲۵۶ کاراکتر است و
    بریدن آن یعنی کاربر یک کانفیگ **خراب** را کپی می‌کرد. حالا به‌جای بریدن
    بی‌صدا، None برمی‌گردد تا فراخوان تصمیم بگیرد (fallback رسمی).
    """
    payload = str(payload or "")
    if not copy_fits(payload):
        return None
    button: dict = {"text": text, "copy_text": {"text": payload}}
    if style in _VALID_STYLES:
        button["style"] = style
    return button


def toggle(text: str, callback_data: str, is_on: bool) -> dict:
    """دکمه‌ی روشن/خاموش با رنگ رسمی وضعیت.

    قاعده (بدون هیچ ایموجی به‌عنوان جایگزین رنگ):
        فعال   -> style = success (سبز)
        غیرفعال -> style = danger  (قرمز)

    وضعیت در متن دکمه هم نوشته می‌شود تا کلاینت‌های قدیمی که style را نادیده
    می‌گیرند هم قابل استفاده بمانند.
    """
    state = "فعال" if is_on else "غیرفعال"
    return btn(f"{text} · {state}", callback_data, SUCCESS if is_on else DANGER)


def _clean(row) -> list:
    """دکمه‌های None را حذف می‌کند (مثلاً وقتی copy_btn جا نشده)."""
    return [button for button in (row or []) if button]


def markup(*rows) -> dict:
    """ردیف‌های خالی/None را حذف می‌کند تا هیچ‌وقت کیبورد نامعتبر ساخته نشود."""
    return {"inline_keyboard": [_clean(row) for row in rows if _clean(row)]}


def kb(rows: list) -> dict:
    return {"inline_keyboard": [_clean(row) for row in rows if _clean(row)]}


# ── دکمه‌های پرتکرار با استایل معنایی یکسان در کل ربات ──────────────────────
def back(callback_data: str = "u:menu", text: str = "⬅️ بازگشت") -> dict:
    """دکمه‌ی بازگشت: در هر صفحه‌ای وجود دارد (الزام UX)."""
    return btn(text, callback_data, PRIMARY)


def cancel(callback_data: str, text: str = "❌ لغو") -> dict:
    """لغو عملیات: مطابق درخواست با style قرمز."""
    return btn(text, callback_data, DANGER)


def confirm(callback_data: str, text: str = "✅ تأیید") -> dict:
    return btn(text, callback_data, SUCCESS)


def danger(text: str, callback_data: str) -> dict:
    return btn(text, callback_data, DANGER)


def add(text: str, callback_data: str) -> dict:
    return btn(text, callback_data, SUCCESS)


def enter(text: str, callback_data: str) -> dict:
    """ورود به یک بخش -> primary."""
    return btn(text, callback_data, PRIMARY)


def pager(prefix: str, page: int, total: int, page_size: int) -> list:
    """ردیف صفحه‌بندی. prefix باید با ':' تمام نشود؛ شماره صفحه به آن چسبانده می‌شود."""
    row = []
    if page > 0:
        row.append(btn("◀️ قبلی", f"{prefix}:{page - 1}", PRIMARY))
    if (page + 1) * page_size < total:
        row.append(btn("بعدی ▶️", f"{prefix}:{page + 1}", PRIMARY))
    return row


def confirm_row(ok_data: str, cancel_data: str,
                ok_text: str = "✅ بله، انجام بده",
                cancel_text: str = "❌ لغو") -> list:
    """ردیف تأیید دومرحله‌ای برای عملیات حساس."""
    return [confirm(ok_data, ok_text), cancel(cancel_data, cancel_text)]


# ── منوی کاربر عادی ─────────────────────────────────────────────────────────
def user_main(support_enabled: bool = True) -> dict:
    """منوی اصلی کاربر: ساده و شبیه چیدمانی که خواسته شده."""
    rows = [
        [btn("🎁 دریافت کانفیگ رایگان", "u:free:menu", SUCCESS)],
        [btn("📦 کانفیگ‌های من", "u:cfgs:0", PRIMARY),
         btn("📊 وضعیت حساب", "u:acct", PRIMARY)],
        [btn("📚 آموزش اتصال", "u:help", PRIMARY)],
    ]
    if support_enabled:
        rows[-1].append(btn("🛟 پشتیبانی", "u:support", PRIMARY))
    return kb(rows)


def admin_main(is_super_admin: bool = False) -> dict:
    """صفحه‌ی اصلی پنل ادمین؛ ابزار Backup فقط برای Super Admin نمایش داده می‌شود."""
    rows = [
        [btn("👤 کاربران", "a:users", PRIMARY), btn("📦 کانفیگ‌ها", "a:cfgs", PRIMARY)],
        [btn("🎁 سهمیه‌ها", "a:quota", PRIMARY), btn("📢 کانال‌ها", "a:chans", PRIMARY)],
        [btn("📊 آمار", "a:stats", PRIMARY), btn("⚙️ تنظیمات", "a:settings", PRIMARY)],
        [btn("📨 پیام همگانی", "a:bc", PRIMARY), btn("🛟 تیکت‌ها", "a:tickets", PRIMARY)],
    ]
    if is_super_admin:
        rows.append([btn("💾 بکاپ / ریستور", "a:backup", PRIMARY)])
    rows += [
        [btn("🧩 ابزارهای پیشرفته", "menu", PRIMARY)],
        [btn("🙋 منوی کاربری", "u:menu", PRIMARY)],
    ]
    return kb(rows)
