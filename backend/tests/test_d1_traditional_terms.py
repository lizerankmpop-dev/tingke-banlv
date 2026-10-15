"""
D1 **产品侧度量**：繁体字对「术语命中」与「生词本收录」的影响（V6 批 A）

## 为什么单独做这一次度量

D1 的**代码层**已经关闭 —— 简体引导语生效后实测繁体 0/448（`docs/asr_eval/known_defects.md` D1）。
但关闭时留了一条**未量化的产品侧缺口**：

> 繁体字是否影响**术语命中**与**生词本收录**？

它必须先有指标定义才可测（V6 方案 §2.5）。本文件就是那份定义 + 一次基线测量。
**刻意不改任何生产代码**：D1 已关闭，这一项只是度量。

## 繁体字还会从哪里来（不是「已经归零了所以不用管」）

简体引导语只保证「识别输出默认是简体」。繁体仍会出现在两条路径上：

1. 用户把课程配置里的「中文输出字形」切成**保留原始字形**（`simpMode=raw`，
   面向古文/繁体教材课程的功能）；
2. 用户在**自定义术语**里填了繁体词（界面上没有任何简繁校验）。

而且实测证据显示，繁体往往是**字级混入**而不是整词繁体 ——
`testset/chinese_lecture/real/real_zhlecture_origin_plot.corrections.tsv` 里
真实观测到 4 处（简体引导语修复之前）：

| 观测到的形态 | 正确形态 | 说明 |
|---|---|---|
| 图表**繪**制 | 图表绘制 | L29 原文 |
| **橫**坐标 | 横坐标 | L35 原文（「橫作表」） |
| 单栏的**寬**度 | 单栏的宽度 | L23 原文（繁体字在非术语词里） |
| **菜檔** | 菜单（栏） | L83 原文 |

所以本文件的样本里**同时包含**「字级混入」与「整词繁体」两种形态。

## 指标定义（先定死，否则不可测）

| 指标 | 定义 | 本文件怎么测 |
|---|---|---|
| **M1 命中率** | 生词本里存的是**简体**形态时，「繁体形态的术语」在 `GET /vocabulary/{word}` 上返回 200 的比例 | 先以简体 collect，再逐个查繁体 |
| **M2 入库率** | 「繁体形态的术语」经 `POST /vocabulary/{word}/collect` 成功入库的比例 | 逐个 collect 繁体，看 200 |
| **M3 重复率** | 同一术语的简体与繁体形态**并存**时产生的额外条目比例 | 采集后数生词本总条目数 vs 术语数 |
| **M4 失败归因** | 未命中是「词表里存的是简体」还是「匹配算法不认简繁」 | 把库里的条目**改成繁体**后再查繁体，看是否立刻命中 |

M4 是整组指标里唯一能**定位责任方**的：它把「数据形态不一致」与「算法不支持」
这两件看起来一样的事分开。做法是同一份代码、同一个查询，只换库里那条记录的写法。

## 样本

- **真实观测样本**：上表里属于课程术语的两条（`图表繪制`、`橫坐标`）。
- **构造样本**：本课 24 个原生中文术语（取自 `scripts/ab_terms_compare.py` 的
  `COURSE_TERMS_ORIGIN`，**不复制一份**）逐个给出人工核对的繁体形态。
  用构造样本不依赖真实用户，符合 V6 方案 §2.5 的验收口径。

`TRADITIONAL_FORMS` 与术语清单**双向**对拍：清单加词而映射表没跟上会直接红 ——
否则「样本覆盖 100%」这句话是假的（漏掉的词会被算成没差异）。
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AB_SCRIPT = PROJECT_ROOT / "scripts" / "ab_terms_compare.py"


def _load_course_terms() -> list[str]:
    """
    从 A/B 脚本里读原生中文术语清单（唯一来源）

    刻意不复制一份到本文件：两份清单必然漂移，而漂移之后本文件的「覆盖率」
    断言会继续全绿 —— 它只是在自己那份过期副本上自洽。
    """
    spec = importlib.util.spec_from_file_location("_ab_terms_for_d1", AB_SCRIPT)
    assert spec and spec.loader, f"无法加载 {AB_SCRIPT}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    terms = list(getattr(mod, "COURSE_TERMS_ORIGIN"))
    assert terms, "COURSE_TERMS_ORIGIN 为空，A/B 脚本可能已改名"
    return terms


COURSE_TERMS = _load_course_terms()

# 24 个原生术语的繁体形态（人工核对）
#
# 注意「厘米 / 毫米」两个字**简繁同形**，所以映射后与原词相同 —— 保留在表里是
# 刻意的：它证明「不是所有术语都有简繁差异」这件事被真的核过，而不是整体漏掉。
TRADITIONAL_FORMS: dict[str, str] = {
    "项目管理器": "項目管理器",
    "工作区": "工作區",
    "工具栏": "工具欄",
    "菜单栏": "菜單欄",
    "新建图": "新建圖",
    "画布": "畫布",
    "坐标系": "坐標系",
    "图层": "圖層",
    "子图": "子圖",
    "折线图": "折線圖",
    "柱形图": "柱形圖",
    "数据表": "數據表",
    "曲线数据": "曲線數據",
    "图表绘制": "圖表繪製",
    "对话框": "對話框",
    "横坐标": "橫坐標",
    "纵坐标": "縱坐標",
    "误差": "誤差",
    "数据标签": "數據標籤",
    "单位": "單位",
    "厘米": "厘米",  # 简繁同形
    "毫米": "毫米",  # 简繁同形
    "双栏": "雙欄",
    "单栏": "單欄",
}

# 真实观测到的「字级混入」形态（来自 corrections.tsv，仅取属于课程术语的两条）
OBSERVED_MIXED_FORMS: dict[str, str] = {
    "图表绘制": "图表繪制",  # L29 原文
    "横坐标": "橫坐标",      # L35 原文（原句为「橫作表」）
}

# 简繁真有不同的术语（用于断言样本不是「全都在测同形词」）
DIFFERING = [t for t, v in TRADITIONAL_FORMS.items() if t != v]


@pytest.fixture(scope="module")
def client():
    # 必须用 with 触发 lifespan，否则 dependencies.db 还是 None
    with TestClient(app) as c:
        yield c


def _make_course(client: TestClient, name: str) -> str:
    resp = client.post(
        "/api/courses",
        json={"name": f"{name}-{uuid.uuid4().hex[:6]}", "domain": "计算机", "language": "zh"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _collect(client: TestClient, cid: str, word: str) -> int:
    return client.post(
        f"/api/courses/{cid}/vocabulary/{word}/collect",
        json={
            "translation": f"{word} 的释义",
            "definition": "",
            "contextSentence": f"这句里有{word}",
        },
    ).status_code


def _lookup(client: TestClient, cid: str, word: str) -> int:
    return client.get(f"/api/courses/{cid}/vocabulary/{word}").status_code


def _total(client: TestClient, cid: str) -> int:
    resp = client.get(f"/api/courses/{cid}/vocabulary/stats")
    assert resp.status_code == 200, resp.text
    return resp.json()["total"]


# ------------------------------------------------------------------ 样本自洽

def test_sample_covers_term_list_both_ways():
    """样本与术语清单双向对拍：漏一个词就等于少测一个样本。"""
    assert len(COURSE_TERMS) == 24, f"原生术语应为 24 个，实际 {len(COURSE_TERMS)}"
    missing = [t for t in COURSE_TERMS if t not in TRADITIONAL_FORMS]
    extra = [t for t in TRADITIONAL_FORMS if t not in COURSE_TERMS]
    assert not missing, f"这些术语没有繁体映射（会被静默跳过）：{missing}"
    assert not extra, f"映射表里有清单之外的词（多半是清单改了）：{extra}"
    # 样本不能全是同形词，否则整份度量等于什么都没测
    assert len(DIFFERING) == 22, f"简繁有差异的术语应为 22 个，实际 {len(DIFFERING)}"
    for term in COURSE_TERMS:
        assert TRADITIONAL_FORMS[term].strip(), f"{term} 的繁体形态为空"


def test_observed_samples_belong_to_course_terms():
    """真实观测样本必须属于课程术语，否则它测的不是术语链路。"""
    for simp, mixed in OBSERVED_MIXED_FORMS.items():
        assert simp in COURSE_TERMS, f"{simp} 不在原生术语清单里"
        assert mixed != simp and mixed != TRADITIONAL_FORMS[simp], (
            f"{mixed} 与整词繁体相同，就不是「字级混入」样本了"
        )


# ------------------------------------------------------------------ M1 / M2 / M3

def test_m1_m2_m3_hit_collect_and_duplicate_rates():
    """
    场景：用户在**简体**模式下划过词（库里是简体），后来切到「保留原始字形」
    或看到字级混入的繁体，再去点那个词。

    这是一次**度量**，不是「期望它失败」：断言写的是「当前实现的确定行为」，
    目的是让数字可复现、且在实现变化时**立刻显形**（见文件末尾的同步提醒）。
    """
    with TestClient(app) as client:
        cid = _make_course(client, "D1繁体度量")

        # 先按简体入库（模拟简体模式下的正常使用）
        for term in COURSE_TERMS:
            assert _collect(client, cid, term) == 200, f"简体入库失败：{term}"
        assert _total(client, cid) == len(COURSE_TERMS), "简体入库后条目数不等于术语数"

        # 样本 = 24 个构造繁体形态，其中 2 个换成真实观测到的字级混入形态
        samples = {t: TRADITIONAL_FORMS[t] for t in COURSE_TERMS}
        samples.update(OBSERVED_MIXED_FORMS)

        # 拆成两组：**有简繁差异的** 与 **简繁同形的**。
        # 后者不是噪声，是**对照组** —— 它命中的比例必须与前者形成反差，
        # 才能说明「差异只来自字形」。把两组混在一起算总命中率会掩盖这件事
        # （首版就是这么写的：总命中率 2/24 = 8.3%，看起来像「有 8% 能命中」）。
        diff_samples = {t: v for t, v in samples.items() if t != v}
        same_samples = {t: v for t, v in samples.items() if t == v}

        # ---- M1：繁体形态能不能查到
        hit, miss = [], []
        for simp, trad in samples.items():
            (hit if _lookup(client, cid, trad) == 200 else miss).append((simp, trad))
        m1_diff = len([1 for s, t in diff_samples.items() if _lookup(client, cid, t) == 200]) / len(diff_samples)
        m1_same = len([1 for s, t in same_samples.items() if _lookup(client, cid, t) == 200]) / len(same_samples)

        # ---- M2：繁体形态能不能入库
        failed_collect = [t for t in samples.values() if _collect(client, cid, t) != 200]
        m2 = 1 - len(failed_collect) / len(samples)

        # ---- M3：重复条目 —— 同一术语的简体与繁体并存
        total_after = _total(client, cid)
        new_forms = {v for v in samples.values() if v not in COURSE_TERMS}
        expected_total = len(COURSE_TERMS) + len(new_forms)
        extra = total_after - len(COURSE_TERMS)

        print(
            f"\n[D1 度量] 样本 {len(samples)} 个 = 有简繁差异 {len(diff_samples)} 个"
            f" + 简繁同形 {len(same_samples)} 个{sorted(same_samples)}\n"
            f"  M1a 命中率（有差异的样本）= "
            f"{int(m1_diff * len(diff_samples))}/{len(diff_samples)} = {m1_diff:.4f}\n"
            f"  M1b 命中率（同形样本，对照组）= "
            f"{int(m1_same * len(same_samples))}/{len(same_samples)} = {m1_same:.4f}\n"
            f"  M2  入库率（全部样本）= "
            f"{len(samples) - len(failed_collect)}/{len(samples)} = {m2:.4f}\n"
            f"  M3  重复条目 = 生词本 {total_after} 条 vs 术语 {len(COURSE_TERMS)} 个"
            f" → 额外 {extra} 条（{extra / len(COURSE_TERMS):.4f}）\n"
            f"  未命中示例（简体形态 → 繁体形态）：{miss[:3]}"
        )

        # ---- 当前实现的确定行为（这是**基线数字**，改动实现必须同步更新结论）
        assert m1_diff == 0.0, (
            f"有简繁差异的样本里竟然命中了 {int(m1_diff * len(diff_samples))} 个，实现已变 —— "
            "说明加了简繁归一化，请更新 known_defects.md D1 与 plan_v6.md §2.5"
        )
        assert m1_same == 1.0, (
            "简繁同形的样本本应全部命中 —— 组内没命中说明查词链路本身坏了，"
            "而不是简繁问题（对照组失效，M1 的结论就不成立）"
        )
        assert m2 == 1.0, f"繁体形态入库失败 {failed_collect[:3]}，采集链路对非 ASCII 有问题"
        assert extra > 0, "繁体入库没有新增条目，简繁归一化可能已经加上了"
        assert total_after == expected_total, (
            f"条目数 {total_after} 与「术语数 + 新形态数」{expected_total} 对不上"
        )


# ------------------------------------------------------------------ M4 归因

def test_m4_attribution_is_data_form_not_algorithm():
    """
    同一份代码、同一个查询，**只换**库里那条记录的写法：

      · 库里存简体 → 查简体命中、查繁体不命中
      · 库里存繁体 → 查繁体命中、查简体不命中

    两边**完全对称** ⇒ 查词算法既不认简体也不认繁体，它只做逐字相等比较。
    因此未命中的责任方是「库里的字」，不是「匹配算法不支持简繁」。

    ⚠️ 两个方向必须用**各自新开的课程**：同一个课程里先收简体再收繁体，
    会同时存在两条记录，两边都命中 —— 那就把「对称性」这个结论糊掉了
    （首版就是这么写的，两个方向都命中了）。
    """
    with TestClient(app) as client:
        term = "坐标系"
        trad = TRADITIONAL_FORMS[term]
        assert trad != term

        # 方向一：库里只有简体
        cid_simp = _make_course(client, "D1归因-存简体")
        assert _collect(client, cid_simp, term) == 200
        assert _lookup(client, cid_simp, term) == 200, "存简体应能查到简体"
        assert _lookup(client, cid_simp, trad) == 404, "存简体却查到了繁体 ⇒ 存在某种归一化"

        # 方向二：库里只有繁体（全新课程，避免两条记录并存）
        cid_trad = _make_course(client, "D1归因-存繁体")
        assert _collect(client, cid_trad, trad) == 200
        assert _lookup(client, cid_trad, trad) == 200, (
            "存繁体却查不到繁体 ⇒ 归因结论要改：问题可能真在匹配算法"
        )
        assert _lookup(client, cid_trad, term) == 404, "存繁体却查到了简体 ⇒ 存在某种归一化"


def test_m4_mixed_and_full_traditional_share_one_rule():
    """
    「字级混入」（`图表繪制`）与「整词繁体」（`圖表繪製`）在查词上**没有区别** ——
    都是「与库里的字符串不逐字相等」。所以不必为两种形态分别定指标，
    M1 的结论对二者同时成立。
    """
    with TestClient(app) as client:
        cid = _make_course(client, "D1字级混入")
        term = "图表绘制"
        assert _collect(client, cid, term) == 200

        for variant in (TRADITIONAL_FORMS[term], OBSERVED_MIXED_FORMS[term]):
            assert variant != term
            assert _lookup(client, cid, variant) == 404, (
                f"{variant} 命中了 —— 说明存在某种模糊匹配，本节结论需重写"
            )


# ------------------------------------------------------------------ 同步提醒

def test_conclusion_premise_change_must_surface():
    """
    这不是「锁定缺陷」，而是**文档同步报警器**。

    上面几条断言写的是当前实现的行为。如果将来有人补上简繁归一化（那是好事），
    它们会红 —— 红的含义是：

      ✅ 「D1 的产品侧缺口已被修掉」→ 请更新
         `docs/asr_eval/known_defects.md` 的 D1 段与 `docs/m3/plan_v6.md` §2.5，
         并把本文件里的度量改写成「归一化生效后」的版本。

    而不是「把断言删掉让它变绿」。
    """
    # 度量所依赖的两条前提：清单非空、且样本里真有 22 个形式不同的词
    assert all(t.strip() for t in COURSE_TERMS), "术语清单应无空串"
    assert len(DIFFERING) == 22, "简繁有差异的样本数变了 —— 前提已动，请复核结论"
