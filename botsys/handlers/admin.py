"""پنل مدیریتی داخل تلگرام.

ساختار: یک داشبورد ساده با ۶+۲ بخش اصلی و زیرمنو داخل هر بخش.
هر اکشن حساس دو شرط دارد:
    1) بررسی Permission مربوطه
    2) تأیید دومرحله‌ای با Callback امضاشده (غیرقابل جعل)
"""

from __future__ import annotations

import logging

from .. import audit, keyboards as k, nav, security, store
from ..services import analytics, broadcast, backups, channels, configs, issuing, quota, support, users
from ..tgapi import h

logger = logging.getLogger("vodiwalker.botsys.handlers.admin")

PAGE_SIZE = 6

#: فیلدهای عددی که ادمین می‌تواند برای کاربر تغییر دهد.
USER_FIELDS = {
    "quota": ("سهمیه (تعداد کانفیگ رایگان)", "عدد صحیح، مثلاً 3"),
    "volume": ("حجم (گیگابایت)", "عدد، مثلاً 20 یا 0 برای نامحدود"),
    "speed": ("سرعت (Mbps)", "عدد، مثلاً 10 یا 0 برای نامحدود"),
    "days": ("مدت اعتبار (روز)", "عدد صحیح، 0 برای بدون انقضا"),
    "iplimit": ("سقف آی‌پی هم‌زمان", "عدد صحیح، 0 برای نامحدود"),
}

SETTING_FIELDS = {
    "free_quota_total": ("تعداد کانفیگ رایگان هر کاربر", int),
    "free_volume_gb": ("حجم کانفیگ رایگان (GB)", float),
    "free_speed_mbps": ("سرعت کانفیگ رایگان (Mbps)", float),
    "free_duration_days": ("مدت اعتبار کانفیگ رایگان (روز)", int),
    "free_ip_limit": ("سقف آی‌پی کانفیگ رایگان", int),
    "free_daily_limit": ("سقف دریافت روزانه", int),
    "free_weekly_limit": ("سقف دریافت هفتگی", int),
    "free_monthly_limit": ("سقف دریافت ماهانه", int),
    "rate_limit_per_minute": ("سقف اکشن در دقیقه (کاربر عادی)", int),
    # این دو تا در DEFAULT_BOT_SETTINGS وجود داشتند و در سرویس‌ها هم خوانده
    # می‌شدند، ولی هیچ راهی برای تغییرشان از UI ربات نبود.
    "free_cooldown_seconds": ("فاصله اجباری بین دو دریافت (ثانیه)", int),
    "broadcast_rate": ("سقف ارسال پیام همگانی در ثانیه", int),
}

#: کلیدهایی که با MANAGE_QUOTA هم قابل تغییرند (بقیه MANAGE_SETTINGS لازم دارند).
QUOTA_SETTING_KEYS = frozenset({
    "free_quota_total", "free_daily_limit", "free_weekly_limit", "free_monthly_limit",
    "free_volume_gb", "free_speed_mbps", "free_duration_days", "free_ip_limit",
})

#: تنظیمات Boolean که با Toggle عوض می‌شوند: callback -> (کلید، عنوان، Permission)
TOGGLE_SETTINGS = {
    "a:settings:support": ("support_enabled", "🛟 پشتیبانی", security.MANAGE_SETTINGS),
    "a:settings:maint": ("maintenance_mode", "🧰 حالت تعمیر", security.MANAGE_SETTINGS),
    "a:settings:reqch": ("require_channels", "📢 الزام عضویت کانال", security.MANAGE_SETTINGS),
    "a:chans:req": ("require_channels", "📢 الزام عضویت کانال", security.MANAGE_CHANNELS),
    "a:quota:renew": ("free_quota_renewable", "🔁 تمدیدپذیری سهمیه", security.MANAGE_QUOTA),
}

TEXT_FIELDS = {
    "welcome_user": "متن خوش‌آمدگویی",
    "need_channels": "متن الزام عضویت کانال",
    "support_intro": "متن پشتیبانی",
    "tutorial_intro": "متن معرفی آموزش",
    "tutorial_android": "آموزش Android",
    "tutorial_ios": "آموزش iOS",
    "tutorial_windows": "آموزش Windows",
    "tutorial_macos": "آموزش macOS",
}

_PENDING: dict[int, dict] = {}


def set_pending(admin_id: int, value: dict) -> None:
    _PENDING[int(admin_id)] = value


def get_pending(admin_id: int) -> dict | None:
    return _PENDING.get(int(admin_id))


def clear_pending(admin_id: int) -> None:
    _PENDING.pop(int(admin_id), None)


# ── داشبورد ──────────────────────────────────────────────────────────────────
def dashboard_text(admin_id: int) -> str:
    stats = analytics.overview()
    role = "Super Admin" if security.is_super_admin(admin_id) else "Admin"
    return (
        f"🛠 <b>پنل مدیریت VodiWalker</b>\n"
        f"<i>سطح دسترسی: {role}</i>\n\n"
        f"👥 کاربران: <b>{stats['users_total']}</b> ({stats['users_active']} فعال)\n"
        f"🆕 امروز: <b>{stats['users_new_today']}</b> · این هفته: <b>{stats['users_new_week']}</b>\n"
        f"📦 کانفیگ‌ها: <b>{stats['configs_total']}</b> ({stats['configs_active']} فعال)\n"
        f"🎁 رایگان ارسال‌شده: <b>{stats['free_sent']}</b>\n"
        f"🛟 تیکت باز: <b>{stats['tickets_open']}</b>\n\n"
        "یک بخش را انتخاب کنید:"
    )


# ── مسیریابی اصلی ────────────────────────────────────────────────────────────
async def handle(client, rec: dict, chat_id: int, message_id: int | None, data: str, cb_id: str | None) -> bool:
    admin_id = int(rec["telegram_id"])

    # اکشن‌های حساس امضا دارند؛ اگر امضا معتبر نبود، رد می‌شود.
    if "~" in data:
        verified = security.verify_callback(data)
        if verified is None:
            await client.answer_callback(cb_id, "این دکمه معتبر نیست یا منقضی شده است.", alert=True)
            return True
        data = verified

    if data == "a:menu":
        clear_pending(admin_id)
        await _render(client, chat_id, message_id, dashboard_text(admin_id), k.admin_main(security.is_super_admin(admin_id)))
        return True

    # Toggle ها *قبل از* مسیریابی پیشوندی اعمال می‌شوند، وگرنه پیشوند بخش
    # (مثل 'a:settings') آن‌ها را می‌بلعد. بعد از اعمال، همان صفحه Refresh
    # می‌شود تا رنگ دکمه در همان لحظه وضعیت جدید را نشان بدهد.
    toggled = await _apply_toggle(client, admin_id, chat_id, message_id, data, cb_id)
    if toggled is not NOT_A_TOGGLE:
        return bool(toggled)

    for prefix, handler in (
        ("a:users", _users_section), ("a:usr", _user_detail_section),
        ("a:cfgs", _configs_section), ("a:cfg", _config_detail_section),
        ("a:quota", _quota_section), ("a:chans", _channels_section),
        ("a:chan", _channel_detail_section), ("a:stats", _stats_section),
        ("a:settings", _settings_section), ("a:set", _setting_edit_section),
        ("a:txt", _text_edit_section), ("a:bc", _broadcast_section),
        ("a:tickets", _tickets_section), ("a:tkt", _ticket_detail_section),
        ("a:audit", _audit_section), ("a:backup", _backup_section),
    ):
        if data == prefix or data.startswith(prefix + ":"):
            return await handler(client, admin_id, chat_id, message_id, data, cb_id)

    return False


def _deny(client, cb_id, message: str = "دسترسی لازم را ندارید."):
    return client.answer_callback(cb_id, message, alert=True)


#: صفحه‌ای که بعد از هر Toggle باید Refresh شود (همان پیام، بدون پیام جدید).
TOGGLE_REFRESH = {
    "a:settings:support": "a:settings",
    "a:settings:maint": "a:settings",
    "a:settings:reqch": "a:settings",
    "a:chans:req": "a:chans",
    # دکمه‌ی تمدیدپذیری روی صفحه‌ی «دوره بازنشانی» است، پس همان Refresh می‌شود
    # تا رنگ دکمه بلافاصله وضعیت جدید را نشان بدهد.
    "a:quota:renew": "a:quota:reset",
}

#: نشانه‌ی «این Callback یک Toggle نیست».
NOT_A_TOGGLE = object()


async def _apply_toggle(client, admin_id: int, chat_id: int, message_id: int | None,
                        data: str, cb_id: str | None):
    """یک Toggle تنظیمات را اعمال، Audit، Persist و سپس همان صفحه را Refresh می‌کند.

    ریشه‌ی مشکل قبلی: `a:settings:support` و `a:settings:maint` امضا می‌شدند و
    ظاهراً در `_setting_edit_section` مدیریت می‌شدند، ولی حلقه‌ی مسیریابی چون
    پیشوند «a:settings» را *قبل از* «a:set» بررسی می‌کرد، همیشه آن‌ها را به
    `_settings_section` می‌فرستاد؛ آنجا هیچ شرطی برایشان نبود و صفحه فقط دوباره
    رندر می‌شد. نتیجه: دکمه‌ها هیچ کاری نمی‌کردند. حالا Toggle ها در یک نقطه
    (همین تابع) و *قبل از* مسیریابی پیشوندی اعمال می‌شوند.

    خروجی NOT_A_TOGGLE یعنی این Callback یک Toggle نیست و باید به مسیریابی
    معمولی سپرده شود.
    """
    entry = TOGGLE_SETTINGS.get(data)
    if entry is None:
        return NOT_A_TOGGLE
    key, label, permission = entry
    if not security.has_permission(admin_id, permission):
        await _deny(client, cb_id)
        return True

    before = bool(store.setting(key))
    after = not before
    store.set_setting(key, after)
    audit.record(admin_id, "setting_change", None, key, before, after)
    # Persist فوری: وضعیت جدید بعد از Restart ربات هم از State خوانده می‌شود.
    await _save()
    await client.answer_callback(cb_id, f"{label}: {'فعال شد' if after else 'غیرفعال شد'}")

    page = TOGGLE_REFRESH[data]
    handler = {
        "a:settings": _settings_section,
        "a:chans": _channels_section,
        "a:quota": _quota_section,
        "a:quota:reset": _quota_section,
    }[page]
    return await handler(client, admin_id, chat_id, message_id, page, cb_id)


# ── بخش کاربران ──────────────────────────────────────────────────────────────
async def _users_section(client, admin_id, chat_id, message_id, data, cb_id) -> bool:
    if not security.has_permission(admin_id, security.MANAGE_USERS):
        await _deny(client, cb_id)
        return True

    if data == "a:users":
        await _render(client, chat_id, message_id,
                      f"👤 <b>کاربران</b>\n\nکل: {users.count()} · مسدود: {users.count(blocked=True)}",
                      k.kb([
                          [k.enter("🔍 جستجو", "a:users:search")],
                          [k.enter("🆕 کاربران اخیر", "a:users:recent:0")],
                          [k.enter("🚫 کاربران مسدود", "a:users:blocked:0")],
                          [k.enter("🛡 ادمین‌ها", "a:users:admins")],
                          [k.back("a:menu")],
                      ]))
        return True

    if data == "a:users:search":
        set_pending(admin_id, {"action": "user_search"})
        await _render(client, chat_id, message_id,
                      "🔍 Telegram ID یا Username کاربر را بفرستید:",
                      k.kb([[k.cancel("a:users")]]))
        return True

    if data.startswith("a:users:recent") or data.startswith("a:users:blocked"):
        blocked = "blocked" in data
        page = _tail_int(data)
        rows = users.recent(limit=200, blocked=True if blocked else None)
        title = "🚫 کاربران مسدود" if blocked else "🆕 کاربران اخیر"
        await _render(client, chat_id, message_id,
                      f"{title} ({len(rows)} مورد)",
                      _users_list_kb(rows, page, "a:users:blocked" if blocked else "a:users:recent"))
        return True

    if data == "a:users:admins":
        if not security.has_permission(admin_id, security.MANAGE_ADMINS):
            await _deny(client, cb_id)
            return True
        rows = users.admins()
        lines = ["🛡 <b>مدیریت ادمین‌ها</b>", ""]
        buttons = []
        for item in rows:
            tag = "👑" if item.get("role") == security.ROLE_SUPER_ADMIN else "🛠"
            tid = item.get("telegram_id")
            lines.append(f"{tag} <code>{tid}</code> · {h(users.display_name(item))}")
            if not security.is_super_admin(int(tid)):
                buttons.append([k.enter(f"⚙️ مدیریت {tid}", f"a:users:admins:{tid}")])
        buttons.append([k.add("➕ افزودن ادمین", "a:users:admins:add")])
        buttons.append([k.back("a:users")])
        await _render(client, chat_id, message_id, "\n".join(lines), k.kb(buttons))
        return True

    if data == "a:users:admins:add":
        if not security.is_super_admin(admin_id):
            await _deny(client, cb_id, "فقط Super Admin می‌تواند ادمین اضافه کند.")
            return True
        set_pending(admin_id, {"action": "admin_add"})
        await _render(client, chat_id, message_id,
                      "➕ <b>افزودن ادمین</b>\n\nTelegram ID کاربر را بفرستید.",
                      k.kb([[k.cancel("a:users:admins")]]))
        return True

    if data.startswith("a:users:admins:") and len(data.split(":")) == 4:
        if not security.is_super_admin(admin_id):
            await _deny(client, cb_id)
            return True
        target = security.valid_telegram_id(data.split(":")[3])
        if target is None or security.is_super_admin(target):
            await _deny(client, cb_id, "این ادمین از داخل ربات قابل تغییر نیست.")
            return True
        item = users.get(target)
        if not item or item.get("role") not in (security.ROLE_ADMIN, security.ROLE_SUPER_ADMIN):
            await _deny(client, cb_id, "ادمین یافت نشد.")
            return True
        perms = security.permissions_of(target)
        perm_lines = "\n".join(
            f"{'✅' if p in perms else '❌'} {security.PERMISSION_LABELS.get(p, p)}"
            for p in security.ALL_BOT_PERMISSIONS
        ) or "• بدون دسترسی"
        perm_buttons = []
        row = []
        for p in security.ALL_BOT_PERMISSIONS:
            icon = "✅" if p in perms else "❌"
            row.append(k.btn(f"{icon} {security.PERMISSION_LABELS.get(p, p)}",
                             security.sign_callback(f"a:users:admins:{target}:perm:{p}"),
                             k.SUCCESS if p in perms else k.DANGER))
            if len(row) == 2:
                perm_buttons.append(row); row = []
        if row:
            perm_buttons.append(row)
        perm_buttons.append([k.danger("🗑 حذف ادمین", security.sign_callback(f"a:users:admins:{target}:remove"))])
        perm_buttons.append([k.back("a:users:admins")])
        await _render(client, chat_id, message_id,
                      f"🛡 <b>ادمین {target}</b>\n\n<b>دسترسی‌ها:</b>\n{perm_lines}\n\nبرای تغییر هر مورد روی همان دکمه بزنید.",
                      k.kb(perm_buttons))
        return True

    if data.startswith("a:users:admins:") and ":perm:" in data:
        if not security.is_super_admin(admin_id):
            await _deny(client, cb_id)
            return True
        parts = data.split(":")
        try:
            target = security.valid_telegram_id(parts[3])
            permission = parts[5] if len(parts) > 5 else ""
        except Exception:
            target, permission = None, ""
        if target is None or security.is_super_admin(target) or permission not in security.ALL_BOT_PERMISSIONS:
            await _deny(client, cb_id, "دسترسی نامعتبر است.")
            return True
        item = users.get(target)
        if not item or item.get("role") != security.ROLE_ADMIN:
            await _deny(client, cb_id, "ادمین یافت نشد.")
            return True
        current = set(security.permissions_of(target))
        if permission in current:
            current.remove(permission)
        else:
            current.add(permission)
        item["permissions"] = [p for p in security.ALL_BOT_PERMISSIONS if p in current]
        item["permissions_override"] = True
        await _save()
        await client.answer_callback(cb_id, "دسترسی به‌روزرسانی شد.")
        # همان صفحه را با وضعیت جدید Edit کن.
        return await handle(client, rec, chat_id, message_id, f"a:users:admins:{target}", cb_id)

    if data.startswith("a:users:admins:") and data.endswith(":remove"):
        if not security.is_super_admin(admin_id):
            await _deny(client, cb_id)
            return True
        target = security.valid_telegram_id(data.split(":")[3] if len(data.split(":")) > 3 else None)
        if target is None or security.is_super_admin(target):
            await _deny(client, cb_id, "این ادمین قابل حذف نیست.")
            return True
        item = users.get(target)
        if not item or item.get("role") != security.ROLE_ADMIN:
            await _deny(client, cb_id, "ادمین یافت نشد.")
            return True
        await users.set_role(admin_id, target, security.ROLE_USER)
        item["permissions"] = []
        await _save()
        await client.answer_callback(cb_id, "ادمین حذف شد.")
        return await _users_section(client, admin_id, chat_id, message_id, "a:users:admins", cb_id)

    return False


def _users_list_kb(rows: list[dict], page: int, prefix: str) -> dict:
    start = page * PAGE_SIZE
    buttons = []
    for item in rows[start:start + PAGE_SIZE]:
        icon = "🚫" if item.get("is_blocked") else "🟢"
        buttons.append([k.enter(f"{icon} {users.display_name(item)}"[:38],
                                f"a:usr:{item.get('telegram_id')}")])
    pages = k.pager(prefix, page, len(rows), PAGE_SIZE)
    if pages:
        buttons.append(pages)
    buttons.append([k.back("a:users")])
    return k.kb(buttons)


def user_detail_text(telegram_id: int) -> str:
    rec = users.get(telegram_id)
    if not rec:
        return "کاربر یافت نشد."
    status = quota.status(telegram_id)
    my = configs.user_configs(telegram_id)
    active = sum(1 for c in my if configs.describe(c)["status"] == "active")

    lines = [
        f"👤 <b>{h(users.display_name(rec))}</b>",
        "",
        f"🆔 <code>{telegram_id}</code>",
        f"نقش: {rec.get('role')}",
        f"وضعیت: {'🚫 مسدود' if rec.get('is_blocked') else '🟢 فعال'}",
        f"اولین ورود: {str(rec.get('first_seen') or '')[:16].replace('T', ' ')}",
        f"آخرین فعالیت: {str(rec.get('last_seen') or '')[:16].replace('T', ' ')}",
        f"کانفیگ دریافتی: {rec.get('configs_received') or 0}",
        f"سهمیه: {status['remaining']} باقی از {status['total']} (مصرف {status['used']})",
        f"بازنشانی: {status['reset_label']}",
        f"کانفیگ فعال: {active} از {len(my)}",
        f"عضویت کانال‌ها: {'✅' if rec.get('channels_ok') else '❌'}",
    ]
    return "\n".join(lines)


def _user_kb(admin_id: int, telegram_id: int) -> dict:
    rec = users.get(telegram_id) or {}
    blocked = bool(rec.get("is_blocked"))
    rows = [[k.enter("📦 کانفیگ‌های کاربر", f"a:usr:{telegram_id}:cfgs:0")]]

    if security.has_permission(admin_id, security.MANAGE_QUOTA):
        rows.append([
            k.enter("🎁 تغییر سهمیه", f"a:usr:{telegram_id}:edit:quota"),
            k.btn("♻️ Reset سهمیه", security.sign_callback(f"a:usr:{telegram_id}:qreset"), k.DANGER),
        ])
    if security.has_permission(admin_id, security.MANAGE_CONFIGS):
        rows.append([
            k.enter("📊 حجم", f"a:usr:{telegram_id}:edit:volume"),
            k.enter("🚀 سرعت", f"a:usr:{telegram_id}:edit:speed"),
            k.enter("⏳ انقضا", f"a:usr:{telegram_id}:edit:days"),
        ])
        rows.append([k.add("🎁 اعطای کانفیگ", security.sign_callback(f"a:usr:{telegram_id}:grant"))])
    if security.has_permission(admin_id, security.MANAGE_USERS):
        if blocked:
            rows.append([k.add("✅ رفع مسدودی", security.sign_callback(f"a:usr:{telegram_id}:unblock"))])
        else:
            rows.append([k.danger("🚫 مسدود کردن", security.sign_callback(f"a:usr:{telegram_id}:block"))])
    rows.append([k.enter("📜 تاریخچه فعالیت", f"a:usr:{telegram_id}:audit")])
    rows.append([k.back("a:users")])
    return k.kb(rows)


async def _user_detail_section(client, admin_id, chat_id, message_id, data, cb_id) -> bool:
    if not security.has_permission(admin_id, security.MANAGE_USERS):
        await _deny(client, cb_id)
        return True

    parts = data.split(":")
    target = security.valid_telegram_id(parts[2] if len(parts) > 2 else None)
    if target is None:
        await _deny(client, cb_id, "شناسه کاربر نامعتبر است.")
        return True
    action = parts[3] if len(parts) > 3 else ""

    if not action:
        await _render(client, chat_id, message_id, user_detail_text(target), _user_kb(admin_id, target))
        return True

    if action == "cfgs":
        page = _tail_int(data)
        rows = configs.user_configs(target)
        buttons = []
        start = page * PAGE_SIZE
        for item in rows[start:start + PAGE_SIZE]:
            info = configs.describe(item)
            icon = {"active": "🟢", "expired": "⌛️", "exhausted": "📭"}.get(info["status"], "🔴")
            buttons.append([k.enter(f"{icon} {info['label']}"[:38], f"a:cfg:{item['id']}")])
        # این لیست، Parent واقعی صفحه‌ی جزئیات کانفیگ است.
        nav.remember(admin_id, "a:cfg", f"a:usr:{target}:cfgs:{page}")
        pages = k.pager(f"a:usr:{target}:cfgs", page, len(rows), PAGE_SIZE)
        if pages:
            buttons.append(pages)
        buttons.append([k.back(nav.parent_of(f"a:usr:{target}:cfgs:{page}", f"a:usr:{target}"))])
        await _render(client, chat_id, message_id,
                      f"📦 کانفیگ‌های کاربر <code>{target}</code> ({len(rows)} مورد)", k.kb(buttons))
        return True

    if action == "audit":
        entries = audit.recent(15, target_id=target)
        body = "\n".join(h(audit.describe(e)) for e in entries) or "رکوردی ثبت نشده."
        await _render(client, chat_id, message_id,
                      f"📜 <b>تاریخچه کاربر {target}</b>\n\n{body}",
                      k.kb([[k.back(f"a:usr:{target}")]]))
        return True

    if action == "edit":
        field = parts[4] if len(parts) > 4 else ""
        if field not in USER_FIELDS:
            return False
        need = security.MANAGE_QUOTA if field == "quota" else security.MANAGE_CONFIGS
        if not security.has_permission(admin_id, need):
            await _deny(client, cb_id)
            return True
        title, hint = USER_FIELDS[field]
        set_pending(admin_id, {"action": "user_field", "field": field, "target": target})
        await _render(client, chat_id, message_id,
                      f"✏️ <b>{title}</b>\nکاربر <code>{target}</code>\n\nمقدار جدید را بفرستید.\n<i>{hint}</i>",
                      k.kb([[k.cancel(f"a:usr:{target}")]]))
        return True

    # ── اکشن‌های حساس: همه امضاشده و با تأیید دومرحله‌ای ──
    if action in ("block", "unblock"):
        confirm_txt = "مسدود کردن" if action == "block" else "رفع مسدودی"
        await _render(client, chat_id, message_id,
                      f"⚠️ <b>تأیید عملیات</b>\n\nآیا مطمئن هستید که می‌خواهید کاربر <code>{target}</code> را {confirm_txt} کنید؟",
                      k.kb([k.confirm_row(security.sign_callback(f"a:usr:{target}:{action}ok"), f"a:usr:{target}")]))
        return True

    if action in ("blockok", "unblockok"):
        await users.set_blocked(admin_id, target, action == "blockok")
        await _save()
        await client.answer_callback(cb_id, "انجام شد.")
        await _render(client, chat_id, message_id, user_detail_text(target), _user_kb(admin_id, target))
        return True

    if action == "qreset":
        if not security.has_permission(admin_id, security.MANAGE_QUOTA):
            await _deny(client, cb_id)
            return True
        await _render(client, chat_id, message_id,
                      f"⚠️ <b>تأیید عملیات</b>\n\nسهمیه مصرف‌شده کاربر <code>{target}</code> صفر شود؟",
                      k.kb([k.confirm_row(security.sign_callback(f"a:usr:{target}:qresetok"), f"a:usr:{target}")]))
        return True

    if action == "qresetok":
        if not security.has_permission(admin_id, security.MANAGE_QUOTA):
            await _deny(client, cb_id)
            return True
        await quota.reset_user(admin_id, target)
        await _save()
        await client.answer_callback(cb_id, "سهمیه Reset شد.")
        await _render(client, chat_id, message_id, user_detail_text(target), _user_kb(admin_id, target))
        return True

    if action == "grant":
        if not security.has_permission(admin_id, security.MANAGE_CONFIGS):
            await _deny(client, cb_id)
            return True
        conf = quota.effective(target)
        await _render(client, chat_id, message_id,
                      f"⚠️ <b>اعطای کانفیگ</b>\n\nبرای کاربر <code>{target}</code> یک کانفیگ ساخته شود؟\n\n"
                      f"حجم: {conf['volume_gb']} GB · مدت: {conf['duration_days']} روز\n"
                      f"<i>سهمیه کاربر مصرف نمی‌شود.</i>",
                      k.kb([k.confirm_row(security.sign_callback(f"a:usr:{target}:grantok"),
                                          f"a:usr:{target}", "✅ بساز و بفرست")]))
        return True

    if action == "grantok":
        if not security.has_permission(admin_id, security.MANAGE_CONFIGS):
            await _deny(client, cb_id)
            return True
        if not security.allow_sensitive(admin_id, "grant", limit=10, window=60):
            await client.answer_callback(cb_id, "درخواست‌های زیاد. کمی صبر کنید.", alert=True)
            return True
        result = await issuing.grant_config(admin_id, target)
        if not result.ok:
            await client.answer_callback(cb_id, result.reason, alert=True)
            return True
        await _save()
        from .user import deliver_config
        sent = await deliver_config(client, target, result.user_config,
                                    title="🎁 <b>یک کانفیگ برای شما ثبت شد</b>")
        await client.answer_callback(cb_id, "کانفیگ ساخته و ارسال شد." if sent else
                                     "کانفیگ ساخته شد ولی ارسال به کاربر ناموفق بود.")
        await _render(client, chat_id, message_id, user_detail_text(target), _user_kb(admin_id, target))
        return True

    return False


# ── بخش کانفیگ‌ها ────────────────────────────────────────────────────────────
async def _configs_section(client, admin_id, chat_id, message_id, data, cb_id) -> bool:
    if not security.has_permission(admin_id, security.MANAGE_CONFIGS):
        await _deny(client, cb_id)
        return True

    if data == "a:cfgs":
        totals = configs.totals()
        await _render(client, chat_id, message_id,
                      f"📦 <b>کانفیگ‌ها</b>\n\n"
                      f"کل: {totals['total']}\n🟢 فعال: {totals['active']}\n"
                      f"⌛️ منقضی: {totals['expired']}\n📭 حجم تمام‌شده: {totals['exhausted']}\n"
                      f"🎁 رایگان ارسال‌شده: {totals['free_sent']}",
                      k.kb([
                          [k.enter("🟢 کانفیگ‌های فعال", "a:cfgs:active:0")],
                          [k.enter("⌛️ منقضی‌شده‌ها", "a:cfgs:expired:0")],
                          [k.enter("📭 حجم تمام‌شده", "a:cfgs:exhausted:0")],
                          [k.back("a:menu")],
                      ]))
        return True

    parts = data.split(":")
    if len(parts) >= 3 and parts[2] in ("active", "expired", "exhausted"):
        status = parts[2]
        page = _tail_int(data)
        rows = configs.by_status(status, limit=500)
        buttons = []
        start = page * PAGE_SIZE
        for item in rows[start:start + PAGE_SIZE]:
            info = configs.describe(item)
            buttons.append([k.enter(f"{info['label']} · {item.get('telegram_id')}"[:38], f"a:cfg:{item['id']}")])
        # این لیست هم می‌تواند Parent صفحه‌ی جزئیات کانفیگ باشد.
        nav.remember(admin_id, "a:cfg", f"a:cfgs:{status}:{page}")
        pages = k.pager(f"a:cfgs:{status}", page, len(rows), PAGE_SIZE)
        if pages:
            buttons.append(pages)
        buttons.append([k.back(nav.parent_of(f"a:cfgs:{status}:{page}", "a:cfgs"))])
        titles = {"active": "🟢 کانفیگ‌های فعال", "expired": "⌛️ منقضی‌شده‌ها",
                  "exhausted": "📭 حجم تمام‌شده"}
        await _render(client, chat_id, message_id,
                      f"{titles.get(status, status)} ({len(rows)} مورد)", k.kb(buttons))
        return True

    return False


async def _config_detail_section(client, admin_id, chat_id, message_id, data, cb_id) -> bool:
    if not security.has_permission(admin_id, security.MANAGE_CONFIGS):
        await _deny(client, cb_id)
        return True

    parts = data.split(":")
    ucid = parts[2] if len(parts) > 2 else ""
    item = configs.get_user_config(ucid)
    if not item:
        await _deny(client, cb_id, "کانفیگ یافت نشد.")
        return True
    action = parts[3] if len(parts) > 3 else ""
    owner = item.get("telegram_id")

    if not action:
        info = configs.describe(item)
        body = (
            f"📦 <b>{h(info['label'])}</b>\n\n"
            f"مالک: <code>{owner}</code>\n"
            f"وضعیت: {info['status_label']}\n"
            f"نوع: {h(info['protocol'])}\n"
            f"حجم: {info['used']} / {info['total']}\n"
            f"سرعت: {info['speed']}\n"
            f"انقضا: {info['expires_at']}\n"
            f"منبع: {item.get('source')}"
        )
        sub_url = configs.subscription_url(item)
        raw = configs.raw_config(item)
        copy_rows = []
        if sub_url and k.copy_fits(sub_url):
            copy_rows.append([k.copy_btn("🔗 کپی لینک ساب", sub_url, k.PRIMARY)])
        if raw and k.copy_fits(raw):
            copy_rows.append([k.copy_btn("🧩 کپی کانفیگ", raw, k.PRIMARY)])
        elif raw:
            # کانفیگ‌های طولانی از سقف CopyTextButton تلگرام عبور می‌کنند؛
            # خود مقدار را در پیام نشان می‌دهیم تا از Clipboard سیستم قابل کپی باشد.
            body += f"\n\n🧩 <b>کانفیگ خام:</b>\n<code>{h(raw)}</code>"
        await _render(client, chat_id, message_id, body, k.kb([
            *copy_rows,
            [k.btn("📤 ارسال به کاربر", f"a:cfg:{ucid}:send", k.SUCCESS)],
            [k.enter("♻️ صفر کردن مصرف", f"a:cfg:{ucid}:usage")],
            [k.btn("⛔️ غیرفعال", security.sign_callback(f"a:cfg:{ucid}:off"), k.DANGER),
             k.btn("✅ فعال", security.sign_callback(f"a:cfg:{ucid}:on"), k.SUCCESS)],
            [k.danger("🗑 حذف کانفیگ", security.sign_callback(f"a:cfg:{ucid}:del"))],
            # Parent واقعی: همان لیستی که ادمین از آن وارد شده. قبلاً همیشه به
            # پروفایل کاربر برمی‌گشت، حتی وقتی ادمین از «کانفیگ‌های فعال» آمده بود.
            [k.back(nav.back_target(admin_id, f"a:cfg:{ucid}", "a:cfg", f"a:usr:{owner}"))],
        ]))
        return True

    if action == "send":
        from .user import deliver_config
        ok = await deliver_config(client, int(owner), item, title="📦 <b>کانفیگ شما</b>")
        await client.answer_callback(
            cb_id,
            "برای کاربر ارسال شد." if ok else "ارسال ناموفق بود (احتمالاً ربات را بلاک کرده).",
            alert=not ok)
        return True

    if action == "usage":
        await configs.reset_usage(admin_id, ucid)
        await client.answer_callback(cb_id, "مصرف صفر شد.")
        return await _config_detail_section(client, admin_id, chat_id, message_id, f"a:cfg:{ucid}", cb_id)

    if action in ("on", "off"):
        await configs.set_active(admin_id, ucid, action == "on")
        await _save()
        await client.answer_callback(cb_id, "اعمال شد.")
        return await _config_detail_section(client, admin_id, chat_id, message_id, f"a:cfg:{ucid}", cb_id)

    if action == "del":
        info = configs.describe(item)
        await _render(client, chat_id, message_id,
                      f"⚠️ <b>آیا مطمئن هستید؟</b>\n\nکانفیگ «{h(info['label'])}» کاربر <code>{owner}</code> "
                      "برای همیشه حذف می‌شود. این عملیات قابل بازگشت نیست.",
                      k.kb([k.confirm_row(security.sign_callback(f"a:cfg:{ucid}:delok"),
                                          f"a:cfg:{ucid}", "✅ حذف کن")]))
        return True

    if action == "delok":
        try:
            await configs.delete(admin_id, ucid)
        except configs.ConfigCreationError as exc:
            await client.answer_callback(cb_id, f"حذف ناموفق: {exc}", alert=True)
            return True
        await client.answer_callback(cb_id, "کانفیگ حذف شد.")
        await _render(client, chat_id, message_id, user_detail_text(int(owner)), _user_kb(admin_id, int(owner)))
        return True

    return False


# ── بخش سهمیه ────────────────────────────────────────────────────────────────
async def _quota_section(client, admin_id, chat_id, message_id, data, cb_id) -> bool:
    if not security.has_permission(admin_id, security.MANAGE_QUOTA):
        await _deny(client, cb_id)
        return True

    if data == "a:quota":
        conf = quota.effective(0)
        await _render(client, chat_id, message_id,
                      f"🎁 <b>سهمیه‌ها</b>\n\n"
                      f"سهمیه پیش‌فرض: <b>{conf['total']}</b>\n"
                      f"بازنشانی: <b>{quota.RESET_LABELS.get(conf['reset'])}</b>\n"
                      f"تمدیدپذیر: <b>{'بله' if conf['renewable'] else 'خیر'}</b>\n"
                      f"سقف روزانه/هفتگی/ماهانه: {conf['daily_limit']} / {conf['weekly_limit']} / {conf['monthly_limit']}\n\n"
                      f"حجم: {conf['volume_gb']} GB · سرعت: {conf['speed_mbps'] or 'نامحدود'} · مدت: {conf['duration_days']} روز",
                      k.kb([
                          [k.enter("⚙️ سهمیه پیش‌فرض", "a:set:free_quota_total")],
                          [k.enter("♻️ دوره بازنشانی", "a:quota:reset")],
                          [k.enter("📊 سقف روزانه", "a:set:free_daily_limit"),
                           k.enter("📊 هفتگی", "a:set:free_weekly_limit")],
                          [k.enter("👤 سهمیه کاربر خاص", "a:quota:user")],
                          [k.back("a:menu")],
                      ]))
        return True

    if data == "a:quota:user":
        set_pending(admin_id, {"action": "user_search"})
        await _render(client, chat_id, message_id,
                      "🔍 Telegram ID یا Username کاربر را بفرستید تا سهمیه‌اش را تغییر دهید:",
                      k.kb([[k.cancel("a:quota")]]))
        return True

    if data == "a:quota:reset":
        current = str(store.setting("free_quota_reset"))
        rows = [[k.btn(f"{label}{' ✓' if mode == current else ''}",
                       security.sign_callback(f"a:quota:reset:{mode}"),
                       k.SUCCESS if mode == current else k.PRIMARY)]
                for mode, label in quota.RESET_LABELS.items()]
        rows.append([k.toggle("🔁 تمدیدپذیری سهمیه", security.sign_callback("a:quota:renew"),
                              bool(store.setting("free_quota_renewable")))])
        rows.append([k.back(nav.parent_of("a:quota:reset", "a:quota"))])
        await _render(client, chat_id, message_id,
                      "♻️ <b>دوره بازنشانی سهمیه</b>\n\nپس از پایان هر دوره، مصرف سهمیه کاربران صفر می‌شود.",
                      k.kb(rows))
        return True

    parts = data.split(":")
    if len(parts) == 4 and parts[2] == "reset":
        mode = parts[3]
        if mode not in quota.RESET_LABELS:
            return False
        before = store.setting("free_quota_reset")
        store.set_setting("free_quota_reset", mode)
        audit.record(admin_id, "setting_change", None, "free_quota_reset", before, mode)
        await _save()
        await client.answer_callback(cb_id, f"بازنشانی: {quota.RESET_LABELS.get(mode)}")
        return await _quota_section(client, admin_id, chat_id, message_id, "a:quota:reset", cb_id)

    # توجه: 'a:quota:renew' به‌صورت مرکزی در _apply_toggle اعمال می‌شود.
    return False


# ── بخش کانال‌ها ─────────────────────────────────────────────────────────────
async def _channels_section(client, admin_id, chat_id, message_id, data, cb_id) -> bool:
    if not security.has_permission(admin_id, security.MANAGE_CHANNELS):
        await _deny(client, cb_id)
        return True

    if data == "a:chans":
        rows = channels.all_channels()
        lines = ["📢 <b>کانال‌های اجباری</b>", ""]
        if rows:
            for item in rows:
                mark = "🟢" if item.get("is_active") else "⚪️"
                ref = item.get("username") and f"@{item['username']}" or item.get("chat_id") or "—"
                lines.append(f"{mark} {h(item.get('title'))} · <code>{h(ref)}</code>")
        else:
            lines.append("هنوز کانالی اضافه نشده است.")
        lines.append("")
        lines.append(f"الزام عضویت: <b>{'فعال' if store.setting('require_channels') else 'غیرفعال'}</b>")
        lines.append("<i>توجه: برای بررسی عضویت، ربات باید در کانال Admin باشد.</i>")

        buttons = [[k.enter(f"{'🟢' if c.get('is_active') else '⚪️'} {c.get('title')}"[:38],
                            f"a:chan:{c['id']}")] for c in rows[:8]]
        buttons.append([k.add("➕ افزودن کانال", "a:chans:add")])
        buttons.append([k.toggle("📢 الزام عضویت", security.sign_callback("a:chans:req"),
                                 bool(store.setting("require_channels")))])
        buttons.append([k.back(nav.parent_of("a:chans", "a:menu"))])
        await _render(client, chat_id, message_id, "\n".join(lines), k.kb(buttons))
        return True

    if data == "a:chans:add":
        set_pending(admin_id, {"action": "channel_add"})
        await _render(client, chat_id, message_id,
                      "➕ <b>افزودن کانال</b>\n\nاطلاعات کانال را در یک پیام و با این قالب بفرستید:\n\n"
                      "<code>عنوان | username یا chat_id | لینک دعوت (اختیاری)</code>\n\n"
                      "مثال:\n<code>کانال اصلی | @vodiwalkervpn03 |</code>\n\n"
                      "<i>یادآوری: ربات را در کانال Admin کنید تا بررسی عضویت کار کند.</i>",
                      k.kb([[k.cancel("a:chans")]]))
        return True

    # توجه: 'a:chans:req' به‌صورت مرکزی در _apply_toggle اعمال می‌شود.
    return False


async def _channel_detail_section(client, admin_id, chat_id, message_id, data, cb_id) -> bool:
    if not security.has_permission(admin_id, security.MANAGE_CHANNELS):
        await _deny(client, cb_id)
        return True

    parts = data.split(":")
    cid = parts[2] if len(parts) > 2 else ""
    item = channels.get(cid)
    if not item:
        await _deny(client, cb_id, "کانال یافت نشد.")
        return True
    action = parts[3] if len(parts) > 3 else ""

    if not action:
        ref = item.get("username") and f"@{item['username']}" or item.get("chat_id") or "—"
        await _render(client, chat_id, message_id,
                      f"📢 <b>{h(item.get('title'))}</b>\n\n"
                      f"شناسه: <code>{h(ref)}</code>\n"
                      f"لینک: {h(channels.join_url(item)) or '—'}\n"
                      f"وضعیت: {'🟢 فعال' if item.get('is_active') else '⚪️ غیرفعال'}\n"
                      f"ترتیب نمایش: {item.get('sort_order')}",
                      k.kb([
                          [k.toggle("وضعیت کانال", security.sign_callback(f"a:chan:{cid}:toggle"),
                                    bool(item.get("is_active")))],
                          [k.danger("🗑 حذف کانال", security.sign_callback(f"a:chan:{cid}:del"))],
                          [k.back(nav.parent_of(f"a:chan:{cid}", "a:chans"))],
                      ]))
        return True

    if action == "toggle":
        await channels.toggle(admin_id, cid)
        await _save()
        now_on = bool((channels.get(cid) or {}).get("is_active"))
        await client.answer_callback(cb_id, f"کانال {'فعال شد' if now_on else 'غیرفعال شد'}")
        return await _channel_detail_section(client, admin_id, chat_id, message_id, f"a:chan:{cid}", cb_id)

    if action == "del":
        await _render(client, chat_id, message_id,
                      f"⚠️ <b>آیا مطمئن هستید؟</b>\n\nکانال «{h(item.get('title'))}» حذف شود؟",
                      k.kb([k.confirm_row(security.sign_callback(f"a:chan:{cid}:delok"),
                                          f"a:chan:{cid}", "✅ حذف کن")]))
        return True

    if action == "delok":
        await channels.delete(admin_id, cid)
        await _save()
        await client.answer_callback(cb_id, "حذف شد.")
        return await _channels_section(client, admin_id, chat_id, message_id, "a:chans", cb_id)

    return False


# ── بخش آمار ─────────────────────────────────────────────────────────────────
async def _stats_section(client, admin_id, chat_id, message_id, data, cb_id) -> bool:
    if not security.has_permission(admin_id, security.VIEW_ANALYTICS):
        await _deny(client, cb_id)
        return True

    if data == "a:stats:daily":
        series = analytics.daily_series(7)
        lines = ["📊 <b>آمار ۷ روز گذشته</b>", ""]
        for row in series:
            lines.append(
                f"<code>{row['date']}</code> · 👥{row['users_new']} · 📦{row['configs_created']} "
                f"· 🎁{row['free_configs']}"
            )
        await _render(client, chat_id, message_id, "\n".join(lines), k.kb([[k.back("a:stats")]]))
        return True

    stats = analytics.overview()
    await _render(client, chat_id, message_id,
                  "📊 <b>آمار ربات</b>\n\n"
                  f"<b>کاربران</b>\n"
                  f"کل: {stats['users_total']} · فعال: {stats['users_active']} · مسدود: {stats['users_blocked']}\n"
                  f"جدید امروز: {stats['users_new_today']} · این هفته: {stats['users_new_week']}\n\n"
                  f"<b>کانفیگ‌ها</b>\n"
                  f"کل: {stats['configs_total']} · فعال: {stats['configs_active']}\n"
                  f"منقضی: {stats['configs_expired']} · حجم تمام‌شده: {stats['configs_exhausted']}\n"
                  f"رایگان ارسال‌شده: {stats['free_sent']}\n"
                  f"ساخته‌شده امروز: {stats['configs_created_today']} · خطا: {stats['config_errors_today']}\n\n"
                  f"<b>سهمیه</b>\n"
                  f"مصرف امروز: {stats['quota_consumed_today']} · این هفته: {stats['quota_consumed_week']}\n\n"
                  f"<b>سایر</b>\n"
                  f"تیکت باز: {stats['tickets_open']} · کانال فعال: {stats['channels_active']}",
                  k.kb([
                      [k.enter("📅 آمار روزانه", "a:stats:daily")],
                      [k.enter("📜 Audit Log", "a:audit")],
                      [k.back("a:menu")],
                  ]))
    return True


async def _audit_section(client, admin_id, chat_id, message_id, data, cb_id) -> bool:
    if not security.has_permission(admin_id, security.VIEW_ANALYTICS):
        await _deny(client, cb_id)
        return True
    entries = audit.recent(20)
    body = "\n".join(h(audit.describe(e)) for e in entries) or "رکوردی ثبت نشده."
    await _render(client, chat_id, message_id, f"📜 <b>Audit Log</b>\n\n{body}",
                  k.kb([[k.back("a:stats")]]))
    return True


# ── بخش Backup / Restore ─────────────────────────────────────────────────────
async def _backup_section(client, admin_id, chat_id, message_id, data, cb_id) -> bool:
    if not security.is_super_admin(admin_id):
        await _deny(client, cb_id, "فقط Super Admin به بکاپ دسترسی دارد.")
        return True

    status = backups.status()
    if data == "a:backup:now":
        await client.answer_callback(cb_id, "در حال ساخت بکاپ...")
        try:
            result = await backups.create_and_send(client, automatic=False)
            if result.get("ok"):
                text = (f"✅ <b>بکاپ ساخته و ارسال شد.</b>\n\n"
                        f"📄 <code>{h(result['filename'])}</code>\n"
                        f"📦 حجم: <b>{result['size'] / 1024:.1f} KB</b>\n"
                        f"👑 گیرندگان: <b>{result['sent']}</b>")
            else:
                text = "⚠️ بکاپ ساخته شد، اما برای هیچ Super Adminی ارسال نشد."
        except Exception as exc:
            logger.exception("manual backup failed")
            text = f"❌ <b>ساخت بکاپ ناموفق بود.</b>\n\n{h(str(exc))}"
        await _render(client, chat_id, message_id, text, k.kb([[k.back("a:backup")]]))
        return True

    if data == "a:backup:restore":
        set_pending(admin_id, {"action": "backup_restore"})
        await _render(client, chat_id, message_id,
                      "🔄 <b>ریستور بکاپ</b>\n\n"
                      "فایل ZIP بکاپ VodiWalker را همین‌جا ارسال کنید.\n"
                      "فقط فایل‌هایی با نام <code>vodiwalker_backup_*.zip</code> پذیرفته می‌شوند.\n\n"
                      "⚠️ اطلاعات فعلی با اطلاعات داخل بکاپ جایگزین می‌شود.",
                      k.kb([[k.cancel("a:backup")]]))
        return True

    if data == "a:backup:toggle":
        store.set_setting("backup_enabled", not bool(store.setting("backup_enabled", True)))
        await _save()
        return await _backup_section(client, admin_id, chat_id, message_id, "a:backup", cb_id)

    if data == "a:backup" or data.startswith("a:backup"):
        enabled = bool(status["enabled"])
        interval = status["interval_hours"]
        last = str(status["last_at"] or "")[:19].replace("T", " ") or "هنوز انجام نشده"
        await _render(client, chat_id, message_id,
                      "💾 <b>بکاپ و ریستور</b>\n\n"
                      f"بکاپ خودکار: <b>{'فعال' if enabled else 'غیرفعال'}</b>\n"
                      f"فاصله: <b>{interval:g} ساعت</b>\n"
                      f"آخرین بکاپ موفق: <code>{h(last)}</code>\n"
                      f"Super Adminهای دریافت‌کننده: <b>{status['super_admins']}</b>\n\n"
                      "بکاپ شامل State کامل پنل و ربات، کاربران، کانفیگ‌ها، کانال‌ها، تنظیمات، فروش و کلید امنیتی پایدار است.",
                      k.kb([
                          [k.add("💾 بکاپ الآن", "a:backup:now")],
                          [k.toggle("⏱ بکاپ خودکار", security.sign_callback("a:backup:toggle"), enabled)],
                          [k.enter(f"⏰ فاصله بکاپ · {interval:g} ساعت", "a:set:backup_interval_hours")],
                          [k.enter("🔄 ریستور بکاپ", "a:backup:restore")],
                          [k.back("a:menu")],
                      ]))
        return True
    return False


# ── بخش تنظیمات ──────────────────────────────────────────────────────────────
async def _settings_section(client, admin_id, chat_id, message_id, data, cb_id) -> bool:
    if not security.has_permission(admin_id, security.MANAGE_SETTINGS):
        await _deny(client, cb_id)
        return True

    if data == "a:settings:texts":
        rows = [[k.enter(label, f"a:txt:{key}")] for key, label in TEXT_FIELDS.items()]
        rows.append([k.back("a:settings")])
        await _render(client, chat_id, message_id, "📝 <b>متن‌ها</b>\n\nمتنی که می‌خواهید تغییر دهید را انتخاب کنید:",
                      k.kb(rows))
        return True

    if data == "a:settings:free":
        # همه‌ی مشخصات کانفیگ رایگان + سقف‌ها، همه با Backend کامل.
        keys = ("free_quota_total", "free_volume_gb", "free_speed_mbps",
                "free_duration_days", "free_ip_limit", "free_daily_limit",
                "free_weekly_limit", "free_monthly_limit", "free_cooldown_seconds")
        rows = [[k.enter(f"{SETTING_FIELDS[key][0]} · {store.setting(key)}"[:38], f"a:set:{key}")]
                for key in keys]
        rows.append([k.back("a:settings")])
        await _render(client, chat_id, message_id,
                      "🎁 <b>تنظیمات کانفیگ رایگان</b>\n\n"
                      "<i>مقدار فعلی هر گزینه روی خود دکمه نوشته شده است.</i>", k.kb(rows))
        return True

    maintenance = bool(store.setting("maintenance_mode"))
    support_on = bool(store.setting("support_enabled"))
    require_channels = bool(store.setting("require_channels"))
    await _render(client, chat_id, message_id,
                  "⚙️ <b>تنظیمات</b>\n\n"
                  f"حالت تعمیر: <b>{'روشن' if maintenance else 'خاموش'}</b>\n"
                  f"پشتیبانی: <b>{'فعال' if support_on else 'غیرفعال'}</b>\n"
                  f"الزام عضویت کانال: <b>{'فعال' if require_channels else 'غیرفعال'}</b>\n"
                  f"سقف اکشن در دقیقه (کاربر عادی): <b>{store.setting('rate_limit_per_minute')}</b>\n"
                  f"سقف Broadcast در ثانیه: <b>{store.setting('broadcast_rate')}</b>\n"
                  f"نسخه Schema: <b>{store.schema_version()}</b>\n\n"
                  "<i>ادمین‌ها از Rate Limit داخلی ربات مستثنا هستند.</i>",
                  k.kb([
                      [k.enter("🎁 تنظیمات کانفیگ رایگان", "a:settings:free")],
                      [k.enter("📝 متن‌ها", "a:settings:texts")],
                      [k.toggle("🛟 پشتیبانی", security.sign_callback("a:settings:support"), support_on)],
                      [k.toggle("📢 الزام عضویت کانال",
                                security.sign_callback("a:settings:reqch"), require_channels)],
                      [k.toggle("🧰 حالت تعمیر",
                                security.sign_callback("a:settings:maint"), maintenance)],
                      [k.enter("⏱ سقف اکشن در دقیقه", "a:set:rate_limit_per_minute")],
                      [k.enter("📨 سقف Broadcast در ثانیه", "a:set:broadcast_rate")],
                      [k.back(nav.parent_of("a:settings", "a:menu"))],
                  ]))
    return True


async def _setting_edit_section(client, admin_id, chat_id, message_id, data, cb_id) -> bool:
    key = data.split(":", 2)[2] if data.count(":") >= 2 else ""
    if key not in SETTING_FIELDS:
        return False

    # دسترسی دقیق: کلیدهای سهمیه‌ای با MANAGE_QUOTA هم قابل تغییرند، بقیه فقط با
    # MANAGE_SETTINGS. قبلاً هر ادمینی که MANAGE_QUOTA داشت می‌توانست
    # rate_limit_per_minute را هم عوض کند.
    if key == "backup_interval_hours" and not security.is_super_admin(admin_id):
        await _deny(client, cb_id, "فقط Super Admin می‌تواند فاصله بکاپ را تغییر دهد.")
        return True
    needed = security.MANAGE_QUOTA if key in QUOTA_SETTING_KEYS else security.MANAGE_SETTINGS
    if not security.has_permission(admin_id, needed) and \
       not security.has_permission(admin_id, security.MANAGE_SETTINGS):
        await _deny(client, cb_id)
        return True

    label, _cast = SETTING_FIELDS[key]
    set_pending(admin_id, {"action": "setting", "key": key})
    await _render(client, chat_id, message_id,
                  f"✏️ <b>{label}</b>\n\nمقدار فعلی: <code>{store.setting(key)}</code>\n\nمقدار جدید را بفرستید:",
                  k.kb([[k.cancel("a:settings")]]))
    return True


async def _text_edit_section(client, admin_id, chat_id, message_id, data, cb_id) -> bool:
    if not security.has_permission(admin_id, security.MANAGE_SETTINGS):
        await _deny(client, cb_id)
        return True
    key = data.split(":", 2)[2] if data.count(":") >= 2 else ""
    if key not in TEXT_FIELDS:
        return False
    set_pending(admin_id, {"action": "text", "key": key})
    current = store.text(key)[:500]
    await _render(client, chat_id, message_id,
                  f"✏️ <b>{TEXT_FIELDS[key]}</b>\n\nمتن فعلی:\n<code>{h(current)}</code>\n\nمتن جدید را بفرستید:",
                  k.kb([[k.cancel("a:settings:texts")]]))
    return True


# ── بخش Broadcast ────────────────────────────────────────────────────────────
async def _broadcast_section(client, admin_id, chat_id, message_id, data, cb_id) -> bool:
    if not security.has_permission(admin_id, security.SEND_BROADCAST):
        await _deny(client, cb_id)
        return True

    if data == "a:bc":
        recent = broadcast.listing(3)
        lines = ["📨 <b>پیام همگانی</b>", ""]
        if recent:
            lines.append("<b>آخرین ارسال‌ها:</b>")
            for item in recent:
                lines.append(f"#{item['id']} · {item['status']} · ✅{item['sent']} ❌{item['failed']} از {item['total']}")
        else:
            lines.append("هنوز پیامی ارسال نشده است.")
        await _render(client, chat_id, message_id, "\n".join(lines), k.kb([
            [k.add("📢 ارسال به همه", "a:bc:all")],
            [k.add("🎯 ارسال به کاربران فعال", "a:bc:active")],
            [k.back("a:menu")],
        ]))
        return True

    if data in ("a:bc:all", "a:bc:active"):
        only_active = data.endswith("active")
        targets = len(users.broadcast_targets(only_active=only_active))
        set_pending(admin_id, {"action": "broadcast", "only_active": only_active})
        await _render(client, chat_id, message_id,
                      f"📨 <b>پیام همگانی</b>\n\nمخاطبان: <b>{targets}</b> کاربر\n\n"
                      "متن پیام را بفرستید. برای ارسال عکس، یک عکس با کپشن بفرستید.",
                      k.kb([[k.cancel("a:bc")]]))
        return True

    if data.startswith("a:bc:go"):
        pending = get_pending(admin_id) or {}
        if pending.get("action") != "broadcast_confirm":
            await client.answer_callback(cb_id, "این درخواست منقضی شده است.", alert=True)
            return True
        if not security.allow_sensitive(admin_id, "broadcast", limit=3, window=300):
            await client.answer_callback(cb_id, "ارسال‌های زیاد. کمی صبر کنید.", alert=True)
            return True
        clear_pending(admin_id)
        record = await broadcast.create(admin_id, pending.get("text", ""),
                                       photo=pending.get("photo", ""),
                                       only_active=bool(pending.get("only_active")))
        await _render(client, chat_id, message_id,
                      f"🚀 ارسال شروع شد (#{record['id']}).\nمخاطبان: {record['total']}\n\n"
                      "نتیجه‌ی نهایی پس از پایان برای شما ارسال می‌شود.",
                      k.kb([[k.back("a:bc")]]))
        return True

    return False


# ── بخش تیکت‌ها ──────────────────────────────────────────────────────────────
async def _tickets_section(client, admin_id, chat_id, message_id, data, cb_id) -> bool:
    if not security.has_permission(admin_id, security.MANAGE_SUPPORT):
        await _deny(client, cb_id)
        return True

    rows = support.listing(limit=200)
    page = _tail_int(data) if data.startswith("a:tickets:") else 0
    start = page * PAGE_SIZE
    buttons = []
    for item in rows[start:start + PAGE_SIZE]:
        icon = {"open": "🔴", "answered": "🟡", "closed": "⚪️"}.get(item.get("status"), "⚪️")
        buttons.append([k.enter(f"{icon} #{item['id']} · {item.get('last_preview') or ''}"[:38],
                                f"a:tkt:{item['id']}")])
    pages = k.pager("a:tickets", page, len(rows), PAGE_SIZE)
    if pages:
        buttons.append(pages)
    buttons.append([k.back("a:menu")])
    await _render(client, chat_id, message_id,
                  f"🛟 <b>تیکت‌ها</b>\n\nباز: {support.open_count()} · کل: {len(rows)}", k.kb(buttons))
    return True


async def _ticket_detail_section(client, admin_id, chat_id, message_id, data, cb_id) -> bool:
    if not security.has_permission(admin_id, security.MANAGE_SUPPORT):
        await _deny(client, cb_id)
        return True

    parts = data.split(":")
    tid = parts[2] if len(parts) > 2 else ""
    ticket = support.get(tid)
    if not ticket:
        await _deny(client, cb_id, "تیکت یافت نشد.")
        return True
    action = parts[3] if len(parts) > 3 else ""

    if action == "reply":
        set_pending(admin_id, {"action": "ticket_reply", "ticket": tid})
        await _render(client, chat_id, message_id,
                      f"✍️ پاسخ خود را برای تیکت #{tid} بفرستید:", k.kb([[k.cancel(f"a:tkt:{tid}")]]))
        return True

    if action == "close":
        await support.close(admin_id, tid)
        await _save()
        await client.answer_callback(cb_id, "تیکت بسته شد.")
        return await _tickets_section(client, admin_id, chat_id, message_id, "a:tickets", cb_id)

    owner = ticket.get("telegram_id")
    rec = users.get(owner) or {}
    lines = [
        f"🛟 <b>تیکت #{tid}</b>",
        f"کاربر: {h(users.display_name(rec))} (<code>{owner}</code>)",
        f"وضعیت: {support.STATUS_LABELS.get(ticket.get('status'))}",
        "",
    ]
    for message in support.messages_of(tid)[-8:]:
        who = "👤" if message.get("sender") == "user" else "🛠"
        lines.append(f"{who} {h(message.get('text'))[:300]}")
    await _render(client, chat_id, message_id, "\n".join(lines), k.kb([
        [k.btn("✍️ پاسخ", f"a:tkt:{tid}:reply", k.SUCCESS)],
        [k.enter("👤 پروفایل کاربر", f"a:usr:{owner}")],
        [k.danger("✅ بستن تیکت", f"a:tkt:{tid}:close")],
        [k.back("a:tickets")],
    ]))
    return True


# ── ورودی متنی ادمین ─────────────────────────────────────────────────────────
async def handle_pending_text(client, rec: dict, chat_id: int, text: str, photo: str = "") -> bool:
    """پیام متنی ادمین در حالت انتظار (جستجو، مقدار فیلد، پاسخ تیکت، Broadcast)."""
    admin_id = int(rec["telegram_id"])
    pending = get_pending(admin_id)
    if not pending:
        return False
    action = pending.get("action")

    # دسترسی دوباره در لحظه‌ی اعمال بررسی می‌شود، نه فقط موقع نمایش منو.
    # اگر دسترسی ادمین بین نمایش فرم و ارسال مقدار گرفته شده باشد، اعمال نمی‌شود.
    required = {
        "user_search": security.MANAGE_USERS,
        "user_field": security.MANAGE_USERS,
        "admin_add": security.MANAGE_ADMINS,
        # 'setting' جداگانه و دقیق‌تر بررسی می‌شود (پایین‌تر).
        "text": security.MANAGE_SETTINGS,
        "channel_add": security.MANAGE_CHANNELS,
        "ticket_reply": security.MANAGE_SUPPORT,
        "broadcast": security.SEND_BROADCAST,
        "broadcast_confirm": security.SEND_BROADCAST,
    }.get(action)

    if required and not security.has_permission(admin_id, required):
        clear_pending(admin_id)
        await client.send_message(chat_id, "⛔️ دسترسی لازم برای این عملیات را ندارید.",
                                  k.kb([[k.back("a:menu")]]))
        return True

    if action == "setting":
        key = pending.get("key") or ""
        if key == "backup_interval_hours" and not security.is_super_admin(admin_id):
            clear_pending(admin_id)
            await client.send_message(chat_id, "⛔️ فقط Super Admin می‌تواند فاصله بکاپ را تغییر دهد.", k.kb([[k.back("a:backup")]]))
            return True
        needed = security.MANAGE_QUOTA if key in QUOTA_SETTING_KEYS else security.MANAGE_SETTINGS
        if not security.has_permission(admin_id, needed) and \
           not security.has_permission(admin_id, security.MANAGE_SETTINGS):
            clear_pending(admin_id)
            await client.send_message(chat_id, "⛔️ دسترسی لازم برای این عملیات را ندارید.",
                                      k.kb([[k.back("a:menu")]]))
            return True

    if action == "user_field":
        field = pending.get("field")
        need = security.MANAGE_QUOTA if field == "quota" else security.MANAGE_CONFIGS
        if not security.has_permission(admin_id, need):
            clear_pending(admin_id)
            await client.send_message(chat_id, "⛔️ دسترسی لازم برای این عملیات را ندارید.",
                                      k.kb([[k.back("a:menu")]]))
            return True

    if action == "admin_add":
        if not security.is_super_admin(admin_id):
            clear_pending(admin_id)
            await client.send_message(chat_id, "⛔️ فقط Super Admin می‌تواند ادمین اضافه کند.",
                                      k.kb([[k.back("a:users:admins")]]))
            return True
        target = security.valid_telegram_id(text)
        if target is None:
            await client.send_message(chat_id, "❗️ Telegram ID نامعتبر است.",
                                      k.kb([[k.cancel("a:users:admins")]]))
            return True
        if security.is_super_admin(target):
            clear_pending(admin_id)
            await client.send_message(chat_id, "این کاربر از قبل Super Admin است.",
                                      k.kb([[k.back("a:users:admins")]]))
            return True
        existing = users.get(target)
        if existing and existing.get("role") == security.ROLE_ADMIN:
            clear_pending(admin_id)
            await client.send_message(chat_id, "این کاربر از قبل ادمین است.",
                                      k.kb([[k.back("a:users:admins")]]))
            return True
        if existing is None:
            existing = await users.touch({"id": target})
        await users.set_role(admin_id, target, security.ROLE_ADMIN)
        existing["permissions"] = list((store.TG_ROLES.get(security.ROLE_ADMIN) or {}).get("permissions") or [])
        existing["permissions_override"] = True
        clear_pending(admin_id)
        await _save()
        await client.send_message(chat_id,
                                  f"✅ کاربر <code>{target}</code> به عنوان Admin اضافه شد.",
                                  k.kb([[k.back("a:users:admins")]]))
        return True

    if action == "user_search":
        clear_pending(admin_id)
        results = users.search(text)
        if not results:
            await client.send_message(chat_id, "کاربری یافت نشد.", k.kb([[k.back("a:users")]]))
            return True
        if len(results) == 1:
            target = int(results[0]["telegram_id"])
            await client.send_message(chat_id, user_detail_text(target), _user_kb(admin_id, target))
            return True
        buttons = [[k.enter(users.display_name(u)[:38], f"a:usr:{u['telegram_id']}")] for u in results[:8]]
        buttons.append([k.back("a:users")])
        await client.send_message(chat_id, f"🔍 {len(results)} کاربر یافت شد:", k.kb(buttons))
        return True

    if action == "user_field":
        target, field = int(pending["target"]), pending["field"]
        value = _parse_number(text, allow_float=field in ("volume", "speed"))
        if value is None:
            await client.send_message(chat_id, "❗️ مقدار نامعتبر است. یک عدد بفرستید.",
                                      k.kb([[k.cancel(f"a:usr:{target}")]]))
            return True
        clear_pending(admin_id)
        await _apply_user_field(admin_id, target, field, value)
        await _save()
        await client.send_message(chat_id, f"✅ اعمال شد.\n\n{user_detail_text(target)}",
                                  _user_kb(admin_id, target))
        return True

    if action == "setting":
        key = pending["key"]
        _label, cast = SETTING_FIELDS.get(key, ("", int))
        value = _parse_number(text, allow_float=cast is float)
        if value is None:
            await client.send_message(chat_id, "❗️ مقدار نامعتبر است.", k.kb([[k.cancel("a:settings")]]))
            return True
        if key == "backup_interval_hours" and not (0.25 <= float(value) <= 720):
            await client.send_message(chat_id, "❗️ فاصله بکاپ باید بین 0.25 تا 720 ساعت باشد.", k.kb([[k.cancel("a:backup")]]))
            return True
        clear_pending(admin_id)
        before = store.setting(key)
        store.set_setting(key, cast(value))
        audit.record(admin_id, "setting_change", None, key, before, cast(value))
        await _save()
        await client.send_message(chat_id, f"✅ ثبت شد: <code>{key}</code> = <b>{cast(value)}</b>",
                                  k.kb([[k.back("a:settings")]]))
        return True

    if action == "text":
        key = pending["key"]
        clear_pending(admin_id)
        before = store.text(key)[:60]
        store.set_text(key, text)
        audit.record(admin_id, "text_change", None, key, before, text[:60])
        await _save()
        await client.send_message(chat_id, "✅ متن به‌روزرسانی شد.",
                                 k.kb([[k.back("a:settings:texts")]]))
        return True

    if action == "channel_add":
        clear_pending(admin_id)
        parts = [p.strip() for p in text.split("|")]
        title = parts[0] if parts else ""
        ref = parts[1] if len(parts) > 1 else ""
        invite = parts[2] if len(parts) > 2 else ""
        if not title or not ref:
            await client.send_message(chat_id, "❗️ قالب درست نیست. مثال:\n<code>کانال اصلی | @channel |</code>",
                                      k.kb([[k.back("a:chans")]]))
            return True
        username, chat_ref_id = ("", ref) if ref.lstrip("-").isdigit() else (ref.lstrip("@"), "")
        await channels.create(admin_id, title, username=username, chat_id=chat_ref_id, invite_link=invite)
        await _save()
        await client.send_message(
            chat_id,
            "✅ کانال اضافه شد.\n\n<i>یادآوری: ربات باید در این کانال Admin باشد تا بررسی عضویت کار کند.</i>",
            k.kb([[k.back("a:chans")]]),
        )
        return True

    if action == "ticket_reply":
        tid = pending["ticket"]
        clear_pending(admin_id)
        ticket = await support.add_admin_reply(admin_id, tid, text)
        if not ticket:
            await client.send_message(chat_id, "تیکت یافت نشد.", k.kb([[k.back("a:tickets")]]))
            return True
        await _save()
        sent = await client.send_message(
            int(ticket["telegram_id"]),
            f"🛟 <b>پاسخ پشتیبانی</b> (تیکت #{tid})\n\n{h(text)}",
            k.kb([[k.enter("🛟 پاسخ دوباره", "u:support")], [k.back("u:menu")]]),
        )
        note = "✅ پاسخ ارسال شد." if sent else "⚠️ پاسخ ثبت شد ولی ارسال به کاربر ناموفق بود (احتمالاً ربات را بلاک کرده)."
        await client.send_message(chat_id, note, k.kb([[k.back(f"a:tkt:{tid}")]]))
        return True

    if action == "broadcast":
        only_active = bool(pending.get("only_active"))
        targets = len(users.broadcast_targets(only_active=only_active))
        set_pending(admin_id, {"action": "broadcast_confirm", "text": text,
                               "photo": photo, "only_active": only_active})
        preview = f"🖼 <i>همراه عکس</i>\n\n" if photo else ""
        await client.send_message(
            chat_id,
            f"📨 <b>پیش‌نمایش پیام همگانی</b>\n\nمخاطبان: <b>{targets}</b>\n\n{preview}{h(text)[:800]}\n\nارسال شود؟",
            k.kb([k.confirm_row("a:bc:go", "a:bc", "🚀 ارسال کن")]),
        )
        return True

    return False


async def _apply_user_field(admin_id: int, target: int, field: str, value) -> None:
    """اعمال تغییر فیلد روی کاربر و همه‌ی کانفیگ‌های فعال او."""
    if field == "quota":
        await quota.set_user_quota(admin_id, target, total=int(value))
        return

    mapping = {
        "volume": ("volume_gb", "volume_gb"),
        "speed": ("speed_mbps", "speed_mbps"),
        "days": ("duration_days", "days"),
        "iplimit": ("ip_limit", "ip_limit"),
    }
    quota_key, config_key = mapping[field]
    # هم پیش‌فرض کانفیگ‌های آینده‌ی کاربر و هم کانفیگ‌های فعلی او به‌روز می‌شوند.
    await quota.set_user_quota(admin_id, target, **{quota_key: value})
    for item in configs.user_configs(target):
        if configs.describe(item)["status"] in ("active", "disabled"):
            await configs.update_limits(admin_id, item["id"], **{config_key: value})


async def handle_pending_document(client, rec: dict, chat_id: int, document: dict) -> bool:
    """Handle a backup ZIP sent by Super Admin."""
    admin_id = int(rec["telegram_id"])
    pending = get_pending(admin_id)
    if not pending or pending.get("action") != "backup_restore":
        return False
    if not security.is_super_admin(admin_id):
        clear_pending(admin_id)
        return True
    clear_pending(admin_id)
    try:
        result = await backups.handle_document(client, chat_id, document)
        if result.get("ignored"):
            await client.send_message(chat_id, "❗️ این فایل یک بکاپ معتبر VodiWalker نیست.")
            return True
        await client.send_message(chat_id,
            f"✅ <b>ریستور با موفقیت انجام شد.</b>\n\n"
            f"👥 کاربران: <b>{result.get('users', 0)}</b>\n"
            f"📦 کانفیگ‌ها: <b>{result.get('configs', 0)}</b>\n"
            f"📢 کانال‌ها: <b>{result.get('channels', 0)}</b>",
            k.kb([[k.back("a:menu", "🏠 پنل مدیریت")]]))
    except Exception as exc:
        logger.exception("backup restore failed")
        await client.send_message(chat_id, f"❌ <b>ریستور انجام نشد.</b>\n\n{h(str(exc))}", k.kb([[k.back("a:backup")]]))
    return True


# ── helpers ──────────────────────────────────────────────────────────────────
async def _render(client, chat_id: int, message_id: int | None, text: str, kb: dict | None):
    if message_id:
        await client.edit_message(chat_id, message_id, text, kb)
    else:
        await client.send_message(chat_id, text, kb)


async def _save() -> None:
    try:
        from main import save_state
        await save_state()
    except Exception:
        logger.warning("save_state failed", exc_info=True)


def _tail_int(data: str) -> int:
    try:
        return max(0, int(data.rsplit(":", 1)[1]))
    except (ValueError, IndexError):
        return 0


def _parse_number(text: str, allow_float: bool = False):
    raw = (text or "").strip().replace(",", "")
    try:
        value = float(raw) if allow_float else int(raw)
    except ValueError:
        return None
    if value < 0:
        return None
    return value
