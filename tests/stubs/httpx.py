"""Stub حداقلی httpx برای اجرای تست‌ها در محیط بدون شبکه.

فقط همان سطحی را پیاده می‌کند که botsys.tgapi استفاده می‌کند.
"""

class Timeout:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class Response:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload


class AsyncClient:
    #: توسط تست‌ها ست می‌شود: callable(method, payload) -> dict
    handler = None
    calls = []

    def __init__(self, *args, **kwargs):
        self.closed = False

    async def post(self, url, json=None, timeout=None):
        method = str(url).rsplit("/", 1)[-1]
        payload = dict(json or {})
        AsyncClient.calls.append((method, payload))
        handler = AsyncClient.handler
        if handler is None:
            return Response({"ok": True, "result": {}})
        return Response(handler(method, payload))

    async def aclose(self):
        self.closed = True
