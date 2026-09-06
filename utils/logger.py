"""
日志工具模块
============

基于 loguru 提供统一日志输出。
loguru 比标准 logging 简单得多——一行配置就能同时输出到控制台和文件。

使用方法：
    from utils import logger
    logger.info("普通信息")
    logger.warning("警告")
    logger.error("出错了")

设计要点：
- 控制台：彩色，人眼友好（开发时用）
- 文件：按行数轮转（默认 10 万行/文件）+ 时间兜底（默认 10 天）
        防止磁盘被日志撑爆
- 单实例：模块级 logger 变量 + 启动时初始化一次

日志保留策略：
- 按行数轮转：单文件达 LOG_MAX_LINES（默认 10 万行）→ 自动切到 .1 备份
- 时间兜底：超过 LOG_RETENTION_DAYS 天（默认 10 天）的旧日志自动删除
- 编码：UTF-8
- 线程安全：自定义 sink 加 threading.Lock，避免多线程交错
"""
import os
import sys
import threading
import time
from pathlib import Path

# loguru 是第三方日志库，这里直接 import 原始 logger 对象
# _logger 加下划线前缀是为了避免和本模块的 logger 变量名冲突
from loguru import logger as _logger

from config import LOG_DIR, LOG_LEVEL


# ============================================================
# 配置项（可被环境变量覆盖，失败时用默认）
# ============================================================
LOG_MAX_LINES = int(os.getenv("LOG_MAX_LINES", "100000"))   # 单文件最多 10 万行
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "10"))  # 兜底保留天数


# ============================================================
# 自定义 sink：按行数轮转
# ============================================================
class LineCountRotatingSink:
    """
    按行数轮转的文件 sink（loguru 自带只支持按时间/按大小轮转，不支持按行数）

    工作原理（每条日志的写入流程）：
    ┌─ write(message) ───────────────────────────┐
    │ 1. 加锁（防止多线程交错写）                │
    │ 2. 写文件 + flush（立即落盘，避免日志丢失）│
    │ 3. 更新本文件已写行数                     │
    │ 4. 超过 max_lines → 触发轮转              │
    └────────────────────────────────────────────┘

    轮转流程：
    1. 关闭当前 app.log
    2. 把旧 app.log 重命名为 app.log.1（如果有先删 .1）
    3. 开新空 app.log
    4. 清理超过 retention_days 的 .1/.2/... 文件

    为什么不直接用 loguru 自带的 Rotation？
    - 它只支持 bytes（按文件大小），但我们更关心"行数"——长行/短行的日志数量一致
    - 而且行数对运维更直观（"100k 行" vs "50MB"）
    """

    def __init__(
        self,
        log_dir: Path,
        base_name: str = "app.log",
        max_lines: int = LOG_MAX_LINES,
        retention_days: int = LOG_RETENTION_DAYS,
    ):
        # 日志目录（自动创建，parents=True 表示中间层缺啥建啥）
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # 完整路径：例如 logs/app.log
        self.base_path = self.log_dir / base_name
        self.max_lines = max_lines
        self.retention_days = retention_days

        # 内部状态：已写入本文件的行数（每次 rotate 后归零）
        self._line_count = 0

        # 文件句柄（始终指向「当前正在写的那个」文件）
        self._fh = None

        # loguru 的 sink 会被多线程调用（loguru 内部用了 thread pool）
        # 必须加锁，否则会出现「一句日志被另一句从中间切开」的乱码
        self._write_lock = threading.Lock()

        # 启动时立即打开文件（追加模式，老内容保留）
        self._open()

    def _open(self) -> None:
        """
        打开当前日志文件（追加模式）

        估算已有行数：用「文件字节数 / 平均行字节」粗估
        - 优点：O(1)，不读文件
        - 缺点：长行（堆栈）会低估，导致轮转延后——能接受
        """
        if self.base_path.exists():
            size = self.base_path.stat().st_size
            # 平均 150 字节/行（经验值；普通 INFO 日志通常 100-200 字节）
            self._line_count = max(0, int(size / 150))
        else:
            self._line_count = 0
        # buffering=1 = 行缓冲；utf-8 因为我们的代码注释里可能有中文
        self._fh = open(self.base_path, mode="a", encoding="utf-8", buffering=1)

    def _rotate(self) -> None:
        """
        轮转当前文件：
        1. 关闭当前
        2. 删除可能存在的 app.log.1
        3. 把当前 app.log 改名为 app.log.1
        4. 开新 app.log
        5. 清理历史文件（按时间）

        注意：保留 1 个备份（.1），更老的会被 retention 自动清掉。
        """
        # 1. 关当前
        try:
            self._fh.close()
        except Exception:
            pass
        # 2. 删旧 .1（如果存在），腾位置
        old = self.base_path.with_suffix(".log.1")
        if old.exists():
            try:
                old.unlink()
            except Exception:
                pass
        # 3. 把现 app.log 改名为 .1
        if self.base_path.exists():
            try:
                self.base_path.rename(old)
            except Exception:
                pass
        # 4. 开新文件
        self._open()
        # 5. 删过期（按 mtime）
        self._clean_old_files()

    def _clean_old_files(self) -> None:
        """
        删除超过 retention_days 的 app.log.* 备份
        只删「app.log 开头的」（避免误删业务自己的 logs）
        """
        if self.retention_days <= 0:
            # 0 表示永久保留
            return
        # 当前时间 - 保留秒数 = 截止时间戳
        cutoff = time.time() - self.retention_days * 86400
        # 遍历所有 app.log* 文件
        for f in self.log_dir.glob("app.log*"):
            try:
                # mtime 小于 cutoff → 删
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except Exception:
                # 文件可能被外部占用（Windows 常见），跳过
                pass

    def write(self, message: str) -> None:
        """
        loguru sink 入口：每条日志都会进来

        message 已经是格式化的字符串（包括 \n 结尾）
        """
        # 加锁：所有读写都在同一把锁内（包括 _rotate）
        with self._write_lock:
            # 双保险：万一 stop 了又被调（loguru 偶尔会发生）
            if self._fh is None:
                self._open()
            # 写 + flush（immediate flush 防进程崩溃丢日志）
            try:
                self._fh.write(message)
                self._fh.flush()
            except Exception:
                # 写失败也不抛（loguru sink 异常会拖垮程序）
                pass
            # 更新行数（消息中可能有 \n，多行堆栈也算）
            if "\n" in message:
                self._line_count += message.count("\n")
                # 达阈值 → 轮转
                if self._line_count >= self.max_lines:
                    self._rotate()

    def stop(self) -> None:
        """loguru 关闭 sink 时调用，确保文件句柄正确释放"""
        with self._write_lock:
            try:
                if self._fh is not None:
                    self._fh.close()
            except Exception:
                pass
            self._fh = None


# ============================================================
# 初始化日志
# ============================================================
def setup_logger(level: str = "INFO") -> None:
    """
    初始化日志配置（在 main.py 启动时调用一次）

    - 控制台：彩色，方便人看
    - 文件：按行数轮转（默认 10 万行），时间兜底 10 天

    可以重复调用以热调整（loguru 会先 remove 旧 handler 再 add）
    """
    # 移除默认 handler（避免重复输出）
    _logger.remove()

    # ----- 控制台 handler -----
    _logger.add(
        sys.stdout,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "    # 时间绿色
            "<level>{level: <8}</level> | "                   # 级别（占 8 字符左对齐）
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "  # 文件位置青色
            "<level>{message}</level>"                        # 消息按级别着色
        ),
        colorize=True,
    )

    # ----- 文件 handler（按行数轮转 + 时间兜底）-----
    # 实例化自定义 sink（持有文件句柄，加锁自动 rotate）
    sink = LineCountRotatingSink(
        log_dir=LOG_DIR,
        base_name="app.log",
        max_lines=LOG_MAX_LINES,
        retention_days=LOG_RETENTION_DAYS,
    )
    _logger.add(
        sink.write,
        level=level,
        format=(
            # 文件不带颜色（ANSI 色码在文本文件里是乱码）
            "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | "
            "{name}:{function}:{line} - {message}"
        ),
    )
    _logger.info(
        f"日志已初始化: 文件={sink.base_path}, "
        f"max_lines={LOG_MAX_LINES}, retention={LOG_RETENTION_DAYS}天"
    )


# ============================================================
# 模块级 logger 实例
# ============================================================
# 直接 `from utils import logger` 就能用
# module 加载时已经做了 setup_logger(LOG_LEVEL)，所以即使没显式调用，
# 也立即可用（日志会在 console + 文件双写）
logger = _logger
setup_logger(LOG_LEVEL)
