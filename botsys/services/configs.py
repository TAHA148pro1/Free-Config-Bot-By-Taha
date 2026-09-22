"""پل بین ربات و سیستم ساخت/مدیریت کانفیگِ *موجود* پنل.

قاعده‌ی حاکم: هیچ منطق جدیدی برای ساخت کانفیگ نوشته نشده است. این ماژول فقط
سرویس‌های فعلی پنل را صدا می‌زند:

    main.make_link()            ساخت کانفیگ
    main.remove_link()          حذف کانفیگ
    main.set_link_active()      فعال/غیرفعال
    main.vless_link_for_link()  ساخت URI اتصال
    main.get_host()             دامنه‌ی عمومی
    main.save_state()           ذخیره‌سازی

TG_USER_CONFIGS نقش جدول واسط (UserConfig) را دارد: هر رکورد یک Telegram User
را به uid یک لینک واقعی در LINKS وصل می‌کند. یعنی منبع حقیقت برای خودِ کانفیگ
همان LINKS پنل است و دو سیستم موازی وجود ندارد.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

from .. import store

logger = logging.getLogger("vodiwalker.botsys.configs")


class ConfigCreationError(Exception):
    """ساخت کانفیگ در لایه‌ی پنل شکست خورد."""


# ── دسترسی تنبل به main برای جلوگیری از Circular Import ──────────────────────
def _panel():
    import main
    return main


def gb_to_bytes(gb: float) -> int:
    return int(max(0.0, float(gb or 0)) * 1024 * 1024 * 1024)


def mbps_to_bytes(mbps: float) -> int:
    return int(max(0.0, float(mbps or 0)) * 1000 * 1000 / 8)


def link_of(user_config: dict) -> dict | None:
    """رکورد واقعی کانفیگ در LINKS پنل."""
    return _panel().LINKS.get(str(user_config.get("link_uid")))


def user_configs(telegram_id: int, include_deleted: bool = False) -> list[dict]:
    """کانفیگ‌های یک کاربر. رکوردهای یتیم (لینک حذف‌شده از پنل) فیلتر می‌شوند."""
    panel = _panel()
    rows = []
    for rec in store.TG_USER_CONFIGS.values():
        if str(rec.get("telegram_id")) != str(telegram_id):
            continue
        if not include_deleted and str(rec.get("link_uid")) not in panel.LINKS:
            continue
        rows.append(rec)
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return rows


def get_user_config(user_config_id: str) -> dict | None:
    return store.TG_USER_CONFIGS.get(str(user_config_id))


def pending_delivery(telegram_id: int, source: str = "free") -> dict | None:
    """کانفیگی که ساخته شده ولی ارسالش به تلگرام موفق نبوده.

    برای Idempotent کردن دریافت کانفیگ رایگان لازم است: اگر ساخت موفق شد و
    فقط ارسال شکست خورد، تلاش بعدی کاربر باید **همان** کانفیگ را تحویل بدهد،
    نه اینکه کانفیگ و ساب دوم بسازد و سهمیه را دوباره بسوزاند.
    """
    for rec in user_configs(telegram_id):
        if rec.get("source") == source and not rec.get("delivered"):
            return rec
    return None


def connection_uri(user_config: dict) -> str:
    """کانفیگ **خام** (Raw Config): vless:// یا vmess:// یا trojan://

    این همان چیزی است که دکمه‌ی «کپی کانفیگ» باید بدهد. با همان تابع پنل ساخته
    می‌شود (بدون منطق موازی). این مقدار هیچ‌وقت نباید جای لینک ساب استفاده شود.
    """
    panel = _panel()
    link = link_of(user_config)
    if not link:
        return ""
    try:
        host = panel.get_host()
        # ربات نباید هیچ‌وقت localhost/loopback را داخل کانفیگ کاربر قرار دهد.
        # آدرس عمومی باید دقیقاً از Resolver خود پنل بیاید تا Railway domain /
        # PUBLIC_BASE_URL / تنظیمات پنل بین لینک خام و Subscription یکسان باشد.
        bad_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
        if str(host).strip().lower() in bad_hosts:
            env_base = str(__import__("os").environ.get("PUBLIC_BASE_URL") or "").strip()
            if env_base and hasattr(panel, "_split_base_url"):
                _, env_host = panel._split_base_url(env_base)
                host = env_host or host
            if str(host).strip().lower() in bad_hosts:
                railway = str(__import__("os").environ.get("RAILWAY_PUBLIC_DOMAIN") or __import__("os").environ.get("RAILWAY_STATIC_URL") or "").strip()
                host = railway.replace("https://", "").replace("http://", "").split("/", 1)[0].split(":")[0] or host

        # برای کانفیگ‌های Manual، address قدیمی ممکن است localhost ذخیره شده باشد.
        # اگر چنین مقداری وجود داشت، اجازه نده خروجی ربات دوباره همان آدرس خراب را منتشر کند.
        render_link = link
        if str(link.get("address") or "").strip().lower() in bad_hosts:
            render_link = dict(link)
            render_link["address"] = ""
        return panel.vless_link_for_link(render_link, str(user_config.get("link_uid")), host)
    except Exception as exc:
        logger.warning("could not build raw config uri: %s", exc)
        return ""


#: نام گویاتر برای همان مقدار؛ در Handler ها از این استفاده می‌شود تا هیچ‌وقت با
#: لینک ساب اشتباه گرفته نشود.
raw_config = connection_uri


def subscription_url(user_config: dict) -> str:
    """**لینک ساب** (Subscription URL) همان کانفیگ.

    منبع: تنها تابع رسمی پنل (`main.subscription_url_for_uid`) که خودِ پنل وب هم
    برای همین لینک از آن استفاده می‌کند. هیچ سیستم Subscription موازی‌ای اینجا
    ساخته نمی‌شود؛ اگر روزی الگوی لینک در پنل عوض شود، ربات خودکار همراه می‌شود.
    """
    panel = _panel()
    uid = str(user_config.get("link_uid") or "")
    if not uid or uid not in panel.LINKS:
        return ""
    try:
        return panel.subscription_url_for_uid(uid)
    except AttributeError:
        # سازگاری با نسخه‌های قدیمی‌تر پنل که هنوز helper را ندارند.
        logger.warning("panel has no subscription_url_for_uid; using scheme+host fallback")
        try:
            scheme = panel.get_scheme() if hasattr(panel, "get_scheme") else "https"
            return f"{scheme}://{panel.get_host()}/sub/{uid}"
        except Exception as exc:
            logger.warning("could not build subscription url: %s", exc)
            return ""
    except Exception as exc:
        logger.warning("could not build subscription url: %s", exc)
        return ""


def _days_left(link: dict) -> str:
    """مدت اعتبار باقی‌مانده به روز (برای نمایش در پیام کانفیگ)."""
    raw = link.get("expires_at")
    if not raw:
        return "بدون انقضا"
    try:
        expires = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return "—"
    now = datetime.now(expires.tzinfo) if getattr(expires, "tzinfo", None) else datetime.now()
    seconds = (expires - now).total_seconds()
    if seconds <= 0:
        return "منقضی‌شده"
    # به بالا گرد می‌شود: کانفیگ ۳۰ روزه‌ای که همین الان ساخته شده باید «۳۰ روز»
    # نشان داده شود، نه «۲۹ روز» (که با .days اتفاق می‌افتاد).
    days = math.ceil(seconds / 86400)
    if days <= 1:
        hours = max(1, math.ceil(seconds / 3600))
        return "1 روز" if hours >= 24 else f"{hours} ساعت"
    return f"{days} روز"


def describe(user_config: dict) -> dict:
    """اطلاعات نمایشی یک کانفیگ (نام، نوع، حجم، مصرف، سرعت، وضعیت...)."""
    panel = _panel()
    link = link_of(user_config)
    if not link:
        return {
            "label": user_config.get("label") or "—",
            "status": "deleted",
            "status_label": "حذف‌شده",
        }

    limit_bytes = int(link.get("limit_bytes") or 0)
    used_bytes = int(link.get("used_bytes") or 0)
    remaining = max(0, limit_bytes - used_bytes) if limit_bytes else 0
    speed_bytes = int(link.get("speed_limit_bytes") or 0)

    expired = False
    try:
        expired = bool(panel.is_link_expired(link))
    except Exception:
        pass

    exhausted = bool(limit_bytes and used_bytes >= limit_bytes)
    active = bool(link.get("active")) and not expired and not exhausted

    if expired:
        status, status_label = "expired", "منقضی‌شده"
    elif exhausted:
        status, status_label = "exhausted", "حجم تمام‌شده"
    elif not link.get("active"):
        status, status_label = "disabled", "غیرفعال"
    else:
        status, status_label = "active", "فعال"

    return {
        "label": link.get("label") or "—",
        "protocol": link.get("protocol_label") or link.get("protocol") or "—",
        "created_at": str(link.get("created_at") or "")[:10],
        "expires_at": str(link.get("expires_at") or "")[:10] or "بدون انقضا",
        "total": panel.fmt_bytes(limit_bytes) if limit_bytes else "نامحدود",
        "used": panel.fmt_bytes(used_bytes),
        "remaining": panel.fmt_bytes(remaining) if limit_bytes else "نامحدود",
        "speed": f"{speed_bytes * 8 / 1000 / 1000:.1f} Mbps" if speed_bytes else "نامحدود",
        "ip_limit": int(link.get("ip_limit") or 0) or "نامحدود",
        "duration_days": _days_left(link),
        "status": status,
        "status_label": status_label,
        "active": active,
        "percent": round(min(100.0, used_bytes / limit_bytes * 100), 1) if limit_bytes else 0.0,
    }


# ── ساخت کانفیگ ───────────────────────────────────────────────────────────────
async def create_for_user(telegram_id: int, spec: dict, source: str = "free",
                          label: str | None = None) -> dict:
    """کانفیگ می‌سازد و آن را به کاربر تلگرام نسبت می‌دهد.

    spec: volume_gb, speed_mbps, duration_days, ip_limit, protocol, port
    در صورت خطا ConfigCreationError پرتاب می‌شود تا فراخوان سهمیه را مصرف نکند.
    """
    panel = _panel()

    volume_gb = float(spec.get("volume_gb") or 0)
    speed_mbps = float(spec.get("speed_mbps") or 0)
    duration_days = int(spec.get("duration_days") or 0)
    ip_limit = int(spec.get("ip_limit") or 0)

    expires_at = None
    if duration_days > 0:
        expires_at = (datetime.now() + timedelta(days=duration_days)).isoformat()

    protocol = str(spec.get("protocol") or "").strip() or panel.DEFAULT_PROTOCOL
    try:
        port = int(spec.get("port") or 0) or panel.DEFAULT_PORT
    except (TypeError, ValueError):
        port = panel.DEFAULT_PORT

    user = store.TG_USERS.get(str(telegram_id)) or {}
    uname = user.get("username") or user.get("first_name") or telegram_id
    final_label = (label or f"tg-{uname}-{source}")[:60]

    try:
        uid, record = await panel.make_link(
            label=final_label,
            limit_bytes=gb_to_bytes(volume_gb),
            expires_at=expires_at,
            note=f"Telegram {source} · user {telegram_id}",
            protocol=protocol,
            port=port,
            ip_limit=ip_limit,
            speed_limit_bytes=mbps_to_bytes(speed_mbps),
        )
    except Exception as exc:
        logger.exception("make_link failed for telegram user %s", telegram_id)
        raise ConfigCreationError(str(exc)) from exc

    if not uid:
        raise ConfigCreationError("panel returned no config id")

    ucid = store.next_id(store.TG_USER_CONFIGS)
    user_config = {
        "id": ucid,
        "telegram_id": int(telegram_id),
        "link_uid": uid,
        "label": record.get("label") if isinstance(record, dict) else final_label,
        "source": source,
        "created_at": store.now_iso(),
        "delivered": False,
    }
    store.TG_USER_CONFIGS[ucid] = user_config

    store.bump_stat("configs_created")
    if source == "free":
        store.bump_stat("free_configs")

    await panel.save_state()
    logger.info("config %s created for telegram user %s (%s)", uid, telegram_id, source)
    return user_config


async def mark_delivered(user_config_id: str) -> None:
    rec = store.TG_USER_CONFIGS.get(str(user_config_id))
    if rec is not None:
        rec["delivered"] = True
        rec["delivered_at"] = store.now_iso()


async def delete(actor_id: int, user_config_id: str) -> bool:
    """حذف کانفیگ: هم از LINKS پنل و هم از جدول واسط."""
    from .. import audit
    panel = _panel()
    rec = store.TG_USER_CONFIGS.get(str(user_config_id))
    if not rec:
        return False
    uid = str(rec.get("link_uid"))
    try:
        if uid in panel.LINKS:
            await panel.remove_link(uid)
    except Exception as exc:
        logger.exception("remove_link failed for %s", uid)
        raise ConfigCreationError(str(exc)) from exc

    store.TG_USER_CONFIGS.pop(str(user_config_id), None)
    audit.record(actor_id, "config_delete", rec.get("telegram_id"), "config",
                 rec.get("label"), None, note=f"link={uid}")
    await panel.save_state()
    return True


async def set_active(actor_id: int, user_config_id: str, active: bool) -> bool:
    from .. import audit
    panel = _panel()
    rec = store.TG_USER_CONFIGS.get(str(user_config_id))
    if not rec:
        return False
    uid = str(rec.get("link_uid"))
    if uid not in panel.LINKS:
        return False
    before = bool(panel.LINKS[uid].get("active"))
    await panel.set_link_active(uid, active)
    audit.record(actor_id, "config_set_active", rec.get("telegram_id"), "active",
                 before, bool(active), note=f"link={uid}")
    return True


# ── تغییر حجم / سرعت / انقضا توسط ادمین ──────────────────────────────────────
async def update_limits(actor_id: int, user_config_id: str, *, volume_gb: float | None = None,
                        speed_mbps: float | None = None, days: int | None = None,
                        ip_limit: int | None = None) -> bool:
    """تغییر مشخصات کانفیگ. مستقیماً روی رکورد LINKS پنل اعمال می‌شود، پس
    بلافاصله در خود پنل وب هم دیده می‌شود."""
    from .. import audit
    panel = _panel()
    rec = store.TG_USER_CONFIGS.get(str(user_config_id))
    if not rec:
        return False
    uid = str(rec.get("link_uid"))
    link = panel.LINKS.get(uid)
    if not link:
        return False

    target = rec.get("telegram_id")

    if volume_gb is not None:
        before = int(link.get("limit_bytes") or 0)
        link["limit_bytes"] = gb_to_bytes(volume_gb)
        audit.record(actor_id, "config_set_volume", target, "limit_bytes", before, link["limit_bytes"])

    if speed_mbps is not None:
        before = int(link.get("speed_limit_bytes") or 0)
        link["speed_limit_bytes"] = mbps_to_bytes(speed_mbps)
        audit.record(actor_id, "config_set_speed", target, "speed_limit_bytes", before, link["speed_limit_bytes"])

    if ip_limit is not None:
        before = int(link.get("ip_limit") or 0)
        link["ip_limit"] = max(0, int(ip_limit))
        audit.record(actor_id, "config_set_iplimit", target, "ip_limit", before, link["ip_limit"])

    if days is not None:
        before = link.get("expires_at")
        link["expires_at"] = (datetime.now() + timedelta(days=int(days))).isoformat() if int(days) > 0 else None
        audit.record(actor_id, "config_set_expiry", target, "expires_at", before, link["expires_at"])

    await panel.save_state()
    return True


async def reset_usage(actor_id: int, user_config_id: str) -> bool:
    """صفر کردن حجم مصرف‌شده."""
    from .. import audit
    panel = _panel()
    rec = store.TG_USER_CONFIGS.get(str(user_config_id))
    if not rec:
        return False
    link = panel.LINKS.get(str(rec.get("link_uid")))
    if not link:
        return False
    before = int(link.get("used_bytes") or 0)
    link["used_bytes"] = 0
    audit.record(actor_id, "config_reset_usage", rec.get("telegram_id"), "used_bytes", before, 0)
    await panel.save_state()
    return True


# ── آمار ─────────────────────────────────────────────────────────────────────
def totals() -> dict:
    """آمار کانفیگ‌های تخصیص‌داده‌شده به کاربران تلگرام."""
    active = expired = exhausted = free_sent = 0
    for rec in store.TG_USER_CONFIGS.values():
        info = describe(rec)
        if info["status"] == "active":
            active += 1
        elif info["status"] == "expired":
            expired += 1
        elif info["status"] == "exhausted":
            exhausted += 1
        if rec.get("source") == "free":
            free_sent += 1
    return {
        "total": len(store.TG_USER_CONFIGS),
        "active": active,
        "expired": expired,
        "exhausted": exhausted,
        "free_sent": free_sent,
    }


def by_status(status: str, limit: int = 10) -> list[dict]:
    rows = [r for r in store.TG_USER_CONFIGS.values() if describe(r)["status"] == status]
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return rows[:limit]
