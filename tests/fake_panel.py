"""پنل جعلی: همان قراردادی را دارد که botsys از main.py انتظار دارد.

هدف: تست منطق ربات بدون نیاز به FastAPI/شبکه. امضای توابع عیناً مطابق main.py
واقعی است، پس اگر قرارداد عوض شود تست‌ها می‌شکنند.
"""

from __future__ import annotations

import uuid
from datetime import datetime

LINKS: dict = {}
SUBS: dict = {}
SECRET_KEY = "test-secret-key"
DEFAULT_PROTOCOL = "vless-ws"
DEFAULT_PORT = 443
CONFIG = {"bot_token": "", "public_base_url": ""}

save_calls = 0
fail_make_link = False


def reset():
    global save_calls, fail_make_link, long_vless
    LINKS.clear()
    SUBS.clear()
    save_calls = 0
    fail_make_link = False
    long_vless = False


async def save_state():
    global save_calls
    save_calls += 1


async def make_link(label="لینک جدید", limit_bytes=0, expires_at=None, note="",
                    sub_id=None, protocol=DEFAULT_PROTOCOL, fingerprint="chrome",
                    alpn="", port=DEFAULT_PORT, ip_limit=0, speed_limit_bytes=0,
                    connection_limit=0, fragment="off", clean_ips=None,
                    alarm_enabled=False, category_id="0", config_count=1,
                    manual_fields=None):
    if fail_make_link:
        raise RuntimeError("simulated panel failure")
    uid = str(uuid.uuid4())
    record = {
        "label": label, "limit_bytes": int(limit_bytes), "used_bytes": 0,
        "created_at": datetime.now().isoformat(), "active": True,
        "expires_at": expires_at, "note": note, "protocol": protocol,
        "protocol_label": protocol, "fingerprint": fingerprint, "alpn": alpn,
        "port": port, "ip_limit": ip_limit, "speed_limit_bytes": speed_limit_bytes,
    }
    LINKS[uid] = record
    await save_state()
    return uid, record


async def remove_link(uid: str):
    return LINKS.pop(uid, None)


async def set_link_active(uid: str, active: bool):
    if uid not in LINKS:
        return None
    LINKS[uid]["active"] = bool(active)
    return LINKS[uid]


def get_host() -> str:
    return "panel.example.com"


def get_scheme() -> str:
    return "https"


def public_base(host: str | None = None) -> str:
    return f"{get_scheme()}://{host or get_host()}"


def subscription_url_for_uid(uid: str, host: str | None = None) -> str:
    uid = str(uid or "").strip()
    return f"{public_base(host)}/sub/{uid}" if uid else ""


def info_url_for_uid(uid: str, host: str | None = None) -> str:
    uid = str(uid or "").strip()
    return f"{public_base(host)}/info/{uid}" if uid else ""


long_vless = False


def vless_link_for_link(link: dict, uid: str, host: str, port_override=None):
    uri = f"vless://{uid}@{host}:{link.get('port')}?type=ws#{link.get('label')}"
    if long_vless:
        uri += "&pad=" + "x" * 300
    return uri


def is_link_expired(link: dict) -> bool:
    raw = link.get("expires_at")
    if not raw:
        return False
    try:
        return datetime.fromisoformat(str(raw)) < datetime.now()
    except (TypeError, ValueError):
        return False


def fmt_bytes(value: int) -> str:
    value = int(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value} B"


def is_link_allowed(link: dict) -> bool:
    return bool(link.get("active")) and not is_link_expired(link)


def get_bot_text(key: str, fallback: str = "") -> str:
    return fallback
