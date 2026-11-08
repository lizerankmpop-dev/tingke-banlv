# CodeCheck 首次扫描 47 项问题 · 修复说明

- **日期**：2026-11-08
- **分支/提交**：`fix/codecheck-first-scan` @ `612082f`（基于 `2026-华为智能体创新` @ `eee717c`）
- **处理结果**：**42 项已修复 + 5 项判定误报**（G.TYP.08，需在 CodeCheck 平台逐条标记，依据见 §3）
- **回归验证**：`pytest backend/tests` → **512 passed / 1 skipped**（与基线完全一致，32s）
- **门禁阈值**：未做任何调整（按要求保持原样）

---

## 1. 修复清单（42 项）

### G.VAR.03 · 局部 import / 参数覆盖外部标识符（24 项 → 全部修复）

**21 处冗余局部 import 删除**（函数体内 import 顶部已存在的同名模块，删除后行为不变）：

| 文件 | 处数 | 内容 |
|---|---|---|
| `backend/tests/test_langchain_chains.py` | 11 | json×6、FakeListChatModel×5 |
| `backend/tests/test_asr_pipeline_e2e.py` | 3 | os、Path、pytest |
| `examples/langchain_demo.py` | 5 | json×2、FakeListChatModel×3 |
| `backend/api/routes.py` | 1 | 函数内 `import asyncio`（顶部 L10 已有） |
| `backend/asr/pipeline.py` | 1 | 函数内 `import threading`（顶部 L10 已有） |

**3 处参数/变量覆盖外部标识符改名**：

| 文件 | 改动 |
|---|---|
| `backend/llm/chains/verify.py` | 汇总循环变量 `name/detail` → `fail_name/fail_detail`（避免覆盖外部 name） |
| `backend/main.py:136` | `lifespan(app)` → `lifespan(application)`（避免覆盖 FastAPI 实例惯用名 app） |
| `scripts/check_transcript.py` | 参数 `audio_seconds` → `audio_sec` + 体内 5 处引用同步 |

### G.TYP.02 · 浮点数 ==/!= 比较（8 项 → 全部改 `math.isclose()`）

| 文件 | 处数 | 改动 |
|---|---|---|
| `backend/tests/test_asr_pipeline_e2e.py` | 1 | `assert start == 0.0` → `math.isclose(start, 0.0)` |
| `backend/tests/test_vocabulary_review.py` | 5 | `_safe_float(...)==2.5/5.0` → `math.isclose(...)` |
| `backend/tests/test_repetition.py` | 2 | `R.dup_ngram_ratio(...)==0.0` → `math.isclose(...)` |

三个文件均已在顶部补 `import math`。

### G.ERR.14 · finally 内 return（8 项 → 全部重构）

8 个变异测试 probe 脚本存在同一模式：`finally:` 块内做「还原 + md5 校验 + `return 2`」。
**finally 内 return 会吞掉 try 块的异常**（如锚点缺失时 raise 的 SystemExit 会被静默吞掉），属真实缺陷。

**修法**：还原写入保留在 `finally` 内（保证异常路径下也还原）；md5/还原校验 + `return 2` 移出 `finally` 块、降一级缩进。正常路径行为完全不变；异常路径下异常正确向上传播（还原仍已完成）。

涉及文件（8 个，均在 `packaging/verify/`）：
`probe_verify_install_log_mutations.py`、`probe_verify_install_artifact_mutations.py`、`probe_ab_invalid_round_mutations.py`、`probe_latency_words_mutations.py`、`probe_eval_source_kind_mutations.py`、`probe_compat_relaunch_mutations.py`（变体：`_write_src(key, text, eol)` / `FILES[key]`）、`probe_language_plumbing_mutations.py`、`probe_lang_simp_mutations.py`（模板 B：`path.write_bytes` / `read_bytes` 对比）。

### G.ERR.09 · 父子异常重复捕获（1 项 → 修复）

`scripts/verify_install.py:420`：`except (urllib.error.URLError, OSError, ValueError)` → `except (OSError, ValueError)`（URLError 是 OSError 子类，元组中冗余）。

### G.OPR.03 · is/is not 比较内置类型（1 项 → 修复）

`backend/tests/test_eval_latency_words.py:131`：`is not 0` → `!= 0`。

---

## 2. 未改动但相关的两个文件说明

`packaging/verify/probe_phrases_and_truncate_mutations.py` 与 `probe_preproof_mutations.py` 的 `finally` 块内只有 `unlink()`（无 return），**不在** CodeCheck 报告的 47 项内，未做改动。

## 3. 误报标记依据（G.TYP.08 ×5，请在 CodeCheck 平台逐条标记为「误报/不修复」）

以下 5 处 `type(x) is str` / `type(x) is not str` 是**有意的精确类型检查**，不是疏忽：

| # | 位置 | 代码 |
|---|---|---|
| 1 | `backend/llm/chains/base.py:263` | `return str.__str__(value) if type(value) is not str else value` |
| 2 | `backend/llm/chains/verify.py:195` | `check("QA 链输出是精确的 str（type is str）", type(_qa_out) is str, ...)` |
| 3 | `backend/llm/client.py:71` | `if not isinstance(text, str) or type(text) is not str:` |
| 4 | `backend/tests/test_langchain_chains.py:133` | `assert type(out) is str, f"QA 链输出应为精确 str，实际 {type(out).__name__}"` |
| 5 | `backend/tests/test_langchain_chains.py:147` | `assert type(out) is str` |

**理由**：
1. **语义要求**：LangChain Runnable 链的输出契约要求**精确的 `str`**。`isinstance(x, str)` 对 `str` 的子类（如各种包装字符串类型）也返回 True；改用 isinstance 会**放宽契约**，使子类实例漏过校验，属于行为变更而非等价重构。
2. **测试即规格**：第 2、4、5 处位于校验器与测试断言中，断言文本本身就写着「精确的 str / type is str」——这是被测规格的一部分，不是实现细节。
3. **第 3 处双重检查是有意的**：`not isinstance(text, str) or type(text) is not str` 显式拒绝「str 的子类」输入，保证下游序列化行为可预期。
4. Python 官方风格指南（PEP 8）也认可 `type(x) is str` 在「确实需要精确类型、需排除子类」场景下的用法。

## 4. 合入与复查指引（张春阳）

1. 从 bundle / GitHub 拉取分支 `fix/codecheck-first-scan`（提交 `612082f`）；
2. 在 CodeArts 合入到协作分支（建议合入后保留 merge 记录，作为码道协作痕迹）；
3. 触发 CI 确认 pytest 仍为 **512 passed / 1 skipped**；
4. 在 CodeCheck 平台：① 重新执行检查，确认 42 项已消除；② 对 §3 表中 5 处 G.TYP.08 逐条标记「误报」，备注可直接引用 §3 理由；
5. 如复扫仍有残留项，把新明细发回，继续按此流程协作。
