#!/usr/bin/env bash
# v4.0.0 打包脚本（合并 feat/huawei-asr + feat/ai-term-suggest 后的首个版本）
# 与 build-v390.sh 同款四步；旧产物已由调用方 mv 到 .pre400。
# ⚠️ 后端代码有新增模块（llm.term_generator / asr.backend_factory / asr.huawei_asr），
#    必须重跑 PyInstaller，不能只热替换前端 dist。
set -euo pipefail

ROOT="C:/Users/Administrator/Desktop/听课伴侣/项目源代码"
VENV="C:/Users/Administrator/Desktop/听课伴侣/安装空间/venv/Scripts"
cd "$ROOT/frontend"

# 一律用 venv 解释器的绝对路径（PATH 里的 python 会命中无 PyInstaller 的系统解释器）
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
ls -la release/ | grep "4.0.0" || true
du -sh release/win-unpacked
