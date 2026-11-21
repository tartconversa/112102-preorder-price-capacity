#!/usr/bin/env python
"""隔离模式启动器。

用 ``python -I run.py <命令>`` 运行：-I 忽略环境变量与用户站点包，
避免处理不可信资料目录时被同名模块（如 json.py）注入。
脚本自身所在目录仍在 sys.path 上，因此可以正常导入 app 包。
"""

import sys
from pathlib import Path

# -I（隔离模式）会把脚本目录移出 sys.path，这里按启动器自身位置显式放回，
# 保证从任意 cwd 调用都能导入 app 包，同时仍享受 -I 的环境变量隔离。
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
