"""数据访问层（生产落地 P0-1.1）：MySQL + Redis。

当前为新增模块，尚未替换 agent/tools/ecom/store.py 的 JSON 文件存储
（替换需要真实 MySQL/Redis 服务，属于高风险改造，见 PRODUCTION_READINESS_GUIDE.md 1.1）。
接线步骤：
1. 启动 MySQL/Redis（可用 docker-compose.yml 一键启动）
2. 执行 migrations/001_init.sql 建表
3. 执行 migrations/migrate_json_to_mysql.py 迁移数据
4. 将电商工具的数据读写从 store.py 切换到 repository（接口保持一致）
"""
