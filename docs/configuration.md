# 配置指南

## 模型与交互渠道

从 `客服页面/config.example.json` 创建 `config.json`。

| 字段 | 说明 |
| --- | --- |
| `api_key` | 模型 API 密钥；留空时运行 OfflineAgent |
| `base_url` | OpenAI 兼容模型接口地址 |
| `models.main / subagent / cheap` | 按任务选择的模型 |
| `web.enabled / host / port` | Web 渠道设置，示例端口为 `8080` |
| `human_transfer_timeout` | 转人工等待超时，单位为秒 |
| `feishu.app_id / app_secret` | 飞书应用凭证 |
| `qq.app_id / app_secret` | QQ 应用凭证 |

`NANOCLAW_API_KEY` 环境变量可覆盖配置文件中的模型密钥。未使用的 IM 凭证保持为空。

## 初始化客服数据

在 `客服页面/` 目录执行以下跨平台命令，创建缺失的数据文件，不覆盖已有数据：

```bash
python -c "from pathlib import Path; import shutil; [(shutil.copyfile(p, p.with_name(p.name.replace('.example.json', '.json')))) for p in Path('data').glob('*.example.json') if not p.with_name(p.name.replace('.example.json', '.json')).exists()]"
```

`users.json` 管理客服端账号。可在本地填写以下测试数据，并自行设置密码：

```json
{
  "users": [
    {
      "username": "demo",
      "password": "设置本地测试密码",
      "customer_id": "C001",
      "name": "演示用户",
      "role": "user"
    }
  ],
  "default_demo_user": "demo"
}
```

默认演示用户用于 CLI 与未绑定的 IM 渠道。Web 登录读取本地账号；兼容明文测试密码，并在成功认证后升级为 bcrypt 哈希。`goods`、`orders`、`tickets` 等示例仅提供空结构，可自行补充测试数据。

## 数据后端与服务

- 商品工具默认使用端口 `8090` 的模拟商家 API，商家服务首次启动会生成独立演示数据。
- 客服本地账号及订单数据与模拟商家数据库独立，启动商家服务不会自动填充客服端 JSON。
- `DATA_BACKEND=json` 使用本地 JSON 数据；`DATA_BACKEND=mysql` 使用 MySQL 适配及迁移代码。
- 模拟商家配置支持 `HOST`、`PORT`、`MERCHANT_MOCK_DB` 等环境变量，见 `模拟商家页面/config.py`。

## 环境变量

`客服页面/.env.example` 列出 JWT、数据库、Redis、模型限流等配置项。模板供参考；启动前需将使用的变量设置到进程环境，不能假定复制为 `.env` 就会自动加载全部配置。

例如在 PowerShell 中设置 `$env:JWT_SECRET`、`$env:DB_URL`；Linux/macOS 使用 `export`。Web 与模型的主要设置以 `config.json` 及代码明确支持的环境覆盖为准。

本地配置、账号、订单、渠道绑定、聊天记录和数据库保持在本地。
