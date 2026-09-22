"""Stub aiofiles: پوشش async open برای خواندن/نوشتن فایل State در تست."""

import builtins


class _AsyncFile:
    def __init__(self, fh):
        self._fh = fh

    async def read(self, *a):
        return self._fh.read(*a)

    async def write(self, data):
        return self._fh.write(data)


class _Ctx:
    def __init__(self, *args, **kwargs):
        self._args = args
        self._kwargs = kwargs
        self._fh = None

    async def __aenter__(self):
        self._fh = builtins.open(*self._args, **self._kwargs)
        return _AsyncFile(self._fh)

    async def __aexit__(self, *exc):
        if self._fh:
            self._fh.close()
        return False


def open(*args, **kwargs):
    return _Ctx(*args, **kwargs)
