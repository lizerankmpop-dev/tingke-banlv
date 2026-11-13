# 华为云实时语音转写后端（骨架）

> 日期：2026-11-13 ｜ 分支：`feat/huawei-asr`（基于 `work/codearts-replication`，版本 3.7.0）
> 状态：**后端骨架 + 切换逻辑已实现；真实 SIS 协议未接入（刻意不猜测）**。
> 密钥纪律：AK/SK/Token 只从环境变量读取，不写代码、不进 git、不出现在日志或 `get_stats()` 里。

## 0. 本次做了什么、没做什么

**已实现**

- `backend/asr/huawei_asr.py`：华为云 ASR 配置、后端生命周期、可注入 transport。
- `backend/asr/backend_factory.py`：按课程配置 / 环境变量选择本地或云端后端。
- `backend/asr/pipeline.py`：只替换后端构造点，并把「不可用」提示改为透出后端原因；
  音频捕获、VAD、`_on_speech_segment`、字幕回调、术语提取等链路**均未改动**。
- `backend/tests/test_huawei_asr_backend.py`：离线单测（fake transport，不联网）。
- 本文档。

**刻意没做（等官方文档/密钥）**

- 不猜测 SIS 的真实 endpoint、AK/SK 签名算法、WebSocket 音频帧/事件格式。
- 默认 transport 在配置齐全时只抛出「协议尚未接入」，**不建立任何网络连接**。
- 未改动主干分支；数据库字段、前端「本地/华为云」开关留到下一阶段。

## 1. 架构

```text
ASRPipeline
   │  构造时调用 create_asr_backend(course_config, **kwargs)
   ▼
backend_factory
   ├─ asrBackend / TINGKEBANLV_ASR_BACKEND = local|whisper
   │     └─► WhisperEngine（现有本地链路，参数原样透传）
   └─ asrBackend / TINGKEBANLV_ASR_BACKEND = huawei|huawei-asr|cloud
         └─► HuaweiASRBackend
                │ add_audio(float32 16k mono)
                │   → int16 PCM → 200ms 分块 → 有界队列
                ▼
           HuaweiASRTransport（可注入）
                ├─ HuaweiSISWebSocketTransport（占位，TODO 待补）
                └─ FakeTransport（测试用，不联网）
                │ on_event(规范化事件)
                ▼
           on_partial(text, words) → ASRPipeline 字幕回调（与本地一致）
```

后端选择优先级：

```text
course_config["asrBackend"] > 环境变量 TINGKEBANLV_ASR_BACKEND > "local"
```

未知取值记录 warning 并回退 `local`，避免配置拼写错误导致整堂课起不来。

## 2. 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `HUAWEI_ASR_AK` | 是* | Access Key。仅环境变量，禁止入库/入 git |
| `HUAWEI_ASR_SK` | 是* | Secret Key。仅环境变量 |
| `HUAWEI_ASR_ENDPOINT` | 是 | 联调入口；真实 URL 模板待官方文档确认，当前不猜测 |
| `HUAWEI_ASR_AUTH_TOKEN` | 否 | 临时 token/预签名；存在时可不填 AK/SK |
| `HUAWEI_ASR_REGION` | 否 | 区域。是否参与鉴权/URL 待协议确认，当前只读取保存 |
| `HUAWEI_ASR_PROJECT_ID` | 否 | 项目 ID。同上 |
| `TINGKEBANLV_ASR_BACKEND` | 否 | `local` / `whisper` / `huawei` / `huawei-asr` / `cloud` |

\* 没有 `HUAWEI_ASR_AUTH_TOKEN` 时必须同时提供 AK 与 SK。

配置缺失时后端**不启动线程、不发起网络**，`last_error` 直接给出缺失的变量名与中文处理建议；
`ASRPipeline.start()` / `_wait_for_model()` 会把该原因透传给前端。

## 3. 如何切换

课程配置（数据库/接口字段下一阶段再接前端开关）：

```json
{ "asrBackend": "cloud" }
```

或全局环境变量：

```bash
# Windows（当前会话）
set TINGKEBANLV_ASR_BACKEND=cloud

# PowerShell
$env:TINGKEBANLV_ASR_BACKEND = "cloud"

# bash
export TINGKEBANLV_ASR_BACKEND=cloud
```

空值/未知值 → 本地 Whisper。课程配置优先于环境变量，便于单课灰度。

## 4. 华为云后端接口

`HuaweiASRBackend` 与 `WhisperEngine` 保持同样的公开生命周期：

| 成员 | 说明 |
|---|---|
| `is_loading` / `is_loaded` | 连接中 / 已连接 |
| `backend_name` | 固定 `"huawei-asr"` |
| `last_error` | 中文错误；配置缺失时为具体缺项 |
| `add_audio(np.ndarray)` | float32 16k mono → int16 PCM，200ms 分块入队 |
| `set_initial_prompt(prompt, sections=None)` | 仅保存；真实热词协议待确认 |
| `set_simp_mode(mode)` / `set_auto_degrade(enabled)` | 兼容 pipeline 热更新调用 |
| `flush(timeout=...)` | 等待已入队音频发送完 |
| `stop()` | 先尽力发完、再停线程并关闭 transport；可重复调用 |
| `get_stats()` | 音频秒数、发送块数、丢弃数、事件数、缺失变量名（不含密钥） |

线程模型：后台单线程 `transport.start()` + 串行消费；队列上限 64 块（约 12.8 秒），
满时丢最旧块并计数。`stop()` 会 join 后台线程，避免测试/切课后残留线程。

## 5. 已实现 / 待补

| 能力 | 状态 |
|---|---|
| 配置读取、缺失校验、中文错误 | ✅ 已实现 |
| 本地/云端工厂切换、未知值回退 | ✅ 已实现 |
| float32 → int16 PCM、分块、有界队列 | ✅ 已实现 |
| 后台连接/收包线程、`flush`/`stop` | ✅ 已实现 |
| 规范化结果事件 → `on_partial(text, words)` | ✅ 已实现（内部约定格式） |
| **真实 SIS endpoint / URL 模板** | ⛔ 待官方文档 |
| **AK/SK 或 Token 签名** | ⛔ 待官方文档 |
| **音频帧、开始/结束指令、partial/final 事件解析** | ⛔ 待官方文档 |
| 心跳、重连、限流、错误码映射 | ⛔ 待官方文档 |
| 云端热词 / 简繁提示下发 | ⛔ 待协议确认 |
| 数据库字段 / 前端开关 | ⛔ 下一阶段 |

> 本次**没有猜测真实协议**。`HuaweiSISWebSocketTransport` 中的
> `TODO(huawei-sis-1..4)` 明确标出了需要官方资料确认的四块内容。

## 6. 用 fake transport 测试（不联网）

测试不依赖 `faster_whisper` / PyAudio，也不访问任何网络：

```bash
cd backend
PYTHONPATH=.venv/deps python -m pytest tests/test_huawei_asr_backend.py -q
```

`FakeTransport` 只需实现三个方法：

```python
class FakeTransport:
    def start(self, on_event):
        self.on_event = on_event          # 保存回调

    def send_audio(self, data: bytes):
        self.audio.append(data)           # 记录 PCM
        self.on_event({                   # 回放一个规范化事件
            "type": "result",
            "text": "你好世界",
            "words": [
                {"word": "你好", "start": 0.0, "end": 0.5, "confidence": 0.97},
            ],
        })

    def close(self):
        self.closed = True
```

注入方式：

```python
backend = HuaweiASRBackend(
    config=HuaweiASRConfig(
        ak="fake-ak", sk="fake-sk", endpoint="wss://example.invalid/asr"
    ),
    transport=FakeTransport(),
    on_partial=lambda text, words: print(text, words),
)
backend.add_audio(np.zeros(16000, dtype=np.float32))
backend.flush()
backend.stop()
```

规范化事件格式（**本仓库内部约定，不是华为云协议**）：

```json
{
  "type": "result | partial",
  "text": "识别文本",
  "words": [
    { "word": "词", "start": 0.1, "end": 0.3, "confidence": 0.9 }
  ]
}
```

`words` 可为空；词级时间戳缺失时传空列表。真实 SIS 事件的时间口径（相对/绝对）
尚未确认，当前只原样透传 + 用累计音频时长兜底，不做无依据的偏移换算。

## 7. 拿到官方文档/密钥后的联调步骤

1. **先只配环境变量**，确认配置校验通过：

   ```bash
   set HUAWEI_ASR_ENDPOINT=wss://<官方地址>
   set HUAWEI_ASR_AK=<AK>
   set HUAWEI_ASR_SK=<SK>
   ```

   （或临时用 `HUAWEI_ASR_AUTH_TOKEN`，不写进代码。）

2. 按官方文档补 `HuaweiSISWebSocketTransport` 的四个 TODO：
   URL/region/project 拼接、鉴权签名、音频帧与事件解析、心跳/错误码。
3. 在 `start()` 建立连接后，把官方 JSON 转成上面的规范化事件再调用 `on_event`；
   **转换只放在 transport 内**，`HuaweiASRBackend` 与 pipeline 不感知 SIS 字段。
4. 用 fake transport 单测继续守住生命周期；再用一段 10~30 秒中文音频做真联调，
   核对 partial 延迟、词级时间戳口径、`stop()` 是否能干净断开。
5. 真联调通过后，再开下一阶段：数据库字段（课程级 `asrBackend` 持久化）与前端开关。

## 8. 纪律声明

- 本次没有猜测任何真实的华为云 endpoint、签名算法或消息格式。
- 未改主干分支；改动仅限 `feat/huawei-asr` 分支的 ASR 后端层，本地 Whisper 链路行为不变。
- 数据库字段与前端开关下一阶段再加；当前仅支持环境变量 / 课程配置字典切换。
- 不 commit、不 push，等待确认。