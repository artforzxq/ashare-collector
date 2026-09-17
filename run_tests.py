#!/usr/bin/env python
"""测试入口：python run_tests.py"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(str(PROJECT_ROOT / "tests"), top_level_dir=str(PROJECT_ROOT))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
