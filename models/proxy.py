"""
代理数据模型模块
================

这是整个项目的「数据层」。
ProxyItem 就是一个代理 IP 的完整描述（IP、端口、协议、分数、统计信息）。

为什么要这么设计？
- 用 dataclass 而不是 dict：让字段有类型提示，编辑器能自动补全
- 用枚举 ProxyProtocol：协议只能是 3 个固定值，避免拼写错
- 用线程锁保护分数：后面你会看到分数会被多个协程并发修改

读这段代码的顺序：
1. 看 ProxyProtocol 枚举（3 种协议）
2. 看 ProxyItem 数据类（每个代理的字段）
3. 看方法（如何安全地读写字段）
"""
import threading
from dataclasses import dataclass, field
from enum import Enum


# ============================================================
# 代理协议枚举
# ============================================================
# 枚举（Enum）就是一种"只能取固定几个值"的类型。
# 这里表示代理支持 3 种协议：
#   - HTTP  只支持 http://  访问
#   - HTTPS 只支持 https:// 访问
#   - BOTH  两种都支持
#
# 使用示例：
#   proto = ProxyProtocol.HTTP
#   proto.value  # → "http"
class ProxyProtocol(str, Enum):
    """代理协议枚举"""
    HTTP = "http"
    HTTPS = "https"
    BOTH = "both"


# ============================================================
# 代理条目（核心数据结构）
# ============================================================
# @dataclass 装饰器会自动帮我们写好 __init__ / __repr__ / __eq__ 等魔法方法。
# 我们只需要写字段名和默认值即可。
#
# 字段含义：
#   ip            代理 IP，比如 "1.2.3.4"
#   port          端口，比如 8080
#   protocol      协议（HTTP/HTTPS/BOTH）
#   score         分数，初始 10，最高 20，<=0 被删除
#   response_time 上次验证耗时（秒）
#   success_count 累计成功次数
#   fail_count    累计失败次数
#   last_check    上次检查时间戳
#   created_at    入池时间戳
#
# 为什么加 _lock 字段？
# 因为在异步场景下，「读分」和「改分」可能在不同协程同时发生，
# 不加锁可能出现「读到一半被改」的奇怪 bug。
# 用 threading.RLock（可重入锁）保护所有读写。
@dataclass
class ProxyItem:
    """
    代理条目（一个代理的全部信息）

    字段含义：
    ip            代理 IP，比如 "1.2.3.4"
    port          端口，比如 8080
    protocol      协议（HTTP/HTTPS/BOTH）
    score         分数，初始 10，最高 20，<=0 被删除
    response_time 上次验证耗时（秒）
    success_count 累计成功次数
    fail_count    累计失败次数
    last_check    上次检查时间戳
    created_at    入池时间戳
    source        来源（哪个代理源采的，便于去重）
    last_used_for_crawl  上次用于爬源的时间戳（冷却追踪）
    """
    ip: str
    port: int
    protocol: ProxyProtocol = ProxyProtocol.HTTP
    score: int = 10
    response_time: float = 0.0
    success_count: int = 0
    fail_count: int = 0
    last_check: float = 0.0
    created_at: float = 0.0
    source: str = ""                    # 来源标识，如 "kuaidaili-free"
    last_used_for_crawl: float = 0.0    # 上次用于爬源的时间戳

    # 内部线程锁，不参与 to_dict
    _lock: "threading.RLock" = field(
        default_factory=threading.RLock,
        repr=False,
        compare=False,
    )

    # ----- 计算属性 -----
    @property
    def key(self) -> str:
        """唯一标识：'ip:port' 格式"""
        return f"{self.ip}:{self.port}"

    # ----- 写操作（都加锁） -----
    def with_score(self, new_score: int) -> None:
        """线程安全地更新分数"""
        with self._lock:
            self.score = new_score

    def with_check_result(self, success: bool, response_time: float) -> None:
        """线程安全地记录一次检查结果（不更新分数）"""
        import time as _t
        with self._lock:
            self.response_time = response_time
            self.last_check = _t.time()
            if success:
                self.success_count += 1
            else:
                self.fail_count += 1

    def mark_used_for_crawl(self) -> None:
        """标记刚用于爬源（更新 last_used_for_crawl）"""
        import time as _t
        with self._lock:
            self.last_used_for_crawl = _t.time()

    # ----- 读操作 -----
    def to_dict(self) -> dict:
        """转字典（用于 JSON 兼容迁移 + API 序列化）"""
        with self._lock:
            return {
                "ip": self.ip,
                "port": int(self.port),
                "protocol": self.protocol.value,
                "score": int(self.score),
                "response_time": float(self.response_time),
                "success_count": int(self.success_count),
                "fail_count": int(self.fail_count),
                "last_check": float(self.last_check),
                "created_at": float(self.created_at),
                "source": self.source,
                "last_used_for_crawl": float(self.last_used_for_crawl),
            }

    @classmethod
    def from_dict(cls, data: dict) -> "ProxyItem":
        """从字典构造（持久化文件加载时用）"""
        return cls(
            ip=str(data["ip"]).strip(),
            port=int(data["port"]),
            protocol=ProxyProtocol(data.get("protocol", "http")),
            score=int(data.get("score", 10)),
            response_time=float(data.get("response_time", 0.0)),
            success_count=int(data.get("success_count", 0)),
            fail_count=int(data.get("fail_count", 0)),
            last_check=float(data.get("last_check", 0.0)),
            created_at=float(data.get("created_at", 0.0)),
            source=str(data.get("source", "")),
            last_used_for_crawl=float(data.get("last_used_for_crawl", 0.0)),
        )

    # ----- 静态工具方法 -----
    @staticmethod
    def is_valid_ip(ip: str) -> bool:
        """
        校验 IPv4 格式是否合法
        用 Python 自带的 ipaddress 模块
        """
        import ipaddress
        try:
            ipaddress.IPv4Address(ip)
            return True
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _coerce_protocol(p) -> "ProxyProtocol":
        """把任意值转成 ProxyProtocol 枚举"""
        if isinstance(p, ProxyProtocol):
            return p
        try:
            return ProxyProtocol(str(p).lower().strip())
        except ValueError:
            return ProxyProtocol.HTTP

    def __post_init__(self):
        """dataclass 自动调用：规范化字段"""
        self.protocol = self._coerce_protocol(self.protocol)
