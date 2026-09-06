"""
IP 代理池 - 入口文件
====================

启动 API 服务。

直接运行：
    python main.py

或者用 uvicorn：
    uvicorn api:app --host 0.0.0.0 --port 8000

启动后访问：
    http://127.0.0.1:8000/docs     → Swagger 自动文档
    http://127.0.0.1:8000/health   → 健康检查
"""
import uvicorn

# 从 config 拿配置（这些都可以用环境变量覆盖）
from config import API_HOST, API_PORT, LOG_LEVEL
from utils import setup_logger


def main() -> None:
    """
    启动入口
    1. 初始化日志
    2. 启动 uvicorn
    """
    setup_logger(LOG_LEVEL)
    uvicorn.run(
        "api:app",          # 引用 api/app.py 里的 app 实例
        host=API_HOST,
        port=API_PORT,
        reload=False,        # 生产模式不开热重载
        log_level=LOG_LEVEL.lower(),
    )


# 脚本直接执行时跑 main()
if __name__ == "__main__":
    main()
