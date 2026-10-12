# NanoClaw 生产镜像（生产落地 P2-3.4 步骤 1）
# 多阶段构建：builder 阶段编译依赖，运行阶段仅携带必要产物

FROM python:3.11-slim AS builder

WORKDIR /app

# 安装系统依赖（MySQL 客户端编译需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc default-libmysqlclient-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖（利用 Docker 层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 运行时阶段
FROM python:3.11-slim

WORKDIR /app

# 复制依赖
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 复制代码
COPY . .

# 非 root 用户运行
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# 健康检查（/health 端点由 channels/web.py 提供）
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

EXPOSE 8080

CMD ["python", "main.py"]
