# accent（口音）场景素材入口

> **`accent` 场景刻意不合成** —— 合成集（`../scenarios/`）全是**同一个** TTS 音源
> 经信号处理劣化得到的，无口音、无口误、无混响，测不出「口音」这个变量。
> 相关登记：`docs/asr_eval/known_defects.md` **D4**、`docs/m3/plan_v6.md` **V6-4**
> （V6-4 是 V5-7 的执行批次，V5-7 建的是本目录与校验脚本）。
> 素材已于 2026-10-22 就位：`accent_zhlecture_bv117411m78e.wav`
> （BV117411m78E，偏微分方程公开课，00:30~11:00 = 630 s，真人录音）。

---

## 一、需要什么才能跑起来

| 项 | 要求 | 为什么 |
|---|---|---|
| 音频 | 16 kHz / 单声道 / wav(16-bit PCM) | 与 `eval_asr_quality.py` 的整条管道一致；其它采样率会静默重采样，破坏与其它格的同口径 |
| 时长 | 30~700 s | 🔴 **V6-4 从「60~130 s 对齐短样本集」放宽到 30~700 s**。原上界是 V5-7 设想的短素材（对齐 clean/noise 那批），实际素材 630 s —— 恰好等于 `real` 格长度，两格同为真人课堂实录、同时长、同规格 ⇒ CER 可直接并排对照；样本也足够长，口音抖动能与测量噪声区分开。**仍保留上界**以拒收超长音频（`--realtime` 下 3 小时音频要跑 3 小时）。`scripts/run_accent_baseline.py` 的 `MIN_SECONDS`/`MAX_SECONDS` 是唯一真源 |
| 参考稿 | `<同名>.preproof.txt`（**定稿优先**），退化到 `<同名>.txt` | 脚本取参考稿的顺序是 `.preproof.txt` → `.txt`（`resolve_reference()`）。裸 `.txt` 可能只是草稿 |
| 文件名 | 必须能被 `scenario_of()` 认成 `accent` | 否则会被归到 `other` 桶，从汇总表里消失（看起来"跑了但没结果"） |
| BV 信息 | **BV 号 + 起止时间**（和已有的 real 素材一样登记） | 无 BV 号就无法复现素材来源；P2 的 real 格就是这么登记的（`BV1BA411i7PT` 00:40~11:10） |

### 文件名怎么取

`scenario_of(name)` 的匹配规则（`scripts/eval_asr_quality.py:854`）：

```
lowered.startswith(s) or f"_{s}_" in lowered or f"_{s}." in lowered
```

所以下面三种都行，`<stem>` 里带上场景词即可：

```
accent_zhlecture_<bvid>.wav          # 前缀命中
real_accent_zhlecture_<bvid>.wav     # _accent_ 命中
short_accent_zhlecture.wav           # _accent. 命中（场景词紧跟扩展名前）
```

**反面例子**：`zhlecture_accented.wav` —— `accented` 不匹配 `accent` 的三种形态，
会被判成 `other`。

---

## 二、素材怎么来

用仓库里已有的抓取脚本（不要手工剪）：

```bash
# 1) 先确认视频信息与时长（不下载）
<venv>/Scripts/python.exe scripts/fetch_bili_audio.py <BV号> --start 0 --end 90 --dry-run

# 2) 拉到本目录，名字里带 accent
<venv>/Scripts/python.exe scripts/fetch_bili_audio.py <BV号> \
    --start MM:SS --end MM:SS \
    --name accent_zhlecture_<bvid> \
    --out-dir testset/chinese_lecture/accent
```

然后**手工**把参考稿校对成定稿：先跑 `scripts/asr_draft_transcript.py` 出草稿，
再人工裁决（见 `scripts/preproof_transcript.py`），存为
`accent_zhlecture_<bvid>.preproof.txt`。

最后在本目录放一份 `metadata.json`（这段不能省 —— 没有它 `sourceKind` 会落
`unknown`，汇总里会被标成"未识别来源类型"）：

```json
{
  "kind": "REAL",
  "sourceKind": "human-recording",
  "note": "真人录音，含口音。kind=REAL 会被映射成 human-recording（scripts/eval_asr_quality.py:107）。",
  "entries": [
    {
      "scenario": "accent",
      "audio": "accent_zhlecture_<bvid>.wav",
      "reference": "accent_zhlecture_<bvid>.preproof.txt",
      "bvid": "<BV号>",
      "clipRange": "MM:SS~MM:SS"
    }
  ]
}
```

---

## 三、校验 + 跑基线的命令

```bash
# 先校验素材就位（缺什么会直接说，不会白等十分钟模型加载）
<venv>/Scripts/python.exe scripts/run_accent_baseline.py

# 校验通过后真跑（会自动加 --realtime 与 --source-kind human-recording）
<venv>/Scripts/python.exe scripts/run_accent_baseline.py --run
```

跑完的产物落在 `packaging/eval/`（**有意 gitignore**）—— 关键数字必须抄进
`docs/asr_eval/WER_REPORT_PLAN.md` 的 §7 表，仓库内文档才是长期留存的那一份。

---

## 四、为什么必须 `--realtime`

非实时模式下音频被尽可能快地灌进去，`readyWall` 会落到墙钟的未来，算出来一堆负数延迟。
P2 已实测同一个 multi 素材：**非实时 0.9420 vs 实时 0.6834** —— 差 0.26，
把非实时的数当结论会得出完全错误的判断。所以本目录的样本一律 `--realtime`。

⚠️ V6-4 起本目录样本是 **630 s**（不再是短样本），`--realtime` 意味着一次基线约
**11 分钟**（实测草稿跑 155.9 s，其中含模型加载；`--realtime` 全程按实时喂）。
**跑之前先确认没有别的长跑在跑** —— CPU 争抢会污染墙钟延迟，而延迟是本套评估的
观测指标（见 §五：不设阈值，但要对得准）。

---

## 五、验收判据（沿用 P2 的 v4 永久判据）

- 片段数相同、字幕数 ±1、`drained` 一致、丢片数相同、参考稿同源；
- CER **分档容差**；可配对样本的时间戳恒等式 `新−旧 == audioEnd−audioWordsEnd ≥ 0` 严格成立；
- 延迟（收尾 / p95）是**观测指标，不设阈值**；跨会话时序量只允许同会话新旧口径对照。

accent 格的定位是**如实报告**：口音样本的 CER 预期高于 clean，这不是缺陷，
是"这个场景本来就是难的"。它要拦住的是**变化**（回归），不是绝对值。
