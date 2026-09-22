"""تست اصلاحات درخواستی: Rate Limit ادمین، Toggle ها، Navigation/Back، Settings،
کانفیگ رایگان + لینک ساب، دکمه‌های کپی، و جلوگیری از Duplicate Config.

اجرا:  python3 tests/test_fixes.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["TELEGRAM_SUPER_ADMIN_IDS"] = "900001"

from tests.harness import FakeTelegram, bootstrap, callback, message  # noqa: E402

SUPER_ADMIN = 900001
ADMIN = 900002
USER = 500001

_results: list[tuple[str, bool, str]] = []


def scenario(name: str):
    def decorator(fn):
        async def wrapper():
            ctx = bootstrap()
            tg = FakeTelegram().install()
            try:
                await fn(ctx, tg)
                _results.append((name, True, ""))
            except AssertionError as exc:
                _results.append((name, False, str(exc) or "assertion failed"))
            except Exception as exc:
                _results.append((name, False, f"{type(exc).__name__}: {exc}"))
        wrapper.__name__ = fn.__name__
        return wrapper
    return decorator


def answers(tg) -> str:
    return " ".join(str(p.get("text") or "") for m, p in tg.calls
                    if m == "answerCallbackQuery")


def backs(tg) -> list[str]:
    return [b.get("callback_data") for b in tg.buttons()
            if "بازگشت" in str(b.get("text", ""))]


async def make_admin(ctx, telegram_id: int) -> None:
    await ctx.users.touch({"id": telegram_id, "first_name": "ادمین"})
    ctx.store.TG_USERS[str(telegram_id)]["role"] = ctx.security.ROLE_ADMIN


# ══ ۱. Rate Limit داخلی برای ادمین اعمال نمی‌شود ═════════════════════════════
@scenario("Admin از Rate Limit داخلی مستثنا است (کلیک سریع بین منوها)")
async def t_admin_rate(ctx, tg):
    ctx.store.set_setting("rate_limit_per_minute", 3)   # سقف خیلی سخت‌گیرانه
    await make_admin(ctx, ADMIN)

    menus = ["a:menu", "a:users", "a:menu", "a:settings", "a:menu",
             "a:cfgs", "a:menu", "a:chans", "a:menu", "a:quota",
             "a:stats", "a:tickets", "a:menu", "a:users", "a:settings"]
    for index, data in enumerate(menus):
        tg.clear()
        await ctx.router.dispatch(callback(ADMIN, data, message_id=300 + index))
        assert "بیش از حد" not in answers(tg), f"ادمین روی {data} Rate Limit خورد"

    for index, data in enumerate(["a:menu", "a:users"] * 20):
        await ctx.router.dispatch(callback(SUPER_ADMIN, data, message_id=400 + index))
    assert "بیش از حد" not in answers(tg), "Super Admin روی Navigation سریع Rate Limit خورد"


@scenario("Rate Limit کاربر عادی همچنان فعال است (ضد Spam)")
async def t_user_rate(ctx, tg):
    ctx.store.set_setting("rate_limit_per_minute", 3)
    await ctx.router.dispatch(message(USER, "/start"))
    tg.clear()
    for index in range(10):
        await ctx.router.dispatch(callback(USER, "u:menu", message_id=500 + index))
    assert "بیش از حد" in answers(tg), "کاربر عادی هیچ Rate Limit ای نخورد"


@scenario("Navigation ادمین با Edit Message انجام می‌شود (بدون پیام اضافه)")
async def t_admin_edit(ctx, tg):
    await make_admin(ctx, ADMIN)
    await ctx.router.dispatch(message(ADMIN, "/start"))
    tg.clear()
    for index, data in enumerate(["a:users", "a:menu", "a:settings", "a:menu"]):
        await ctx.router.dispatch(callback(ADMIN, data, message_id=600 + index))
    assert not tg.sent("sendMessage"), \
        f"Navigation ادمین پیام جدید ساخت: {[p.get('text') for p in tg.sent('sendMessage')]}"
    assert tg.sent("editMessageText"), "هیچ Edit ای انجام نشد"


# ══ ۲. Toggle ها ═════════════════════════════════════════════════════════════
@scenario("Toggle پشتیبانی واقعاً کار می‌کند و همان لحظه رنگ عوض می‌شود")
async def t_toggle_support(ctx, tg):
    ctx.store.set_setting("support_enabled", True)
    data = ctx.security.sign_callback("a:settings:support")

    await ctx.router.dispatch(callback(SUPER_ADMIN, data, message_id=700))
    assert ctx.store.setting("support_enabled") is False, "Toggle مقدار را عوض نکرد"
    button = next(b for b in tg.buttons() if "پشتیبانی" in b["text"])
    assert button["style"] == "danger", f"غیرفعال باید danger باشد: {button}"
    assert "غیرفعال" in answers(tg), f"Notification کوتاه نمایش داده نشد: {answers(tg)!r}"

    tg.clear()
    await ctx.router.dispatch(callback(SUPER_ADMIN, data, message_id=701))
    assert ctx.store.setting("support_enabled") is True
    button = next(b for b in tg.buttons() if "پشتیبانی" in b["text"])
    assert button["style"] == "success", f"فعال باید success باشد: {button}"


@scenario("Toggle حالت تعمیر / الزام کانال / تمدیدپذیری همه کار می‌کنند")
async def t_toggle_all(ctx, tg):
    cases = [
        ("a:settings:maint", "maintenance_mode"),
        ("a:settings:reqch", "require_channels"),
        ("a:chans:req", "require_channels"),
        ("a:quota:renew", "free_quota_renewable"),
    ]
    for index, (raw, key) in enumerate(cases):
        before = bool(ctx.store.setting(key))
        tg.clear()
        await ctx.router.dispatch(callback(
            SUPER_ADMIN, ctx.security.sign_callback(raw), message_id=800 + index))
        after = bool(ctx.store.setting(key))
        assert after != before, f"{raw} مقدار {key} را عوض نکرد ({before} -> {after})"
        assert answers(tg).strip(), f"{raw} هیچ Notification ای نداد"
        styles = [b.get("style") for b in tg.buttons()]
        assert "success" in styles or "danger" in styles, f"{raw}: رنگ وضعیت نیست"


@scenario("وضعیت Toggle بعد از Restart از State خوانده می‌شود")
async def t_toggle_persist(ctx, tg):
    await ctx.router.dispatch(callback(
        SUPER_ADMIN, ctx.security.sign_callback("a:settings:support"), message_id=900))
    assert ctx.store.setting("support_enabled") is False
    snapshot = ctx.store.export_state()

    fresh = bootstrap()                       # شبیه‌سازی Restart کامل ربات
    fresh.store.import_state(snapshot)
    assert fresh.store.setting("support_enabled") is False, \
        "وضعیت Toggle بعد از Restart از Database بارگذاری نشد"

    tg2 = FakeTelegram().install()
    await fresh.router.dispatch(callback(SUPER_ADMIN, "a:settings", message_id=901))
    button = next(b for b in tg2.buttons() if "پشتیبانی" in b["text"])
    assert button["style"] == "danger", f"رنگ بعد از Restart درست نیست: {button}"


@scenario("هیچ Toggle ای از ایموجی به‌جای رنگ استفاده نمی‌کند")
async def t_toggle_no_emoji(ctx, tg):
    button = ctx.keyboards.toggle("تست", "d", True)
    off = ctx.keyboards.toggle("تست", "d", False)
    assert button["style"] == "success" and off["style"] == "danger"
    for candidate in (button, off):
        for emoji in ("🔴", "🟢", "🔵", "🟡", "⚪️", "🟠", "🟣"):
            assert emoji not in candidate["text"], f"ایموجی رنگی در «{candidate['text']}»"


# ══ ۳. Navigation و Back ═════════════════════════════════════════════════════
@scenario("زنجیره Back ادمین: User Configs -> User Details -> Users -> Admin Panel")
async def t_back_chain(ctx, tg):
    await ctx.users.touch({"id": USER, "first_name": "کاربر"})
    result = await ctx.issuing.grant_config(SUPER_ADMIN, USER)
    assert result.ok

    steps = [
        (f"a:usr:{USER}:cfgs:0", f"a:usr:{USER}"),
        (f"a:usr:{USER}", "a:users"),
        ("a:users", "a:menu"),
    ]
    for index, (page, expected) in enumerate(steps):
        tg.clear()
        await ctx.router.dispatch(callback(SUPER_ADMIN, page, message_id=1000 + index))
        assert expected in backs(tg), f"Back صفحه {page} باید {expected} باشد، شد {backs(tg)}"


@scenario("Back جزئیات کانفیگ، Parent واقعی (مسیر ورود) را می‌شناسد")
async def t_back_config_origin(ctx, tg):
    await ctx.users.touch({"id": USER, "first_name": "کاربر"})
    result = await ctx.issuing.grant_config(SUPER_ADMIN, USER)
    ucid = result.user_config["id"]

    # مسیر ۱: از پروفایل کاربر
    await ctx.router.dispatch(callback(SUPER_ADMIN, f"a:usr:{USER}:cfgs:0", message_id=1100))
    tg.clear()
    await ctx.router.dispatch(callback(SUPER_ADMIN, f"a:cfg:{ucid}", message_id=1101))
    assert f"a:usr:{USER}:cfgs:0" in backs(tg), f"Back باید به لیست کانفیگ کاربر برگردد: {backs(tg)}"

    # مسیر ۲: از «کانفیگ‌های فعال» — همان صفحه، Parent متفاوت
    await ctx.router.dispatch(callback(SUPER_ADMIN, "a:cfgs:active:0", message_id=1102))
    tg.clear()
    await ctx.router.dispatch(callback(SUPER_ADMIN, f"a:cfg:{ucid}", message_id=1103))
    assert "a:cfgs:active:0" in backs(tg), f"Back باید به لیست کانفیگ‌های فعال برگردد: {backs(tg)}"


@scenario("Back کانفیگ رایگان به Parent واقعی برمی‌گردد")
async def t_back_free(ctx, tg):
    ctx.store.set_setting("require_channels", False)
    await ctx.router.dispatch(message(USER, "/start"))

    # از منوی اصلی
    tg.clear()
    await ctx.router.dispatch(callback(USER, "u:free:menu", message_id=1200))
    assert "u:menu" in backs(tg), f"Back باید Main Menu باشد: {backs(tg)}"

    # از «کانفیگ‌های من» (مسیر دیگر، همان صفحه)
    ctx.store.set_setting("free_quota_total", 5)
    tg.clear()
    await ctx.router.dispatch(callback(USER, "u:free:cfgs", message_id=1201))
    assert "u:cfgs:0" in backs(tg), f"Back باید کانفیگ‌های من باشد: {backs(tg)}"


@scenario("نقشه Parent هیچ مسیر مبهم یا بی‌والدی ندارد")
async def t_nav_map(ctx, tg):
    from botsys import nav
    cases = {
        "a:usr:5:cfgs:0": "a:usr:5",
        "a:usr:5:audit": "a:usr:5",
        "a:usr:5": "a:users",
        "a:users:recent:3": "a:users",
        "a:users": "a:menu",
        "a:txt:welcome_user": "a:settings:texts",
        "a:set:free_volume_gb": "a:settings",
        "a:audit": "a:stats",
        "a:tkt:4:reply": "a:tkt:4",
        "u:cfg:9": "u:cfgs:0",
        "u:help:ios": "u:help",
    }
    for data, expected in cases.items():
        actual = nav.parent_of(data, "MISS")
        assert actual == expected, f"{data}: انتظار {expected}، نتیجه {actual}"


@scenario("Callback ناشناس کاربر را با Edit به منو برمی‌گرداند (بدون پیام جدید)")
async def t_unknown_callback(ctx, tg):
    await ctx.router.dispatch(message(USER, "/start"))
    tg.clear()
    await ctx.router.dispatch(callback(USER, "u:this:does:not:exist", message_id=1300))
    assert "منقضی" in answers(tg), f"پیام انقضای دکمه نمایش داده نشد: {answers(tg)!r}"
    assert not tg.sent("sendMessage"), "برای Callback ناشناس پیام جدید ساخته شد"


# ══ ۴. Settings ══════════════════════════════════════════════════════════════
@scenario("همه گزینه‌های Settings دارای Handler و Backend کامل هستند")
async def t_settings_complete(ctx, tg):
    pages = ["a:settings", "a:settings:free", "a:settings:texts"]
    seen: list[dict] = []
    for index, page in enumerate(pages):
        tg.clear()
        await ctx.router.dispatch(callback(SUPER_ADMIN, page, message_id=1400 + index))
        assert tg.buttons(), f"صفحه {page} دکمه‌ای ندارد"
        seen.extend(tg.buttons())

    checked = 0
    for index, button in enumerate(seen):
        data = button.get("callback_data")
        if not data or "بازگشت" in button["text"]:
            continue
        payload = ctx.security.verify_callback(data) if "~" in data else data
        assert payload, f"دکمه «{button['text']}» امضای نامعتبر دارد"
        tg.clear()
        handled = await ctx.admin.handle(ctx.tgapi.client, ctx.store.TG_USERS[str(SUPER_ADMIN)],
                                         SUPER_ADMIN, 1500 + index, data, f"cb-set-{index}")
        assert handled, f"گزینه Settings بدون Handler: «{button['text']}» ({data})"
        checked += 1
    assert checked >= 10, f"تعداد گزینه‌های بررسی‌شده کم است: {checked}"


@scenario("هر Setting عددی ذخیره، Load و در UI Refresh می‌شود")
async def t_setting_roundtrip(ctx, tg):
    values = {
        "free_volume_gb": "25", "free_speed_mbps": "10", "free_duration_days": "45",
        "free_ip_limit": "3", "free_quota_total": "4", "free_daily_limit": "2",
        "free_cooldown_seconds": "30", "broadcast_rate": "15",
        "rate_limit_per_minute": "40",
    }
    for index, (key, raw) in enumerate(values.items()):
        await ctx.router.dispatch(callback(SUPER_ADMIN, f"a:set:{key}", message_id=1600 + index))
        await ctx.router.dispatch(message(SUPER_ADMIN, raw))
        stored = ctx.store.setting(key)
        assert float(stored) == float(raw), f"{key} ذخیره نشد: {stored}"

    snapshot = ctx.store.export_state()
    fresh = bootstrap()
    fresh.store.import_state(snapshot)
    for key, raw in values.items():
        assert float(fresh.store.setting(key)) == float(raw), f"{key} بعد از Restart Load نشد"

    tg.clear()
    await ctx.router.dispatch(callback(SUPER_ADMIN, "a:settings:free", message_id=1700))
    body = " ".join(b["text"] for b in tg.buttons())
    assert "25" in body and "45" in body, f"UI مقدار جدید را Refresh نکرد: {body}"


@scenario("ادمین بدون MANAGE_SETTINGS نمی‌تواند Rate Limit را عوض کند")
async def t_setting_permission(ctx, tg):
    await make_admin(ctx, ADMIN)
    assert not ctx.security.has_permission(ADMIN, ctx.security.MANAGE_SETTINGS)
    assert ctx.security.has_permission(ADMIN, ctx.security.MANAGE_QUOTA)

    before = ctx.store.setting("rate_limit_per_minute")
    await ctx.router.dispatch(callback(ADMIN, "a:set:rate_limit_per_minute", message_id=1800))
    assert "دسترسی" in answers(tg), f"دسترسی رد نشد: {answers(tg)!r}"
    await ctx.router.dispatch(message(ADMIN, "999"))
    assert ctx.store.setting("rate_limit_per_minute") == before, "تنظیم بدون دسترسی عوض شد!"

    # ولی کلیدهای سهمیه‌ای برای همین ادمین مجاز است
    tg.clear()
    await ctx.router.dispatch(callback(ADMIN, "a:set:free_volume_gb", message_id=1801))
    await ctx.router.dispatch(message(ADMIN, "77"))
    assert float(ctx.store.setting("free_volume_gb")) == 77.0, "کلید سهمیه‌ای اعمال نشد"


# ══ ۵+۶+۷+۸. کانفیگ رایگان، لینک ساب و دکمه‌های کپی ══════════════════════════
async def issue_free(ctx, tg, origin: str = "u:free:menu", message_id: int = 2000):
    ctx.store.set_setting("require_channels", False)
    await ctx.router.dispatch(message(USER, "/start"))
    tg.clear()
    await ctx.router.dispatch(callback(USER, origin, message_id=message_id))
    return ctx.configs.user_configs(USER)[0]


@scenario("لینک ساب از همان سیستم پنل تولید می‌شود (سیستم موازی ساخته نشده)")
async def t_sub_source(ctx, tg):
    item = await issue_free(ctx, tg)
    uid = item["link_uid"]
    expected = ctx.panel.subscription_url_for_uid(uid)
    assert ctx.configs.subscription_url(item) == expected, "ربات لینک ساب را خودش می‌سازد"
    assert expected == f"https://panel.example.com/sub/{uid}", expected
    assert expected == ctx.panel.get_link_info_sub(uid) if hasattr(ctx.panel, "get_link_info_sub") \
        else True


@scenario("یک پیام واحد با اطلاعات کانفیگ و ترتیب دقیق چهار دکمه")
async def t_free_single_message(ctx, tg):
    item = await issue_free(ctx, tg)

    assert not tg.sent("sendMessage"), \
        f"پیام اضافه ساخته شد: {[p.get('text') for p in tg.sent('sendMessage')]}"
    edits = tg.sent("editMessageText")
    assert len(edits) == 1, f"باید فقط یک پیام واحد باشد، شد {len(edits)}"

    body = edits[-1]["text"]
    for needed in ("آماده است", "حجم:", "مدت اعتبار:", "تاریخ انقضا:", "سرعت:"):
        assert needed in body, f"«{needed}» در پیام نیست"
    assert "در حال ارسال" not in body, "پیام واسط حذف نشده"

    rows = edits[-1]["reply_markup"]["inline_keyboard"]
    assert len(rows) == 4, f"باید دقیقاً ۴ ردیف باشد: {rows}"
    labels = [row[0]["text"] for row in rows]
    assert "کپی لینک ساب" in labels[0], f"ردیف ۱ باید «کپی لینک ساب» باشد: {labels}"
    assert "کپی کانفیگ" in labels[1], f"ردیف ۲ باید «کپی کانفیگ» باشد: {labels}"
    assert "کانفیگ‌های من" in labels[2], f"ردیف ۳ باید «کانفیگ‌های من» باشد: {labels}"
    assert "بازگشت" in labels[3], f"ردیف ۴ باید «بازگشت» باشد: {labels}"
    for row in rows:
        assert row[0].get("style") in ("primary", "success", "danger"), f"style ندارد: {row}"


@scenario("«کپی لینک ساب» فقط Subscription URL را کپی می‌کند، نه کانفیگ خام")
async def t_copy_sub(ctx, tg):
    item = await issue_free(ctx, tg)
    rows = tg.sent("editMessageText")[-1]["reply_markup"]["inline_keyboard"]
    button = rows[0][0]

    assert "copy_text" in button, "دکمه کپی رسمی تلگرام استفاده نشده"
    assert "callback_data" not in button, \
        "دکمه کپی نباید Callback بفرستد (وگرنه پیام/منو عوض می‌شود)"
    value = button["copy_text"]["text"]
    assert value == ctx.panel.subscription_url_for_uid(item["link_uid"]), \
        f"مقدار کپی‌شده لینک ساب نیست: {value}"
    for banned in ("vless://", "vmess://", "trojan://", "{"):
        assert banned not in value, f"مقدار کپی‌شده شامل {banned} است: {value}"


@scenario("«کپی کانفیگ» فقط Raw Config را کپی می‌کند")
async def t_copy_raw(ctx, tg):
    item = await issue_free(ctx, tg)
    rows = tg.sent("editMessageText")[-1]["reply_markup"]["inline_keyboard"]
    button = rows[1][0]

    assert "copy_text" in button, "دکمه کپی رسمی استفاده نشده"
    assert "callback_data" not in button
    value = button["copy_text"]["text"]
    assert value == ctx.configs.raw_config(item), "مقدار با کانفیگ خام واقعی یکی نیست"
    assert value.startswith("vless://"), value
    assert "/sub/" not in value, "کانفیگ خام با لینک ساب قاتی شده"


@scenario("کانفیگ خام بلندتر از سقف رسمی ۲۵۶ کاراکتری بریده نمی‌شود")
async def t_copy_long(ctx, tg):
    ctx.panel.long_vless = True
    item = await issue_free(ctx, tg)
    raw = ctx.configs.raw_config(item)
    assert len(raw) > 256, "سناریو نامعتبر است"

    assert ctx.keyboards.copy_btn("x", raw) is None, "payload بلند باید رد شود، نه بریده"
    edit = tg.sent("editMessageText")[-1]
    rows = edit["reply_markup"]["inline_keyboard"]
    assert len(rows) == 4 and "کپی کانفیگ" in rows[1][0]["text"], "ترتیب دکمه‌ها عوض شد"
    import html
    assert html.escape(raw, quote=False) in edit["text"], \
        "کانفیگ بلند باید در همان پیام قابل کپی باشد"
    for button in (b for row in rows for b in row):
        copied = (button.get("copy_text") or {}).get("text")
        if copied:
            assert copied in (ctx.configs.subscription_url(item), raw), \
                f"مقدار بریده‌شده در دکمه: {copied}"


@scenario("«کانفیگ‌های من» تنها دکمه‌ای است که Navigation می‌کند")
async def t_my_configs(ctx, tg):
    await issue_free(ctx, tg)
    rows = tg.sent("editMessageText")[-1]["reply_markup"]["inline_keyboard"]
    assert rows[2][0]["callback_data"] == "u:cfgs:0", rows[2]
    tg.clear()
    await ctx.router.dispatch(callback(USER, "u:cfgs:0", message_id=2100))
    assert "کانفیگ‌های من" in tg.all_text(), "منوی My Configs باز نشد"


# ══ ۱۴. جلوگیری از Duplicate Config ══════════════════════════════════════════
@scenario("کلیک سریع و پشت‌سرهم، چند کانفیگ/ساب نمی‌سازد و سهمیه را چند بار کم نمی‌کند")
async def t_no_duplicate(ctx, tg):
    ctx.store.set_setting("require_channels", False)
    ctx.store.set_setting("free_quota_total", 5)
    ctx.store.set_setting("free_daily_limit", 0)
    await ctx.router.dispatch(message(USER, "/start"))

    await asyncio.gather(*[
        ctx.router.dispatch(callback(USER, "u:free:menu", message_id=2200 + index))
        for index in range(12)
    ])
    items = ctx.configs.user_configs(USER)
    assert len(items) == 1, f"{len(items)} کانفیگ ساخته شد، باید ۱ باشد"
    assert ctx.quota.status(USER)["used"] == 1, "سهمیه چند بار مصرف شد"
    subs = {ctx.configs.subscription_url(i) for i in items}
    assert len(subs) == 1, f"چند Subscription ساخته شد: {subs}"


@scenario("شکست ارسال تلگرام باعث ساخت کانفیگ دوم نمی‌شود (Retry ایمن)")
async def t_retry_safe(ctx, tg):
    ctx.store.set_setting("require_channels", False)
    ctx.store.set_setting("free_quota_total", 5)
    ctx.store.set_setting("free_daily_limit", 0)
    ctx.store.set_setting("free_cooldown_seconds", 0)
    await ctx.router.dispatch(message(USER, "/start"))

    # ارسال/ویرایش تلگرام شکست می‌خورد: کانفیگ ساخته می‌شود ولی تحویل نمی‌شود.
    tg.fail_methods = {"editMessageText": "Bad Request: chat not found",
                       "sendMessage": "Bad Request: chat not found"}
    await ctx.router.dispatch(callback(USER, "u:free:menu", message_id=2300))
    first = ctx.configs.user_configs(USER)
    assert len(first) == 1, f"کانفیگ ساخته نشد: {first}"
    assert first[0]["delivered"] is False, "کانفیگ اشتباهاً delivered شد"
    used_after_first = ctx.quota.status(USER)["used"]

    # Retry: باید *همان* کانفیگ تحویل شود، نه کانفیگ و ساب دوم.
    tg.fail_methods = {}
    await ctx.router.dispatch(callback(USER, "u:free:menu", message_id=2301))
    second = ctx.configs.user_configs(USER)
    assert len(second) == 1, f"Retry کانفیگ دوم ساخت: {len(second)}"
    assert second[0]["id"] == first[0]["id"], "کانفیگ عوض شد"
    assert second[0]["delivered"] is True, "Retry کانفیگ را تحویل نداد"
    assert ctx.quota.status(USER)["used"] == used_after_first, "سهمیه دوباره مصرف شد"


# ══ اجرای کامل ═══════════════════════════════════════════════════════════════
SCENARIOS = [
    t_admin_rate, t_user_rate, t_admin_edit,
    t_toggle_support, t_toggle_all, t_toggle_persist, t_toggle_no_emoji,
    t_back_chain, t_back_config_origin, t_back_free, t_nav_map, t_unknown_callback,
    t_settings_complete, t_setting_roundtrip, t_setting_permission,
    t_sub_source, t_free_single_message, t_copy_sub, t_copy_raw, t_copy_long,
    t_my_configs, t_no_duplicate, t_retry_safe,
]


async def main() -> int:
    for run in SCENARIOS:
        await run()
    print("\n" + "=" * 64)
    print("تست اصلاحات درخواستی (Rate Limit / Toggle / Navigation / Settings / Sub)")
    print("=" * 64)
    passed = 0
    for name, ok, err in _results:
        print(f"{'✅' if ok else '❌'}  {name}")
        if not ok:
            print(f"      {err[:600]}")
        passed += ok
    print("=" * 64)
    print(f"{passed}/{len(_results)} سناریو موفق")
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
