"""بارگذاری botsys با پنل جعلی و سرور تلگرام جعلی."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "stubs"))   # stub httpx
sys.path.insert(0, str(ROOT))


def bootstrap():
    """ماژول‌ها را تازه بارگذاری می‌کند و `main` را با پنل جعلی جایگزین می‌کند."""
    import tests.fake_panel as fake_panel
    fake_panel.reset()
    sys.modules["main"] = fake_panel

    for name in [m for m in list(sys.modules) if m == "botsys" or m.startswith("botsys.")]:
        del sys.modules[name]

    import botsys.store as store
    import botsys.security as security
    import botsys.keyboards as keyboards
    import botsys.audit as audit
    import botsys.tgapi as tgapi
    import botsys.middlewares as middlewares
    from botsys.services import analytics, broadcast, channels, configs, issuing, quota, support, users
    from botsys.handlers import admin, router, user

    store.run_migrations()
    tgapi.client.set_token("123456:TEST-TOKEN")

    return type("Ctx", (), {
        "store": store, "security": security, "keyboards": keyboards, "audit": audit,
        "tgapi": tgapi, "middlewares": middlewares, "analytics": analytics,
        "broadcast": broadcast, "channels": channels, "configs": configs,
        "issuing": issuing, "quota": quota, "support": support, "users": users,
        "admin": admin, "router": router, "user": user, "panel": fake_panel,
    })


class FakeTelegram:
    """سرور تلگرام جعلی: پاسخ‌ها را می‌سازد و تماس‌ها را ضبط می‌کند."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.memberships: dict[tuple[str, int], str] = {}
        self.default_member_status = "member"
        self.fail_methods: dict[str, str] = {}
        self.unverifiable_chats: set[str] = set()

    def install(self):
        import httpx
        httpx.AsyncClient.handler = self.handle
        httpx.AsyncClient.calls = []
        return self

    # ── helpers for assertions ──
    def sent(self, method: str = "sendMessage") -> list[dict]:
        return [payload for name, payload in self.calls if name == method]

    def last_text(self, method: str = "sendMessage") -> str:
        msgs = self.sent(method)
        return str(msgs[-1].get("text") or msgs[-1].get("caption") or "") if msgs else ""

    def all_text(self) -> str:
        return "\n".join(
            str(p.get("text") or p.get("caption") or "") for _m, p in self.calls
        )

    def buttons(self) -> list[dict]:
        """همه‌ی دکمه‌های آخرین پیام/ویرایش."""
        for name, payload in reversed(self.calls):
            if name in ("sendMessage", "editMessageText", "sendPhoto"):
                markup = payload.get("reply_markup") or {}
                return [b for row in markup.get("inline_keyboard", []) for b in row]
        return []

    def clear(self):
        self.calls.clear()

    # ── fake API ──
    def handle(self, method: str, payload: dict) -> dict:
        self.calls.append((method, payload))

        if method in self.fail_methods:
            return {"ok": False, "error_code": 400, "description": self.fail_methods[method]}

        if method == "getMe":
            return {"ok": True, "result": {"id": 1, "username": "VodiWalkerTestBot"}}

        if method == "getChatMember":
            chat = str(payload.get("chat_id"))
            user_id = int(payload.get("user_id"))
            if chat in self.unverifiable_chats:
                return {"ok": False, "error_code": 400,
                        "description": "Bad Request: member list is inaccessible"}
            status = self.memberships.get((chat, user_id), self.default_member_status)
            return {"ok": True, "result": {"status": status}}

        if method == "editMessageText":
            return {"ok": True, "result": {"message_id": payload.get("message_id")}}

        if method in ("sendMessage", "sendPhoto"):
            return {"ok": True, "result": {"message_id": len(self.calls)}}

        return {"ok": True, "result": True}


# ── سازنده‌های Update ────────────────────────────────────────────────────────
def message(user_id: int, text: str, username: str = "", first_name: str = "کاربر",
            photo: bool = False) -> dict:
    payload = {
        "message_id": 100,
        "chat": {"id": user_id, "type": "private"},
        "from": {"id": user_id, "username": username, "first_name": first_name, "is_bot": False},
        "text": text,
    }
    if photo:
        payload["photo"] = [{"file_id": "PHOTO123"}]
        payload["caption"] = text
        payload.pop("text", None)
    return {"update_id": user_id * 1000, "message": payload}


def callback(user_id: int, data: str, username: str = "", message_id: int = 200) -> dict:
    return {
        "update_id": user_id * 1000 + 1,
        "callback_query": {
            "id": f"cb-{user_id}-{message_id}",
            "data": data,
            "from": {"id": user_id, "username": username, "first_name": "کاربر", "is_bot": False},
            "message": {"message_id": message_id, "chat": {"id": user_id, "type": "private"}},
        },
    }
