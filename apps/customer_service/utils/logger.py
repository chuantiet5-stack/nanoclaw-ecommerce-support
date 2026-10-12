"""结构化日志配置（生产落地 P1-2.3）。

控制台输出人类可读格式，文件输出 JSON 格式（方便 ELK/Loki 采集）。
本模块为新增能力，现有代码中的 print 暂不替换（保持行为不变），
新代码可逐步采用：

    from utils.logger import logger
    logger.info("已注册工具", extra={"session_key": session_key})
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """JSON 格式日志Formatter，方便日志采集系统解析。"""

    def format(self, record):
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # 附加异常信息
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        # 附加自定义字段（通过 extra 传入）
        for field in ("session_key", "tool_name", "channel", "error"):
            if hasattr(record, field):
                log_entry[field] = getattr(record, field)
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logger(name: str = "nanoclaw", level: str | None = None) -> logging.Logger:
    """配置并返回 logger（重复调用不会叠加 handler）。"""
    level = level or os.getenv("LOG_LEVEL", "INFO")
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    # 避免重复添加 handler
    if logger.handlers:
        return logger

    # 控制台输出（人类可读）
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(console_handler)

    # 文件输出（JSON 格式，供日志采集）
    log_dir = os.getenv("LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.FileHandler(f"{log_dir}/app.log", encoding="utf-8")
    file_handler.setFormatter(JSONFormatter())
    logger.addHandler(file_handler)

    return logger


# 全局 logger 实例
logger = setup_logger()
