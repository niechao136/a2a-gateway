"""大输出探针：stdout 共 40000 字节，越过 32768 字节截断阈值。

预期：exit_code=0、truncated=true（stdout 截断到 32768 字节）。
用法：python3 scripts/noisy.py
"""

import sys

LINE = "bench " + "x" * 73  # 79 字节 + 换行 = 每行 80 字节


def main() -> int:
    # 写二进制流：避免 Windows 文本模式把 \n 翻译成 \r\n，保证各平台恒为 40000 字节
    line = f"{LINE}\n".encode("ascii")
    out = sys.stdout.buffer
    for _ in range(500):  # 500 × 80 = 40000 字节
        out.write(line)
    out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
