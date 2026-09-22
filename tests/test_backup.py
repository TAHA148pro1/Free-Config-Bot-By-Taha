"""Integration tests for Backup / Restore."""
from __future__ import annotations

import asyncio
import io
import json
import tempfile
import zipfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from botsys import keyboards, store
from botsys.services import backups


class FakeClient:
    def __init__(self):
        self.sent = []

    async def send_document(self, chat_id, document, filename, caption=""):
        self.sent.append((chat_id, document, filename, caption))
        return {"message_id": len(self.sent)}


async def run():
    tmp = Path(tempfile.mkdtemp(prefix="vodiwalker-backup-test-"))
    main.DATA_DIR = tmp
    main.DATA_FILE = tmp / "vodiwalker_state.json"
    main.SECRET_FILE = tmp / "vodiwalker_secret.key"

    # Isolated fixture state.
    for collection in (main.LINKS, main.SUBS, main.CATEGORIES, main.ADMINS,
                        main.ADMIN_REQUESTS, main.DAILY_STATS):
        collection.clear()
    for collection in (store.TG_USERS, store.TG_ROLES, store.TG_USER_CONFIGS,
                       store.TG_QUOTAS, store.TG_CHANNELS, store.TG_TICKETS,
                       store.TG_MESSAGES, store.TG_BROADCASTS, store.TG_STATS):
        collection.clear()
    store.TG_AUDIT.clear()

    main.LINKS["cfg-1"] = {"label": "Backup Test", "used_bytes": 42}
    store.TG_USERS["12345"] = {"telegram_id": 12345, "role": "super_admin"}
    store.TG_CHANNELS["1"] = {"chat_id": "-100123", "title": "Backup Channel"}
    store.TG_SETTINGS["backup_enabled"] = True
    store.TG_SETTINGS["backup_interval_hours"] = 6
    main.SECRET_KEY = "backup-test-secret"
    main.CONFIG["secret"] = main.SECRET_KEY

    await main.save_state()
    archive, filename = await backups.create_archive()

    assert filename.startswith("vodiwalker_backup_") and filename.endswith(".zip")
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        names = set(zf.namelist())
        required = {"manifest.json", "state.json", "secret.key",
                    "vodiwalker_plans.json", "vodiwalker_sales.json"}
        assert required <= names
        state = json.loads(zf.read("state.json"))
        assert state["links"]["cfg-1"]["label"] == "Backup Test"
        assert state["tg_users"]["12345"]["role"] == "super_admin"

    # Delivery goes to Super Admin private chat(s).
    client = FakeClient()
    result = await backups.send_archive(client, archive, filename)
    assert result == 1
    assert client.sent[0][0] == 12345
    assert client.sent[0][2] == filename

    # Destroy live state, then restore the exact archive.
    main.LINKS.clear()
    store.TG_USERS.clear()
    store.TG_CHANNELS.clear()
    restored = await backups.restore_archive(archive)
    assert restored["ok"]
    assert main.LINKS["cfg-1"]["label"] == "Backup Test"
    assert store.TG_USERS["12345"]["role"] == "super_admin"
    assert store.TG_CHANNELS["1"]["title"] == "Backup Channel"
    assert main.SECRET_KEY == "backup-test-secret"

    # Super Admin sees the Backup menu; a normal Admin does not.
    assert any(
        b.get("callback_data") == "a:backup"
        for row in keyboards.admin_main(True)["inline_keyboard"]
        for b in row
    )
    assert not any(
        b.get("callback_data") == "a:backup"
        for row in keyboards.admin_main(False)["inline_keyboard"]
        for b in row
    )

    print("BACKUP: archive / send / restore / access-control all passed")


if __name__ == "__main__":
    asyncio.run(run())
