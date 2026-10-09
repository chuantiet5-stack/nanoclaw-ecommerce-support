# NanoClaw 电商智能客服

基于 NanoClaw Agent 的电商客服项目，包含客服界面与独立的模拟商家服务。支持商品查询、订单及物流查询、售后退款、工单与人工接入等业务工具。

## 功能与技术栈

- 将商品搜索、下单、订单查询、物流和售后处理封装为 Agent 业务工具。
- 提供退款资格与规则判断、工单流转和人工客服接入。
- 模拟商家服务提供独立 API、商家后台及演示数据，便于展示客服与商家联动。
- 支持 Web、飞书、QQ 渠道，以及无需模型密钥的离线演示 Agent。
- 技术栈：Python、NanoClaw Agent、FastAPI、OpenAI 兼容模型接口、SQLite、JWT；包含 MySQL/Redis 接入代码。

## 项目结构

- `客服页面/`：Agent、Web/飞书/QQ 渠道、电商工具与测试代码。
- `模拟商家页面/`：FastAPI 模拟商家 API 与后台，首次启动自动创建数据库并生成演示数据。

## 本地启动

建议为两个子项目分别创建 Python 虚拟环境，在各自目录安装原有 `requirements.txt`。这些依赖文件沿用交付版本。

先启动模拟商家服务：

```powershell
cd 模拟商家页面
python -m pip install -r requirements.txt
python main.py
```

商家后台地址为 `http://127.0.0.1:8090/merchant`，演示账号与接口说明见该目录的 README。

在另一个终端从仓库根目录启动客服：

```powershell
cd 客服页面
python -m pip install -r requirements.txt
Copy-Item config.example.json config.json
Get-ChildItem data/*.example.json | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $_.DirectoryName ($_.Name -replace '\.example\.json$', '.json'))
}
# 编辑本地 data/users.json 配置测试账号；在线模式另需填入 config.json 的 API 密钥。
python main.py
```

客服 Web 默认端口为 `8080`。未配置模型密钥时，代码会使用离线演示 Agent。公开版本的数据示例为空；客服登录需自行建立本地测试账号。默认商品后端连接端口 `8090` 的模拟商家服务。

## 发布说明

公开版本仅保留源码和运行所需资源。原始 API 密钥、机器人凭证、聊天记录、用户及订单数据、数据库、日志、缓存和虚拟环境均未提交；视频、PPT 和原始压缩包不上传。本地配置和运行数据已加入 `.gitignore`。

本次整理验证了发布文件及 Python 语法，未安装完整依赖或执行联网业务测试。
