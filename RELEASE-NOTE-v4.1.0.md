# RELEASE-NOTE v4.1.0（2026-11-15 深夜）

> 分支 `release/v4.0.0`（版本号 4.1.0，分支名不改）。
> 本版 = v4.0.0 + 李梓萌视觉 CSS + 队长三处后端修复 + **张春阳 12 条反馈前端功能**。

## 一、合入内容

### 1. 张春阳 `feat/online-course-improvements`（`44f0ea4`，2 提交，16 文件 +729/−136，零后端改动）

| 12 条反馈 | 实现 | 证据 |
|---|---|---|
| 8 记忆回放 | `RecognitionPage` 接 `hydrateSubtitles`，读 `/subtitles?limit=200` + `/sessions` | 打包后 `dist` 含 `hydrateSubtitles` |
| 9 结束并总结 | 识别页「结束并总结」按钮 + 汇总面板 | `dist` 含「结束并总结」 |
| 11 导出 | 新增 `shell/components/SubtitleExportPanel.tsx`（142 行），Blob 落盘 | `dist` 含 `createObjectURL` |
| 12 悬浮设置 | `OverlayControlPanel` / `OverlayWindow` / `Sidebar` / `Shell` 接线进主路由 | 独立 chunk `OverlayControlPanel-*.js` |
| 5 网页恢复 | `main.cjs` 新增 `course:get/set-web-link` IPC + `isMainCourseFrame` | 主进程 diff 49 行 |
| 10 速度档 | 前端 `latencyProfile` 状态（**后端 VAD 联动未做**） | `dist` 含 `latencyProfile` |

`useSubtitleStore.ts` 重构（147 行）、`mapping.ts` +27、`recognition-web.css` 新增。

### 2. 队长（`1f17883`，11/15 晚）

- 三处后端缺陷修复：`SUBTITLE_MAX_ROWS=5000` 统一 `Query` / `/sessions` / `/export-srt`；
  `backup.json` 补 `Content-Disposition`。真实课 715 条 HTTP 复验 16 项 PASS 15 / WARN 1 / FAIL 0。
- 李梓萌视觉 CSS：线上墨靛 `#5F6F96` / 线下黛青 `#5F8F84`、8 档 `--fs-*` 字号 token、
  `html[data-contrast="high"]`、`.recognition-page.web-fill`。

### 3. 版本

`backend/main.py APP_VERSION`、`frontend/package.json`、`package-lock.json`、`Shell.tsx` fallback
四处同步 **4.1.0**（提交 `362bc6f`）。

## 二、门禁（合并后实测）

| 关卡 | 结果 |
|---|---|
| 后端 `pytest` | **624 passed / 1 skipped** |
| 前端 `npm test` | **264 pass / 0 fail** |
| `tsc --noEmit` | 0 |
| `vite build` | OK（1608 modules，`OverlayControlPanel` 独立 chunk 12.34 kB） |

合并产物双特征共存验证：他的 `hydrateSubtitles` / `结束并总结` / `createObjectURL` /
`latencyProfile` 与新色 `#5f6f96` 同包，旧色 `#8b84e8` 为 0。

## 三、为什么必须重打包

张春阳 23:02 自建的 `tingkebanlv-4.0.0-online-x64-setup.exe`（sha256 前 16
`14839df0457d2a31`，394,733,628 B）经解包实测：

- ✅ 他的前端功能全在；
- ❌ **冻结后端 `limit max = 500`**（openapi 实测），即 11/15 晚的三处修复不在；
- ❌ 前端仍是旧配色（`#8b84e8` / `#2dd4cf`，无 `--fs-*` token）——他基于 `2482dcc` 构建，
  在视觉合并（`1f17883`）之前。

该包**不可作为提交版本**。体积比本地大 82MB 只是 NSIS 压缩率差异
（解压后 748MB vs 746MB，992 vs 976 文件），不是多打了内容。

## 四、产物

构建于提交 `362bc6f`（2026-11-15 24:00，`bash scripts/build-v400.sh`）：

| 文件 | 大小（B） | sha256 前 16 |
|---|---:|---|
| `frontend/release/tingkebanlv-4.1.0-setup.exe` | 312,163,576 | `fd914e98ea9671dd` |
| `frontend/release/win-unpacked-4.1.0.zip` | 373,450,259 | `45337ec109017f5e` |

包内验收（2026-11-15 24:00 实测，冻结后端）：`/api/ping` → `4.1.0`；
openapi `/subtitles` limit max = **5000**；`backup.json` → 200 +
`Content-Disposition: attachment`；前端 `hydrateSubtitles` / `结束并总结` /
`createObjectURL` / 新色 `#5f6f96` 均在包内，旧色 `#8b84e8` 为 0。
操作指引见《本地跑通操作卡_免安装成品版_v4.1.0_2026-11-15.md》。

## 五、明确没做 / 下一步

- 后端 `latencyProfile` 联动（VAD 按档收紧）—— 第 10 条后端半边，按执行卡推进；
- 他的记忆回放目前取 `limit=200`（保守），后端已支持 5000，是否拉全量由他决定；
- 真实 LLM 链路、06/07 证据、`ppt/project/video/readme` 四个文件夹、F13 加噪 A/B、
  1 小时 N=3 WER —— 均未收口，口径同 v4.0.0。

## 六、提交链

```
362bc6f release: 版本号升至 4.1.0
2c1cf48 merge: 合入张春阳线上课程 12 条前端功能
d471a58 docs: 三份个人回执
1f17883 merge: 合入李梓萌视觉 CSS 与朱鸿康 codearts_proof(2)，修真实课三处后端缺陷
5b215d7 test: 真实线下课端到端实测与课程内容资产
```
