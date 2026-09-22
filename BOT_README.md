# VodiWalker — پنل + ربات تلگرام

مستندات راه‌اندازی ربات تلگرام که به پنل مدیریت کانفیگ موجود اضافه شده است.

> ربات و پنل وب **یک سیستم** هستند: ربات کانفیگ‌ها را با همان سرویس‌های پنل
> (`make_link` / `remove_link` / `vless_link_for_link`) می‌سازد و مدیریت می‌کند.
> هیچ سیستم موازی‌ای ساخته نشده است.

---

## ۱. ساخت ربات و گرفتن Token

1. در تلگرام به [@BotFather](https://t.me/BotFather) پیام بدهید.
2. دستور `/newbot` را بفرستید.
3. یک نام و سپس یک username (که به `bot` ختم می‌شود) انتخاب کنید.
4. BotFather یک توکن می‌دهد، چیزی شبیه `1234567890:AA...`.
5. این توکن را در متغیر `TELEGRAM_BOT_TOKEN` بگذارید.

> ⚠️ توکن را هرگز داخل کد یا Git نگذارید. فقط Environment Variable یا بخش
> تنظیمات پنل. اگر توکن لو رفت، با `/revoke` در BotFather باطلش کنید.

توصیه‌های BotFather (اختیاری ولی مفید):
- `/setcommands` → لیست دستورات:
  ```
  start - منوی اصلی
  menu - نمایش منو
  support - ارتباط با پشتیبانی
  id - نمایش شناسه عددی من
  cancel - لغو عملیات فعلی
  ```
- `/setprivacy` → **Enabled** (ربات فقط در چت خصوصی کار می‌کند).

---

## ۲. تعیین Super Admin

1. ربات را استارت کنید و دستور `/id` را بفرستید تا شناسه عددی‌تان را بگیرید.
2. آن عدد را در `TELEGRAM_SUPER_ADMIN_IDS` بگذارید (چند نفر را با کاما جدا کنید):
   ```
   TELEGRAM_SUPER_ADMIN_IDS=123456789,987654321
   ```
3. سرویس را Restart کنید. حالا با `/start` پنل مدیریت را می‌بینید.

**Super Admin** دسترسی کامل دارد و از داخل ربات قابل حذف نیست (محافظت در برابر
قفل‌شدگی). **Admin** معمولی دسترسی محدود دارد و از پنل ادمین → کاربران → نقش
کاربر، قابل تعیین است.

### Permission ها

| Permission | Admin | Super Admin |
|---|---|---|
| `MANAGE_USERS` | ✅ | ✅ |
| `MANAGE_CONFIGS` | ✅ | ✅ |
| `MANAGE_QUOTA` | ✅ | ✅ |
| `MANAGE_CHANNELS` | ✅ | ✅ |
| `VIEW_ANALYTICS` | ✅ | ✅ |
| `MANAGE_SUPPORT` | ✅ | ✅ |
| `SEND_BROADCAST` | ❌ | ✅ |
| `MANAGE_SETTINGS` | ❌ | ✅ |
| `MANAGE_ADMINS` | ❌ | ✅ |

این جدول از `botsys/store.py` (نقش‌های پایه) می‌آید و بعداً بدون بازنویسی قابل
تفکیک است: کافی است `permissions` آن نقش یا فیلد `permissions` خود کاربر را
تغییر دهید.

---

## ۳. Environment Variables

فایل کامل: [`.env.example`](.env.example)

| متغیر | الزامی | توضیح |
|---|---|---|
| `ADMIN_USERNAME` | ✅ | نام کاربری پنل وب |
| `ADMIN_PASSWORD` | ✅ | رمز پنل وب (حتماً عوض کنید) |
| `SECRET_KEY` | — | خالی = خودکار ساخته و روی Volume ذخیره می‌شود |
| `PORT` | — | Railway خودکار تزریق می‌کند |
| `RAILWAY_VOLUME_MOUNT_PATH` | ✅ | مسیر Volume پایدار، مثلاً `/data` |
| `PUBLIC_BASE_URL` | — | آدرس عمومی؛ خالی = از `RAILWAY_PUBLIC_DOMAIN` |
| `TELEGRAM_BOT_TOKEN` | ✅ (برای ربات) | توکن BotFather |
| `TELEGRAM_SUPER_ADMIN_IDS` | ✅ (برای ربات) | شناسه‌های Super Admin |
| `TELEGRAM_ADMIN_IDS` | — | ادمین‌های معمولی (سازگاری با نسخه قبل) |
| `TELEGRAM_UPDATE_MODE` | — | `auto` (پیش‌فرض) / `webhook` / `polling` |
| `TELEGRAM_WEBHOOK_URL` | — | خالی = از دامنه Railway ساخته می‌شود |
| `TELEGRAM_WEBHOOK_SECRET` | — | خالی = تصادفی؛ برای ثبات بین Restart ست کنید |
| `TELEGRAM_CALLBACK_SECRET` | — | خالی = از `SECRET_KEY` استفاده می‌شود |
| `TELEGRAM_BROADCAST_RATE` | — | پیش‌فرض ۲۰ پیام در ثانیه |
| `TCP_PUBLIC_HOST` / `TCP_PUBLIC_PORT` | — | مربوط به قابلیت فعلی TCP Relay |

> `DATABASE_URL` لازم **نیست** — بخش «دیتابیس» را پایین‌تر ببینید.

---

## ۴. اجرا روی Railway

1. **پروژه را به Railway وصل کنید** (از GitHub یا `railway up`).
2. **Volume اضافه کنید** و مسیرش را روی `/data` بگذارید، سپس:
   ```
   RAILWAY_VOLUME_MOUNT_PATH=/data
   ```
   ⚠️ بدون Volume، با هر Deploy همه کانفیگ‌ها و کاربران ربات پاک می‌شوند.
3. **Variables** را از جدول بالا ست کنید.
4. **Deploy** کنید. Health Check روی `/health` تنظیم شده است.
5. لاگ‌ها را ببینید؛ باید این خط را ببینید:
   ```
   Telegram bot running in webhook mode.
   ```
6. در تلگرام `/start` بزنید.

روشن/خاموش کردن ربات بدون Deploy مجدد: پنل وب → تنظیمات → ربات، یا:
```
POST /api/settings/bot/start
POST /api/settings/bot/stop
POST /api/bot/restart
```

### Webhook

در حالت `auto` اگر آدرس عمومی موجود باشد، Webhook خودکار روی این مسیر ثبت می‌شود:

```
POST https://<your-domain>/telegram/webhook/<TELEGRAM_WEBHOOK_SECRET>
```

امنیت دو لایه دارد: Secret داخل مسیر **و** هدر رسمی
`X-Telegram-Bot-Api-Secret-Token`. هر درخواستی که هر دو را نداشته باشد با ۴۰۳ رد
می‌شود.

بررسی وضعیت Webhook:
```
GET /api/bot/webhook-info
```

اگر Webhook به هر دلیلی ثبت نشود، سیستم **خودکار** به Long Polling برمی‌گردد تا
ربات از کار نیفتد. برای اجبار Polling:
```
TELEGRAM_UPDATE_MODE=polling
```

> ربات در همان پراسس و همان Event Loop وب‌سرور اجرا می‌شود، پس هیچ پورت اضافه یا
> سرویس جداگانه‌ای لازم نیست و با پنل وب تداخل ندارد.

---

## ۵. افزودن ربات به کانال و Admin کردن آن

برای اینکه بررسی عضویت اجباری کار کند، تلگرام لازم دارد ربات عضو کانال و
**ادمین** آن باشد (متد `getChatMember`).

1. وارد کانال شوید → Manage Channel → Administrators → Add Admin.
2. username ربات را جست‌وجو و اضافه کنید.
3. دسترسی‌ها را می‌توانید همه خاموش بگذارید؛ فقط Admin بودن مهم است.
4. در ربات: پنل ادمین → 📢 کانال‌ها → ➕ افزودن کانال، و این قالب را بفرستید:
   ```
   عنوان کانال | @username | لینک دعوت (اختیاری)
   ```
   برای کانال خصوصی از Chat ID عددی (مثل `-1001234567890`) و لینک دعوت استفاده کنید.

اگر ربات ادمین نباشد، آن کانال «نامشخص» در نظر گرفته می‌شود: کاربر **بی‌دلیل رد
نمی‌شود** ولی در لاگ هشدار ثبت می‌شود:
```
membership check failed for channel ... — مطمئن شوید ربات در این کانال Admin است.
```

مدیریت کانال‌ها از پنل وب هم ممکن است:
```
GET    /api/bot/channels
POST   /api/bot/channels
PATCH  /api/bot/channels/{id}
DELETE /api/bot/channels/{id}
```

---

## ۶. دیتابیس و Migration

این پروژه دیتابیس SQL یا ORM **ندارد**. State کل پنل در یک فایل JSON روی Volume
نگهداری می‌شود و نوشتن آن اتمیک است (نوشتن در فایل `.tmp` و سپس `replace`):

```
$RAILWAY_VOLUME_MOUNT_PATH/vodiwalker_state.json
```

زیرسیستم ربات از **همان فایل و همان مکانیزم** استفاده می‌کند، پس فایل موازی و
ناسازگاری وجود ندارد. کلیدهای اضافه‌شده:

| کلید | موجودیت |
|---|---|
| `tg_users` | User (کلید اصلی = Telegram User ID) |
| `tg_roles` | Role + Permission |
| `tg_user_configs` | UserConfig (اتصال کاربر تلگرام ↔ کانفیگ واقعی در `links`) |
| `tg_quotas` | Quota (Override هر کاربر) |
| `tg_channels` | Channel (کانال‌های اجباری) |
| `tg_tickets` / `tg_messages` | SupportTicket / SupportMessage |
| `tg_broadcasts` | Broadcast |
| `tg_audit` | AuditLog |
| `tg_stats` | آمار روزانه |
| `tg_settings` / `tg_texts` | تنظیمات و متن‌های قابل ویرایش |

### اجرای Migration

Migration ها **خودکار** در زمان بالا آمدن سرویس اجرا می‌شوند (داخل
`load_state`) و Idempotent هستند، پس کار دستی لازم نیست. نسخه فعلی در
`/health` و در پنل ادمین → تنظیمات دیده می‌شود.

برای افزودن Migration جدید، در `botsys/store.py`:

```python
def _mig_5_something() -> None:
    for rec in TG_USERS.values():
        rec.setdefault("new_field", 0)

MIGRATIONS = {..., 5: _mig_5_something}
SCHEMA_VERSION = 5
```

> چون کانفیگ‌ها همان `links` قبلی هستند، داده‌ی موجود شما دست‌نخورده می‌ماند.

---

## ۷. اجرای پروژه به‌صورت لوکال

```bash
pip install -r requirements.txt

export ADMIN_USERNAME=admin
export ADMIN_PASSWORD=my-strong-pass
export RAILWAY_VOLUME_MOUNT_PATH=./data
export TELEGRAM_BOT_TOKEN=123456:AA...
export TELEGRAM_SUPER_ADMIN_IDS=123456789
export TELEGRAM_UPDATE_MODE=polling      # لوکال آدرس عمومی ندارد

uvicorn main:app --host 0.0.0.0 --port 8000
```

پنل: <http://localhost:8000> · سلامت: <http://localhost:8000/health>

هیچ وابستگی جدیدی اضافه نشده است: ربات از همان `httpx` موجود در
`requirements.txt` استفاده می‌کند.

---

## ۸. تست ربات

```bash
python3 tests/run_all.py
```

سه مجموعه تست، بدون نیاز به شبکه یا توکن واقعی (پنل و Telegram API جعلی‌اند):

| فایل | پوشش |
|---|---|
| `tests/test_bot.py` | ۲۰ سناریوی رفتاری (کاربر جدید، سهمیه، Double-Click، مسدودی، Broadcast، Support، Restart، خطاها، Callback جعلی، دسترسی) |
| `tests/test_styles.py` | دکمه‌های رنگی رسمی و نبودِ ایموجی به‌جای رنگ |
| `tests/test_integration.py` | بالا آمدن `main.py` واقعی، حفظ روت‌های قبلی، اشتراک سرویس ساخت کانفیگ، پایداری State |

تست دستی در تلگرام:

1. `/start` با یک اکانت معمولی → منوی کاربر
2. «دریافت کانفیگ رایگان» بدون عضویت کانال → لیست کانال‌ها + «بررسی عضویت»
3. عضو شوید → «بررسی عضویت» → کانفیگ دریافت می‌شود
4. دوباره سریع کلیک کنید → سهمیه دوباره مصرف نمی‌شود
5. با اکانت Super Admin `/start` → پنل مدیریت

---

## ۹. معماری

```
Telegram (Webhook | Polling)
        ↓
botsys/handlers/router.py         مسیریابی + مهار خطا
        ↓
botsys/middlewares.py             اعتبارسنجی، ثبت کاربر، مسدودی، Rate Limit
        ↓
botsys/handlers/{user,admin}.py   فقط UX، بدون Business Logic
        ↓
botsys/services/*.py              کل Business Logic
        ↓
botsys/store.py  +  main.py       State پنل و سرویس‌های فعلی ساخت کانفیگ
```

```
botsys/
├── settings.py      Environment + پیش‌فرض تنظیمات
├── store.py         موجودیت‌ها، Migration، قفل‌ها، آمار
├── security.py      Role/Permission، امضای Callback، Rate Limit، ضد IDOR
├── keyboards.py     دکمه‌ها با فیلد رسمی style
├── tgapi.py         کلاینت Bot API با Rate Limit و Retry
├── audit.py         Audit Log
├── middlewares.py
├── runner.py        Webhook/Polling و چرخه حیات
├── legacy.py        پل سازگاری با telegram_bot.py قبلی
├── panel_api.py     روت‌های /api/bot برای پنل وب
├── services/        users, quota, channels, configs, issuing, support, broadcast, analytics
└── handlers/        router, user, admin
```

### دکمه‌های رنگی

از فیلد رسمی `style` در `InlineKeyboardButton` استفاده شده است
(`primary` آبی / `success` سبز / `danger` قرمز) — مطابق
[مستندات رسمی Bot API](https://core.telegram.org/bots/api#inlinekeyboardbutton).
هیچ ایموجی‌ای به‌عنوان جایگزین رنگ و هیچ روش غیررسمی‌ای استفاده نشده.

قاعده معنایی: ورود به بخش‌ها `primary`، افزودن/تأیید `success`، حذف/لغو/عملیات
خطرناک `danger`. چون `style` فیلدی اختیاری است، کلاینت‌های قدیمی‌تر فقط رنگ را
نادیده می‌گیرند و دکمه کامل کار می‌کند.

### امنیت

- توکن فقط از Environment یا تنظیمات پنل؛ هیچ Secret در کد یا Git.
- Super Admin جدا از Admin، با Permission های دانه‌درشت.
- هر Callback حساس با HMAC امضا می‌شود؛ دستکاری آن رد می‌شود.
- Telegram User ID ها Validate می‌شوند؛ ربات‌ها رد می‌شوند.
- بررسی مالکیت روی کانفیگ و تیکت (ضد IDOR).
- Rate Limit عمومی هر کاربر + Rate Limit سخت‌گیرانه برای عملیات حساس.
- محافظ Double-Click دو لایه (claim + قفل مخصوص کاربر) و Cooldown بعد از صدور.
- Webhook با Secret مسیر + هدر رسمی.
- تأیید دومرحله‌ای برای حذف کانفیگ، مسدودسازی و Reset سهمیه.
- Audit Log: چه ادمینی، روی چه کاربری، چه فیلدی، مقدار قبل → بعد، چه زمانی.

### اتمی بودن عملیات سهمیه

ترتیب عمداً این است:

```
قفل کاربر → بررسی شرایط → ساخت کانفیگ → مصرف سهمیه → ارسال
```

- شکست ساخت کانفیگ ⇒ سهمیه **هرگز** کم نمی‌شود (خطا لاگ و آمارگیری می‌شود).
- شکست ارسال ⇒ کانفیگ با `delivered=false` باقی می‌ماند و از «کانفیگ‌های من»
  قابل دریافت مجدد است.

---

## ۱۰. تنظیمات قابل تغییر بدون Deploy

از پنل ادمین تلگرام (⚙️ تنظیمات) یا `POST /api/bot/settings`:

تعداد کانفیگ رایگان · حجم · سرعت · مدت اعتبار · سقف آی‌پی · سقف
روزانه/هفتگی/ماهانه · دوره بازنشانی سهمیه · تمدیدپذیری · Cooldown · الزام عضویت
کانال · حالت تعمیر · روشن/خاموش بودن پشتیبانی · Rate Limit · متن خوش‌آمدگویی ·
متن‌های آموزش (Android/iOS/Windows/macOS) · متن پشتیبانی

---

## ۱۱. عیب‌یابی

| نشانه | علت و راه حل |
|---|---|
| ربات جواب نمی‌دهد | لاگ را ببینید. `TELEGRAM_BOT_TOKEN` ست است؟ `/health` را چک کنید. |
| `Telegram bot idle` در لاگ | توکن ست نشده یا نامعتبر است. |
| خطای 409 Conflict | یک نمونه دیگر با همان توکن اجراست. یکی را خاموش کنید. |
| بررسی عضویت کار نمی‌کند | ربات در کانال Admin نیست. |
| کاربر عضو است ولی رد می‌شود | «بررسی عضویت» را بزنید (کش ۲ دقیقه‌ای). |
| داده‌ها با Deploy پاک می‌شوند | Volume وصل نیست یا `RAILWAY_VOLUME_MOUNT_PATH` غلط است. |
| Broadcast کند است | عمدی است؛ Rate Limit تلگرام. `TELEGRAM_BROADCAST_RATE` را تنظیم کنید. |
| دکمه‌ها رنگی نیستند | کلاینت تلگرام را به‌روز کنید؛ `style` فیلد جدیدی است. |

لاگ‌های مفید: `vodiwalker.botsys.*` (runner, router, issuing, audit, broadcast).
