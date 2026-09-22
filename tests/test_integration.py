"""تست یکپارچگی: main.py واقعی با زیرسیستم ربات.

FastAPI/aiofiles/psutil در محیط تست Stub شده‌اند، ولی main.py *واقعی* اجرا
می‌شود. هدف: اطمینان از اینکه پنل موجود سالم بالا می‌آید، روت‌های قبلی خراب
نشده‌اند، و State ربات در همان فایل پنل ذخیره/بازیابی می‌شود.

اجرا:  python3 tests/test_integration.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "stubs"))
sys.path.insert(0, str(ROOT))

DATA_DIR = tempfile.mkdtemp()
os.environ["RAILWAY_VOLUME_MOUNT_PATH"] = DATA_DIR
os.environ["ADMIN_USERNAME"] = "VodiAdmin"
os.environ["ADMIN_PASSWORD"] = "integration-pass"
os.environ["TELEGRAM_SUPER_ADMIN_IDS"] = "900001"
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:INTEGRATION-TOKEN"
os.environ["TELEGRAM_UPDATE_MODE"] = "polling"

results: list[tuple[str, bool, str]] = []


def check(name: str, fn):
    try:
        fn()
        results.append((name, True, ""))
    except AssertionError as exc:
        results.append((name, False, str(exc) or "assertion failed"))
    except Exception:
        results.append((name, False, traceback.format_exc(limit=3)))


async def main():
    import main as panel

    paths = {r.path for r in panel.app.routes}

    # ── ۱. پنل موجود سالم است ──
    def existing_routes():
        expected = ["/health", "/login", "/dashboard", "/stats", "/api/links",
                    "/api/subs", "/api/settings", "/api/plans", "/api/categories",
                    "/api/telemetry", "/api/activity", "/logout"]
        missing = [p for p in expected if p not in paths]
        assert not missing, f"روت‌های قبلی پنل گم شدند: {missing}"
    check("روت‌های فعلی پنل حفظ شده‌اند", existing_routes)

    # ── ۲. روت‌های جدید ربات ──
    def bot_routes():
        assert panel._BOTSYS_READY, "زیرسیستم ربات بارگذاری نشد"
        expected = ["/api/bot/status", "/api/bot/settings", "/api/bot/channels",
                    "/api/bot/users", "/api/bot/stats", "/api/bot/audit",
                    "/api/bot/tickets", "/api/bot/broadcast"]
        missing = [p for p in expected if p not in paths]
        assert not missing, f"روت‌های ربات ثبت نشدند: {missing}"
        webhook = [p for p in paths if "/telegram/webhook" in p]
        assert webhook, f"روت Webhook ثبت نشد: {sorted(paths)[:5]}"
        assert all("{secret}" in p for p in webhook), f"Webhook بدون Secret: {webhook}"
    check("روت‌های جدید ربات ثبت شده‌اند", bot_routes)

    # ── ۳. Migration ها ──
    from botsys import store as bstore

    def migrations():
        assert bstore.schema_version() == bstore.SCHEMA_VERSION, \
            f"Migration اجرا نشد: {bstore.schema_version()} != {bstore.SCHEMA_VERSION}"
        assert "super_admin" in bstore.TG_ROLES and "admin" in bstore.TG_ROLES
        assert "MANAGE_SUPPORT" in bstore.TG_ROLES["admin"]["permissions"]
    await panel.load_state()
    bstore.run_migrations()
    check("Migration ها اجرا می‌شوند", migrations)

    # ── ۴. ساخت کانفیگ پنل سالم است (قابلیت اصلی فعلی) ──
    uid, record = await panel.make_link(label="تست یکپارچگی", limit_bytes=1024 ** 3)

    def panel_config():
        assert uid in panel.LINKS, "ساخت کانفیگ پنل خراب شد"
        # نکته: پنل نام کانفیگ را sanitize می‌کند (رفتار فعلی و دست‌نخورده).
        assert record["label"], "برچسب کانفیگ خالی شد"
        assert record["limit_bytes"] == 1024 ** 3
        link = panel.vless_link_for_link(panel.LINKS[uid], uid, panel.get_host())
        assert link.startswith(("vless://", "vmess://", "trojan://", "ss://")), link
    check("ساخت کانفیگ و لینک پنل کار می‌کند", panel_config)

    # ── ۵. ربات از همان سرویس پنل استفاده می‌کند (سیستم موازی نیست) ──
    from botsys.services import configs as bconfigs
    from botsys.services import issuing, users

    await users.touch({"id": 700001, "username": "integ", "first_name": "کاربر"})
    bstore.set_setting("require_channels", False)
    bstore.set_setting("free_quota_total", 2)
    bstore.set_setting("free_volume_gb", 5)
    result = await issuing.issue_free_config(700001)

    def shared_service():
        assert result.ok, f"صدور کانفیگ ناموفق: {result.reason}"
        link_uid = result.user_config["link_uid"]
        assert link_uid in panel.LINKS, "کانفیگ ربات در LINKS پنل ثبت نشد (سیستم موازی!)"
        assert panel.LINKS[link_uid]["limit_bytes"] == 5 * 1024 ** 3
        uri = bconfigs.connection_uri(result.user_config)
        assert uri and panel.get_host() in uri, f"لینک اتصال از سرویس پنل ساخته نشد: {uri}"
    check("ربات از سرویس ساخت کانفیگ پنل استفاده می‌کند", shared_service)

    # ── ۶. ذخیره و بازیابی State در همان فایل ──
    await panel.save_state()
    raw = json.loads(Path(panel.DATA_FILE).read_text(encoding="utf-8"))

    def persistence():
        for key in ("links", "subs", "admins"):
            assert key in raw, f"کلید قبلی پنل گم شد: {key}"
        for key in ("tg_users", "tg_user_configs", "tg_settings", "tg_meta", "tg_texts"):
            assert key in raw, f"کلید ربات ذخیره نشد: {key}"
        assert "700001" in raw["tg_users"], "کاربر تلگرام ذخیره نشد"
        assert raw["tg_settings"]["free_quota_total"] == 2
        assert raw["tg_meta"]["schema_version"] == bstore.SCHEMA_VERSION
        assert raw["links"], "کانفیگ‌های پنل در همان فایل هستند"
    check("State ربات در همان فایل پنل ذخیره می‌شود", persistence)

    # ── ۷. Health Check ──
    health = await panel.health()

    def health_check():
        assert health["status"] == "ok"
        assert "telegram_bot" in health, "وضعیت ربات در Health Check نیست"
        assert "schema_version" in health["telegram_bot"] or not health["telegram_bot"]["running"]
    check("Health Check وضعیت ربات را گزارش می‌کند", health_check)

    # ── ۸. امنیت Webhook ──
    from botsys import runner

    def webhook_security():
        secret = runner.cfg.WEBHOOK_SECRET
        assert runner.verify_webhook_secret(secret, secret), "Secret درست رد شد"
        assert not runner.verify_webhook_secret("wrong-secret", secret), "Secret غلط پذیرفته شد!"
        assert not runner.verify_webhook_secret(secret, "wrong-header"), "هدر غلط پذیرفته شد!"
        assert not runner.verify_webhook_secret("", None), "Secret خالی پذیرفته شد!"
    check("Webhook فقط با Secret درست پذیرفته می‌شود", webhook_security)

    # ── ۹. توکن از Environment خوانده می‌شود و Hard-code نیست ──
    def token_from_env():
        assert runner.resolve_token() == "123456:INTEGRATION-TOKEN", "توکن از ENV خوانده نشد"
        sources = ROOT.glob("botsys/**/*.py")
        for path in sources:
            body = path.read_text(encoding="utf-8")
            assert "123456:" not in body, f"توکن داخل کد پیدا شد: {path}"
            assert ":AAH" not in body, f"الگوی توکن تلگرام داخل کد: {path}"
    check("توکن فقط از Environment خوانده می‌شود", token_from_env)

    # ── ۱۰. PORT از Environment ──
    def port_env():
        assert isinstance(panel.PORT, int) and panel.PORT > 0
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        assert 'environ' in src and 'PORT' in src, "PORT از Environment خوانده نمی‌شود"
    check("PORT از Environment خوانده می‌شود", port_env)

    # ── ۱۱. پنل ادمین قدیمی (ابزارهای پیشرفته) حفظ شده ──
    def legacy_preserved():
        import telegram_bot
        for fn in ("_handle_callback", "_handle_message", "_wizard_prompt",
                   "_main_menu_kb", "_links_list_kb", "_clients_list_kb"):
            assert hasattr(telegram_bot, fn), f"قابلیت قدیمی گم شد: {fn}"
        from botsys import legacy
        assert legacy.owns_callback("newcfg"), "Callback ویزارد قدیمی مسیریابی نمی‌شود"
        assert legacy.owns_callback("w:proto:vless-ws")
        assert not legacy.owns_callback("u:free"), "تداخل با Callback کاربر"
        assert not legacy.owns_callback("a:users"), "تداخل با Callback ادمین"
    check("ابزارهای ربات قبلی حفظ شده‌اند", legacy_preserved)

    # ── ۱۲. .env.example کامل است ──
    def env_example():
        path = ROOT / ".env.example"
        assert path.exists(), ".env.example وجود ندارد"
        body = path.read_text(encoding="utf-8")
        for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_URL",
                    "TELEGRAM_SUPER_ADMIN_IDS", "TELEGRAM_UPDATE_MODE",
                    "TELEGRAM_WEBHOOK_SECRET", "PORT", "ADMIN_PASSWORD",
                    "RAILWAY_VOLUME_MOUNT_PATH"):
            assert var in body, f"{var} در .env.example نیست"
        assert "123456:AA" not in body, "Secret واقعی در .env.example!"
    check(".env.example شامل همه متغیرها است", env_example)

    print("\n" + "=" * 64)
    print("تست یکپارچگی پنل + ربات")
    print("=" * 64)
    passed = 0
    for name, ok, err in results:
        print(f"{'✅' if ok else '❌'}  {name}")
        if not ok:
            print("      " + err.replace("\n", "\n      ")[:900])
        passed += ok
    print("=" * 64)
    print(f"{passed}/{len(results)} بررسی موفق")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
