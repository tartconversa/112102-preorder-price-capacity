#!/usr/bin/env python
"""测试运行器：以隔离模式（python -I run_tests.py）跑全部 unittest。

项目根与 tests 目录按本文件位置显式加入 sys.path，因此：
- 从任意 cwd 调用均可；
- 配合 -I 时忽略 PYTHONPATH/用户 site-packages，当前工作目录中的同名模块
  （如 csv.py、json.py）无法注入导入。
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TESTS_DIR = PROJECT_ROOT / "tests"
for path in (TESTS_DIR, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

loader = unittest.defaultTestLoader
names = sorted(
    path.stem for path in TESTS_DIR.glob("test_*.py")
)
suite = unittest.TestSuite(
    loader.loadTestsFromName(name) for name in names
)
verbosity = 2 if "-v" in sys.argv[1:] else 1
result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
