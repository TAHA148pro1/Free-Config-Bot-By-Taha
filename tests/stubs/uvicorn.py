"""Stub uvicorn: main.py آن را ایمپورت می‌کند ولی در تست اجرا نمی‌شود."""


def run(*args, **kwargs):
    raise RuntimeError("uvicorn.run should not be called during tests")


class Config:
    def __init__(self, *a, **k): pass


class Server:
    def __init__(self, *a, **k): pass
