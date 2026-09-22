"""Navigation و Back: هر صفحه Parent واقعی خودش را می‌شناسد.

مشکل قبلی: مقصد Back داخل هر Keyboard Builder به‌صورت دستی نوشته می‌شد، پس
بعضی صفحه‌ها Parent اشتباه داشتند (مثلاً صفحه‌ی جزئیات کانفیگ همیشه به پروفایل
کاربر برمی‌گشت، حتی وقتی ادمین از «کانفیگ‌های فعال» آمده بود) و بعضی صفحه‌ها
مثل «کانفیگ رایگان» مقصد Back ثابت داشتند.

راه‌حل (یک محل مرکزی، بدون بازنویسی معماری):

  * PARENTS: نقشه‌ی اعلانی Parent هر صفحه بر اساس همان Callback Data موجود.
    پارامترهای مسیر با {0},{1},... از خودِ Callback جایگزین می‌شوند.
  * origin: فقط برای صفحه‌هایی که واقعاً Parent چندگانه دارند (Free Config و
    جزئیات کانفیگ)، مسیر ورود کاربر در حافظه‌ی پراسس ثبت می‌شود.

بنابراین زنجیره‌ی خواسته‌شده تضمین می‌شود:

    Admin Panel -> Users -> User Details -> User Configs
    Back(User Configs) -> User Details
    Back(User Details) -> Users
    Back(Users)        -> Admin Panel
"""

from __future__ import annotations

#: صفحه -> Parent. کلید یک الگوی Callback است که '*' جای یک سگمنت آزاد است.
#: مقدار می‌تواند شامل {n} باشد که با n-امین سگمنتِ * پر می‌شود.
PARENTS: tuple[tuple[str, str], ...] = (
    # ── کاربر عادی ──
    ("u:menu", ""),                       # ریشه
    ("u:acct", "u:menu"),
    ("u:help", "u:menu"),
    ("u:help:*", "u:help"),
    ("u:support", "u:menu"),
    ("u:cfgs:*", "u:menu"),
    ("u:cfg:*", "u:cfgs:0"),
    ("u:free", "u:menu"),                 # مقصد واقعی از origin خوانده می‌شود
    ("u:chk", "u:menu"),

    # ── پنل ادمین ──
    ("a:menu", ""),                       # ریشه
    ("a:users", "a:menu"),
    ("a:users:*", "a:users"),
    ("a:users:recent:*", "a:users"),
    ("a:users:blocked:*", "a:users"),
    ("a:usr:*", "a:users"),
    ("a:usr:*:cfgs:*", "a:usr:{0}"),
    ("a:usr:*:audit", "a:usr:{0}"),
    ("a:usr:*:edit:*", "a:usr:{0}"),
    ("a:cfgs", "a:menu"),
    ("a:cfgs:*:*", "a:cfgs"),
    ("a:cfg:*", "a:cfgs"),                # مقصد واقعی از origin خوانده می‌شود
    ("a:quota", "a:menu"),
    ("a:quota:*", "a:quota"),
    ("a:chans", "a:menu"),
    ("a:chans:*", "a:chans"),
    ("a:chan:*", "a:chans"),
    ("a:stats", "a:menu"),
    ("a:stats:*", "a:stats"),
    ("a:audit", "a:stats"),
    ("a:settings", "a:menu"),
    ("a:settings:*", "a:settings"),
    ("a:set:*", "a:settings"),
    ("a:txt:*", "a:settings:texts"),
    ("a:bc", "a:menu"),
    ("a:bc:*", "a:bc"),
    ("a:tickets", "a:menu"),
    ("a:tickets:*", "a:tickets"),
    ("a:tkt:*", "a:tickets"),
    ("a:tkt:*:*", "a:tkt:{0}"),
)


def _match(pattern: str, data: str) -> list[str] | None:
    """اگر data با الگو بخواند، مقادیر سگمنت‌های '*' را برمی‌گرداند."""
    p_parts = pattern.split(":")
    d_parts = data.split(":")
    if len(p_parts) != len(d_parts):
        return None
    captured: list[str] = []
    for expected, actual in zip(p_parts, d_parts):
        if expected == "*":
            captured.append(actual)
            continue
        if expected != actual:
            return None
    return captured


def parent_of(data: str, default: str = "") -> str:
    """Parent واقعی یک صفحه بر اساس نقشه‌ی PARENTS.

    دقیق‌ترین الگو (بیشترین سگمنت ثابت) برنده است، پس 'a:usr:5:cfgs:0' به
    'a:usr:5' می‌رسد و نه به 'a:users'.
    """
    data = str(data or "")
    best: tuple[int, str] | None = None
    for pattern, target in PARENTS:
        captured = _match(pattern, data)
        if captured is None:
            continue
        score = pattern.count(":") * 10 + (pattern.count(":") - len(captured))
        if best is None or score > best[0]:
            resolved = target
            for index, value in enumerate(captured):
                resolved = resolved.replace(f"{{{index}}}", value)
            best = (score, resolved)
    if best is None:
        return default
    return best[1] or default


# ── Origin: فقط برای صفحه‌های با Parent چندگانه ──────────────────────────────
#: (telegram_id, page_key) -> callback مقصد Back
_ORIGINS: dict[tuple[int, str], str] = {}
_ORIGIN_CAP = 20000


def remember(telegram_id: int, page_key: str, origin: str) -> None:
    """مسیر ورود کاربر به یک صفحه‌ی چند-والده را ثبت می‌کند."""
    if not origin:
        return
    if len(_ORIGINS) > _ORIGIN_CAP:
        for stale in list(_ORIGINS)[: _ORIGIN_CAP // 2]:
            _ORIGINS.pop(stale, None)
    _ORIGINS[(int(telegram_id), str(page_key))] = str(origin)


def origin(telegram_id: int, page_key: str, default: str = "") -> str:
    return _ORIGINS.get((int(telegram_id), str(page_key))) or default


def forget(telegram_id: int, page_key: str | None = None) -> None:
    if page_key is None:
        for key in [k for k in _ORIGINS if k[0] == int(telegram_id)]:
            _ORIGINS.pop(key, None)
        return
    _ORIGINS.pop((int(telegram_id), str(page_key)), None)


def back_target(telegram_id: int, data: str, page_key: str | None = None,
                default: str = "u:menu") -> str:
    """مقصد Back یک صفحه: اول origin ثبت‌شده، بعد نقشه‌ی PARENTS، بعد default."""
    if page_key:
        remembered = origin(telegram_id, page_key)
        if remembered:
            return remembered
    return parent_of(data, default)
