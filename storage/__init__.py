"""
存储层入口
==========

对外暴露：
- db:  SQLite 连接管理器（单例）
- dao: ProxyDAO 数据访问对象（单例）
"""
from .db import db, Database
from .dao import dao, ProxyDAO

__all__ = ["db", "Database", "dao", "ProxyDAO"]
