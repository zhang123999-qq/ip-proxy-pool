"""
FastAPI 应用初始化
==================

本文件负责：
1. 创建 FastAPI 应用实例
2. 注册路由
3. 注册中间件（CORS / 访问日志）
4. 注册生命周期（lifespan：启动/关闭钩子）

启动方式（main.py 里）：
    uvicorn.run("api:app", host="0.0.0.0", port=8000)

阅读顺序：
1. lifespan  →  启动时/关闭时执行什么
2. create_app → 工厂函数
3. 中间件注册 → 顺序很重要（外层先注册）
4. app = create_app() → 真正创建实例
"""
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from config import API_TITLE, API_VERSION
from .routes import router
from scheduler import scheduler_service
from pool import pool
from utils import logger


# ============================================================
# 生命周期管理
# ============================================================
# @asynccontextmanager 是 Python 的异步上下文管理器
# yield 之前的代码在「启动」时跑，yield 之后在「关闭」时跑
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期

    启动流程：
    1. 加载持久化数据
    2. 启动定时调度器

    关闭流程：
    1. 保存当前数据
    2. 关闭调度器
    """
    # ---- 启动 ----
    logger.info("=== 应用启动 ===")
    await pool.load()           # 从 SQLite 加载历史数据（含 JSON 自动迁移）
    scheduler_service.start()   # 启动爬虫/验证/持久化三个周期任务

    yield  # 应用运行中

    # ---- 关闭 ----
    logger.info("=== 应用关闭 ===")
    try:
        await pool.flush()      # 全量刷盘 + WAL checkpoint
    except Exception as e:
        logger.error(f"关闭时持久化失败: {e}")
    scheduler_service.shutdown()


# ============================================================
# 应用工厂
# ============================================================
def create_app() -> FastAPI:
    """
    工厂函数：创建并配置 FastAPI 应用

    中间件注册顺序：最后注册的离业务代码最近
    - CORS（最外层）
    - 访问日志
    - 业务路由
    """
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        lifespan=lifespan,
    )

    # ----- CORS 跨域中间件 -----
    # 默认全开（*），生产环境应改为具体域名
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ----- 访问日志中间件 -----
    @app.middleware("http")
    async def access_log(request: Request, call_next):
        """
        每次 HTTP 请求都会经过这里
        记录：方法 + 路径 + 状态码 + 耗时
        """
        start = time.time()
        response = await call_next(request)
        elapsed = round((time.time() - start) * 1000, 1)
        logger.info(
            f"[{request.method}] {request.url.path} "
            f"-> {response.status_code} ({elapsed}ms)"
        )
        # 加个自定义响应头方便客户端排查
        response.headers["X-Process-Time-Ms"] = str(elapsed)
        return response

    # ----- 注册路由 -----
    app.include_router(router)

    return app


# ============================================================
# 模块加载时就创建 app（uvicorn 通过 "api:app" 引用）
# ============================================================
app = create_app()
