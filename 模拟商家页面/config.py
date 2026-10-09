"""模拟商家服务全局配置（独立服务，与 V4 零代码耦合）。

所有配置均可通过环境变量覆盖，默认值保证 `python main.py` 开箱即用。
"""

import os
from pathlib import Path

# 项目根目录（sj/）
BASE_DIR = Path(__file__).resolve().parent

# ---------- 服务 ----------
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8090"))
API_PREFIX = "/mock-api/v1"
MERCHANT_PAGE_PATH = "/merchant"

# ---------- 数据库 ----------
# 独立 SQLite 数据库文件（首次启动自动建表并写入种子数据）
DB_PATH = Path(os.getenv("MERCHANT_MOCK_DB", BASE_DIR / "data" / "merchant_mock.db"))

# ---------- 鉴权 ----------
TOKEN_TTL_HOURS = int(os.getenv("MERCHANT_MOCK_TOKEN_TTL_HOURS", "24"))

# ---------- 退款风控（与 V4 agent/tools/ecom/refund_policy.py 严格对齐）----------
AUTO_APPROVE_ENABLED = os.getenv("MOCK_AUTO_APPROVE_ENABLED", "true").lower() == "true"
AUTO_APPROVE_MAX_AMOUNT = float(os.getenv("MOCK_AUTO_APPROVE_MAX_AMOUNT", "50"))
FREQ_WINDOW_DAYS = int(os.getenv("MOCK_FREQ_WINDOW_DAYS", "30"))
FREQ_MAX_TIMES = int(os.getenv("MOCK_FREQ_MAX_TIMES", "2"))
MAX_REFUND_RATE = float(os.getenv("MOCK_MAX_REFUND_RATE", "0.3"))
MAX_RISK_SCORE = float(os.getenv("MOCK_MAX_RISK_SCORE", "70"))

# 无理由类退货原因（命中任一关键词才可能自动通过）
NO_REASON_TAGS = ("不喜欢", "尺码不合适", "拍错", "买错", "不想要")
# 质量问题类原因（一律转人工，需照片/视频证据）
QUALITY_TAGS = ("质量问题", "破损", "开胶", "掉色", "故障", "假货", "与描述不符")

# ---------- 售后时效 ----------
RETURN_WINDOW_DAYS = int(os.getenv("MOCK_RETURN_WINDOW_DAYS", "7"))  # 签收后 7 天内可无理由退货

# ---------- 故障模拟（默认关闭，不影响正常演示）----------
MOCK_MERCHANT_ENABLE_FAILURE_SIMULATION = (
    os.getenv("MOCK_MERCHANT_ENABLE_FAILURE_SIMULATION", "false").lower() == "true"
)
MOCK_MERCHANT_LATENCY_MS = int(os.getenv("MOCK_MERCHANT_LATENCY_MS", "0"))
MOCK_MERCHANT_ERROR_RATE = float(os.getenv("MOCK_MERCHANT_ERROR_RATE", "0"))
