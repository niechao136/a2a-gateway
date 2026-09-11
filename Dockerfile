# ===== 后端镜像：FastAPI + LangGraph =====
FROM python:3.11-slim AS base

# 系统依赖（psycopg 编译、ca-certificates）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 安装 uv（比 pip 快得多）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 先复制依赖清单与源码
COPY pyproject.toml README.md ./
COPY src/ ./src/

# 数据库迁移脚本（容器内可直接执行 `alembic upgrade head` / `alembic current`）
COPY alembic.ini ./
COPY alembic/ ./alembic/

# 安装项目（包含全部运行时依赖），装完即清理编译工具
RUN uv pip install --system --no-cache . \
    && apt-get purge -y gcc && apt-get autoremove -y

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# 容器健康检查（供 docker compose 的 depends_on: service_healthy 使用）
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

# 直接用 uvicorn 启动（不走 reload）
CMD ["uvicorn", "a2a_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
