"""
API 模块入口
============

对外暴露：
- app: FastAPI 应用实例
- create_app: 工厂函数
- router: 路由
- 各种 schemas（请求/响应模型）
"""
from .app import app, create_app
from .schemas import (
    FeedbackRequest,
    RemoveRequest,
    BatchGetRequest,
    ProxyItemResponse,
    CommonResponse,
)
from .routes import router

__all__ = [
    "app",
    "create_app",
    "router",
    "FeedbackRequest",
    "RemoveRequest",
    "BatchGetRequest",
    "ProxyItemResponse",
    "CommonResponse",
]
