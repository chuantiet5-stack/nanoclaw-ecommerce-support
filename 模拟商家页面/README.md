# 模拟商家服务（merchant-mock / sj）

独立于 V4 客服系统的**模拟商家 API + 商家后台**服务。无需任何真实商家凭证、无需外网，
开箱即可演示商品、订单、物流、售后、退款风控、工单、看板全流程。

---

## 一、整体架构

```
┌─────────────────────────┐        HTTP (localhost)        ┌─────────────────────────┐
│  V4 客服系统 (端口 8080)  │ ───────────────────────────▶  │  模拟商家服务 (端口 8090) │
│  python main.py          │   merchant_api_client.py      │  python main.py          │
│  数据源: JSON/MySQL/Mock │ ◀───────────────────────────  │  SQLite 演示数据          │
└─────────────────────────┘     统一响应 / 错误码           └─────────────────────────┘
```

- **物理隔离**：本目录（`sj/`）与 V4（`v4/`）完全平级，各自拥有独立的 `main.py`、
  `requirements.txt`、虚拟环境（`.venv/`）和 SQLite 数据库（`data/merchant_mock.db`），代码零耦合。
- **通信方式**：V4 仅通过 HTTP 调用本服务，所有 API 前缀 `/mock-api/v1`，
  商家后台挂载在 `/merchant`。
- **技术栈**：FastAPI + Uvicorn + SQLite（标准库 sqlite3，无驱动依赖）+ Pydantic。

### 目录结构

```
sj/
├── main.py                    # 独立启动入口（默认 0.0.0.0:8090）
├── config.py                  # 全局配置（环境变量可覆盖）
├── requirements.txt           # 独立依赖
├── merchant_api_client.py     # 交付给 V4 的 HTTP 客户端（复制到 V4 即用）
├── smoke_test.py              # 冒烟测试脚本
├── data/merchant_mock.db      # 独立 SQLite（首次启动自动建表 + 种子数据）
├── tests/                     # pytest 集成测试（55 个用例）
└── mock_merchant/
    ├── app.py                 # FastAPI 应用工厂（中间件/异常/路由注册）
    ├── db.py                  # SQLite 访问层 + 建表
    ├── seed.py                # 演示种子数据（日期相对"今天"动态生成）
    ├── auth.py                # Token 鉴权 + 角色权限（RBAC）
    ├── rules.py               # 订单/售后/退款状态机、脱敏、ID 生成
    ├── refund_policy.py       # 退款风控规则引擎（与 V4 R0-R8 对齐）
    ├── faults.py              # API 故障模拟中间件
    ├── response.py            # 统一响应结构 + 错误码
    ├── order_service.py       # 订单共享服务（状态流转日志）
    ├── routes/                # API 路由（auth/products/inventory/orders/…）
    └── web/templates/merchant.html   # 商家后台单页应用
```

---

## 二、如何启动

```bash
# 终端 1：启动模拟商家服务（本目录）
cd 项目/sj
python main.py                # 默认 0.0.0.0:8090
#   可选参数：
#   python main.py --port 8091   # 自定义端口
#   python main.py --reseed      # 重置数据库并重新写入演示数据

# 终端 2：启动 V4 客服（V4 目录，互不影响）
cd 项目/v4
python main.py                # 默认 8080
```

启动后：

| 入口 | 地址 |
|---|---|
| 接口文档（Swagger） | http://localhost:8090/docs |
| 商家后台 | http://localhost:8090/merchant |
| API 前缀 | http://localhost:8090/mock-api/v1 |
| 健康检查 | http://localhost:8090/health |

首次启动自动创建 `data/merchant_mock.db` 并写入全部演示数据（删除该文件后重启即可恢复初始状态）。

---

## 三、如何登录商家后台

1. 打开 http://localhost:8090/merchant；
2. 输入演示账号（见下表），密码统一 `123456`；
3. 后台功能：经营看板、商品/库存/订单/物流/售后/退款/工单管理、顾客查询、
   操作日志、故障模拟控制台（管理员可新建商品、员工视角按权限展示菜单）；
4. 商家后台**仅供商家员工登录**：顾客账号（han / lei）登录会被拒绝，
   顾客请走 V4 客服系统（Web 页面右上角【登录】）访问自己的数据。

## 四、演示账号

| 用户名 | 密码 | 角色 | 说明 |
|---|---|---|---|
| merchant_admin | 123456 | 商家管理员 | 全部权限（含商品删除、故障模拟），可登录商家后台 |
| operator | 123456 | 商家运营 | 商品/库存/订单/物流/工单/看板，可登录商家后台 |
| after_sales | 123456 | 售后客服 | 售后/退款审核、工单、顾客查询，可登录商家后台 |
| han | 123456 | 顾客 C001 韩梅梅 | 黄金会员，正常退款风险画像（仅限 API 调用，不能登录商家后台） |
| lei | 123456 | 顾客 C002 李雷 | 高风险顾客（退款率 0.75，用于风控演示；仅限 API 调用，不能登录商家后台） |

---

## 五、API 列表（全部前缀 `/mock-api/v1`）

### 鉴权
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /auth/token | 登录获取 access_token |
| POST | /auth/refresh | 刷新 token |
| GET | /auth/me | 当前身份信息 |

### 商品与库存
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /products | 商品列表（keyword/category/status/分页） |
| GET | /products/{sku} | 商品详情 |
| POST | /products | 新增商品 |
| PUT | /products/{sku} | 更新商品 |
| PATCH | /products/{sku}/status | 上架/下架 |
| DELETE | /products/{sku} | 删除商品（仅管理员） |
| GET | /inventory | 库存列表（low_stock_only 预警） |
| GET | /inventory/{sku} | 单品库存（含尺码分布） |
| POST | /inventory/adjust | 调整库存（change_qty 正负） |
| POST | /inventory/reserve | 预留库存 |
| POST | /inventory/release | 释放预留 |

### 订单（状态机：待付款→已付款/已取消→待发货→已发货→已签收→已完成）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /orders | 订单列表（customer_id/status/keyword/分页） |
| GET | /orders/{order_id} | 订单详情（含物流、售后、状态流转日志） |
| POST | /orders | 创建订单（校验库存、自动锁库存） |
| POST | /orders/{order_id}/pay | 支付 |
| POST | /orders/{order_id}/cancel | 取消（回滚库存） |
| POST | /orders/{order_id}/ship | 发货（生成物流单） |
| POST | /orders/{order_id}/confirm | 确认收货 |
| POST | /orders/{order_id}/complete | 完成（7 天售后期开始） |

### 物流
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /logistics/{order_id} | 物流轨迹 |
| POST | /logistics/create | 手工建物流单 |
| POST | /logistics/{order_id}/advance | 推进一程（可模拟异常） |
| POST | /logistics/{order_id}/sign | 签收 |

### 售后（仅退款/退货退款/换货）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /after-sales | 售后列表 |
| GET | /after-sales/{id} | 售后详情 |
| POST | /after-sales | 发起售后（校验 7 天售后期） |
| POST | /after-sales/{id}/approve | 审核通过 |
| POST | /after-sales/{id}/reject | 驳回（订单状态回滚） |
| POST | /after-sales/{id}/receive | 收到退回商品 |
| POST | /after-sales/{id}/complete | 完成售后 |

### 退款（风控规则引擎 R0-R8）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /refunds | 退款单列表 |
| GET | /refunds/{id} | 退款单详情（含 risk_decision） |
| POST | /refunds | 发起退款（自动跑风控：自动通过/自动拒绝/转人工） |
| POST | /refunds/{id}/approve | 人工审核通过 |
| POST | /refunds/{id}/reject | 人工驳回 |
| POST | /refunds/{id}/success | 打款成功（终态） |

### 工单
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /tickets | 工单列表 |
| GET | /tickets/{id} | 工单详情（含往来记录） |
| POST | /tickets | 创建工单 |
| POST | /tickets/{id}/assign | 分配客服 |
| POST | /tickets/{id}/reply | 回复 |
| POST | /tickets/{id}/close | 关闭 |

### 顾客（手机号/地址脱敏）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /customers | 顾客列表（脱敏） |
| GET | /customers/{customer_id} | 顾客详情（脱敏，本人查询还原） |
| GET | /customers/{customer_id}/orders | 顾客订单 |
| GET | /customers/{customer_id}/after-sales | 顾客售后 |
| GET | /customers/{customer_id}/tickets | 顾客工单 |

### 看板
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /dashboard/summary | KPI 汇总 |
| GET | /dashboard/sales | 近 7 天趋势 + 商品销量 TOP10 |
| GET | /dashboard/orders | 订单状态分布 |
| GET | /dashboard/after-sales | 售后/退款统计 |
| GET | /dashboard/customer-service | 客服指标 |

### 运维
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /admin/faults | 查看故障模拟配置 |
| PUT | /admin/faults | 修改故障模拟配置 |
| POST | /admin/faults/reset | 重置为默认（全部关闭） |
| GET | /admin/op-logs | 操作日志 |
| GET | /health | 健康检查（白名单，不受故障模拟影响） |

---

## 六、API 请求示例

```bash
# 1. 登录（员工或顾客通用）
curl -X POST http://localhost:8090/mock-api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username": "operator", "password": "123456"}'

# 2. 搜索商品
curl "http://localhost:8090/mock-api/v1/products?keyword=跑鞋&page=1&page_size=10" \
  -H "Authorization: Bearer mtk_xxx"

# 3. 创建订单
curl -X POST http://localhost:8090/mock-api/v1/orders \
  -H "Authorization: Bearer mtk_xxx" -H "Content-Type: application/json" \
  -d '{"customer_id": "C001",
       "items": [{"sku": "RUN-001", "qty": 1, "size": "41", "color": "黑色"}],
       "receiver_name": "韩梅梅", "receiver_phone": "13812345678",
       "address": "北京市朝阳区建国路88号"}'

# 4. 发起退款（自动风控）
curl -X POST http://localhost:8090/mock-api/v1/refunds \
  -H "Authorization: Bearer mtk_xxx" -H "Content-Type: application/json" \
  -d '{"order_id": "SO202609070001", "customer_id": "C001", "reason": "尺码不合适"}'
```

## 七、API 响应示例

成功（统一结构 `success / request_id / timestamp / data`）：

```json
{
  "success": true,
  "request_id": "req-1a2b3c",
  "timestamp": "2026-09-08T10:00:00Z",
  "data": {
    "order_id": "SO202609070001",
    "customer_id": "C001",
    "status": "待付款",
    "items": [{"sku": "RUN-001", "name": "轻风慢跑鞋", "qty": 1, "price": 299.0}],
    "goods_amount": 299.0,
    "discount": 0.0,
    "freight": 0.0,
    "total": 299.0,
    "receiver_name": "韩梅梅",
    "receiver_phone": "138****5678",
    "created_at": "2026-09-08 10:00:00"
  }
}
```

失败（含业务错误码，HTTP 状态码同步 4xx/5xx）：

```json
{
  "success": false,
  "request_id": "req-4d5e6f",
  "timestamp": "2026-09-08T10:00:01Z",
  "error": {"code": "INSUFFICIENT_STOCK", "message": "商品 RUN-003 库存不足，剩余 3 件"}
}
```

常见错误码：`UNAUTHORIZED`（未登录）、`FORBIDDEN`（无权限）、
`ORDER_NOT_FOUND` / `PRODUCT_NOT_FOUND`（资源不存在）、
`INVALID_STATUS_TRANSITION`（非法状态跳转）、`INSUFFICIENT_STOCK`（库存不足）、
`AFTER_SALE_WINDOW_CLOSED`（超出售后时效）、`ORDER_NOT_OWNED`（非本人订单）、
`RISK_CONTROL_REJECTED`（风控拒绝）。

---

## 八、V4 如何调用模拟 API

**V4 已默认接入本服务**（2026-09-08 起）：`v4/agent/tools/ecom/store.py` 的
`DATA_BACKEND` 默认为 `merchant_api`，商品数据实时来自本服务——
本服务上架新商品、改价、下架，V4 的 AI 客服立即可见并向顾客推荐。

| 组件 | 位置 | 作用 |
|---|---|---|
| HTTP 客户端 | `v4/agent/tools/ecom/merchant_api_client.py` | 本目录 `merchant_api_client.py` 的副本，零第三方依赖 |
| 接入点 | `v4/agent/tools/ecom/store.py` → `load_goods()` | 按后端开关委托到 SJ API，SJ 未启动时自动回退 JSON |
| AI 工具 | `v4/agent/tools/ecom/search_goods.py` / `get_goods_detail.py` | 商品搜索推荐 / 详情查询（免登录公共能力） |

切换数据源（环境变量，改配置不改代码）：

```bash
DATA_BACKEND=merchant_api   # 默认：实时走本服务（推荐）
DATA_BACKEND=json           # 回退纯本地 JSON（本服务未启动时自动降级）
DATA_BACKEND=mysql          # 走 V4 自有 MySQL
MERCHANT_API_BASE_URL=http://localhost:8090   # 可选，默认即此值
```

代码级调用示例（与 V4 内部用法一致）：

```python
from merchant_api_client import MerchantApiClient, MerchantApiError

client = MerchantApiClient()          # 自动读环境变量并登录
products = client.get_products(keyword="跑鞋")["list"]
order = client.get_order("SO202609070001")
refund = client.create_refund(order["order_id"], "C001", "尺码不合适")
print(refund["risk_decision"]["rule"])   # 例如 R8
```

客户端特性：自动登录与 token 缓存、401 自动重登、网络错误重试、
统一抛出 `MerchantApiError`（含 `error_code`），零第三方依赖（仅标准库 urllib）。

## 九、如何切换 JSON / MySQL / Mock API

V4 侧数据源由其自身配置决定，本服务不侵入 V4 代码：

| 数据源 | V4 侧做法 | 适用场景 |
|---|---|---|
| JSON（现状） | 不设置 `MERCHANT_API_BASE_URL`，继续读 `v4/data/*.json` | 纯本地最小演示 |
| **Mock API（本服务）** | 设置 `MERCHANT_API_BASE_URL=http://localhost:8090`，经 `merchant_api_client.py` 调用 | 完整业务流程演示（推荐） |
| MySQL | V4 自行配置数据库连接（本服务不受影响） | 联调/集成测试 |

切换只改配置文件，不改代码；取消环境变量即回退 JSON 数据源。

## 十、如何模拟超时 / 库存不足 / 退款风控

先开启总开关，再按需打开具体故障（运行时动态生效，或通过商家后台「故障模拟」页操作；
`PUT /admin/faults` 需要 **merchant_admin** 管理员 token）：

```bash
# 查看当前配置
curl http://localhost:8090/mock-api/v1/admin/faults -H "Authorization: Bearer mtk_xxx"

# 1. 模拟延迟 3 秒（V4 可演练超时重试）
curl -X PUT http://localhost:8090/mock-api/v1/admin/faults \
  -H "Authorization: Bearer mtk_xxx" -H "Content-Type: application/json" \
  -d '{"enabled": true, "latency_ms": 3000}'

# 2. 接下来 5 次请求强制返回 401（演练 token 失效重连）
curl -X PUT http://localhost:8090/mock-api/v1/admin/faults \
  -H "Authorization: Bearer mtk_xxx" -H "Content-Type: application/json" \
  -d '{"enabled": true, "force_status": 401, "force_next": 5}'

# 3. 随机 50% 请求返回 500
curl -X PUT http://localhost:8090/mock-api/v1/admin/faults \
  -H "Authorization: Bearer mtk_xxx" -H "Content-Type: application/json" \
  -d '{"enabled": true, "error_rate": 0.5}'

# 4. 服务不可用（503）
curl -X PUT http://localhost:8090/mock-api/v1/admin/faults \
  -H "Authorization: Bearer mtk_xxx" -H "Content-Type: application/json" \
  -d '{"enabled": true, "service_unavailable": true}'

# 一键恢复（全部关闭）
curl -X POST http://localhost:8090/mock-api/v1/admin/faults/reset \
  -H "Authorization: Bearer mtk_xxx"
```

业务层故障开关：`inventory_shortage`（下单必报库存不足）、`refund_risk_block`
（退款一律风控拦截）、`logistics_query_fail`（物流查询失败）、`duplicate_request`（重复请求）。

**无需故障开关的天然演示场景：**
- 库存不足：直接下单 SKU `RUN-003`（竞速碳板跑鞋，库存仅 3 件）购买 5 件即触发；
- 退款风控（规则短路命中，顺序 R0→R8）：
  - 顾客 `lei`（C002，高退款率）发起退款 → R1（风险分>70）或 R7（退款率>0.3）**转人工**；
  - 签收超 7 天 + 无理由 → R4 **自动拒绝**（售后期外）；
  - 质量问题（破损/开胶/掉色等）→ R3 转人工（需核验照片证据）；
  - 大额（>50 元）无理由 → R5 转人工；
  - 小额（≤50 元，如 `SOCK-001` ¥19.9）+ 7 天内 + 无理由 + 账号正常 → R8 **自动通过**。

## 十一、常见错误排查

| 现象 | 原因与处理 |
|---|---|
| `ModuleNotFoundError: fastapi` | 未用本项目虚拟环境。用 `sj/.venv/Scripts/python main.py` 启动，或先 `pip install -r requirements.txt` |
| 端口被占用 | `python main.py --port 8091` 换端口，V4 侧同步改 `MERCHANT_API_BASE_URL` |
| `UNAUTHORIZED` / token 失效 | token 有效期默认 24h，重新登录或调 `/auth/refresh`；测试脚本会自动重登 |
| `FORBIDDEN` 无权限 | 角色权限不足（如顾客调看板接口）。换对应员工账号，见角色权限表 |
| 数据脏了想重置 | 停服后删除 `data/merchant_mock.db` 重启，或 `python main.py --reseed` |
| 演示订单"过期" | 种子数据日期相对启动日生成，重启（或 --reseed）即刷新到最新日期 |
| 故障模拟一直生效 | 调 `POST /admin/faults/reset` 一键关闭；登录/故障/健康接口在白名单内不受影响 |
| V4 调不通 | 确认本服务已启动（`curl http://localhost:8090/health`）、
  `MERCHANT_API_BASE_URL` 无多余路径后缀（客户端会自动去掉 `/mock-api/v1`） |

---

## 测试与验收

```bash
# 集成测试（55 个用例，独立临时库，不污染演示数据）
sj/.venv/Scripts/python -m pytest tests/ -v

# 冒烟测试（对运行中的服务跑全流程）
sj/.venv/Scripts/python smoke_test.py
```

验收结论（2026-09-08）：
- 无需真实商家凭证、无需外网即可完整运行；
- 商家后台可管理商品/库存/订单/物流/售后/退款/工单（浏览器实测通过）；
- 退款风控 R0-R8、订单状态机、7 天售后时效、归属校验、脱敏、故障模拟全部生效；
- 55 个 pytest 用例全部通过；商家后台无 JS 报错，仅供商家员工登录（顾客账号已拦截）。
