"""پایش عضویت اجباری کانال‌ها و قطع/وصل خودکار کانفیگ‌ها."""

from __future__ import annotations

import asyncio
import logging

from .. import keyboards as k, security, store
from . import channels, configs, users
from ..tgapi import client

logger = logging.getLogger("vodiwalker.botsys.membership")

INTERVAL_SECONDS = 30


def _has_configs(telegram_id: int) -> bool:
    return bool(configs.user_configs(telegram_id))


async def _set_configs_membership_state(telegram_id: int, blocked: bool) -> int:
    """فقط کانفیگ‌هایی را که توسط همین سیستم قطع شده‌اند دوباره وصل می‌کند."""
    changed = 0
    for item in configs.user_configs(telegram_id):
        link = configs.link_of(item)
        if not link:
            continue
        if blocked:
            if bool(link.get("active")):
                await configs.set_active(0, item["id"], False)
                item["auto_disabled_by_membership"] = True
                changed += 1
            elif item.get("auto_disabled_by_membership"):
                # قبلاً توسط ما قطع شده و همچنان قطع است.
                continue
        else:
            if item.get("auto_disabled_by_membership"):
                if not link.get("active"):
                    await configs.set_active(0, item["id"], True)
                item["auto_disabled_by_membership"] = False
                changed += 1
    return changed


def _join_keyboard(missing: list[dict]) -> dict:
    rows = []
    for channel in missing[:10]:
        url = channels.join_url(channel)
        if url:
            rows.append([k.url_btn(f"📢 عضویت در {channel.get('title') or 'کانال'}"[:40], url, k.PRIMARY)])
    rows.append([k.btn("🔄 بررسی عضویت", "u:membership:check", k.SUCCESS)])
    rows.append([k.back("u:menu")])
    return k.kb(rows)


async def enforce_user(telegram_id: int, notify: bool = True) -> dict:
    """عضویت کاربر را بررسی و در صورت تغییر وضعیت، کانفیگ‌ها را قطع/وصل می‌کند."""
    if not store.setting("require_channels", True):
        return {"changed": False, "missing": [], "unverifiable": []}

    rec = users.get(telegram_id)
    if not rec or not _has_configs(telegram_id):
        return {"changed": False, "missing": [], "unverifiable": []}

    # Join/Leave update و fallback monitor ممکن است هم‌زمان برسند.
    async with store.user_lock(telegram_id):
        was_blocked = bool(rec.get("membership_blocked"))
        ok, missing, unverifiable = await channels.check_membership(telegram_id, force=True)

        # «نامشخص» نباید وضعیت قبلی را دست‌کاری کند.
        if unverifiable:
            return {"changed": False, "missing": missing, "unverifiable": unverifiable}

        changed = False
        if missing:
            await _set_configs_membership_state(telegram_id, True)
            changed = not was_blocked
            rec["membership_blocked"] = True

            if notify and changed:
                text = (
                    "⛔️ <b>کانفیگ‌های شما قطع شدند</b>\n\n"
                    "به دلیل خروج از کانال/کانال‌های اجباری، کانفیگ‌های شما موقتاً غیرفعال شدند.\n"
                    "برای وصل شدن دوباره، در کانال‌های زیر عضو شوید و سپس «بررسی عضویت» را بزنید."
                )
                kb = _join_keyboard(missing)
                old_mid = rec.get("membership_message_id")
                result = None
                if old_mid:
                    result = await client.edit_message(telegram_id, int(old_mid), text, kb)
                if not old_mid or not result:
                    result = await client.send_message(telegram_id, text, kb)
                    if isinstance(result, dict):
                        rec["membership_message_id"] = result.get("message_id")

        else:
            # فقط کانفیگ‌هایی را که همین سیستم قطع کرده دوباره فعال کن.
            await _set_configs_membership_state(telegram_id, False)
            changed = was_blocked
            rec["membership_blocked"] = False

            if notify and was_blocked:
                text = (
                    "✅ <b>عضویت شما تأیید شد</b>\n\n"
                    "کانفیگ‌هایی که به‌دلیل عضویت قطع شده بودند دوباره فعال شدند."
                )
                kb = k.kb([[k.enter("📦 کانفیگ‌های من", "u:cfgs:0")], [k.back("u:menu")]])
                old_mid = rec.get("membership_message_id")
                result = None
                if old_mid:
                    result = await client.edit_message(telegram_id, int(old_mid), text, kb)
                if not old_mid or not result:
                    result = await client.send_message(telegram_id, text, kb)
                    if isinstance(result, dict):
                        rec["membership_message_id"] = result.get("message_id")

        if changed:
            try:
                from main import save_state
                await save_state()
            except Exception:
                logger.warning("could not persist membership state", exc_info=True)

        return {"changed": changed, "missing": missing, "unverifiable": unverifiable}


async def check_callback(telegram_id: int, chat_id: int, message_id: int | None) -> dict:
    """بررسی دستی عضویت؛ بدون صدور کانفیگ جدید و با API تازه."""
    if not store.setting("require_channels", True):
        return {"ok": True, "missing": []}

    rec = users.get(telegram_id)
    if not rec:
        return {"ok": False, "missing": []}

    async with store.user_lock(telegram_id):
        channels.invalidate(telegram_id)
        ok, missing, unverifiable = await channels.check_membership(telegram_id, force=True)

        if missing:
            rec["membership_blocked"] = True
            await _set_configs_membership_state(telegram_id, True)
            text = (
                "⛔️ <b>هنوز عضویت کامل نیست</b>\n\n"
                "لطفاً در همه کانال‌های زیر عضو شوید و دوباره بررسی کنید."
            )
            if message_id:
                await client.edit_message(chat_id, message_id, text, _join_keyboard(missing))
            else:
                await client.send_message(chat_id, text, _join_keyboard(missing))
            try:
                from main import save_state
                await save_state()
            except Exception:
                logger.warning("could not persist membership state", exc_info=True)
            return {"ok": False, "missing": missing, "unverifiable": unverifiable}

        if unverifiable:
            await client.answer_callback(
                None,
                "⚠️ عضویت بعضی کانال‌ها قابل بررسی نیست. مطمئن شوید ربات در آن کانال‌ها Admin است.",
                alert=True,
            )
            return {"ok": False, "missing": [], "unverifiable": unverifiable}

        was_blocked = bool(rec.get("membership_blocked"))
        rec["membership_blocked"] = False
        await _set_configs_membership_state(telegram_id, False)

        if was_blocked:
            try:
                from main import save_state
                await save_state()
            except Exception:
                logger.warning("could not persist membership state", exc_info=True)

        text = "✅ <b>عضویت تأیید شد</b>\n\nکانفیگ‌های قطع‌شده دوباره فعال شدند."
        kb = k.kb([
            [k.enter("📦 کانفیگ‌های من", "u:cfgs:0")],
            [k.back("u:menu")],
        ])
        if message_id:
            await client.edit_message(chat_id, message_id, text, kb)
        else:
            await client.send_message(chat_id, text, kb)
        return {"ok": True, "missing": []}


async def monitor_loop() -> None:
    """Fallback برای Webhook/Polling: هر چند ثانیه اعضای دارای کانفیگ را چک می‌کند."""
    logger.info("membership monitor started")
    while True:
        try:
            if store.setting("require_channels", True):
                targets = [
                    int(r["telegram_id"]) for r in store.TG_USERS.values()
                    if r.get("telegram_id") and _has_configs(int(r["telegram_id"]))
                ]
                for telegram_id in targets:
                    try:
                        await enforce_user(telegram_id, notify=True)
                    except Exception:
                        logger.exception("membership monitor failed for user %s", telegram_id)
                    await asyncio.sleep(0.05)
            await asyncio.sleep(INTERVAL_SECONDS)
        except asyncio.CancelledError:
            logger.info("membership monitor cancelled")
            raise
        except Exception:
            logger.exception("membership monitor loop error")
            await asyncio.sleep(INTERVAL_SECONDS)
