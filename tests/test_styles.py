"""تست دکمه‌های رنگی رسمی تلگرام.

مطابق مستندات رسمی Bot API، InlineKeyboardButton فیلد اختیاری `style` با مقادیر
primary / success / danger دارد. این تست تضمین می‌کند:

  * فقط از همین فیلد رسمی استفاده شده و هیچ روش غیررسمی‌ای به کار نرفته است.
  * هیچ‌جا ایموجی به‌عنوان *جایگزین رنگ* استفاده نشده (مثل 🔴/🟢 روی دکمه تأیید/حذف).
  * نقش معنایی رنگ‌ها درست است: ورود=primary، افزودن/تأیید=success، حذف/لغو=danger.

اجرا:  python3 tests/test_styles.py
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "stubs"))
sys.path.insert(0, str(ROOT))

os.environ["TELEGRAM_SUPER_ADMIN_IDS"] = "900001"

from tests.harness import FakeTelegram, bootstrap, callback, message  # noqa: E402

VALID = {"primary", "success", "danger"}
SUPER_ADMIN = 900001
USER = 500001

results: list[tuple[str, bool, str]] = []


def check(name, fn):
    try:
        fn()
        results.append((name, True, ""))
    except AssertionError as exc:
        results.append((name, False, str(exc) or "assertion failed"))
    except Exception as exc:
        results.append((name, False, f"{type(exc).__name__}: {exc}"))


def walk(markup: dict):
    for row in (markup or {}).get("inline_keyboard", []):
        for button in row:
            yield button


async def main():
    ctx = bootstrap()
    tg = FakeTelegram().install()
    k = ctx.keyboards

    # ── ۱. مقادیر مجاز ──
    def constants():
        assert (k.PRIMARY, k.SUCCESS, k.DANGER) == ("primary", "success", "danger"), \
            "مقادیر style با مستندات رسمی تلگرام یکی نیست"
    check("مقادیر style مطابق مستندات رسمی است", constants)

    # ── ۲. style نامعتبر هرگز به API فرستاده نمی‌شود ──
    def invalid_dropped():
        assert "style" not in k.btn("x", "d", "rainbow"), "style نامعتبر به payload اضافه شد"
        assert "style" not in k.btn("x", "d"), "style بی‌دلیل اضافه شد"
        assert k.btn("x", "d", "danger")["style"] == "danger"
    check("style نامعتبر به تلگرام فرستاده نمی‌شود", invalid_dropped)

    # ── ۳. نقش معنایی رنگ‌ها ──
    def semantics():
        assert k.enter("ورود", "d")["style"] == "primary", "ورود به بخش باید primary باشد"
        assert k.add("افزودن", "d")["style"] == "success", "افزودن باید success باشد"
        assert k.confirm("d")["style"] == "success", "تأیید باید success باشد"
        assert k.danger("حذف", "d")["style"] == "danger", "حذف باید danger باشد"
        assert k.cancel("d")["style"] == "danger", "لغو باید danger باشد"
        assert k.back("d")["style"] == "primary", "بازگشت باید primary باشد"
        row = k.confirm_row("ok", "no")
        assert [b["style"] for b in row] == ["success", "danger"], f"ردیف تأیید: {row}"
    check("نقش معنایی رنگ‌ها درست است", semantics)

    # ── ۴. همه دکمه‌های منوها style معتبر دارند ──
    def menus_valid():
        for name, markup in (("منوی کاربر", k.user_main()), ("پنل ادمین", k.admin_main())):
            buttons = list(walk(markup))
            assert buttons, f"{name} دکمه‌ای ندارد"
            for button in buttons:
                assert button.get("style") in VALID, \
                    f"{name}: دکمه «{button['text']}» style ندارد یا نامعتبر است ({button.get('style')})"
    check("همه دکمه‌های منوی اصلی style رسمی دارند", menus_valid)

    # ── ۵. هیچ ایموجی به‌عنوان جایگزین رنگ نیست ──
    def no_emoji_as_color():
        """ایموجی‌های دایره‌ی رنگی نباید نقش رنگ دکمه را بازی کنند.

        استفاده از آن‌ها به‌عنوان *نشانگر وضعیت* (مثل 🟢 فعال بودن کانفیگ در لیست)
        اشکالی ندارد؛ چیزی که ممنوع است استفاده به‌جای رنگ دکمه‌های عملیاتی است.
        """
        color_emoji = ("🔴", "🟢", "🔵", "🟡", "⚪️", "🟠", "🟣")
        for markup in (k.user_main(), k.admin_main()):
            for button in walk(markup):
                for emoji in color_emoji:
                    assert emoji not in button["text"], \
                        f"دکمه «{button['text']}» از ایموجی رنگی به‌جای style استفاده می‌کند"
        # دکمه‌های عملیاتی کلیدی هم بررسی می‌شوند
        for button in (k.confirm("d"), k.cancel("d"), k.danger("حذف", "d"), k.add("افزودن", "d")):
            for emoji in color_emoji:
                assert emoji not in button["text"], f"ایموجی رنگی در «{button['text']}»"
    check("ایموجی جایگزین رنگ دکمه نشده است", no_emoji_as_color)

    # ── ۶. هیچ روش غیررسمی در کد نیست ──
    def no_unofficial():
        pattern = re.compile(r'"style"\s*:\s*"([a-z_]+)"')
        for path in ROOT.glob("botsys/**/*.py"):
            body = path.read_text(encoding="utf-8")
            for found in pattern.findall(body):
                assert found in VALID, f"{path.name}: style غیرمجاز «{found}»"
            for banned in ("color=", "button_color", "bg_color", "html_color"):
                assert banned not in body, f"{path.name}: روش غیررسمی رنگ‌آمیزی «{banned}»"
    check("هیچ روش غیررسمی رنگ‌آمیزی استفاده نشده", no_unofficial)

    # ── ۷. در پیام‌های واقعی هم style ارسال می‌شود ──
    await ctx.router.dispatch(message(USER, "/start"))

    def runtime_user():
        markup = tg.sent("sendMessage")[-1].get("reply_markup") or {}
        buttons = list(walk(markup))
        assert buttons, "منوی کاربر بدون دکمه ارسال شد"
        styled = [b for b in buttons if b.get("style") in VALID]
        assert len(styled) == len(buttons), f"دکمه‌های بی‌style: {[b['text'] for b in buttons if not b.get('style')]}"
        free = next(b for b in buttons if "رایگان" in b["text"])
        assert free["style"] == "success", "دکمه دریافت کانفیگ رایگان باید success باشد"
    check("منوی واقعی کاربر با style ارسال می‌شود", runtime_user)

    # ── ۸. دکمه حذف در تأیید دومرحته‌ای واقعی danger/success است ──
    ctx.store.set_setting("require_channels", False)
    result = await ctx.issuing.issue_free_config(USER)
    tg.clear()
    await ctx.router.dispatch(callback(
        SUPER_ADMIN, ctx.security.sign_callback(f"a:cfg:{result.user_config['id']}:del")))

    def runtime_delete():
        buttons = tg.buttons()
        assert buttons, "صفحه تأیید حذف دکمه‌ای ندارد"
        styles = {b["text"]: b.get("style") for b in buttons}
        assert "success" in styles.values(), f"دکمه تأیید success نیست: {styles}"
        assert "danger" in styles.values(), f"دکمه لغو danger نیست: {styles}"
    check("تأیید حذف واقعی از رنگ‌های درست استفاده می‌کند", runtime_delete)

    # ── ۹. دکمه کپی رسمی (CopyTextButton) ──
    def copy_button():
        button = k.copy_btn("کپی", "vless://abc", k.SUCCESS)
        assert button["copy_text"]["text"] == "vless://abc", "CopyTextButton درست ساخته نشد"
        assert button["style"] == "success"
        assert "callback_data" not in button, "CopyTextButton نباید callback_data داشته باشد"
    check("دکمه کپی رسمی درست ساخته می‌شود", copy_button)

    print("\n" + "=" * 64)
    print("تست دکمه‌های رنگی رسمی تلگرام")
    print("=" * 64)
    passed = 0
    for name, ok, err in results:
        print(f"{'✅' if ok else '❌'}  {name}")
        if not ok:
            print(f"      {err[:500]}")
        passed += ok
    print("=" * 64)
    print(f"{passed}/{len(results)} بررسی موفق")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
