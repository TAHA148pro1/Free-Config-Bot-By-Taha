"""سرویس کانال‌های اجباری + بررسی عضویت از طریق Telegram API.

نکته‌ی مهم تنظیمات: برای اینکه getChatMember کار کند، ربات باید در آن کانال
عضو و ترجیحاً Admin باشد. این موضوع در README مستند شده است.
"""

from __future__ import annotations

import logging
import time

from .. import store
from ..tgapi import TelegramError, client

logger = logging.getLogger("vodiwalker.botsys.channels")

#: وضعیت‌هایی که «عضو» حساب می‌شوند.
MEMBER_STATUSES = {"creator", "administrator", "member", "restricted"}

#: مدت اعتبار کش نتیجه‌ی بررسی عضویت (ثانیه) تا به Telegram API فشار نیاید.
CACHE_TTL = 15


def all_channels() -> list[dict]:
    rows = list(store.TG_CHANNELS.values())
    rows.sort(key=lambda r: (int(r.get("sort_order") or 0), str(r.get("id"))))
    return rows


def active_channels() -> list[dict]:
    return [c for c in all_channels() if c.get("is_active")]


def get(channel_id: str) -> dict | None:
    return store.TG_CHANNELS.get(str(channel_id))


def chat_ref(channel: dict) -> str | int:
    """مرجعی که به Telegram API داده می‌شود: Chat ID عددی یا @username."""
    chat_id = str(channel.get("chat_id") or "").strip()
    if chat_id:
        try:
            return int(chat_id)
        except ValueError:
            return chat_id if chat_id.startswith("@") else f"@{chat_id}"
    username = str(channel.get("username") or "").strip().lstrip("@")
    return f"@{username}" if username else ""


def join_url(channel: dict) -> str:
    invite = str(channel.get("invite_link") or "").strip()
    if invite:
        return invite
    username = str(channel.get("username") or "").strip().lstrip("@")
    if username:
        return f"https://t.me/{username}"
    return ""


async def create(actor_id: int, title: str, username: str = "", chat_id: str = "",
                 invite_link: str = "", sort_order: int | None = None) -> dict:
    from .. import audit
    cid = store.next_id(store.TG_CHANNELS)
    if sort_order is None:
        sort_order = len(store.TG_CHANNELS)
    rec = {
        "id": cid,
        "title": (title or "").strip()[:100] or f"کانال {cid}",
        "username": (username or "").strip().lstrip("@")[:64],
        "chat_id": (chat_id or "").strip()[:32],
        "invite_link": (invite_link or "").strip()[:200],
        "is_active": True,
        "sort_order": int(sort_order),
        "created_at": store.now_iso(),
    }
    store.TG_CHANNELS[cid] = rec
    audit.record(actor_id, "channel_add", cid, "title", None, rec["title"])
    return rec


async def update(actor_id: int, channel_id: str, **fields) -> dict | None:
    from .. import audit
    rec = get(channel_id)
    if not rec:
        return None
    allowed = {"title", "username", "chat_id", "invite_link", "is_active", "sort_order"}
    for key, value in fields.items():
        if key not in allowed:
            continue
        before = rec.get(key)
        if key == "is_active":
            value = bool(value)
        elif key == "sort_order":
            value = int(value or 0)
        elif key == "username":
            value = str(value or "").strip().lstrip("@")[:64]
        else:
            value = str(value or "").strip()[:200]
        rec[key] = value
        audit.record(actor_id, "channel_update", channel_id, key, before, value)
    return rec


async def delete(actor_id: int, channel_id: str) -> bool:
    from .. import audit
    rec = store.TG_CHANNELS.pop(str(channel_id), None)
    if not rec:
        return False
    audit.record(actor_id, "channel_delete", channel_id, "title", rec.get("title"), None)
    return True


async def toggle(actor_id: int, channel_id: str) -> dict | None:
    rec = get(channel_id)
    if not rec:
        return None
    return await update(actor_id, channel_id, is_active=not rec.get("is_active"))


# ── بررسی عضویت ───────────────────────────────────────────────────────────────
async def check_membership(telegram_id: int, force: bool = False) -> tuple[bool, list[dict], list[dict]]:
    """عضویت کاربر را در همه‌ی کانال‌های فعال بررسی می‌کند.

    خروجی: (همه_عضو_است, کانال‌های_عضو_نشده, کانال‌های_قابل_بررسی_نبود)

    اگر ربات در کانالی Admin نباشد، تلگرام خطا می‌دهد. در این حالت آن کانال را
    «نامشخص» در نظر می‌گیریم و کاربر را بی‌دلیل بلوکه نمی‌کنیم، ولی خطا لاگ
    می‌شود تا ادمین متوجه اشکال تنظیمات بشود.
    """
    if not store.setting("require_channels", True):
        return True, [], []

    channels = active_channels()
    if not channels:
        return True, [], []

    cache_key = f"chk:{telegram_id}"
    if not force:
        cached = _CACHE.get(cache_key)
        if cached and cached[0] > time.monotonic():
            return cached[1], list(cached[2]), list(cached[3])

    missing: list[dict] = []
    unverifiable: list[dict] = []

    for channel in channels:
        ref = chat_ref(channel)
        if not ref:
            unverifiable.append(channel)
            continue
        try:
            member = await client.get_chat_member(ref, telegram_id)
        except TelegramError as exc:
            logger.warning(
                "membership check failed for channel %s (%s): %s — "
                "مطمئن شوید ربات در این کانال Admin است.",
                channel.get("title"), ref, exc.description,
            )
            unverifiable.append(channel)
            continue
        except Exception as exc:
            logger.warning("membership check error for %s: %s", ref, exc)
            unverifiable.append(channel)
            continue

        status = str((member or {}).get("status") or "")
        # ChatMemberRestricted می‌تواند status=restricted داشته باشد ولی
        # is_member=False باشد؛ در این حالت کاربر عضو محسوب نمی‌شود.
        is_member = (member or {}).get("is_member")
        if status not in MEMBER_STATUSES or (status == "restricted" and is_member is False):
            missing.append(channel)

    ok = not missing
    rec = store.TG_USERS.get(str(telegram_id))
    if rec is not None:
        rec["channels_ok"] = ok
        rec["channels_checked_at"] = store.now_iso()

    # نتیجه‌ی «عضو نیست» را cache نکن؛ چون کاربر ممکن است همین الان
    # Join کرده باشد و باید درخواست بعدی فوراً وضعیت تازه را ببیند.
    # فقط نتیجه‌ی کاملاً موفق برای مدت کوتاه cache می‌شود تا فشار API کم شود.
    if ok and not unverifiable:
        _CACHE[cache_key] = (time.monotonic() + CACHE_TTL, ok, [], [])
    else:
        _CACHE.pop(cache_key, None)

    if len(_CACHE) > 10000:
        _CACHE.clear()
    return ok, missing, unverifiable


def invalidate(telegram_id: int) -> None:
    """کش عضویت یک کاربر را پاک می‌کند (مثلاً بعد از فشردن «بررسی عضویت»)."""
    _CACHE.pop(f"chk:{telegram_id}", None)


#: cache_key -> (expires_at, ok, missing, unverifiable)
_CACHE: dict = {}
