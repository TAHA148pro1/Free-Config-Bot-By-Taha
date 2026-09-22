"""اجرای همه تست‌ها.

    python3 tests/run_all.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUITES = ["tests/test_bot.py", "tests/test_fixes.py", "tests/test_styles.py",
          "tests/test_integration.py", "tests/test_backup.py"]


def main() -> int:
    failed = []
    for suite in SUITES:
        print(f"\n{'#' * 64}\n# {suite}\n{'#' * 64}")
        proc = subprocess.run([sys.executable, suite], cwd=ROOT)
        if proc.returncode != 0:
            failed.append(suite)
    print(f"\n{'=' * 64}")
    if failed:
        print("❌ تست‌های ناموفق: " + ", ".join(failed))
        return 1
    print("✅ همه تست‌ها با موفقیت اجرا شدند")
    return 0


if __name__ == "__main__":
    sys.exit(main())
