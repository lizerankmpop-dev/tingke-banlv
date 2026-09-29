"""
共享依赖模块
解决循环导入问题，提供全局单例访问
"""

from database.manager import DatabaseManager

# 全局数据库管理器单例
db = DatabaseManager()
