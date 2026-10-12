# 开发与测试

## 开发环境

进入对应应用目录后，创建独立虚拟环境：

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
```

Linux/macOS：

```bash
source .venv/bin/activate
```

安装该应用目录中的依赖文件后再运行。配置、数据库与会话均在本地生成。

## 依赖选择

- 客服应用优先使用已有的 `requirements-lite.txt`，包含核心服务、渠道、数据访问及测试依赖。
- 客服的 `requirements.txt` 为完整开发环境依赖列表。
- 模拟商家使用自身的 `requirements.txt`，与客服应用分别安装。
- 可选 RAG 与 MySQL/Redis 服务按需要配置，避免将它们作为离线演示的前置条件。

## 代码阅读入口

| 路径 | 职责 |
| --- | --- |
| `客服页面/main.py` | Agent 选择、工具注册与网关组装 |
| `客服页面/agent/offline_agent.py` | 离线规则路由 |
| `客服页面/agent/tools/ecom/` | 商品、订单、物流、退款与工单工具 |
| `客服页面/security/` | 登录、JWT 与密码校验 |
| `模拟商家页面/mock_merchant/routes/` | 商家 API |
| `模拟商家页面/mock_merchant/refund_policy.py` | 退款规则 |
| `模拟商家页面/mock_merchant/seed.py` | 模拟数据生成 |

## 自动化测试

客服应用，在其虚拟环境中执行：

```bash
cd 客服页面
python -m pytest tests -v
```

模拟商家，在商家虚拟环境中安装 `pytest` 后执行：

```bash
cd 模拟商家页面
python -m pytest tests -v
```

客服测试涵盖认证、工具、退款规则、Agent 和网关；商家测试覆盖鉴权、商品库存、订单、物流、售后与工单。商家集成测试使用独立的临时 SQLite 数据库。是否通过以实际测试输出为准。

## 手动验证

1. 启动模拟商家，检查 `/health` 与 `/docs`。
2. 初始化客服本地 JSON 及测试账号，启动离线客服。
3. 检查登录、商品咨询、订单查询与人工接入。
4. 配置模型密钥后验证在线工具调用；需要订单或售后演示时准备相应本地测试数据。

新增业务工具时，明确输入参数、身份与归属检查、业务规则和失败返回；同步更新测试与配置文档。
