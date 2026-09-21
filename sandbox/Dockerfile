# ===== 沙箱执行器镜像（单镜像双角色：gate / runner）=====
# - gate：compose 默认 CMD，接入 compose 网络，仅内网可达
# - runner：compose 以 command 覆盖 + network none + read_only + cap_drop ALL
FROM python:3.12-slim

# 预置运行时：脚本只用标准库，apt 版 bash/nodejs 即可
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir "fastapi>=0.115.0" "uvicorn>=0.30.0"

# /ipc 预创建并赋属主：named volume 首次挂载会按镜像目录内容初始化权限，
# 使 runner 以非 root（uid 1000）创建 socket 成为可能
RUN mkdir -p /ipc && chown 1000:1000 /ipc \
    && useradd -u 1000 -m sandbox

COPY src /app/src
ENV PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8100
USER 1000
# 默认启动 gate；runner 由 compose command 覆盖
CMD ["python", "-m", "sandbox.gate"]
