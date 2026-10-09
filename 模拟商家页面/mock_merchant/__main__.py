"""支持 `python -m mock_merchant` 启动模拟商家服务。"""

import uvicorn

import config


def main() -> None:
    uvicorn.run(
        "mock_merchant.app:create_app",
        host=config.HOST,
        port=config.PORT,
        factory=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
