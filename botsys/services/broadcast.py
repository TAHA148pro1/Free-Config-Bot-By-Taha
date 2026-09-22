"""سیستم Broadcast با صف و Rate Limit.

* ارسال در یک Task پس‌زمینه انجام می‌شود تا ربات قفل نشود.
* نرخ ارسال محدود است (پیش‌فرض ۲۰ پیام در ثانیه) تا تلگرام ربات را محدود نکند.
* خطای یک مخاطب باعث توقف کل ارسال نمی‌شود؛ موفق/ناموفق شمرده و ثبت می‌شود.
* اگر کاربری ربات را Block کرده باشد (403)، خودکار غیرفعال علامت می‌خورد.
"""

from __future__ import annotations

import asyncio
import logging

from .. import store
from ..tgapi import TelegramError, client

logger = logging.getLogger("vodiwalker.botsys.broadcast")

_TASKS: dict[str, asyncio.Task] = {}


def get(broadcast_id: str) -> dict | None:
    return store.TG_BROADCASTS.get(str(broadcast_id))


def listing(limit: int = 5) -> list[dict]:
    rows = list(store.TG_BROADCASTS.values())
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return rows[:limit]


async def create(actor_id: int, text: str, photo: str = "", only_active: bool = False) -> dict:
    """یک Broadcast می‌سازد و صف ارسال را در پس‌زمینه شروع می‌کند."""
    from .. import audit
    from . import users

    targets = users.broadcast_targets(only_active=only_active)
    bid = store.next_id(store.TG_BROADCASTS)
    record = {
        "id": bid,
        "actor_id": int(actor_id),
        "text": str(text or "")[:4000],
        "photo": str(photo or ""),
        "only_active": bool(only_active),
        "total": len(targets),
        "sent": 0,
        "failed": 0,
        "status": "queued",
        "created_at": store.now_iso(),
        "finished_at": None,
    }
    store.TG_BROADCASTS[bid] = record
    audit.record(actor_id, "broadcast_start", None, "targets", None, len(targets))

    task = asyncio.create_task(_run(bid, targets))
    _TASKS[bid] = task
    task.add_done_callback(lambda _t, key=bid: _TASKS.pop(key, None))
    return record


async def _run(broadcast_id: str, targets: list[int]) -> None:
    record = store.TG_BROADCASTS.get(str(broadcast_id))
    if record is None:
        return
    record["status"] = "running"

    rate = max(1, int(store.setting("broadcast_rate", 0) or 0) or 20)
    delay = 1.0 / rate
    text = record.get("text") or ""
    photo = record.get("photo") or ""

    for index, chat_id in enumerate(targets):
        try:
            if photo:
                await client.send_photo_strict(chat_id, photo, caption=text)
            else:
                await client.send_message_strict(chat_id, text)
            record["sent"] = int(record.get("sent") or 0) + 1
        except TelegramError as exc:
            record["failed"] = int(record.get("failed") or 0) + 1
            desc = exc.description.lower()
            # کاربری که ربات را بلاک کرده یا اکانتش حذف شده: دیگر هدف نیست.
            if exc.error_code == 403 or "blocked" in desc or "deactivated" in desc:
                rec = store.TG_USERS.get(str(chat_id))
                if rec is not None:
                    rec["bot_blocked"] = True
            logger.info("broadcast %s -> %s failed: %s", broadcast_id, chat_id, exc.description)
        except Exception as exc:
            record["failed"] = int(record.get("failed") or 0) + 1
            logger.warning("broadcast %s -> %s error: %s", broadcast_id, chat_id, exc)

        # هر ۵۰ پیام یک‌بار ذخیره می‌کنیم تا با Restart پیشرفت گم نشود.
        if index % 50 == 49:
            try:
                from main import save_state
                await save_state()
            except Exception:
                pass
        await asyncio.sleep(delay)

    record["status"] = "done"
    record["finished_at"] = store.now_iso()
    store.bump_stat("broadcasts_sent")
    logger.info("broadcast %s finished: %s sent / %s failed",
                broadcast_id, record.get("sent"), record.get("failed"))
    try:
        from main import save_state
        await save_state()
    except Exception:
        pass

    # گزارش نتیجه برای ادمین آغازکننده
    try:
        await client.send_message(
            int(record.get("actor_id")),
            f"📨 <b>پیام همگانی تمام شد</b>\n\n"
            f"✅ موفق: <b>{record.get('sent')}</b>\n"
            f"❌ ناموفق: <b>{record.get('failed')}</b>\n"
            f"👥 کل مخاطبان: <b>{record.get('total')}</b>",
        )
    except Exception:
        pass


async def shutdown() -> None:
    """لغو تمیز صف‌های در جریان هنگام خاموش شدن سرویس."""
    for task in list(_TASKS.values()):
        task.cancel()
    _TASKS.clear()
