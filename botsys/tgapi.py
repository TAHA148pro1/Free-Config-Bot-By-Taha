"""کلاینت Telegram Bot API.

از httpx (وابستگی موجود پروژه) استفاده می‌کند تا وابستگی سنگین جدید اضافه نشود.
شامل Rate Limit سراسری، Retry روی 429 و مدیریت کامل خطاست: هیچ خطای تلگرام
باعث Crash شدن ربات یا توقف صف Broadcast نمی‌شود.
"""

from __future__ import annotations

import asyncio
import html
import logging
import time

import httpx

logger = logging.getLogger("vodiwalker.botsys.tgapi")


class TelegramError(Exception):
    """خطای سمت Telegram API که قابل نمایش/لاگ است."""

    def __init__(self, method: str, description: str, error_code: int | None = None):
        super().__init__(f"{method}: {description}")
        self.method = method
        self.description = description
        self.error_code = error_code


def h(value) -> str:
    """Escape کردن متن آزاد کاربر/ادمین برای parse_mode=HTML.

    بدون این کار یک کاراکتر '<' در نام کاربر باعث رد شدن کل پیام توسط تلگرام
    می‌شود (400 Bad Request: can't parse entities).
    """
    return html.escape(str(value if value is not None else ""), quote=False)


class TelegramClient:
    """کلاینت async با محدودیت نرخ ارسال."""

    def __init__(self, token: str = "", rate: int = 25):
        self._token = (token or "").strip()
        self._client: httpx.AsyncClient | None = None
        self._rate = max(1, int(rate))
        self._slot_lock = asyncio.Lock()
        self._next_slot = 0.0
        #: وضعیت پاسخ‌دهی Callback در حال پردازش (Router مدیریتش می‌کند).
        self._answered: dict[str, bool] = {}

    # ── lifecycle ────────────────────────────────────────────────────────────
    @property
    def token(self) -> str:
        return self._token

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def set_token(self, token: str) -> None:
        self._token = (token or "").strip()

    @property
    def api_base(self) -> str:
        return f"https://api.telegram.org/bot{self._token}"

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0))

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None

    # ── rate limiting ────────────────────────────────────────────────────────
    async def _throttle(self) -> None:
        """حداکثر self._rate درخواست در ثانیه، سراسری برای کل ربات."""
        async with self._slot_lock:
            now = time.monotonic()
            gap = 1.0 / self._rate
            slot = max(now, self._next_slot)
            self._next_slot = slot + gap
        delay = slot - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

    # ── core call ────────────────────────────────────────────────────────────
    async def call(self, method: str, _retries: int = 2, **params):
        """یک متد Bot API را صدا می‌زند.

        در صورت خطا None برمی‌گرداند (به‌جای Raise) تا Handler ها ساده بمانند؛
        برای جاهایی که باید خطا را تشخیص دهیم از call_strict استفاده می‌شود.
        """
        try:
            return await self.call_strict(method, _retries=_retries, **params)
        except TelegramError as exc:
            logger.warning("Telegram %s failed: %s", method, exc.description)
            return None
        except Exception as exc:  # network / transport
            logger.warning("Telegram %s transport error: %s", method, exc)
            return None

    async def call_strict(self, method: str, _retries: int = 2, **params):
        """مثل call ولی در صورت خطا TelegramError پرتاب می‌کند."""
        if not self.configured:
            raise TelegramError(method, "bot token is not configured")
        if self._client is None:
            await self.start()

        payload = {k: v for k, v in params.items() if v is not None}
        attempt = 0
        while True:
            await self._throttle()
            try:
                resp = await self._client.post(f"{self.api_base}/{method}", json=payload)
                data = resp.json()
            except Exception as exc:
                if attempt >= _retries:
                    raise TelegramError(method, f"transport error: {exc}") from exc
                attempt += 1
                await asyncio.sleep(1.5 * attempt)
                continue

            if data.get("ok"):
                return data.get("result")

            code = data.get("error_code")
            desc = str(data.get("description") or "unknown error")

            # 429: تلگرام می‌گوید چند ثانیه صبر کن.
            if code == 429 and attempt < _retries:
                retry_after = int((data.get("parameters") or {}).get("retry_after") or 1)
                await asyncio.sleep(min(retry_after, 60) + 0.5)
                attempt += 1
                continue

            raise TelegramError(method, desc, code)

    # ── helpers ──────────────────────────────────────────────────────────────
    async def send_message(self, chat_id: int, text: str, kb: dict | None = None, **extra):
        return await self.call(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            link_preview_options={"is_disabled": True},
            reply_markup=kb,
            **extra,
        )

    async def send_message_strict(self, chat_id: int, text: str, kb: dict | None = None, **extra):
        return await self.call_strict(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            link_preview_options={"is_disabled": True},
            reply_markup=kb,
            **extra,
        )

    async def send_document(self, chat_id: int, document: bytes, filename: str, caption: str = ""):
        """ارسال فایل باینری با multipart/form-data (برای بکاپ/ریستور)."""
        if not self.configured:
            return None
        if self._client is None:
            await self.start()
        await self._throttle()
        files = {"document": (filename, document, "application/zip")}
        data = {"chat_id": str(chat_id), "caption": caption or "", "parse_mode": "HTML"}
        try:
            resp = await self._client.post(f"{self.api_base}/sendDocument", data=data, files=files)
            payload = resp.json()
            if payload.get("ok"):
                return payload.get("result")
            logger.warning("Telegram sendDocument failed: %s", payload.get("description"))
        except Exception:
            logger.warning("Telegram sendDocument transport error", exc_info=True)
        return None

    async def download_file(self, file_id: str) -> bytes:
        """Download a Telegram file by file_id."""
        info = await self.call_strict("getFile", file_id=file_id)
        path = str((info or {}).get("file_path") or "")
        if not path:
            raise TelegramError("getFile", "file_path missing")
        if self._client is None:
            await self.start()
        await self._throttle()
        resp = await self._client.get(f"https://api.telegram.org/file/bot{self._token}/{path}")
        if resp.status_code != 200:
            raise TelegramError("downloadFile", f"HTTP {resp.status_code}")
        return resp.content

    async def send_photo_strict(self, chat_id: int, photo: str, caption: str = "", kb: dict | None = None):
        return await self.call_strict(
            "sendPhoto",
            chat_id=chat_id,
            photo=photo,
            caption=caption or None,
            parse_mode="HTML",
            reply_markup=kb,
        )

    async def edit_message(self, chat_id: int, message_id: int, text: str, kb: dict | None = None):
        """ویرایش پیام؛ اگر ممکن نبود پیام تازه می‌فرستد.

        'message is not modified' خطای واقعی نیست و نباید باعث ارسال پیام تکراری شود.
        """
        try:
            return await self.call_strict(
                "editMessageText",
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
                link_preview_options={"is_disabled": True},
                reply_markup=kb,
            )
        except TelegramError as exc:
            if "not modified" in exc.description.lower():
                # پیام از قبل همین محتوا را دارد: یعنی وضعیت مطلوب برقرار است.
                # باید «موفق» گزارش شود، وگرنه فراخوان آن را شکست ارسال می‌فهمد.
                return True
            logger.debug("edit fallback to send: %s", exc.description)
            return await self.send_message(chat_id, text, kb)
        except Exception:
            return await self.send_message(chat_id, text, kb)

    # ── callback answers ─────────────────────────────────────────────────────
    #: هر callback_query فقط *یک بار* قابل پاسخ است. قبلاً Router بی‌درنگ یک
    #: Ack خالی می‌فرستاد و بعد Handler ها متن واقعی («تغییر کرد»، «لینک کپی شد»)
    #: را می‌فرستادند که تلگرام دیگر آن را نمایش نمی‌داد؛ یعنی هیچ Notification
    #: کوتاهی به کاربر نمی‌رسید. حالا اولین پاسخِ *دارای متن* برنده است و
    #: Ack خالی فقط در صورتی فرستاده می‌شود که هیچ Handler ای پاسخ نداده باشد.
    #: دامنه‌ی این وضعیت *فقط* طول عمر پردازش یک Update است: Router با
    #: begin_callback باز می‌کند و با end_callback می‌بندد. بنابراین هیچ حالت
    #: کهنه‌ای باقی نمی‌ماند و حافظه رشد نمی‌کند.
    def begin_callback(self, callback_id: str | None) -> None:
        if callback_id:
            self._answered[str(callback_id)] = False

    def end_callback(self, callback_id: str | None) -> None:
        if callback_id:
            self._answered.pop(str(callback_id), None)

    def answered(self, callback_id: str | None) -> bool:
        return bool(callback_id) and self._answered.get(str(callback_id), False)

    async def answer_callback(self, callback_id: str, text: str = "", alert: bool = False):
        """پاسخ کوتاه به یک Callback (Notification تلگرام، بدون پیام جدید در چت)."""
        if not callback_id or self.answered(callback_id):
            return None
        if str(callback_id) in self._answered:
            self._answered[str(callback_id)] = True
        return await self.call(
            "answerCallbackQuery",
            callback_query_id=callback_id,
            text=text or None,
            show_alert=alert or None,
        )

    async def get_chat_member(self, chat: str | int, user_id: int):
        return await self.call_strict("getChatMember", chat_id=chat, user_id=user_id)

    async def get_me(self):
        return await self.call_strict("getMe")

    # ── webhook management ───────────────────────────────────────────────────
    async def set_webhook(self, url: str, secret_token: str = ""):
        return await self.call_strict(
            "setWebhook",
            url=url,
            secret_token=secret_token or None,
            allowed_updates=["message", "callback_query", "chat_member"],
            drop_pending_updates=False,
            max_connections=40,
        )

    async def delete_webhook(self, drop_pending: bool = False):
        return await self.call("deleteWebhook", drop_pending_updates=drop_pending)

    async def get_webhook_info(self):
        return await self.call("getWebhookInfo")

    async def get_updates(self, offset: int, timeout: int = 30):
        return await self.call(
            "getUpdates",
            offset=offset,
            timeout=timeout,
            allowed_updates=["message", "callback_query", "chat_member"],
            _retries=0,
        )


#: نمونه‌ی مشترک (Singleton) که کل زیرسیستم از آن استفاده می‌کند.
client = TelegramClient()
