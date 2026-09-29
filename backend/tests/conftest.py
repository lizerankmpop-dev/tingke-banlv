"""pytest 公共配置

必须在导入 database.manager 之前设置 TINGKEBANLV_DATA_DIR，
否则 DB_PATH 会指向真实用户目录（~/.tingkebanlv/data），污染本机数据。
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["TINGKEBANLV_DATA_DIR"] = tempfile.mkdtemp(prefix="tingke-banlv-test-")

# 让测试可以直接导入 backend 目录下的模块（api/ asr/ database/ llm/ ...）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
