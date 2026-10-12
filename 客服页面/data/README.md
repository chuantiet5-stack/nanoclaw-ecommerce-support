# 客服本地数据

各个 `*.example.json` 定义本地数据文件的结构。使用时创建同名 `*.json` 文件，并填入自己的测试数据；真实数据文件由 `.gitignore` 排除。

| 文件 | 用途 |
| --- | --- |
| `users.example.json` | 本地账号与默认演示用户 |
| `goods.example.json` | 本地商品回退数据 |
| `orders.example.json` | 订单 |
| `logistics.example.json` | 物流轨迹 |
| `tickets.example.json` | 售后工单 |
| `refund_records.example.json` | 退款记录 |
| `refund_audit_log.example.json` | 退款审计记录 |
| `channel_bindings.example.json` | QQ 与飞书身份绑定 |

模拟商家使用独立的 SQLite 数据库，首次启动会生成模拟数据。它不会自动创建客服本地账号或填充上述 JSON。

初始化命令与账号字段见[配置指南](../../docs/configuration.md#初始化客服数据)。
