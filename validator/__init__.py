"""
代理验证模块入口
================

对外暴露 ProxyValidator 类和全局实例 validator
"""
from .checker import validator, ProxyValidator

__all__ = ["validator", "ProxyValidator"]
