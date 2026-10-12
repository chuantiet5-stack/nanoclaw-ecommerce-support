"""结构化日志模块测试（生产落地 P1-2.3）。"""

import json
import logging

from utils.logger import JSONFormatter, setup_logger


class TestJSONFormatter:
    def _make_record(self, level=logging.INFO, msg="hello", extra=None):
        record = logging.LogRecord(
            name="nanoclaw", level=level, pathname=__file__, lineno=1,
            msg=msg, args=(), exc_info=None,
        )
        for k, v in (extra or {}).items():
            setattr(record, k, v)
        return record

    def test_valid_json_output(self):
        formatter = JSONFormatter()
        parsed = json.loads(formatter.format(self._make_record()))
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "hello"
        assert parsed["logger"] == "nanoclaw"
        assert "timestamp" in parsed

    def test_custom_fields(self):
        formatter = JSONFormatter()
        record = self._make_record(
            extra={"session_key": "web:han", "tool_name": "query_order"}
        )
        parsed = json.loads(formatter.format(record))
        assert parsed["session_key"] == "web:han"
        assert parsed["tool_name"] == "query_order"

    def test_exception_attached(self):
        formatter = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = logging.LogRecord(
                name="nanoclaw", level=logging.ERROR, pathname=__file__, lineno=1,
                msg="failed", args=(), exc_info=sys.exc_info(),
            )
        parsed = json.loads(formatter.format(record))
        assert "ValueError" in parsed["exception"]


class TestSetupLogger:
    def test_no_duplicate_handlers(self):
        """重复调用不叠加 handler。"""
        logger1 = setup_logger("nanoclaw-test-dup")
        count = len(logger1.handlers)
        logger2 = setup_logger("nanoclaw-test-dup")
        assert len(logger2.handlers) == count
        assert logger1 is logger2

    def test_logger_writes(self):
        logger = setup_logger("nanoclaw-test-write")
        logger.info("test message")
        # 文件在 LOG_DIR（默认 logs/app.log）
        import os
        log_file = os.path.join("logs", "app.log")
        assert os.path.isfile(log_file)
