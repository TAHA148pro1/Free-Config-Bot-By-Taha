"""مسیریاب Update های تلگرام.

مسئولیت‌ها:
  * عبور دادن Update از Middleware ها
  * تفکیک پیام/Callback
  * تشخیص ادمین و ارسال به پنل مناسب
  * مدیریت خطا: هیچ Exception ای نباید حلقه‌ی دریافت Update را بکشد و هیچ
    خطای فنی‌ای به کاربر عادی نشان داده نشود.
"""

from __future__ import annotations

import logging

from .. import keyboards as k
from .. import legacy, middlewares, security, store
from ..tgapi import client
from ..services import membership
from . import admin as admin_handlers
from . import user as user_handlers

logger = logging.getLogger("vodiwalker.botsys.router")


async def dispatch(update: dict) -> None:
    """ورودی اصلی. هر خطایی اینجا مهار می‌شود."""
    try:
        if "message" in update:
            await _on_message(update["message"])
        elif "callback_query" in update:
            await _on_callback(update["callback_query"])
        elif "chat_member" in update:
            await _on_chat_member(update["chat_member"])
    except middlewares.Rejected as rejected:
        await _reply_rejected(update, rejected)
    except Exception:
        logger.exception("unhandled error while dispatching update")
        await _reply_generic_error(update)


# ── پیام‌ها ──────────────────────────────────────────────────────────────────
async def _on_message(message: dict) -> None:
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None or chat.get("type") != "private":
        return  # ربات فقط در چت خصوصی کار می‌کند

    # Backup ZIP ارسالی Super Admin مستقیماً وارد Restore می‌شود.
    document = message.get("document")
    if document and security.is_super_admin(int(chat_id)):
        tg_user = message.get("from") or {}
        rec = await middlewares.process(tg_user)
        if await admin_handlers.handle_pending_document(client, rec, int(chat_id), document):
            return
        # بدون فعال‌کردن حالت Restore هم یک بکاپ معتبر را نپذیر؛ این کار جلوی
        # جایگزینی ناخواسته State با هر ZIP تصادفی را می‌گیرد.
        await client.send_message(int(chat_id), "ℹ️ برای ریستور ابتدا از منوی 💾 بکاپ / ریستور گزینه «ریستور بکاپ» را بزنید.")
        return

    tg_user = message.get("from") or {}
    rec = await middlewares.process(tg_user)
    telegram_id = int(rec["telegram_id"])

    text = (message.get("text") or message.get("caption") or "").strip()
    photo = ""
    photos = message.get("photo") or []
    if photos:
        photo = photos[-1].get("file_id") or ""

    # ── دستورات ──
    if text.startswith("/"):
        command = text.split()[0].split("@")[0].lower()
        if command in ("/start", "/menu"):
            user_handlers.clear_pending(telegram_id)
            admin_handlers.clear_pending(telegram_id)
            await _send_home(chat_id, rec)
            return
        if command == "/cancel":
            user_handlers.clear_pending(telegram_id)
            admin_handlers.clear_pending(telegram_id)
            await client.send_message(chat_id, "لغو شد.", None)
            await _send_home(chat_id, rec)
            return
        if command == "/admin":
            if security.is_admin(telegram_id):
                await client.send_message(chat_id, admin_handlers.dashboard_text(telegram_id), k.admin_main(security.is_super_admin(telegram_id)))
            else:
                # هیچ نشانه‌ای از وجود پنل ادمین به کاربر عادی داده نمی‌شود.
                await _send_home(chat_id, rec)
            return
        if command == "/id":
            await client.send_message(chat_id, f"🆔 شناسه شما: <code>{telegram_id}</code>")
            return
        if command == "/support":
            if store.setting("support_enabled", True):
                user_handlers.set_pending(telegram_id, {"action": "support"})
                await client.send_message(chat_id, store.text("support_intro"),
                                          k.kb([[k.cancel("u:menu", "❌ انصراف")]]))
            return
        await _send_home(chat_id, rec)
        return

    # ── ورودی در انتظار ──
    if security.is_admin(telegram_id):
        if await admin_handlers.handle_pending_text(client, rec, chat_id, text, photo=photo):
            return
        # ویزارد ابزارهای پیشرفته‌ی قدیمی (ساخت کانفیگ، گروه ساب) هنوز فعال است.
        if legacy.is_attached() and legacy.has_pending(telegram_id):
            await legacy.handle_message(message)
            return
    if await user_handlers.handle_pending_text(client, rec, chat_id, text):
        return

    # پیام آزاد: کاربر را به منو هدایت کن.
    await _send_home(chat_id, rec)


async def _send_home(chat_id: int, rec: dict, message_id: int | None = None) -> None:
    """منوی خانه. با message_id همان پیام Edit می‌شود (بدون پیام جدید)."""
    telegram_id = int(rec["telegram_id"])
    if security.is_admin(telegram_id):
        text, markup = admin_handlers.dashboard_text(telegram_id), k.admin_main(security.is_super_admin(telegram_id))
    else:
        text = user_handlers.welcome_text(rec)
        markup = k.user_main(bool(store.setting("support_enabled", True)))
    if message_id:
        await client.edit_message(chat_id, message_id, text, markup)
        return
    await client.send_message(chat_id, text, markup)


# ── Callback ها ──────────────────────────────────────────────────────────────
async def _on_callback(callback: dict) -> None:
    cb_id = callback.get("id")
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    data = str(callback.get("data") or "")

    if chat_id is None:
        await client.answer_callback(cb_id)
        return

    # شروع دامنه‌ی پاسخ‌دهی این Callback (جزئیات در tgapi.begin_callback).
    client.begin_callback(cb_id)
    try:
        await _route_callback(callback, cb_id, chat_id, message_id, data)
    finally:
        client.end_callback(cb_id)


async def _route_callback(callback: dict, cb_id: str | None, chat_id: int,
                          message_id: int | None, data: str) -> None:
    tg_user = callback.get("from") or {}
    rec = await middlewares.process(tg_user, is_callback=True)
    telegram_id = int(rec["telegram_id"])

    # نکته‌ی مهم: قبلاً همین‌جا یک Ack *خالی* فرستاده می‌شد. هر callback_query فقط
    # یک بار قابل پاسخ است، پس همه‌ی Notification های واقعی Handler ها
    # («تغییر کرد»، «سهمیه Reset شد»، «لینک کپی شد») توسط تلگرام نادیده گرفته
    # می‌شدند و کاربر هیچ بازخوردی نمی‌دید. حالا Handler ها اول پاسخ می‌دهند و
    # Ack خالی فقط در پایان و فقط اگر کسی پاسخ نداده باشد فرستاده می‌شود
    # (tgapi.answer_callback خودش تکرار را حذف می‌کند).
    handled = False

    if data.startswith("a:"):
        # کاربر عادی هرگز نباید بتواند با ساختن Callback دستی وارد پنل ادمین شود.
        if not security.is_admin(telegram_id):
            logger.warning("non-admin %s attempted admin callback %r", telegram_id, data)
            await client.answer_callback(cb_id, "دسترسی ندارید.", alert=True)
            return
        handled = await admin_handlers.handle(client, rec, chat_id, message_id, data, cb_id)
    elif data.startswith("u:"):
        handled = await user_handlers.handle(client, rec, chat_id, message_id, data, cb_id)
    elif legacy.is_attached() and legacy.owns_callback(data):
        # ابزارهای پیشرفته‌ی ماژول قدیمی: فقط برای ادمین‌های دارای MANAGE_CONFIGS.
        if not security.has_permission(telegram_id, security.MANAGE_CONFIGS):
            await client.answer_callback(cb_id, "دسترسی ندارید.", alert=True)
            return
        await legacy.handle_callback(callback)
        handled = True

    if not handled:
        # Callback قدیمی/ناشناس/تکراری: کاربر را بدون خطای فنی به منو برگردان.
        # با Edit روی همان پیام، تا در Navigation سریع پیام اضافه ساخته نشود.
        await client.answer_callback(cb_id, "این دکمه منقضی شده است.", alert=False)
        await _send_home(chat_id, rec, message_id)

    # اگر هیچ Handler ای پاسخ نداده بود، ساعت شنی را همین‌جا متوقف می‌کنیم.
    await client.answer_callback(cb_id)


async def _on_chat_member(update: dict) -> None:
    """واکنش سریع به join/leave اعضای کانال‌های اجباری."""
    chat = update.get("chat") or {}
    user = update.get("new_chat_member") or {}
    telegram_id = security.valid_telegram_id((user.get("user") or {}).get("id"))
    if telegram_id is None:
        return
    # فقط کانال‌هایی که در پنل تعریف شده‌اند مهم‌اند.
    chat_ref = str(chat.get("id") or "")
    configured = any(str(c.get("chat_id") or "") == chat_ref for c in store.TG_CHANNELS.values())
    if not configured:
        username = str(chat.get("username") or "").lstrip("@").lower()
        configured = any(str(c.get("username") or "").lstrip("@").lower() == username and username
                         for c in store.TG_CHANNELS.values())
    if configured:
        await membership.enforce_user(telegram_id, notify=True)


# ── پاسخ به خطاها ────────────────────────────────────────────────────────────
def _extract_ids(update: dict) -> tuple[int | None, str | None]:
    if "callback_query" in update:
        callback = update["callback_query"]
        return ((callback.get("message") or {}).get("chat") or {}).get("id"), callback.get("id")
    message = update.get("message") or {}
    return (message.get("chat") or {}).get("id"), None


async def _reply_rejected(update: dict, rejected: middlewares.Rejected) -> None:
    if not rejected.reply:
        return
    chat_id, cb_id = _extract_ids(update)
    if cb_id:
        await client.answer_callback(cb_id, rejected.reply[:190], alert=rejected.alert)
        return
    if chat_id:
        await client.send_message(chat_id, rejected.reply)


async def _reply_generic_error(update: dict) -> None:
    """پیام قابل فهم برای کاربر؛ جزئیات فنی فقط در لاگ می‌ماند."""
    chat_id, cb_id = _extract_ids(update)
    text = "⚠️ مشکلی پیش آمد. لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید."
    try:
        if cb_id:
            await client.answer_callback(cb_id, "مشکلی پیش آمد. دوباره تلاش کنید.", alert=True)
        elif chat_id:
            await client.send_message(chat_id, text, k.kb([[k.back("u:menu", "🏠 منوی اصلی")]]))
    except Exception:
        logger.debug("could not deliver error notice", exc_info=True)
