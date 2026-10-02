#!/usr/bin/env node
/**
 * 打包前置校验：内置模型种子是否就绪
 *
 * 为什么需要这个脚本
 * ------------------
 * 安装包内置 base 模型是本轮的明确目标（校园网计费、学生网络波动，首启必须即可用）。
 * 但种子目录是 gitignore 的 141MB 二进制，由 scripts/prepare_seed_model.py 生成，
 * 新克隆/新副本上没有。
 *
 * 如果带着缺失的种子去跑 electron-builder，结果是「安装包照样产出、体积却少 141MB」——
 * 用户装完首启还是要联网下载，而这个失败**在打包日志里几乎看不出来**。
 * 因此必须在打包前硬性拦下。
 *
 * 校验内容：
 *   1. 种子目录存在且有 manifest.json
 *   2. 清单可解析且 schema 匹配
 *   3. 清单里每个文件的实际大小与记录一致（大小不符 = 拷贝被截断）
 *
 * 退出码非 0 时 electron-builder 不会执行。
 */

const fs = require('fs');
const path = require('path');

const SEED_DIR = path.resolve(__dirname, '..', '..', 'packaging', 'seed', 'whisper_models');
const MANIFEST = path.join(SEED_DIR, 'manifest.json');
const EXPECTED_SCHEMA = 1;

function fail(lines) {
  console.error('\n[preflight] ✗ 内置模型种子不可用，已阻止打包\n');
  for (const line of lines) console.error('  ' + line);
  console.error('\n  修复：python scripts/prepare_seed_model.py --source <模型目录> --size base');
  console.error('  （源目录可以是已下载好的 faster-whisper base 模型，或 HF 缓存目录）\n');
  process.exit(1);
}

if (!fs.existsSync(MANIFEST)) {
  fail([
    `未找到 ${MANIFEST}`,
    '原因：packaging/seed/ 是 gitignore 项，新克隆或 tingke-banlv/ 副本上需要重新生成。',
  ]);
}

let manifest;
try {
  manifest = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'));
} catch (err) {
  fail([`manifest.json 解析失败: ${err.message}`]);
}

if (manifest.schema !== EXPECTED_SCHEMA) {
  fail([`清单 schema=${manifest.schema}，期望 ${EXPECTED_SCHEMA}（清单格式变更，请重新生成）`]);
}

if (!Array.isArray(manifest.files) || manifest.files.length === 0) {
  fail(['清单 files 为空']);
}

const modelDir = `models--${String(manifest.repoId).replace('/', '--')}`;
const snapshotDir = path.join(SEED_DIR, modelDir, 'snapshots', manifest.snapshot || 'seed');

const problems = [];
let totalBytes = 0;

for (const entry of manifest.files) {
  const target = path.join(snapshotDir, entry.name);
  if (!fs.existsSync(target)) {
    problems.push(`缺少 ${entry.name}`);
    continue;
  }
  const actual = fs.statSync(target).size;
  totalBytes += actual;
  if (actual !== entry.size) {
    problems.push(`${entry.name} 大小不符（${actual} != ${entry.size}）`);
  }
}

if (problems.length) {
  fail([`种子文件不完整（${snapshotDir}）`, ...problems]);
}

// blobs/ 不该存在：Windows 无符号链接，保留等于把 141MB 变成 282MB
if (fs.existsSync(path.join(SEED_DIR, modelDir, 'blobs'))) {
  console.warn('[preflight] ⚠ 种子目录里存在 blobs/，会额外占用一份空间（Windows 无符号链接）');
}

const mb = (totalBytes / 1048576).toFixed(1);
console.log(`[preflight] ✓ 内置模型种子就绪：${manifest.size} ${mb}MB，${manifest.files.length} 个文件`);
console.log(`[preflight]   来源 ${SEED_DIR}`);
