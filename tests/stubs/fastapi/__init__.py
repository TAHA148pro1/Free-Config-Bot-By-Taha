"""Stub حداقلی FastAPI: فقط برای Import کردن main.py در تست‌های بدون شبکه.

هیچ سروری اجرا نمی‌کند؛ صرفاً روت‌ها را ثبت می‌کند تا بتوانیم صحت اتصال
زیرسیستم ربات به پنل (ثبت روت‌ها، هوک‌های startup/shutdown) را بررسی کنیم.
"""


class HTTPException(Exception):
    def __init__(self, status_code: int = 400, detail: str = ""):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class Route:
    def __init__(self, path, endpoint, methods=None):
        self.path = path
        self.endpoint = endpoint
        self.methods = methods or ["GET"]
        self.name = getattr(endpoint, "__name__", "endpoint")


def Depends(dependency=None):
    return dependency


class WebSocket:
    pass


class Request:
    def __init__(self, headers=None, json_body=None, query_params=None, cookies=None):
        self.headers = headers or {}
        self._json = json_body
        self.query_params = query_params or {}
        self.cookies = cookies or {}
        self.client = type("C", (), {"host": "127.0.0.1"})()

    async def json(self):
        return self._json

    async def body(self):
        return b"{}" if self._json is None else b"x"


class _RouterBase:
    def __init__(self, prefix: str = "", **kwargs):
        self.prefix = prefix
        self.routes: list = []

    def _register(self, path, methods, **kwargs):
        def decorator(fn):
            self.routes.append(Route(self.prefix + path, fn, methods))
            return fn
        return decorator

    def get(self, path, **kw): return self._register(path, ["GET"], **kw)
    def post(self, path, **kw): return self._register(path, ["POST"], **kw)
    def patch(self, path, **kw): return self._register(path, ["PATCH"], **kw)
    def put(self, path, **kw): return self._register(path, ["PUT"], **kw)
    def delete(self, path, **kw): return self._register(path, ["DELETE"], **kw)
    def head(self, path, **kw): return self._register(path, ["HEAD"], **kw)

    def api_route(self, path, methods=None, **kw):
        return self._register(path, methods or ["GET"], **kw)

    def websocket(self, path, **kw):
        return self._register(path, ["WS"], **kw)


class APIRouter(_RouterBase):
    def __init__(self, prefix: str = "", tags=None, **kwargs):
        super().__init__(prefix=prefix)
        self.tags = tags or []


class FastAPI(_RouterBase):
    def __init__(self, *args, **kwargs):
        super().__init__()
        self.startup_handlers: list = []
        self.shutdown_handlers: list = []
        self.exception_handlers: dict = {}
        self.middlewares: list = []

    def include_router(self, router, **kwargs):
        self.routes.extend(router.routes)

    def add_middleware(self, cls, **kwargs):
        self.middlewares.append((cls, kwargs))

    def on_event(self, event: str):
        def decorator(fn):
            (self.startup_handlers if event == "startup" else self.shutdown_handlers).append(fn)
            return fn
        return decorator

    def exception_handler(self, exc):
        def decorator(fn):
            self.exception_handlers[exc] = fn
            return fn
        return decorator

    def mount(self, *args, **kwargs):
        return None
