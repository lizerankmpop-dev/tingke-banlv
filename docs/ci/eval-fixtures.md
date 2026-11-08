# 后端 CI 历史评测夹具

## 来源

五份 JSON 由队长于 **2026-11-08** 提供，来自原开发机的历史 ASR 质量评估报告。
按原始字节复制入库，未重新序列化、未修改数据、未归一化换行。

## 文件清单

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `packaging/eval/long-realtime.json` | 36,737 | `f17dca0e8232da20da2c106a2c1a5a20c3f0f0deaaa490db3d81a48dc78228bd` |
| `packaging/eval/p2_probe_short_clean.json` | 5,325 | `5a29af80d2df5060bcbe18e5409a0d9e62b8c98a3a8568cbd29fa8d705765d43` |
| `packaging/eval/real_zhlecture_ab4_phrases.phrases-on.json` | 19,347 | `b16b91454312f02322470656b420028252679a91633930c59759d7a263c5c705` |
| `packaging/eval/short-terms.json` | 7,114 | `5a223d147962150e656d012aad4ee0531c04b6fd23180ef4f511f7b819f45bb7` |
| `packaging/eval/short.json` | 6,408 | `507cf36dacbb7f1f534ce905dd2e4ac76585e057df687e0ba5fd2da368dbcc1c` |

## 用途与边界

- 它们是**历史回归测试输入**，不代表当前版本的实时识别效果。
- 测试直接回灌历史 `results`，与历史 `summary.byScenario` 逐字比较，用于证明代码改动没有改变分桶行为。
- 不得把其中的 WER/CER 当作当前版本的能力指标。

## 对应的 9 项测试

`backend/tests/test_eval_source_kind.py`（4 项，`test_replay_history_by_scenario_is_identical` 参数化）：

- `long-realtime.json`
- `p2_probe_short_clean.json`
- `short-terms.json`
- `short.json`

`backend/tests/test_ab_invalid_round.py`（5 项，`test_replay_history_reports` 参数化）：

- `long-realtime.json`
- `p2_probe_short_clean.json`
- `real_zhlecture_ab4_phrases.phrases-on.json`
- `short-terms.json`
- `short.json`

关键输入要求（由现有测试断言决定，不得修改）：

- `short.json` 的 `summary.totalSubtitles` 必须为 `0`（无效轮次守卫的反向样本）。
- 其余四份必须能被 `invalid_round_reason()` 放行。
- 五份报告的 `results` 均不含 `sourceKind`，回灌后应整批落入 `unknown`。

## 与模型资源的关系

模型种子、tokenizer 与真模型端到端测试所需的另外 **18 项**资源由
`ci/backend/prepare_test_resources.py` 在 CI 中准备：

- 内置模型种子 12 项
- 真 tokenizer 5 项
- 真模型端到端 1 项

本目录的五份 JSON 不覆盖上述模型资源，二者互补。

## 冻结纪律

- 这五份文件是**冻结夹具**：不得在 CI 中重新生成、重新序列化或覆盖。
- 重跑 ASR 会引入 run-to-run 波动，且无法复现 `short.json` 的 0 字幕轮次。
- `.gitignore` 只放行这五个路径；其他评测报告、日志与子目录继续忽略。
- 如确需更新夹具，必须由原开发机提供真实历史产物并单独评审，不得用合成数据替代。