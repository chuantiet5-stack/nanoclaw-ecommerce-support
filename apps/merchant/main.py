"""模拟商家服务独立启动入口。

启动方式：
    python main.py                 # 默认 0.0.0.0:8090
    python main.py --port 8091     # 自定义端口
    python main.py --reseed        # 强制重置演示数据

启动后：
    - 接口文档：http://localhost:8090/docs
    - 商家后台：http://localhost:8090/merchant
    - API 前缀：http://localhost:8090/mock-api/v1
"""

import argparse

import uvicorn

import config


def main() -> None:
    parser = argparse.ArgumentParser(description="模拟商家 API 服务（独立端口 8090）")
    parser.add_argument("--host", default=config.HOST, help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=config.PORT,
                        help=f"监听端口（默认 {config.PORT}）")
    parser.add_argument("--reseed", action="store_true",
                        help="启动前重置数据库并重新写入演示种子数据")
    args = parser.parse_args()

    if args.reseed:
        from mock_merchant import db
        db.init_db(force_reseed=True)
        print("✅ 演示数据已重置")

    print("=" * 62)
    print("  模拟商家服务启动中（独立服务，与 V4 通过 HTTP 协作）")
    print(f"  - 接口文档 : http://localhost:{args.port}/docs")
    print(f"  - 商家后台 : http://localhost:{args.port}/merchant")
    print(f"  - API 前缀 : http://localhost:{args.port}{config.API_PREFIX}")
    print("  - 演示账号 : merchant_admin / 123456（管理员）")
    print("               operator / 123456（运营）")
    print("               after_sales / 123456（售后客服）")
    print("=" * 62)

    uvicorn.run(
        "mock_merchant.app:create_app",
        host=args.host,
        port=args.port,
        factory=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
