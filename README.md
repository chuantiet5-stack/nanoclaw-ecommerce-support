# NanoClaw Commerce — 电商智能客服

面向商品咨询、订单查询和售后处理的 Python Agent 应用，配套独立模拟商家 API 和商家后台。

## 功能

- 商品搜索、详情查询、下单与用户偏好记录。
- 订单、收货地址、物流、退款与售后工单处理。
- 人工客服接入与工作台。
- 模拟商家商品库存、订单物流、售后与经营看板。
- 模型驱动 Agent 与无模型密钥的离线规则模式。
- 支持 Web、命令行、飞书和 QQ 渠道。

## 技术栈

Python · FastAPI · Uvicorn · SQLite · JWT · bcrypt · OpenAI 兼容接口

## 项目结构

```text
nanoclaw-ecommerce-support/
├── apps/
│   ├── customer_service/   # 客服 Agent、业务工具、数据示例及测试
│   └── merchant/           # 模拟商家 API、后台及测试
├── docs/                  # 配置与开发文档
├── requirements/          # 原开发环境依赖快照
├── requirements.txt       # 项目依赖入口
├── run.py                 # 服务启动入口
├── .editorconfig
├── .gitignore
└── README.md
```

## 快速开始

```bash
git clone https://github.com/chuantiet5-stack/nanoclaw-ecommerce-support.git
cd nanoclaw-ecommerce-support
python -m venv .venv
```

激活虚拟环境（[操作说明](docs/development.md#开发环境)），然后安装依赖并创建配置：

```bash
python -m pip install -r requirements.txt
python run.py customer --init-config
```

按[配置指南](docs/configuration.md#初始化客服数据)准备本地数据和测试账号。`api_key` 留空时使用离线规则 Agent，填写自己的模型配置后使用在线 Agent。

启动模拟商家：

```bash
python run.py merchant
```

首次启动自动创建数据库和演示数据。商家后台：<http://127.0.0.1:8090/merchant>，API 文档：<http://127.0.0.1:8090/docs>。

在另一个终端启动客服：

```bash
python run.py customer
```

客服地址：<http://127.0.0.1:8080>。两个服务也可以分别安装各自的依赖文件，单独运行。

## 测试

```bash
cd apps/customer_service
python -m pytest tests -v
```

商家测试在 `apps/merchant` 中执行相同命令。可选 MySQL、Redis 与 RAG 服务的接入方式见配置文档。

## 文档

- [配置与数据初始化](docs/configuration.md)
- [开发与测试](docs/development.md)
- [模拟商家说明](apps/merchant/README.md)

真实凭证、账号、订单、会话和数据库在本地生成；仓库仅保留配置与数据结构示例。
