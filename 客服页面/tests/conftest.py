"""pytest 全局配置：确保项目根目录在 sys.path 中。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
