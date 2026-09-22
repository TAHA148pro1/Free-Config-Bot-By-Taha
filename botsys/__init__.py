"""VodiWalker Telegram subsystem.

لایه‌بندی:
    Telegram Handler  ->  Service Layer  ->  Store / Existing panel services (main.py)

هیچ Handler ای مستقیماً به دیتای خام دست نمی‌زند و هیچ Business Logic ای داخل
Handler نوشته نمی‌شود. ساخت/حذف کانفیگ هم *فقط* از طریق سرویس‌های موجود پنل
(make_link / add_client_to_inbound / remove_link) انجام می‌شود تا سیستم موازی
ساخته نشود.
"""

__all__ = ["settings", "store", "tgapi", "keyboards", "security", "audit"]
