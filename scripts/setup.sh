#!/bin/bash
# 听课伴侣 一键安装脚本
set -e

echo "========================================="
echo "  听课伴侣 环境安装"
echo "========================================="

# 1. 安装 Python 依赖
echo ""
echo "[1/4] 安装 Python 后端依赖..."
cd "$(dirname "$0")/../backend"
pip install -r requirements.txt

# 2. 下载 ECDICT 词典（可选）
echo ""
echo "[2/4] 下载 ECDICT 英汉词典..."
python -c "
from dictionary.local_dict import LocalDictionary
LocalDictionary()
print('ECDICT 词典就绪')
"

# 3. 安装前端依赖
echo ""
echo "[3/4] 安装前端依赖..."
cd "$(dirname "$0")/../frontend"
npm install

# 4. 提示 Ollama
echo ""
echo "[4/4] 检查 Ollama..."
if command -v ollama &> /dev/null; then
    echo "Ollama 已安装"
    echo "推荐拉取模型: ollama pull qwen2.5:7b"
else
    echo "⚠️  Ollama 未安装"
    echo "   请访问 https://ollama.com 安装"
    echo "   然后运行: ollama pull qwen2.5:7b"
fi

echo ""
echo "========================================="
echo "  安装完成！"
echo ""
echo "  启动开发环境:"
echo "    npm run dev"
echo "========================================="
