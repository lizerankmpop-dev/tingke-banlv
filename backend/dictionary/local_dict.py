"""
本地英汉词典服务
集成 ECDICT（开源英汉词典），提供离线单词查询
当 AI 尚未提取术语时，作为基础词典兜底
"""

import logging
import sqlite3
import os
import json
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ECDICT 数据库路径：优先环境变量，默认 ~/.tingkebanlv/ecdict
def _get_ecdict_dir() -> Path:
    if env_dir := os.environ.get("ECDICT_DIR"):
        return Path(env_dir)
    if home := os.environ.get("TINGKEBANLV_HOME"):
        return Path(home) / "ecdict"
    return Path.home() / ".tingkebanlv" / "ecdict"

ECDICT_DIR = _get_ecdict_dir()
ECDICT_DB = ECDICT_DIR / "ecdict.db"

# ECDICT 下载地址
ECDICT_DOWNLOAD_URL = "https://github.com/skywind3000/ECDICT/raw/master/ecdict.db"

# 内置基础词表路径（随源码分发）
BASIC_VOCAB_PATH = Path(__file__).parent / "basic_vocab.json"


class LocalDictionary:
    """
    本地英汉词典
    基于 ECDICT 离线数据
    """

    def __init__(self):
        self._conn: Optional[sqlite3.Connection] = None
        self._basic_vocab: Optional[dict] = None
        self._ensure_dict()

    def _ensure_dict(self):
        """确保词典数据库存在"""
        if ECDICT_DB.exists():
            return

        ECDICT_DIR.mkdir(parents=True, exist_ok=True)

        # 尝试下载 ECDICT
        logger.info("ECDICT 词典未找到，正在下载...")
        try:
            import requests
            logger.info(f"从 {ECDICT_DOWNLOAD_URL} 下载...")
            resp = requests.get(ECDICT_DOWNLOAD_URL, stream=True, timeout=300)
            total = int(resp.headers.get('content-length', 0))

            with open(ECDICT_DB, 'wb') as f:
                downloaded = 0
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        progress = downloaded / total * 100
                        if int(progress) % 20 == 0:
                            logger.info(f"下载进度: {progress:.0f}%")

            logger.info("ECDICT 词典下载完成")
        except Exception as e:
            logger.warning(f"ECDICT 下载失败: {e}")
            logger.warning("将使用基础词表作为兜底")

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接（懒加载）"""
        if self._conn is None and ECDICT_DB.exists():
            self._conn = sqlite3.connect(str(ECDICT_DB))
        return self._conn

    def lookup(self, word: str) -> Optional[dict]:
        """
        查询单词

        Returns:
            {word, translation, definition} 或 None
        """
        word = word.lower().strip()

        # 先尝试 ECDICT
        result = self._lookup_ecdict(word)
        if result:
            return result

        # 降级：基础词表
        return self._lookup_basic(word)

    def _lookup_ecdict(self, word: str) -> Optional[dict]:
        """从 ECDICT 数据库查询"""
        conn = self._get_conn()
        if conn is None:
            return None

        try:
            cursor = conn.execute(
                "SELECT word, translation, definition FROM stardict WHERE word = ?",
                (word,),
            )
            row = cursor.fetchone()
            if row:
                translation = row[1] or ""
                definition = row[2] or ""

                # 清理 translation（ECDICT 的翻译可能很长）
                if len(translation) > 200:
                    translation = translation[:200]

                return {
                    "word": row[0],
                    "translation": translation,
                    "definition": definition[:500] if definition else translation,
                }
        except Exception as e:
            logger.debug(f"ECDICT 查询失败 ({word}): {e}")

        return None

    def _lookup_basic(self, word: str) -> Optional[dict]:
        """基础词表查询（ECDICT 不可用时的降级方案）"""
        # 懒加载内置词表
        if self._basic_vocab is None:
            try:
                with open(BASIC_VOCAB_PATH, "r", encoding="utf-8") as f:
                    self._basic_vocab = json.load(f)
                logger.info(f"已加载内置词表: {len(self._basic_vocab)} 词")
            except Exception as e:
                logger.warning(f"加载内置词表失败: {e}")
                self._basic_vocab = {}

        entry = self._basic_vocab.get(word)
        if entry:
            return {
                "word": word,
                "translation": entry.get("translation", ""),
                "definition": entry.get("definition", ""),
            }

        # 尝试短语匹配（如 "gradient descent"）
        for key, val in self._basic_vocab.items():
            if " " in key and key == word:
                return {
                    "word": key,
                    "translation": val.get("translation", ""),
                    "definition": val.get("definition", ""),
                }

        return None

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


# 全局单例
_local_dict: Optional[LocalDictionary] = None


def get_local_dict() -> LocalDictionary:
    global _local_dict
    if _local_dict is None:
        _local_dict = LocalDictionary()
    return _local_dict
