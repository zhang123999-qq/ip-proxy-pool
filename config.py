"""
全局配置模块
所有可调参数统一在此管理
优先级：环境变量 > .env 文件 > 默认值
"""
import os
from pathlib import Path
from typing import Set

# ==================== 路径配置 ====================
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"

DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

PROXY_POOL_FILE = DATA_DIR / "proxy_pool.json"

# ==================== .env 加载 ====================
def _load_env():
    """简易 .env 加载（不依赖 dotenv）"""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            os.environ.setdefault(k, v)
    except Exception:
        pass

_load_env()


def _getenv_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _getenv_str(key: str, default: str) -> str:
    return os.getenv(key, default)


def _getenv_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, default))
    except (TypeError, ValueError):
        return default

# ==================== 代理池配置 ====================
INITIAL_SCORE = _getenv_int("INITIAL_SCORE", 10)
SCORE_UP = _getenv_int("SCORE_UP", 1)
SCORE_DOWN = _getenv_int("SCORE_DOWN", 3)
MAX_SCORE = _getenv_int("MAX_SCORE", 20)
MIN_SCORE = 0  # 分数下限（<=即删除）

# ==================== 验证参数 ====================
VALIDATE_TIMEOUT = _getenv_int("VALIDATE_TIMEOUT", 10)
VALIDATE_CONCURRENCY = _getenv_int("VALIDATE_CONCURRENCY", 50)
VALIDATE_URL = _getenv_str("VALIDATE_URL", "https://httpbin.org/ip")

# ==================== 采集参数 ====================
CRAWL_TIMEOUT = _getenv_int("CRAWL_TIMEOUT", 15)
CRAWL_CONCURRENCY = _getenv_int("CRAWL_CONCURRENCY", 10)

# ==================== 反爬参数 ====================
# 单源两次请求之间的最小/最大间隔（秒），随机化更难被识别
CRAWL_MIN_DELAY = _getenv_float("CRAWL_MIN_DELAY", 1.0)
CRAWL_MAX_DELAY = _getenv_float("CRAWL_MAX_DELAY", 3.0)
# 全局限速：每秒最多发起的请求数（令牌桶）
CRAWL_RATE_PER_SEC = _getenv_float("CRAWL_RATE_PER_SEC", 2.0)
# 单源连续失败 N 次后熔断
CRAWL_MAX_FAIL = _getenv_int("CRAWL_MAX_FAIL", 3)
# 失败退避（秒）
CRAWL_BACKOFF_BASE = _getenv_float("CRAWL_BACKOFF_BASE", 5.0)
# 是否使用 Accept-Language / Referer 等更真实头
CRAWL_FAKE_BROWSER = _getenv_str("CRAWL_FAKE_BROWSER", "1") == "1"

# ==================== 代理爬源参数（核心创新）====================
# 模式：
#   auto   = 根据池状态自动选 DIRECT/PROXY/HYBRID（推荐）
#   direct = 强制直连
#   proxy  = 强制用代理爬（池空会失败）
#   hybrid = 混合（部分直连 + 部分代理）
CRAWL_MODE = _getenv_str("CRAWL_MODE", "auto")
# 用来爬源的代理最低分门槛（避免拿刚入池的代理）
PROXY_MIN_SCORE_FOR_CRAWL = _getenv_int("PROXY_MIN_SCORE_FOR_CRAWL", 10)
# 池里至少有几个代理才走 PROXY 模式
PROXY_MIN_COUNT_FOR_CRAWL = _getenv_int("PROXY_MIN_COUNT_FOR_CRAWL", 5)
# 同一代理爬源的冷却时间（秒）
PROXY_USE_COOLDOWN_SEC = _getenv_int("PROXY_USE_COOLDOWN_SEC", 300)
# 单源最多用几个代理（避免代理污染）
SAME_PROXY_PER_SOURCE_MAX = _getenv_int("SAME_PROXY_PER_SOURCE_MAX", 2)
# 爬源失败软扣分（区别于业务失败 -3）
SCORE_DOWN_CRAWL_FAIL = _getenv_int("SCORE_DOWN_CRAWL_FAIL", 1)
# 爬源连续失败 N 次后整个跳到 SKIP 模式
CRAWL_TOTAL_FAIL_THRESHOLD = _getenv_int("CRAWL_TOTAL_FAIL_THRESHOLD", 5)

# ==================== 调度配置 ====================
JOB_CRAWL_INTERVAL_MIN = _getenv_int("JOB_CRAWL_INTERVAL_MIN", 10)
JOB_VALIDATE_INTERVAL_MIN = _getenv_int("JOB_VALIDATE_INTERVAL_MIN", 5)
JOB_PERSIST_INTERVAL_HOUR = _getenv_int("JOB_PERSIST_INTERVAL_HOUR", 1)

# ==================== API 配置 ====================
API_HOST = _getenv_str("API_HOST", "0.0.0.0")
API_PORT = _getenv_int("API_PORT", 8000)
API_TITLE = "IP Proxy Pool"
API_VERSION = "1.0.0"
# 管理接口（/admin/*）的 Bearer Token（空字符串 = 关闭鉴权）
ADMIN_TOKEN = _getenv_str("ADMIN_TOKEN", "")

# ==================== 日志 ====================
LOG_LEVEL = _getenv_str("LOG_LEVEL", "INFO")
# 单文件最多保留多少行（达到即切新文件）
LOG_MAX_LINES = _getenv_int("LOG_MAX_LINES", 100000)
# 兜底保留天数（超过这个时间的老日志一定删）
LOG_RETENTION_DAYS = _getenv_int("LOG_RETENTION_DAYS", 10)

# ==================== 代理源配置 ====================
# UA 池：包含主流桌面/移动浏览器，越像真人越不容易被识别为爬虫
USER_AGENTS: Set[str] = {
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
}

# 通用 Referer 池：很多站点会校验来源页
REFERERS: Set[str] = {
    "https://www.google.com/",
    "https://www.bing.com/",
    "https://duckduckgo.com/",
    "https://www.baidu.com/",
    "https://www.sogou.com/",
}

# 常用 Accept-Language 头
ACCEPT_LANGUAGES: Set[str] = {
    "zh-CN,zh;q=0.9,en;q=0.8",
    "en-US,en;q=0.9,zh-CN;q=0.8",
    "zh-CN,zh-Hans;q=0.9,en-US;q=0.8",
    "ja,en-US;q=0.9,en;q=0.8",
}

# ==================== SQLite 存储配置 ====================
# 数据库文件路径（单文件，跟 JSON 一样零部署成本）
SQLITE_FILE = DATA_DIR / "proxy_pool.db"
# WAL 模式：读写并行，性能更高
SQLITE_WAL_MODE = _getenv_str("SQLITE_WAL_MODE", "1") == "1"
# 是否在启动时自动迁移旧 JSON 数据到 SQLite
SQLITE_AUTO_MIGRATE = _getenv_str("SQLITE_AUTO_MIGRATE", "1") == "1"
