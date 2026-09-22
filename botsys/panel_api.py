"""API پنل وب برای مدیریت زیرسیستم ربات.

این روت‌ها به پنل *موجود* اضافه می‌شوند و هیچ روت قبلی را تغییر نمی‌دهند.
احراز هویت از همان Dependency های فعلی پنل استفاده می‌کند (require_auth /
require_owner)، پس مدل امنیتی پنل دست‌نخورده می‌ماند.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from . import audit, security, store
from .services import analytics, broadcast, channels, configs, quota, support, users

logger = logging.getLogger("vodiwalker.botsys.panel_api")

router = APIRouter(prefix="/api/bot", tags=["telegram-bot"])


# ── Dependencies (import تنبل برای پرهیز از Circular Import) ────────────────
async def _auth(request: Request):
    import main
    return await main.require_auth(request)


async def _owner(request: Request):
    import main
    return await main.require_owner(request)


async def _save():
    import main
    await main.save_state()


# ── وضعیت ربات ───────────────────────────────────────────────────────────────
@router.get("/status")
async def bot_status(_=Depends(_auth)):
    from . import runner
    return {"ok": True, "bot": runner.status(), "overview": analytics.overview()}


@router.post("/restart")
async def bot_restart(_=Depends(_owner)):
    from . import runner
    return {"ok": True, "bot": await runner.restart()}


@router.get("/webhook-info")
async def webhook_info(_=Depends(_owner)):
    from . import runner
    from .tgapi import client
    return {"ok": True, "url": runner.webhook_url(), "telegram": await client.get_webhook_info()}


# ── تنظیمات ──────────────────────────────────────────────────────────────────
@router.get("/settings")
async def get_settings(_=Depends(_auth)):
    return {"ok": True, "settings": dict(store.TG_SETTINGS), "texts": dict(store.TG_TEXTS)}


@router.post("/settings")
async def update_settings(request: Request, _=Depends(_owner)):
    """تنظیمات ربات را به‌روز می‌کند. هیچ مقداری Hard-code نیست."""
    body = await request.json()
    settings = body.get("settings") or {}
    texts = body.get("texts") or {}

    changed = []
    for key, value in settings.items():
        if key not in store.DEFAULT_BOT_SETTINGS:
            continue
        before = store.setting(key)
        default = store.DEFAULT_BOT_SETTINGS[key]
        try:
            if isinstance(default, bool):
                value = bool(value)
            elif isinstance(default, int):
                value = int(value)
            elif isinstance(default, float):
                value = float(value)
            else:
                value = str(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"invalid value for {key}")
        store.set_setting(key, value)
        audit.record("panel", "setting_change", None, key, before, value)
        changed.append(key)

    for key, value in texts.items():
        if not isinstance(value, str):
            continue
        before = store.text(key)[:60]
        store.set_text(key, value)
        audit.record("panel", "text_change", None, key, before, value[:60])
        changed.append(key)

    await _save()
    return {"ok": True, "changed": changed}


# ── کانال‌های اجباری ─────────────────────────────────────────────────────────
@router.get("/channels")
async def list_channels(_=Depends(_auth)):
    return {"ok": True, "channels": channels.all_channels()}


@router.post("/channels")
async def create_channel(request: Request, _=Depends(_owner)):
    body = await request.json()
    title = str(body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    if not (body.get("username") or body.get("chat_id")):
        raise HTTPException(status_code=400, detail="username or chat_id is required")
    record = await channels.create(
        "panel", title,
        username=str(body.get("username") or ""),
        chat_id=str(body.get("chat_id") or ""),
        invite_link=str(body.get("invite_link") or ""),
        sort_order=body.get("sort_order"),
    )
    await _save()
    return {"ok": True, "channel": record}


@router.patch("/channels/{channel_id}")
async def patch_channel(channel_id: str, request: Request, _=Depends(_owner)):
    body = await request.json()
    record = await channels.update("panel", channel_id, **body)
    if record is None:
        raise HTTPException(status_code=404, detail="channel not found")
    await _save()
    return {"ok": True, "channel": record}


@router.delete("/channels/{channel_id}")
async def remove_channel(channel_id: str, _=Depends(_owner)):
    if not await channels.delete("panel", channel_id):
        raise HTTPException(status_code=404, detail="channel not found")
    await _save()
    return {"ok": True}


# ── کاربران ──────────────────────────────────────────────────────────────────
@router.get("/users")
async def list_users(request: Request, _=Depends(_auth)):
    term = str(request.query_params.get("q") or "").strip()
    limit = min(200, max(1, int(request.query_params.get("limit") or 50)))
    rows = users.search(term, limit=limit) if term else users.recent(limit=limit)
    return {
        "ok": True,
        "total": users.count(),
        "users": [
            {
                **{key: rec.get(key) for key in (
                    "telegram_id", "username", "first_name", "last_name", "role",
                    "is_blocked", "first_seen", "last_seen", "configs_received", "channels_ok",
                )},
                "quota": quota.status(int(rec["telegram_id"])),
                "configs": len(configs.user_configs(int(rec["telegram_id"]))),
            }
            for rec in rows if rec.get("telegram_id")
        ],
    }


@router.get("/users/{telegram_id}")
async def user_detail(telegram_id: int, _=Depends(_auth)):
    rec = users.get(telegram_id)
    if not rec:
        raise HTTPException(status_code=404, detail="user not found")
    return {
        "ok": True,
        "user": rec,
        "quota": quota.status(telegram_id),
        "configs": [
            {**{"id": item["id"], "link_uid": item.get("link_uid"), "source": item.get("source")},
             **configs.describe(item)}
            for item in configs.user_configs(telegram_id)
        ],
        "audit": audit.recent(20, target_id=telegram_id),
    }


@router.patch("/users/{telegram_id}")
async def patch_user(telegram_id: int, request: Request, _=Depends(_owner)):
    """مسدودسازی، تغییر نقش و تغییر سهمیه‌ی کاربر از پنل."""
    if security.valid_telegram_id(telegram_id) is None:
        raise HTTPException(status_code=400, detail="invalid telegram id")
    rec = users.get(telegram_id)
    if not rec:
        raise HTTPException(status_code=404, detail="user not found")
    body = await request.json()

    if "is_blocked" in body:
        await users.set_blocked("panel", telegram_id, bool(body["is_blocked"]))
    if "role" in body:
        if not await users.set_role("panel", telegram_id, str(body["role"])):
            raise HTTPException(status_code=400, detail="invalid role")

    quota_fields = {key: body[key] for key in (
        "total", "reset", "renewable", "daily_limit", "weekly_limit",
        "monthly_limit", "volume_gb", "speed_mbps", "duration_days", "ip_limit",
    ) if key in body}
    if quota_fields:
        await quota.set_user_quota("panel", telegram_id, **quota_fields)
    if body.get("reset_quota"):
        await quota.reset_user("panel", telegram_id)

    await _save()
    return {"ok": True, "user": users.get(telegram_id), "quota": quota.status(telegram_id)}


@router.post("/users/{telegram_id}/grant")
async def grant(telegram_id: int, request: Request, _=Depends(_owner)):
    """اعطای کانفیگ به کاربر از پنل (از همان سرویس ربات/پنل استفاده می‌کند)."""
    from .services import issuing
    body = await request.json() if await request.body() else {}
    result = await issuing.grant_config(
        "panel", telegram_id,
        volume_gb=body.get("volume_gb"),
        speed_mbps=body.get("speed_mbps"),
        duration_days=body.get("duration_days"),
        ip_limit=body.get("ip_limit"),
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.reason)
    await _save()
    return {"ok": True, "config": result.user_config}


# ── کانفیگ‌ها ────────────────────────────────────────────────────────────────
@router.delete("/configs/{user_config_id}")
async def delete_config(user_config_id: str, _=Depends(_owner)):
    try:
        removed = await configs.delete("panel", user_config_id)
    except configs.ConfigCreationError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if not removed:
        raise HTTPException(status_code=404, detail="config not found")
    return {"ok": True}


@router.patch("/configs/{user_config_id}")
async def patch_config(user_config_id: str, request: Request, _=Depends(_owner)):
    body = await request.json()
    updated = await configs.update_limits(
        "panel", user_config_id,
        volume_gb=body.get("volume_gb"),
        speed_mbps=body.get("speed_mbps"),
        days=body.get("days"),
        ip_limit=body.get("ip_limit"),
    )
    if not updated:
        raise HTTPException(status_code=404, detail="config not found")
    return {"ok": True}


# ── آمار / Audit / تیکت‌ها / Broadcast ───────────────────────────────────────
@router.get("/stats")
async def stats(request: Request, _=Depends(_auth)):
    days = min(90, max(1, int(request.query_params.get("days") or 7)))
    return {"ok": True, "overview": analytics.overview(), "daily": analytics.daily_series(days)}


@router.get("/audit")
async def audit_log(request: Request, _=Depends(_auth)):
    limit = min(500, max(1, int(request.query_params.get("limit") or 100)))
    return {"ok": True, "entries": audit.recent(limit)}


@router.get("/tickets")
async def tickets(request: Request, _=Depends(_auth)):
    status_filter = request.query_params.get("status")
    rows = support.listing(status=status_filter, limit=100)
    return {"ok": True, "open": support.open_count(), "tickets": [
        {**row, "messages": support.messages_of(row["id"])} for row in rows
    ]}


@router.post("/tickets/{ticket_id}/reply")
async def reply_ticket(ticket_id: str, request: Request, _=Depends(_owner)):
    from .tgapi import client, h
    body = await request.json()
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    ticket = await support.add_admin_reply("panel", ticket_id, text)
    if not ticket:
        raise HTTPException(status_code=404, detail="ticket not found")
    await _save()
    delivered = await client.send_message(
        int(ticket["telegram_id"]),
        f"🛟 <b>پاسخ پشتیبانی</b> (تیکت #{ticket_id})\n\n{h(text)}",
    )
    return {"ok": True, "delivered": bool(delivered)}


@router.post("/broadcast")
async def send_broadcast(request: Request, _=Depends(_owner)):
    body = await request.json()
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    record = await broadcast.create(
        "panel", text,
        photo=str(body.get("photo") or ""),
        only_active=bool(body.get("only_active")),
    )
    return {"ok": True, "broadcast": record}


@router.get("/broadcasts")
async def list_broadcasts(_=Depends(_auth)):
    return {"ok": True, "broadcasts": broadcast.listing(20)}
