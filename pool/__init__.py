"""
代理池模块入口
==============

对外暴露 ProxyPool 类和全局单例 pool
"""
from .core import pool, ProxyPool

__all__ = ["pool", "ProxyPool"]
