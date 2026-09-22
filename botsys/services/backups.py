"""Backup/restore service for the complete VodiWalker persistent state.

Backups are ZIP archives containing the panel/bot state, secret key, sales data,
and editable plans. Automatic backups are sent to every configured Super Admin
in a private Telegram chat.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .. import security, store
from ..tgapi import TelegramError

logger = logging.getLogger("vodiwalker.botsys.backups")

BACKUP_PREFIX = "vodiwalker_backup_"
BACKUP_FORMAT = 1
MAX_RESTORE_BYTES = 50 * 1024 * 1024

_LOCK = asyncio.Lock()
_TASK: asyncio.Task | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _filename() -> str:
    return f"{BACKUP_PREFIX}{_now().strftime('%Y%m%d_%H%M%S')}.zip"


def _super_admin_ids() -> set[int]:
    ids = set(security.env_super_admins())
    for key, rec in store.TG_USERS.items():
        if isinstance(rec, dict) and rec.get("role") == security.ROLE_SUPER_ADMIN:
            try:
                ids.add(int(key))
            except (TypeError, ValueError):
                pass
    return ids


def status() -> dict:
    return {
        "enabled": bool(store.setting("backup_enabled", True)),
        "interval_hours": float(store.setting("backup_interval_hours", 24) or 24),
        "last_at": store.setting("backup_last_at", "") or "",
        "super_admins": len(_super_admin_ids()),
        "running": bool(_TASK and not _TASK.done()),
    }


async def create_archive() -> tuple[bytes, str]:
    """Flush current state and build an in-memory ZIP archive."""
    async with _LOCK:
        from main import DATA_DIR, DATA_FILE, SECRET_FILE, save_state

        await save_state()
        # Sales are persisted separately from the main JSON state.
        try:
            import sales
            await sales.save_plans()
            await sales.save_sales()
        except Exception:
            logger.warning("Could not flush sales files before backup", exc_info=True)

        files: list[tuple[str, Path]] = [
            ("state.json", DATA_FILE),
        ]
        data_dir = Path(DATA_DIR)
        for name in ("vodiwalker_plans.json", "vodiwalker_sales.json"):
            path = data_dir / name
            if path.exists():
                files.append((name, path))

        manifest = {
            "format": BACKUP_FORMAT,
            "app": "VodiWalker",
            "created_at": _now().isoformat(),
            "files": [name for name, path in files if path.exists()] + ["secret.key"],
        }

        bio = io.BytesIO()
        with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for name, path in files:
                if path.exists():
                    zf.write(path, arcname=name)
            # SECRET_KEY is part of the persistent security identity. Include it
            # even when the deployment provides SECRET_KEY through Environment.
            from main import SECRET_KEY
            zf.writestr("secret.key", str(SECRET_KEY).strip())
        return bio.getvalue(), _filename()


async def send_archive(client, archive: bytes, filename: str, *, automatic: bool = False) -> int:
    """Send one backup to all Super Admin private chats."""
    sent = 0
    caption = (
        "💾 <b>بکاپ خودکار VodiWalker</b>\n\n"
        "تمام اطلاعات پایدار پنل و ربات داخل این فایل ذخیره شده است."
        if automatic else
        "💾 <b>بکاپ VodiWalker</b>\n\n"
        "این فایل شامل State پنل، کاربران، کانفیگ‌ها، تنظیمات ربات و داده‌های فروش است."
    )
    ids = _super_admin_ids()
    for chat_id in sorted(ids):
        try:
            result = await client.send_document(chat_id, archive, filename, caption=caption)
            if result:
                sent += 1
        except Exception:
            logger.warning("Could not send backup to super admin %s", chat_id, exc_info=True)
    return sent


async def create_and_send(client, *, automatic: bool = False) -> dict:
    archive, filename = await create_archive()
    sent = await send_archive(client, archive, filename, automatic=automatic)
    if sent:
        store.set_setting("backup_last_at", _now().isoformat())
        from main import save_state
        await save_state()
    return {"ok": sent > 0, "sent": sent, "filename": filename, "size": len(archive)}


def _safe_zip_bytes(archive: bytes) -> dict[str, bytes]:
    if not archive or len(archive) > MAX_RESTORE_BYTES:
        raise ValueError("backup file is too large")
    try:
        zf = zipfile.ZipFile(io.BytesIO(archive), "r")
    except zipfile.BadZipFile as exc:
        raise ValueError("فایل بکاپ معتبر نیست") from exc

    with zf:
        names = set(zf.namelist())
        if "manifest.json" not in names or "state.json" not in names:
            raise ValueError("ساختار بکاپ ناقص است")
        for name in names:
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError("مسیر فایل بکاپ نامعتبر است")
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        if manifest.get("app") != "VodiWalker" or int(manifest.get("format", 0)) != BACKUP_FORMAT:
            raise ValueError("نسخه/فرمت بکاپ با این پروژه سازگار نیست")
        result = {"state.json": zf.read("state.json")}
        for optional in ("secret.key", "vodiwalker_plans.json", "vodiwalker_sales.json"):
            if optional in names:
                result[optional] = zf.read(optional)
        return result


async def restore_archive(archive: bytes) -> dict:
    """Restore the archive into live in-memory state and persistent files."""
    files = _safe_zip_bytes(archive)
    try:
        state = json.loads(files["state.json"].decode("utf-8"))
    except Exception as exc:
        raise ValueError("state.json داخل بکاپ معتبر نیست") from exc
    if not isinstance(state, dict):
        raise ValueError("state.json باید یک object باشد")

    async with _LOCK:
        import main
        from main import DATA_DIR, DATA_FILE, SECRET_FILE
        from botsys import store as bot_store

        # Validate before changing live state.
        if not isinstance(state.get("links", {}), dict) or not isinstance(state.get("subs", {}), dict):
            raise ValueError("داده‌های اصلی بکاپ نامعتبر هستند")

        # Restore regular panel state using the project's own loader-compatible format.
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = DATA_FILE.with_suffix(".restore.tmp")
        tmp.write_bytes(files["state.json"])
        tmp.replace(DATA_FILE)
        if "secret.key" in files and not str(__import__("os").environ.get("SECRET_KEY") or "").strip():
            secret_tmp = SECRET_FILE.with_suffix(".restore.tmp")
            secret_tmp.write_bytes(files["secret.key"])
            secret_tmp.replace(SECRET_FILE)
            main.SECRET_KEY = files["secret.key"].decode("utf-8").strip()
            main.CONFIG["secret"] = main.SECRET_KEY

        # Replace sales files when present. A backup created before sales existed
        # remains valid and simply keeps current sales data.
        for name in ("vodiwalker_plans.json", "vodiwalker_sales.json"):
            if name in files:
                target = DATA_DIR / name
                tmp = target.with_suffix(".restore.tmp")
                tmp.write_bytes(files[name])
                tmp.replace(target)

        # Clear all in-memory collections before loading to avoid stale records.
        for collection in (main.LINKS, main.SUBS, main.CATEGORIES, main.ADMINS, main.ADMIN_REQUESTS, main.DAILY_STATS):
            collection.clear()
        for collection in (bot_store.TG_USERS, bot_store.TG_ROLES, bot_store.TG_USER_CONFIGS,
                           bot_store.TG_QUOTAS, bot_store.TG_CHANNELS, bot_store.TG_TICKETS,
                           bot_store.TG_MESSAGES, bot_store.TG_BROADCASTS, bot_store.TG_STATS):
            collection.clear()
        bot_store.TG_AUDIT.clear()

        await main.load_state()

        try:
            import sales
            sales.load_plans()
            sales.load_sales()
        except Exception:
            logger.warning("Sales data reload after restore failed", exc_info=True)

        # The restored state is now canonical.
        return {
            "ok": True,
            "users": len(bot_store.TG_USERS),
            "configs": len(main.LINKS),
            "channels": len(bot_store.TG_CHANNELS),
        }


async def handle_document(client, chat_id: int, document: dict) -> dict:
    """Download a Telegram document and restore it if it is a VodiWalker backup."""
    if not document or not security.is_super_admin(int(chat_id)):
        return {"ok": False, "ignored": True}
    filename = str(document.get("file_name") or "")
    if not filename.lower().endswith(".zip") or not filename.startswith(BACKUP_PREFIX):
        return {"ok": False, "ignored": True}
    file_id = document.get("file_id")
    if not file_id:
        raise ValueError("شناسه فایل بکاپ موجود نیست")
    data = await client.download_file(file_id)
    result = await restore_archive(data)
    # Re-apply the restored bot configuration. Environment super admins remain
    # available even if the backup contained an older user list.
    try:
        from main import _tg_restart_bot
        await _tg_restart_bot()
    except Exception:
        logger.warning("Bot restart after restore failed", exc_info=True)
    return result


async def monitor_loop(client) -> None:
    """Periodic automatic backup loop. Interval is configurable in hours."""
    global _TASK
    try:
        while True:
            interval = max(1.0, float(store.setting("backup_interval_hours", 24) or 24))
            await asyncio.sleep(min(60.0, interval * 3600))
            if not store.setting("backup_enabled", True):
                continue
            last_raw = str(store.setting("backup_last_at", "") or "")
            due = True
            if last_raw:
                try:
                    last = datetime.fromisoformat(last_raw)
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    due = (_now() - last).total_seconds() >= interval * 3600
                except ValueError:
                    due = True
            if due:
                try:
                    result = await create_and_send(client, automatic=True)
                    if not result["ok"]:
                        logger.warning("Automatic backup produced no successful recipient")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Automatic backup failed")
    except asyncio.CancelledError:
        raise


def start_scheduler(client) -> None:
    global _TASK
    if _TASK and not _TASK.done():
        return
    _TASK = asyncio.create_task(monitor_loop(client), name="vodiwalker-backup-monitor")


async def stop_scheduler() -> None:
    global _TASK
    task = _TASK
    _TASK = None
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
