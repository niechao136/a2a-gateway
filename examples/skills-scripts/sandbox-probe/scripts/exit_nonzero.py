"""退出码 / stderr 探针：试跑应看到 exit_code=3，且 stdout 与 stderr 各有一行。

用法：python3 scripts/exit_nonzero.py
"""

import sys


def main() -> int:
    print("probe: 这行来自 stdout")
    print("probe: 这行来自 stderr", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
