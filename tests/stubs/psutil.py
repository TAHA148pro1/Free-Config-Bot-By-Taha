"""Stub psutil: مقادیر ثابت برای تست تلمتری."""


def cpu_percent(*a, **k): return 5.0
def virtual_memory(): return type("M", (), {"percent": 30.0, "total": 1 << 30, "used": 1 << 28, "available": 1 << 29})()
def disk_usage(p="/"): return type("D", (), {"percent": 20.0, "total": 1 << 35, "used": 1 << 33, "free": 1 << 34})()
def net_io_counters(): return type("N", (), {"bytes_sent": 0, "bytes_recv": 0})()
def boot_time(): return 0.0
def cpu_count(*a, **k): return 2
def Process(*a, **k): return type("P", (), {"memory_info": lambda self: type("MI", (), {"rss": 1 << 24})(), "cpu_percent": lambda self, *a, **k: 1.0})()
