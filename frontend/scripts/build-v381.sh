#!/usr/bin/env bash
# v3.8.1 打包脚本（一次性）
# 为什么单独写脚本：npm run package:win 依赖 PATH 里的 python 指向
# 安装空间/venv（系统 python 缺 PyInstaller 依赖），且 PyInstaller 清空
# dist 时会触发沙箱批量删除保护 —— 旧产物已由调用方 mv 到 .pre380bak。
set -euo pipefail

ROOT="C:/Users/Administrator/Desktop/听课伴侣/项目源代码"
VENV="C:/Users/Administrator/Desktop/听课伴侣/安装空间/venv/Scripts"
cd "$ROOT/frontend"

# ⚠️ 不要靠 export PATH 找 python：Git Bash 下 Windows 风格路径在 PATH 里的
# 解析优先级不稳，实际会命中 WorkBuddy 自带的 python（无 PyInstaller 模块）。
# 一律用绝对路径调用 venv 解释器。
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
ls -la release/ | head -20
du -sh release/win-unpacked
