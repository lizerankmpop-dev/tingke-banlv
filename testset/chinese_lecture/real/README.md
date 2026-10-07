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

> `.gitignore` 已屏蔽 `testset/**/*.{wav,mp3,m4a,flac,mp4,aac,ogg,opus,m4s,webm}` ——
> **音频不入库**（版权 + 体积），只入库逐字稿与 metadata。换机器跑评估时按 metadata 的
> `sha256` 核对音频是否一致。（`opus` 覆盖学术数据集的格式，`m4s`/`webm` 覆盖视频站拉流的
> 容器——这三种都曾漏屏蔽，属已修缺陷。）

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

## 制作流程（四步，工具已就绪）

```bash
# ⓪ 拉素材（仅第三方平台素材需要；自录的跳到 ①）
#     BV 号 → DASH 音频 → 16k 单声道 WAV + 按时间戳连续切片
<venv>/python.exe scripts/fetch_bili_audio.py BV1BA411i7PT \
    --start 00:40 --end 11:10 --name real_zhlecture_<主题> \
    --out-dir testset/chinese_lecture/real
#     建议先加 --dry-run 看时长是否够切；工具会打印 RMS/峰值/近静音占比，
#     近静音占比过高说明窗口选在了停顿/片头音乐里，换个区间重切。

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
      "channel": "self-record | bilibili | dataset | other",
      "provenance": {
        "url": "<原始视频/音频页 URL，第三方素材必填>",
        "title": "<原标题>",
        "author": "<原作者/上传者>",
        "clip": "<截取起止，如 12:30 ~ 23:05>",
        "license": "<平台协议；学术数据集填官方许可，并注明镜像标注是否一致>"
      },
      "consent": "仅用于本地评估，不再分发；含他人声音，故音频不入库",
      "recordedAt": "2026-10-XX",
      "notes": "手机录音，教室后排，有翻页声；中英混说术语 12 个"
    }
  ]
}
```

## 素材获取渠道（2026-10-07 实测结论，避免重复踩坑）

### ✅ 自录（首选，零版权风险）

逐字稿可入库，CER 结论可外推性最强。要求见上文「音频要求」。

### ✅ B 站公开课切片（链路已实测打通）

2026-10-07 实测：`api.bilibili.com` 可达（200 / 0.9s），全链路走通 ——
`ranking` → `view?bvid` 取 `cid` → `player/playurl?fnval=16` 拿 DASH 音频
→ 带 `Referer: https://www.bilibili.com` 下载 `m4s` 成功（ftyp 头正确）。

两条边界必须先知道：

- **搜索 API 匿名被风控**（返回 HTML 而非 JSON）→ 不能自动找片，**需人工给出 BV 号/链接**。
- **字幕 API 匿名返回空数组** → **B 站字幕不可依赖**（且其多为 ASR 自动生成，本身也不合
  「人工逐字稿」的定义）。这反而与 `check_transcript.py` 的设计一致：逐字稿必须人工校。
- 容器是 `m4s`（fMP4/AAC）。项目 venv 有 `av`，`eval_asr_quality.load_audio` 走 PyAV
  分支即可解码，**无需 ffmpeg CLI**。

版权：公开传播 ≠ 授权 → 按下方「第三方素材」条款处理，**音频与逐字稿都只留本地**。

### ❌ WenetSpeech（已实测否决，不要再走）

三重锁，全部实测确认：

1. 音频以 **AES 加密包**分发，解压密码需**填写 Google 表单申请并等邮件** —— 本机
   `google.com` 不可达，且有人工审批延迟。
2. **元数据 `WenetSpeech.json.aes.tgz` 同样加密** → 「筛 `domain=talk` + 时长 > 600s」
   这一步**根本执行不了**。ModelScope 上那个 `WenetSpeech.json` 只是 **681 字节的路径
   描述文件**（指向上述 aes 包），不是数据。
3. 总量 ~697 GB（HF 卡片标 1.02 TB），且**三分之二来源是 YouTube**（本机不可达）。

另有两处事实口径要注意（别写进答辩）：

- 官方**直接托管已转码的 `.opus` 音频包**，元数据里的 `url` 字段是**版权溯源**用途，
  **不是**「需要自己按 URL 去拉」。
- 许可在 **HF/官网 = CC BY 4.0 + 非商用**，而 **ModelScope 镜像页写的是 Apache-2.0**，
  渠道标注自相矛盾。以官网/HF 为准，并在元数据里注明镜像标注不一致。

### ❌ EchoFlow（`luzhenhua/echo-flow`）

英语点读跟读工具，仓库**零音频零字幕语料**，README 自声明「不提供任何教材音频或课文
内容」。其播放器交互（倍速/循环/连播）已借到 `review.html`，但**不能作素材源**。

## 隐私与合规

- 音频含教师/同学声音：**只做本地评估，不上传、不入库、不外发**。
- **自录素材**：逐字稿与 metadata 入库（自己写的，可入库）。
- **第三方素材（公开课/数据集/他人录音）**：音频与逐字稿**都只留在本地**，只在
  `metadata.json` 里登记来源、授权方式与 `sha256` —— 逐字稿同样是受版权保护的转录作品，
  不能因为是我们整理的就可以入库。
- 逐字稿入库前确认不含姓名、学号、联系方式等个人信息（口误里常出现点名，注意删改）。
- 公开课素材请注明来源与授权方式；不确定时优先用**自己录的课**。
