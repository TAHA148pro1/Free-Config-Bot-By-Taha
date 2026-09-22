"""Audit Log عملیات مدیریتی.

هر رکورد شامل: چه ادمینی، روی چه کاربری، چه عملیاتی، چه زمانی، مقدار قبل،
مقدار بعد. برای Debug و بررسی‌های امنیتی حیاتی است.
"""

from __future__ import annotations

import logging

from . import store

logger = logging.getLogger("vodiwalker.botsys.audit")


def record(actor_id: int, action: str, target_id: int | str | None = None,
           field: str = "", before=None, after=None, note: str = "") -> dict:
    """یک رکورد Audit ثبت می‌کند و همان را برمی‌گرداند."""
    entry = {
        "at": store.now_iso(),
        "actor_id": int(actor_id) if str(actor_id).lstrip("-").isdigit() else actor_id,
        "action": action,
        "target_id": str(target_id) if target_id is not None else None,
        "field": field,
        "before": before,
        "after": after,
        "note": note,
    }
    store.TG_AUDIT.append(entry)
    logger.info(
        "AUDIT admin=%s action=%s target=%s %s: %r -> %r",
        actor_id, action, target_id, field or "-", before, after,
    )
    return entry


def recent(limit: int = 20, target_id: str | int | None = None) -> list[dict]:
    rows = list(store.TG_AUDIT)
    if target_id is not None:
        rows = [r for r in rows if str(r.get("target_id")) == str(target_id)]
    return rows[-limit:][::-1]


def describe(entry: dict) -> str:
    """رندر یک خط خوانا برای نمایش داخل ربات."""
    when = str(entry.get("at") or "")[:16].replace("T", " ")
    field = entry.get("field") or ""
    before, after = entry.get("before"), entry.get("after")
    change = ""
    if field:
        change = f" · {field}: {before} → {after}"
    target = entry.get("target_id")
    target_txt = f" روی {target}" if target else ""
    return f"{when} · ادمین {entry.get('actor_id')} · {entry.get('action')}{target_txt}{change}"
