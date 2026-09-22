"""منوی کاربر عادی.

هیچ Business Logic ای اینجا نیست: همه چیز از لایه سرویس صدا زده می‌شود.
از Edit Message استفاده می‌شود تا چت کاربر با پیام‌های تکراری شلوغ نشود.
"""

from __future__ import annotations

import logging

from .. import keyboards as k
from .. import nav, security, store
from ..services import channels, configs, issuing, quota, support, users, membership
from ..tgapi import h

logger = logging.getLogger("vodiwalker.botsys.handlers.user")

PAGE_SIZE = 5


# ── متن‌ها ────────────────────────────────────────────────────────────────────
def welcome_text(rec: dict) -> str:
    base = store.text("welcome_user")
    status = quota.status(rec["telegram_id"])
    return (
        f"{base}\n\n"
        f"🎁 سهمیه باقی‌مانده: <b>{status['remaining']}</b> از {status['total']}"
    )


def account_text(telegram_id: int) -> str:
    rec = users.get(telegram_id) or {}
    status = quota.status(telegram_id)
    my = configs.user_configs(telegram_id)
    active = sum(1 for c in my if configs.describe(c)["status"] == "active")

    lines = [
        "📊 <b>وضعیت حساب</b>",
        "",
        f"👤 نام: {h(users.display_name(rec))}",
        f"🆔 شناسه: <code>{telegram_id}</code>",
        f"📅 عضویت: {str(rec.get('first_seen') or '')[:10]}",
        f"🎁 سهمیه: {status['remaining']} باقی‌مانده از {status['total']}",
        f"♻️ بازنشانی سهمیه: {status['reset_label']}",
        f"📦 کانفیگ‌ها: {len(my)} ({active} فعال)",
    ]

    total_bytes = used_bytes = 0
    panel_links = _panel_links()
    for item in my:
        link = panel_links.get(str(item.get("link_uid"))) or {}
        total_bytes += int(link.get("limit_bytes") or 0)
        used_bytes += int(link.get("used_bytes") or 0)

    if my:
        from main import fmt_bytes
        remaining = max(0, total_bytes - used_bytes) if total_bytes else 0
        lines += [
            f"📈 حجم کل: {fmt_bytes(total_bytes) if total_bytes else 'نامحدود'}",
            f"📉 مصرف‌شده: {fmt_bytes(used_bytes)}",
            f"📊 باقی‌مانده: {fmt_bytes(remaining) if total_bytes else 'نامحدود'}",
        ]

    if store.setting("require_channels", True) and channels.active_channels():
        lines.append(f"📢 عضویت کانال‌ها: {'✅ تأیید شده' if rec.get('channels_ok') else '❌ تأیید نشده'}")

    return "\n".join(lines)


def _panel_links() -> dict:
    import main
    return main.LINKS


def config_text(user_config: dict) -> str:
    info = configs.describe(user_config)
    icon = {"active": "🟢", "expired": "⌛️", "exhausted": "📭",
            "disabled": "🔴", "deleted": "🗑"}.get(info["status"], "⚪️")
    lines = [
        f"{icon} <b>{h(info['label'])}</b>",
        "",
        f"وضعیت: {info['status_label']}",
    ]
    if info["status"] != "deleted":
        lines += [
            f"نوع: {h(info['protocol'])}",
            f"تاریخ ایجاد: {info['created_at']}",
            f"تاریخ انقضا: {info['expires_at']}",
            f"حجم کل: {info['total']}",
            f"مصرف‌شده: {info['used']}",
            f"باقی‌مانده: {info['remaining']}",
            f"سرعت: {info['speed']}",
        ]
        if info["percent"]:
            lines.append(f"\n{_bar(info['percent'])} {info['percent']}%")
    return "\n".join(lines)


def _bar(percent: float, width: int = 10) -> str:
    percent = max(0.0, min(100.0, float(percent)))
    filled = round(percent / 100 * width)
    return "█" * filled + "░" * (width - filled)


# ── کیبوردها ─────────────────────────────────────────────────────────────────
def channels_kb(missing: list[dict], origin_token: str = "menu") -> dict:
    rows = []
    for channel in missing[:10]:
        url = channels.join_url(channel)
        title = channel.get("title") or "کانال"
        if url:
            rows.append([k.url_btn(f"📢 عضویت در {title}"[:40], url, k.PRIMARY)])
    rows.append([k.btn("🔄 بررسی عضویت", f"u:chk:{origin_token}", k.SUCCESS)])
    rows.append([k.back(FREE_ORIGINS.get(origin_token, "u:menu"))])
    return k.kb(rows)


def configs_kb(telegram_id: int, page: int) -> dict:
    items = configs.user_configs(telegram_id)
    start = page * PAGE_SIZE
    rows = []
    for item in items[start:start + PAGE_SIZE]:
        info = configs.describe(item)
        icon = {"active": "🟢", "expired": "⌛️", "exhausted": "📭",
                "disabled": "🔴"}.get(info["status"], "⚪️")
        rows.append([k.enter(f"{icon} {info['label']}"[:38], f"u:cfg:{item['id']}")])
    nav = k.pager("u:cfgs", page, len(items), PAGE_SIZE)
    if nav:
        rows.append(nav)
    rows.append([k.back("u:menu")])
    return k.kb(rows)


def config_kb(user_config: dict) -> dict:
    """جزئیات یک کانفیگ در «کانفیگ‌های من»: همان دو دکمه‌ی کپی رسمی + آموزش."""
    info = configs.describe(user_config)
    ucid = user_config["id"]
    rows = []
    if info["status"] != "deleted":
        sub_url = configs.subscription_url(user_config)
        raw = configs.raw_config(user_config)
        if sub_url:
            rows.append([k.copy_btn("🔗 کپی لینک ساب", sub_url, k.PRIMARY)])
        if raw:
            rows.append([k.copy_btn("🧩 کپی کانفیگ", raw, k.PRIMARY)]
                        if k.copy_fits(raw) else
                        [k.btn("🧩 کپی کانفیگ", f"u:raw:{ucid}", k.PRIMARY)])
    rows.append([k.enter("📚 آموزش اتصال", f"u:help:from:cfg:{ucid}")])
    rows.append([k.back(nav.parent_of(f"u:cfg:{ucid}", "u:cfgs:0"), "⬅️ بازگشت به کانفیگ‌ها")])
    return k.kb(rows)


def help_kb(back_to: str = "u:menu") -> dict:
    return k.kb([
        [k.enter("🤖 Android", "u:help:android"), k.enter("🍎 iOS", "u:help:ios")],
        [k.enter("🪟 Windows", "u:help:windows"), k.enter("💻 macOS", "u:help:macos")],
        [k.back(back_to)],
    ])


def support_kb() -> dict:
    return k.kb([[k.cancel("u:menu", "❌ انصراف")]])


# ── نمایش کانفیگ (یک پیام واحد) ──────────────────────────────────────────────
#: مقصدهای مجاز برای Back صفحه‌ی کانفیگ رایگان. Token داخل Callback کدگذاری
#: می‌شود تا Parent واقعی بعد از Restart ربات هم درست بماند.
FREE_ORIGINS = {"menu": "u:menu", "cfgs": "u:cfgs:0"}


def config_info_text(user_config: dict, title: str = "✅ <b>کانفیگ شما آماده است</b>",
                     raw_inline: str = "") -> str:
    """اطلاعات کانفیگ بر اساس داده‌ی واقعی خودِ پنل (LINKS)، در یک پیام."""
    info = configs.describe(user_config)
    lines = [
        title,
        "",
        f"📦 نام: {h(info['label'])}",
        f"🔌 نوع: {h(info['protocol'])}",
        f"📊 حجم: {info['total']}",
        f"⏳ مدت اعتبار: {info['duration_days']}",
        f"📅 تاریخ انقضا: {info['expires_at']}",
        f"🚀 سرعت: {info['speed']}",
        f"📱 آی‌پی هم‌زمان: {info['ip_limit']}",
    ]
    if info["status"] != "active":
        lines.append(f"ℹ️ وضعیت: {info['status_label']}")
    if raw_inline:
        lines += ["", "🧩 کانفیگ خام (برای کپی روی آن ضربه بزنید):",
                  f"<code>{h(raw_inline)}</code>"]
    return "\n".join(lines)


def config_actions_kb(user_config: dict, back_to: str) -> dict:
    """چهار دکمه، دقیقاً به همین ترتیب (ترتیب تغییر نمی‌کند):

        1) کپی لینک ساب   -> CopyTextButton با Subscription URL واقعی
        2) کپی کانفیگ     -> CopyTextButton با Raw Config
        3) کانفیگ‌های من   -> تنها دکمه‌ای که Navigation انجام می‌دهد
        4) بازگشت         -> Parent واقعی

    دکمه‌های ۱ و ۲ از قابلیت رسمی `copy_text` استفاده می‌کنند: تلگرام خودش متن
    را در Clipboard می‌گذارد و یک Notification کوتاه نشان می‌دهد. هیچ Callback ای
    فرستاده نمی‌شود، پس هیچ پیام جدیدی ساخته نمی‌شود، Keyboard عوض نمی‌شود و
    کاربر وارد هیچ منویی نمی‌شود.
    """
    ucid = user_config["id"]
    sub_url = configs.subscription_url(user_config)
    raw = configs.raw_config(user_config)

    sub_row = [k.copy_btn("🔗 کپی لینک ساب", sub_url, k.PRIMARY)] if sub_url else \
              [k.btn("🔗 لینک ساب در دسترس نیست", f"u:nosub:{ucid}", k.DANGER)]

    # اگر کانفیگ خام بلندتر از سقف رسمی ۲۵۶ کاراکتری CopyTextButton باشد، به‌جای
    # بریدن و خراب کردن آن، خودِ کانفیگ داخل همان پیام (code block) نمایش داده
    # می‌شود و این دکمه فقط یک Notification کوتاه می‌دهد.
    raw_row = [k.copy_btn("🧩 کپی کانفیگ", raw, k.PRIMARY)] if k.copy_fits(raw) else \
              [k.btn("🧩 کپی کانفیگ", f"u:raw:{ucid}", k.PRIMARY)]

    return k.kb([
        sub_row,
        raw_row,
        [k.btn("📦 کانفیگ‌های من", "u:cfgs:0", k.PRIMARY)],
        [k.back(back_to)],
    ])


async def render_config_view(client, chat_id: int, message_id: int | None,
                             user_config: dict, back_to: str,
                             title: str = "✅ <b>کانفیگ شما آماده است</b>") -> bool:
    """یک پیام واحد: اطلاعات کانفیگ + چهار دکمه. در صورت امکان Edit، نه ارسال."""
    raw = configs.raw_config(user_config)
    sub_url = configs.subscription_url(user_config)

    if not raw and not sub_url:
        await _render(client, chat_id, message_id,
                      "⚠️ کانفیگ ساخته شد اما ساخت لینک آن ناموفق بود. با پشتیبانی تماس بگیرید.",
                      k.kb([[k.enter("🛟 پشتیبانی", "u:support")], [k.back(back_to)]]))
        return False

    text = config_info_text(user_config, title,
                            raw_inline="" if k.copy_fits(raw) else raw)
    sent = await _render(client, chat_id, message_id, text,
                         config_actions_kb(user_config, back_to))
    if sent is not False:
        await configs.mark_delivered(user_config["id"])
        return True
    return False


async def deliver_config(client, chat_id: int, user_config: dict,
                         message_id: int | None = None,
                         back_to: str = "u:menu",
                         title: str = "✅ <b>کانفیگ شما آماده است</b>") -> bool:
    """تحویل کانفیگ به کاربر در قالب یک پیام واحد.

    از پنل ادمین (اعطای کانفیگ / ارسال مجدد) هم بدون message_id صدا زده می‌شود
    و در آن حالت طبیعتاً یک پیام تازه برای کاربر مقصد فرستاده می‌شود.
    """
    return await render_config_view(client, chat_id, message_id, user_config, back_to, title)


# ── Callback routing ─────────────────────────────────────────────────────────
async def handle(client, rec: dict, chat_id: int, message_id: int | None, data: str, cb_id: str | None):
    """مسیریابی Callback های کاربر عادی. True یعنی مدیریت شد."""
    telegram_id = int(rec["telegram_id"])

    if data == "u:menu":
        _clear_pending(telegram_id)
        await _render(client, chat_id, message_id, welcome_text(rec),
                      k.user_main(bool(store.setting("support_enabled", True))))
        return True

    if data == "u:acct":
        await _render(client, chat_id, message_id, account_text(telegram_id), k.kb([[k.back("u:menu")]]))
        return True

    # ── کانفیگ رایگان ──
    # Callback مجاز: u:free | u:free:<origin> | u:chk | u:chk:<origin>
    # origin داخل خود Callback کدگذاری می‌شود تا Back بعد از Restart هم درست بماند.
    head = data.split(":")[1] if data.count(":") >= 1 else ""
    if head in ("free", "chk"):
        parts = data.split(":")
        origin_token = parts[2] if len(parts) > 2 else "menu"
        back_to = FREE_ORIGINS.get(origin_token, "u:menu")
        nav.remember(telegram_id, "free", back_to)
        if head == "chk":
            # «بررسی عضویت» باید واقعاً فقط وضعیت عضویت را تازه‌سازی کند؛
            # قبلاً این Callback اشتباهاً وارد مسیر صدور کانفیگ می‌شد.
            channels.invalidate(telegram_id)
            await membership.check_callback(telegram_id, chat_id, message_id)
        else:
            await _free_flow(client, rec, chat_id, message_id, cb_id, back_to, origin_token)
        return True

    if data == "u:membership:check":
        channels.invalidate(telegram_id)
        await membership.check_callback(telegram_id, chat_id, message_id)
        return True

    if data.startswith("u:raw:"):
        # CopyTextButton سقف ۲۵۶ کاراکتر دارد. برای کانفیگ‌های بلند، دکمه
        # واقعاً کار می‌کند و خود کانفیگ را در یک پیام قابل Copy می‌فرستد؛
        # دیگر پیام گمراه‌کننده‌ی «روی همان پیام بزنید» نمایش داده نمی‌شود.
        ucid = data.split(":", 2)[2]
        if not security.owns_config(telegram_id, ucid):
            await client.answer_callback(cb_id, "این کانفیگ در دسترس شما نیست.", alert=True)
            return True
        item = configs.get_user_config(ucid)
        raw = configs.raw_config(item) if item else ""
        if not raw:
            await client.answer_callback(cb_id, "کانفیگ خام در دسترس نیست.", alert=True)
            return True
        await client.answer_callback(cb_id, "کانفیگ برای کپی ارسال شد.")
        await client.send_message(
            chat_id,
            "🧩 <b>کانفیگ خام</b>\n\n<code>" + h(raw) + "</code>\n\n"
            "روی متن بالا لمس کنید و Copy را بزنید.",
        )
        return True

    if data.startswith("u:nosub:"):
        await client.answer_callback(cb_id, "لینک ساب در دسترس نیست. با پشتیبانی تماس بگیرید.",
                                     alert=True)
        return True

    if data.startswith("u:cfgs:"):
        page = _int_tail(data)
        items = configs.user_configs(telegram_id)
        if not items:
            await _render(client, chat_id, message_id,
                          "📦 هنوز هیچ کانفیگی ندارید.\n\nبا دکمه «دریافت کانفیگ رایگان» اولین کانفیگ خود را بگیرید.",
                          k.kb([[k.btn("🎁 دریافت کانفیگ رایگان", "u:free:cfgs", k.SUCCESS)],
                                [k.back("u:menu")]]))
            return True
        await _render(client, chat_id, message_id,
                      f"📦 <b>کانفیگ‌های من</b> ({len(items)} مورد)", configs_kb(telegram_id, page))
        return True

    if data.startswith("u:cfg:"):
        ucid = data.split(":", 2)[2]
        # جلوگیری از IDOR: بدون بررسی مالکیت هیچ داده‌ای برگردانده نمی‌شود.
        if not security.owns_config(telegram_id, ucid):
            await client.answer_callback(cb_id, "این کانفیگ در دسترس شما نیست.", alert=True)
            return True
        item = configs.get_user_config(ucid)
        nav.remember(telegram_id, "help", f"u:cfg:{ucid}")
        await _render(client, chat_id, message_id, config_text(item), config_kb(item))
        return True

    if data.startswith("u:send:"):
        ucid = data.split(":", 2)[2]
        if not security.owns_config(telegram_id, ucid):
            await client.answer_callback(cb_id, "این کانفیگ در دسترس شما نیست.", alert=True)
            return True
        if not security.allow_sensitive(telegram_id, "resend", limit=5, window=60):
            await client.answer_callback(cb_id, "درخواست‌های زیاد. کمی صبر کنید.", alert=True)
            return True
        await render_config_view(client, chat_id, message_id, configs.get_user_config(ucid),
                                 f"u:cfg:{ucid}", "📦 <b>کانفیگ شما</b>")
        return True

    if data == "u:help" or data.startswith("u:help:from:"):
        # ورود به آموزش از «منوی اصلی» یا از «جزئیات یک کانفیگ»؛ Parent واقعی ثبت می‌شود.
        if data.startswith("u:help:from:cfg:"):
            nav.remember(telegram_id, "help", f"u:cfg:{data.rsplit(':', 1)[1]}")
        else:
            nav.remember(telegram_id, "help", "u:menu")
        await _render(client, chat_id, message_id, store.text("tutorial_intro"),
                      help_kb(nav.origin(telegram_id, "help", "u:menu")))
        return True

    if data.startswith("u:help:"):
        platform = data.split(":", 2)[2]
        body = store.text(f"tutorial_{platform}", "آموزش این پلتفرم هنوز تنظیم نشده است.")
        await _render(client, chat_id, message_id, body,
                      k.kb([[k.back("u:help", "⬅️ بازگشت به آموزش‌ها")],
                            [k.back("u:menu", "🏠 منوی اصلی")]]))
        return True

    if data == "u:support":
        if not store.setting("support_enabled", True):
            await client.answer_callback(cb_id, "پشتیبانی موقتاً غیرفعال است.", alert=True)
            return True
        set_pending(telegram_id, {"action": "support"})
        await _render(client, chat_id, message_id, f"🛟 <b>پشتیبانی</b>\n\n{store.text('support_intro')}", support_kb())
        return True

    return False


async def _free_flow(client, rec: dict, chat_id: int, message_id: int | None,
                     cb_id: str | None, back_to: str = "u:menu",
                     origin_token: str = "menu"):
    """دریافت کانفیگ رایگان: بررسی کانال -> سهمیه -> ساخت -> نمایش یک پیام واحد.

    نکته‌ی UX: نتیجه با Edit روی همان پیام نوشته می‌شود، پس هیچ پیام واسطِ
    «در حال ارسال...» و هیچ پیام دوم حاوی لینک ساخته نمی‌شود.
    """
    telegram_id = int(rec["telegram_id"])

    if not security.allow_sensitive(telegram_id, "free", limit=6, window=60):
        await client.answer_callback(cb_id, "درخواست‌های بیش از حد. یک دقیقه صبر کنید.", alert=True)
        return

    # ساخت کانفیگ در پنل چند ثانیه طول می‌کشد؛ Callback را همین‌جا Ack می‌کنیم تا
    # ساعت شنی کاربر متوقف شود (این هیچ پیامی در چت ایجاد نمی‌کند).
    await client.answer_callback(cb_id, "⏳ در حال آماده‌سازی کانفیگ...")

    result = await issuing.issue_free_config(telegram_id)

    if result.ok:
        title = ("📦 <b>کانفیگ رایگان شما</b>" if result.reused
                 else "✅ <b>کانفیگ رایگان شما آماده است</b>")
        delivered = await render_config_view(client, chat_id, message_id,
                                            result.user_config, back_to, title)
        if not delivered:
            logger.warning("config %s created but not delivered to %s",
                           result.user_config.get("id"), telegram_id)
        return

    if result.reason == "channels":
        await _render(client, chat_id, message_id,
                      f"📢 <b>عضویت در کانال‌ها</b>\n\n{store.text('need_channels')}",
                      channels_kb(result.missing_channels, origin_token))
        return

    if result.busy:
        await client.answer_callback(cb_id, result.reason, alert=True)
        return

    await _render(client, chat_id, message_id,
                  f"ℹ️ {h(result.reason)}",
                  k.kb([[k.enter("📦 کانفیگ‌های من", "u:cfgs:0")], [k.back(back_to)]]))


# ── Pending input state (پشتیبانی) ───────────────────────────────────────────
_PENDING: dict[int, dict] = {}


def set_pending(telegram_id: int, value: dict) -> None:
    _PENDING[int(telegram_id)] = value


def get_pending(telegram_id: int) -> dict | None:
    return _PENDING.get(int(telegram_id))


def _clear_pending(telegram_id: int) -> None:
    _PENDING.pop(int(telegram_id), None)


clear_pending = _clear_pending


async def handle_pending_text(client, rec: dict, chat_id: int, text: str) -> bool:
    """پیام متنی کاربر عادی در حالت انتظار (مثلاً ارسال پیام پشتیبانی)."""
    telegram_id = int(rec["telegram_id"])
    pending = get_pending(telegram_id)
    if not pending:
        return False

    if pending.get("action") == "support":
        if not security.allow_sensitive(telegram_id, "ticket", limit=5, window=300):
            await client.send_message(chat_id, "پیام‌های زیادی فرستاده‌اید. کمی بعد تلاش کنید.",
                                      k.kb([[k.back("u:menu")]]))
            return True
        _clear_pending(telegram_id)
        ticket = await support.add_user_message(telegram_id, text)
        await client.send_message(
            chat_id,
            f"✅ پیام شما ثبت شد (تیکت #{ticket['id']}).\nپشتیبانی به‌زودی پاسخ می‌دهد.",
            k.kb([[k.back("u:menu")]]),
        )
        await _notify_admins(client, rec, ticket, text)
        try:
            from main import save_state
            await save_state()
        except Exception:
            pass
        return True

    return False


async def _notify_admins(client, rec: dict, ticket: dict, text: str) -> None:
    """اطلاع‌رسانی تیکت جدید به ادمین‌های دارای دسترسی پشتیبانی."""
    from .. import keyboards as kb_mod
    body = (
        f"🛟 <b>تیکت جدید #{ticket['id']}</b>\n\n"
        f"از: {h(users.display_name(rec))}\n"
        f"شناسه: <code>{rec['telegram_id']}</code>\n\n"
        f"{h(text[:500])}"
    )
    markup = kb_mod.kb([[kb_mod.enter("✍️ پاسخ", f"a:tkt:{ticket['id']}")]])
    for admin in users.admins():
        admin_id = security.valid_telegram_id(admin.get("telegram_id"))
        if not admin_id or not security.has_permission(admin_id, security.MANAGE_SUPPORT):
            continue
        await client.send_message(admin_id, body, markup)


# ── helpers ──────────────────────────────────────────────────────────────────
async def _render(client, chat_id: int, message_id: int | None, text: str, kb: dict | None):
    """نمایش یک صفحه: در صورت وجود پیام قبلی Edit، وگرنه ارسال.

    False فقط وقتی برگردانده می‌شود که ارسال واقعاً شکست خورده باشد (تا مثلاً
    کانفیگ به‌اشتباه delivered علامت نخورد). edit_message روی «not modified»
    مقدار None برمی‌گرداند که خطا نیست.
    """
    if message_id:
        return bool(await client.edit_message(chat_id, message_id, text, kb))
    return bool(await client.send_message(chat_id, text, kb))


def _int_tail(data: str) -> int:
    try:
        return max(0, int(data.rsplit(":", 1)[1]))
    except (ValueError, IndexError):
        return 0
