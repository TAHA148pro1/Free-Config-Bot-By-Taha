"""سرویس کاربران تلگرام.

کلید اصلی کاربر = Telegram User ID.
"""

from __future__ import annotations

import logging

from .. import security, store

logger = logging.getLogger("vodiwalker.botsys.users")


def get(telegram_id: int) -> dict | None:
    return store.TG_USERS.get(str(telegram_id))


async def touch(tg_user: dict) -> dict:
    """کاربر را می‌سازد یا اطلاعاتش را به‌روز می‌کند (روی هر Update صدا زده می‌شود)."""
    telegram_id = security.valid_telegram_id(tg_user.get("id"))
    if telegram_id is None:
        raise ValueError("invalid telegram user id")

    key = str(telegram_id)
    rec = store.TG_USERS.get(key)
    created = rec is None

    if created:
        rec = {
            "telegram_id": telegram_id,
            "first_seen": store.now_iso(),
            "role": security.ROLE_SUPER_ADMIN if security.is_super_admin(telegram_id) else security.ROLE_USER,
            "is_blocked": False,
            "configs_received": 0,
            "quota_used": 0,
            "quota_period_key": "",
            "channels_ok": False,
            "channels_checked_at": None,
            "permissions": [],
        }
        store.TG_USERS[key] = rec
        store.bump_stat("users_new")
        logger.info("new telegram user %s", telegram_id)

    rec["username"] = tg_user.get("username") or ""
    rec["first_name"] = tg_user.get("first_name") or ""
    rec["last_name"] = tg_user.get("last_name") or ""
    rec["language_code"] = tg_user.get("language_code") or ""
    rec["last_seen"] = store.now_iso()

    # Super Admin های Environment همیشه نقش درست را دارند.
    if security.is_super_admin(telegram_id) and rec.get("role") != security.ROLE_SUPER_ADMIN:
        rec["role"] = security.ROLE_SUPER_ADMIN

    return rec


def display_name(rec: dict) -> str:
    name = " ".join(x for x in [rec.get("first_name"), rec.get("last_name")] if x).strip()
    if not name:
        name = f"#{rec.get('telegram_id')}"
    if rec.get("username"):
        name += f" (@{rec['username']})"
    return name


def search(term: str, limit: int = 10) -> list[dict]:
    """جست‌وجوی کاربر با Telegram ID یا Username یا نام."""
    term = (term or "").strip().lstrip("@").lower()
    if not term:
        return []

    exact = store.TG_USERS.get(term)
    if exact:
        return [exact]

    out = []
    for rec in store.TG_USERS.values():
        haystack = " ".join([
            str(rec.get("telegram_id") or ""),
            str(rec.get("username") or ""),
            str(rec.get("first_name") or ""),
            str(rec.get("last_name") or ""),
        ]).lower()
        if term in haystack:
            out.append(rec)
        if len(out) >= limit:
            break
    return out


def recent(limit: int = 10, blocked: bool | None = None) -> list[dict]:
    rows = list(store.TG_USERS.values())
    if blocked is not None:
        rows = [r for r in rows if bool(r.get("is_blocked")) == blocked]
    rows.sort(key=lambda r: str(r.get("first_seen") or ""), reverse=True)
    return rows[:limit]


def count(blocked: bool | None = None) -> int:
    if blocked is None:
        return len(store.TG_USERS)
    return sum(1 for r in store.TG_USERS.values() if bool(r.get("is_blocked")) == blocked)


async def set_blocked(actor_id: int, telegram_id: int, blocked: bool) -> dict | None:
    from .. import audit
    rec = get(telegram_id)
    if not rec:
        return None
    before = bool(rec.get("is_blocked"))
    rec["is_blocked"] = bool(blocked)
    audit.record(actor_id, "block_user" if blocked else "unblock_user",
                 telegram_id, "is_blocked", before, bool(blocked))
    return rec


async def set_role(actor_id: int, telegram_id: int, role: str) -> dict | None:
    """تغییر نقش. فقط Super Admin مجاز است (کنترل در Handler انجام می‌شود)."""
    from .. import audit
    rec = get(telegram_id)
    if not rec or role not in (security.ROLE_USER, security.ROLE_ADMIN, security.ROLE_SUPER_ADMIN):
        return None
    before = rec.get("role")
    rec["role"] = role
    audit.record(actor_id, "set_role", telegram_id, "role", before, role)
    return rec


def admins() -> list[dict]:
    rows = [r for r in store.TG_USERS.values()
            if r.get("role") in (security.ROLE_ADMIN, security.ROLE_SUPER_ADMIN)]
    for env_id in security.env_super_admins():
        if not any(str(r.get("telegram_id")) == str(env_id) for r in rows):
            rows.append({"telegram_id": env_id, "role": security.ROLE_SUPER_ADMIN,
                         "first_name": "(از Environment)", "username": ""})
    return rows


def broadcast_targets(only_active: bool = False) -> list[int]:
    """لیست مخاطبان Broadcast. کاربران مسدود هیچ‌وقت پیام نمی‌گیرند."""
    out = []
    for rec in store.TG_USERS.values():
        if rec.get("is_blocked"):
            continue
        if only_active and not rec.get("configs_received"):
            continue
        tid = security.valid_telegram_id(rec.get("telegram_id"))
        if tid:
            out.append(tid)
    return out
