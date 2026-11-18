#!/usr/bin/env bash
# v4.1.4 打包脚本：修复窗口枚举：type 字段缺失导致列表为空。
# 与 build-v400.sh 同款四步；后端版本字符串变更，必须重跑 PyInstaller。
set -euo pipefail

ROOT="C:/Users/Administrator/Desktop/听课伴侣/项目源代码"
VENV="C:/Users/Administrator/Desktop/听课伴侣/安装空间/venv/Scripts"
cd "$ROOT/frontend"

PY="$VENV/python.exe"

echo "==================== [1/4] preflight:seed ===================="
node scripts/preflight-seed.cjs

echo "==================== [2/4] 前端构建 (tsc && vite build) ===================="
npm run build

echo "==================== [3/4] PyInstaller 打包后端 ===================="
echo "解释器：$("$PY" -c 'import sys;print(sys.executable)')"
"$PY" -m PyInstaller ../packaging/backend_server.spec \
  --noconfirm \
  --distpath ../packaging/out \
  --workpath ../packaging/build

echo "==================== [4/4] electron-builder --win ===================="
npx electron-builder --win

echo "==================== 打包完成 ===================="
ls -la release/ | grep "4.1.4" || true
du -sh release/win-unpacked
