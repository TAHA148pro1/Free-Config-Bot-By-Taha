"""داشبورد آماری ربات."""

from __future__ import annotations

from datetime import datetime, timedelta

from .. import store
from . import configs, support, users


def _new_since(days: int) -> int:
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    count = 0
    for rec in store.TG_USERS.values():
        raw = rec.get("first_seen")
        if not raw:
            continue
        try:
            seen = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            continue
        if seen.tzinfo is None:
            seen = seen.astimezone()
        if seen >= cutoff:
            count += 1
    return count


def _active_users(days: int = 7) -> int:
    """کاربر فعال = در N روز گذشته با ربات تعامل داشته."""
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    count = 0
    for rec in store.TG_USERS.values():
        raw = rec.get("last_seen")
        if not raw:
            continue
        try:
            seen = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            continue
        if seen.tzinfo is None:
            seen = seen.astimezone()
        if seen >= cutoff:
            count += 1
    return count


def overview() -> dict:
    cfg_totals = configs.totals()
    today = store.TG_STATS.get(store.today_key(), {})
    return {
        "users_total": users.count(),
        "users_blocked": users.count(blocked=True),
        "users_active": _active_users(7),
        "users_new_today": _new_since(1),
        "users_new_week": _new_since(7),
        "configs_total": cfg_totals["total"],
        "configs_active": cfg_totals["active"],
        "configs_expired": cfg_totals["expired"],
        "configs_exhausted": cfg_totals["exhausted"],
        "free_sent": cfg_totals["free_sent"],
        "quota_consumed_today": int(today.get("quota_consumed", 0) or 0),
        "quota_consumed_week": int(store.stats_range(7).get("quota_consumed", 0) or 0),
        "configs_created_today": int(today.get("configs_created", 0) or 0),
        "config_errors_today": int(today.get("config_errors", 0) or 0),
        "tickets_open": support.open_count(),
        "channels_active": len([c for c in store.TG_CHANNELS.values() if c.get("is_active")]),
    }


def daily_series(days: int = 7) -> list[dict]:
    """سری زمانی آمار روزانه برای نمایش/خروجی."""
    out = []
    for offset in range(days - 1, -1, -1):
        day = (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
        row = store.TG_STATS.get(day, {})
        out.append({
            "date": day,
            "users_new": int(row.get("users_new", 0) or 0),
            "configs_created": int(row.get("configs_created", 0) or 0),
            "free_configs": int(row.get("free_configs", 0) or 0),
            "quota_consumed": int(row.get("quota_consumed", 0) or 0),
            "config_errors": int(row.get("config_errors", 0) or 0),
        })
    return out
