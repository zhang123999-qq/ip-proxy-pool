# ============================================================
# IP 代理池 — 生产镜像（多阶段构建）
# ============================================================
# 目标镜像：约 180MB（含 Python 3.13 + 全部依赖）
# 构建：docker build -t ip-proxy-pool:latest .
# 运行：docker run -d -p 8000:8000 --name proxy-pool ip-proxy-pool:latest
# ------------------------------------------------------------

# ----- 阶段 1：构建依赖 -----
FROM python:3.13-slim AS builder

# 关键：让 print/log 立即输出到容器日志（不被缓冲）
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# 安装编译工具（lxml 需要 C 编译器，构建完就丢）
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# 先单独装依赖（缓存复用：requirements 不变就跳过这一步）
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

# ----- 阶段 2：运行时镜像 -----
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# 安装运行时需要的系统库
# - curl：健康检查用
# - tzdata：让 TZ=Asia/Shanghai 真正生效（容器日志时间本地化）
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 复制阶段 1 装好的包
COPY --from=builder /install /usr/local

# 创建非 root 用户运行（安全最佳实践）
RUN groupadd -r app && useradd -r -g app -d /app -s /sbin/nologin app \
    && mkdir -p /app/data /app/logs \
    && chown -R app:app /app

WORKDIR /app

# 先复制代码（chown 后用户切换）
COPY --chown=app:app . /app

USER app

# 暴露端口（与 config.py 的 API_PORT 默认值保持一致）
EXPOSE 8000

# 健康检查：每 30s 探一次，连续 3 次失败视为不健康
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# 默认启动命令（可用 docker run 时覆盖）
CMD ["python", "main.py"]