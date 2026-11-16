# RELEASE-NOTE v4.1.1（2026-11-16 中午）

> v4.1.0 之上的一次收尾：把 12 条反馈第 10 条的「速度档」真正接到后端、
> 第 8 条记忆回放改为一次拉全量。提交 `fd2b7fd`，版本四处同步 4.1.1。

## 一、改了什么

### 1. `latencyProfile` 后端联动（第 10 条收尾）

**根因**：张春阳的前端一直在 `/asr/start` body 里发 `latencyProfile`，但后端
`AsrStartRequest` 没这个字段，pydantic **静默丢弃** ⇒ 用户在配置页切「快/均衡」
实际无效（这正是第 10 条「选了没反应」的一部分）。

- `AsrStartRequest` 增加 `latencyProfile: Literal["fast","balanced"] = "balanced"`；
- `start_asr` 对**在线课也合并**该字段（`micDeviceIndex` / `agc` / `vadProfile`
  仍仅线下课生效，其余键不漏进 `course_config`）；
- `vad.py` 新增 `apply_latency_profile()`：fast 档**按比例**收紧
  （静音判定 ×0.75、最长片段 ×0.7），**不写死绝对值**，避免覆盖
  `TINGKEBANLV_VAD_*` env 调参；`pipeline` 构造 VAD 后消费。
- 生效时机：**下次开始识别**（改档后不用重启应用，重新点开始即可）。

### 2. 记忆回放一次拉全量（第 8 条收尾）

`RecognitionPage` 回放请求 `subtitles?limit=200 → 5000`（C6 修复后契约）。
真实课《生成式人工智能（二）》有 715 条，原来只回填最新 200 条，大半节课
历史被静默截掉。本机回环拉 ~1500 行 JSON 毫秒级，比分页简单。

### 3. 测试

新增 3 条守卫（`backend/tests/test_vad_segmentation.py`）：
比例缩放不覆盖 env / balanced 无操作（不能越调越快）/ 请求模型校验（非法档位拒绝）。

## 二、门禁与产物

| 关卡 | 结果 |
|---|---|
| 后端 `pytest` | **627 passed / 1 skipped**（+3） |
| 前端 `npm test` | **264 pass / 0 fail** |
| `tsc` / `vite build` | 0 / OK |

| 文件 | 大小（B） | sha256 前 16 |
|---|---:|---|
| `frontend/release/tingkebanlv-4.1.1-setup.exe` | 312,166,756 | `4d479c89e1c54e7d` |
| `frontend/release/win-unpacked-4.1.1.zip` | 373,454,076 | `c063fa32a6f65c5e` |

**包内验收**（冻结后端实跑，2026-11-16 13:25）：`/api/ping` → `4.1.1`；
openapi `AsrStartRequest.latencyProfile` enum `[fast, balanced]` 在包内；
`/subtitles` limit max = **5000**；前端 `subtitles?limit=5000` / `hydrateSubtitles` /
`结束并总结` / 新色 `#5f6f96` 均在包内，旧色 `#8b84e8` 为 0。

## 三、口径

- 「速度档」现在**真实生效**（VAD 收紧），但出字延迟的主导项仍是最长片段 + 推理；
  fast 档的预期收益 ≈ 片段上限降 30%，属可感知但不是质变，答辩别吹「延迟减半」。
- 张春阳 11/15 晚的 `tingkebanlv-4.0.0-online-x64-setup.exe` 与队长的
  v4.1.0 包**都已被本版取代**；对外只发 v4.1.1 双产物。

## 四、提交链

```
fd2b7fd feat: latencyProfile 后端联动 + 记忆回放一次拉全量
53eb0a7 chore: 移除误提交的包内验收测试库
d1eb468 docs: v4.1.0 操作卡与产物指纹
d264812 docs: RELEASE-NOTE v4.1.0 + HANDOFF §0.7
362bc6f release: 版本号升至 4.1.0
2c1cf48 merge: 合入张春阳线上课程 12 条前端功能
```
