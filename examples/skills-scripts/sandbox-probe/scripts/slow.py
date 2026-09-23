"""超时探针：睡 600 秒，用于验证沙箱超时 kill 进程组。

预期：试跑时把超时设为 3~5 秒 → timeout=true，且不回传半截输出（stdout 为空）。
用法：python3 scripts/slow.py
"""

import sys
import time


def main() -> int:
    print("probe: 开始睡眠，等待沙箱超时 kill", flush=True)
    time.sleep(600)
    print("probe: 超时后不应看到这行（进程组已被 kill）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
