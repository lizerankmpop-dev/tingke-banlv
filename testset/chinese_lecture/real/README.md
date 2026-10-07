# real/ —— 真实中文课程素材（P3 唯一阻塞项的落位目录）

这里的素材**决定了 P3 的最终结论**。`../long/` 下的 `long_zhlecture_cnn.*` 是 TTS 合成代理集
（`metadata.json` 里 `kind: "PROXY"`，无噪声、无口音、无口误），它的 CER 显著偏乐观，
**不能**用来回答「真实网课能不能用」；只有本目录的 `kind: "REAL"` 素材可以。

## 目录约定

```
testset/chinese_lecture/real/
  <name>.wav|mp3|m4a|mp4|aac|ogg   ← 音频（不入库：体积大 + 含他人声音）
  <name>.txt                       ← 逐字稿（UTF-8 无 BOM，一段一行）★核心资产，入库
  <name>.draft.json / .draft.txt   ← ASR 草稿（校对过程中间产物，可不入库）
  metadata.json                    ← 素材清单（kind: "REAL" + sha256 + 时长）★入库
```

> `.gitignore` 已屏蔽 `testset/**/*.{wav,mp3,m4a,flac,mp4,aac,ogg}` ——
> **音频不入库**（版权 + 体积），只入库逐字稿与 metadata。换机器跑评估时按 metadata 的
> `sha256` 核对音频是否一致。

## 音频要求

| 项 | 要求 | 说明 |
|---|---|---|
| 时长 | **≥ 10 分钟**（连续，不拼接） | P3 验收硬指标 |
| 内容 | **真实中文专业课**（教师讲课，非朗读、非播客、非 TTS） | 决定结论可外推 |
| 质量 | 允许真实缺陷：口误、口水话、板书停顿、键盘/翻页声、轻微混响 | **这些正是要测的**，不要后期净化 |
| 术语 | 尽量含中英混说的专业词（如 `convolution`、`f1score`、`adam`） | 直接考验术语热词与防循环 |
| 采样率 | 不限（脚本按 PyAV 解码并重采样到 16k 单声道） | 手机录音（m4a）即可 |

## 逐字稿要求（人工产出）

- UTF-8 **无 BOM**，一段一行（与 `long_zhlecture_cnn.txt` 同格式）
- 记录**实际听到的内容**，包括口误（可加 `（口误）` 标注）、重复词、口头禅
- 英文术语按实际读音记（读作字母则记字母，如 `f one score` → `f1score`；按读音则记 `adam`）
- 宁缺毋滥：听不清的片段标 `[听不清]`，**不要猜**——猜出来的字会污染 CER

## 制作流程（三步，工具已就绪）

```bash
# ① 生成带时间戳的分段草稿（分块喂入，产出 draft.json/txt/srt + review.html）
<venv>/python.exe scripts/asr_draft_transcript.py --audio testset/chinese_lecture/real/<name>.wav

# ② 打开 review.html 逐段校对（空格播放当前段 / Ctrl+←→ 切段 / 自动存浏览器）
#     改完点「导出 txt」，覆盖到 <name>.txt

# ③ 合规校验（字数/语速/未校对标记/重复段/校对痕迹，含 --selftest 探针）
<venv>/python.exe scripts/check_transcript.py --audio testset/chinese_lecture/real/<name>.wav
```

只有 ③ 打印「可用 ✓」的逐字稿才算完成，之后才能跑最终评估：

```bash
<venv>/python.exe scripts/eval_asr_quality.py \
  --audio testset/chinese_lecture/real/<name>.wav \
  --language zh --terms on --custom-terms <本课术语，逗号分隔> \
  --report packaging/eval/real-<name>.json
```

再对同一素材跑一轮 `--terms off` 与 `--autodegrade off`，即可回答 P3 的四个问题：
**真实场景 CER 是多少 / 术语热词是否帮上忙 / 防循环兜底是否必要 / 延迟与稳定性是否达标**。

## metadata.json 模板

```json
{
  "kind": "REAL",
  "entries": [
    {
      "name": "real_zhlecture_<主题>",
      "audio": "real_zhlecture_<主题>.m4a",
      "reference": "real_zhlecture_<主题>.txt",
      "sha256": "<音频 sha256，用于换机核对>",
      "durationSeconds": 720.0,
      "durationMinutes": 12.0,
      "referenceChars": 3200,
      "source": "自录 / 某公开课（写明来源与获取方式）",
      "consent": "仅用于本地评估，不再分发；含他人声音，故音频不入库",
      "recordedAt": "2026-10-XX",
      "notes": "手机录音，教室后排，有翻页声；中英混说术语 12 个"
    }
  ]
}
```

## 隐私与合规

- 音频含教师/同学声音：**只做本地评估，不上传、不入库、不外发**。
- 逐字稿入库前确认不含姓名、学号、联系方式等个人信息（口误里常出现点名，注意删改）。
- 公开课素材请注明来源与授权方式；不确定时优先用**自己录的课**。
