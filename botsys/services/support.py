"""سیستم تیکت پشتیبانی ساده.

مدل: User -> SupportTicket -> SupportMessage  (و ادمین پاسخ‌دهنده)
عمداً ساده نگه داشته شده: هر کاربر یک تیکت باز دارد.
"""

from __future__ import annotations

import logging

from .. import store

logger = logging.getLogger("vodiwalker.botsys.support")

STATUS_OPEN = "open"
STATUS_ANSWERED = "answered"
STATUS_CLOSED = "closed"

STATUS_LABELS = {
    STATUS_OPEN: "باز",
    STATUS_ANSWERED: "پاسخ داده‌شده",
    STATUS_CLOSED: "بسته",
}


def open_ticket_of(telegram_id: int) -> dict | None:
    for rec in store.TG_TICKETS.values():
        if str(rec.get("telegram_id")) == str(telegram_id) and rec.get("status") != STATUS_CLOSED:
            return rec
    return None


def get(ticket_id: str) -> dict | None:
    return store.TG_TICKETS.get(str(ticket_id))


def messages_of(ticket_id: str) -> list[dict]:
    rows = [m for m in store.TG_MESSAGES.values() if str(m.get("ticket_id")) == str(ticket_id)]
    rows.sort(key=lambda m: str(m.get("at") or ""))
    return rows


def listing(status: str | None = None, limit: int = 10) -> list[dict]:
    rows = list(store.TG_TICKETS.values())
    if status:
        rows = [r for r in rows if r.get("status") == status]
    rows.sort(key=lambda r: str(r.get("updated_at") or r.get("created_at") or ""), reverse=True)
    return rows[:limit]


def open_count() -> int:
    return sum(1 for r in store.TG_TICKETS.values() if r.get("status") == STATUS_OPEN)


async def add_user_message(telegram_id: int, text: str) -> dict:
    """پیام کاربر را در تیکت باز او ثبت می‌کند (در صورت نبود، تیکت می‌سازد)."""
    ticket = open_ticket_of(telegram_id)
    if ticket is None:
        tid = store.next_id(store.TG_TICKETS)
        ticket = {
            "id": tid,
            "telegram_id": int(telegram_id),
            "status": STATUS_OPEN,
            "created_at": store.now_iso(),
            "updated_at": store.now_iso(),
        }
        store.TG_TICKETS[tid] = ticket
        store.bump_stat("tickets_opened")

    mid = store.next_id(store.TG_MESSAGES)
    message = {
        "id": mid,
        "ticket_id": ticket["id"],
        "sender": "user",
        "sender_id": int(telegram_id),
        "text": str(text or "")[:3000],
        "at": store.now_iso(),
    }
    store.TG_MESSAGES[mid] = message
    ticket["status"] = STATUS_OPEN
    ticket["updated_at"] = store.now_iso()
    ticket["last_preview"] = message["text"][:80]
    return ticket


async def add_admin_reply(actor_id: int, ticket_id: str, text: str) -> dict | None:
    from .. import audit
    ticket = get(ticket_id)
    if not ticket:
        return None
    mid = store.next_id(store.TG_MESSAGES)
    store.TG_MESSAGES[mid] = {
        "id": mid,
        "ticket_id": str(ticket_id),
        "sender": "admin",
        "sender_id": int(actor_id),
        "text": str(text or "")[:3000],
        "at": store.now_iso(),
    }
    ticket["status"] = STATUS_ANSWERED
    ticket["updated_at"] = store.now_iso()
    ticket["answered_by"] = int(actor_id)
    audit.record(actor_id, "support_reply", ticket.get("telegram_id"), "ticket", None, ticket_id)
    return ticket


async def close(actor_id: int, ticket_id: str) -> bool:
    from .. import audit
    ticket = get(ticket_id)
    if not ticket:
        return False
    before = ticket.get("status")
    ticket["status"] = STATUS_CLOSED
    ticket["updated_at"] = store.now_iso()
    audit.record(actor_id, "support_close", ticket.get("telegram_id"), "status", before, STATUS_CLOSED)
    return True
