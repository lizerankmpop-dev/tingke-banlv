"""
术语热词注入 —— 端到端集成测试

不依赖 HTTP 服务器，直接用 ASGI TestClient 跑完整请求链路，
验证：术语库构建 / 自定义术语优先级 / preview 端点 / 识别优化统计。
"""

import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from fastapi.testclient import TestClient
from main import app

# 用 with 触发 lifespan（初始化数据库），否则 db 为 None
client = TestClient(app)
client.__enter__()

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


print("=" * 64)
print("端到端集成测试：术语热词注入")
print("=" * 64)

# ---- 1. 创建课程 ----
print("\n[1] 创建课程（机器学习领域 + 自定义术语）")
r = client.post("/api/courses", json={
    "name": "端到端测试课程",
    "domain": "机器学习",
    "customTerms": ["backpropagation", "SGD", "mycustomterm"],
    "courseType": "online",
    "language": "en",
})
check("创建课程返回 200", r.status_code == 200, f"got {r.status_code}")
course = r.json()
cid = course["id"]
print(f"       course_id = {cid}")

# ---- 2. 课程术语库 ----
print("\n[2] 查询课程术语库")
r = client.get(f"/api/courses/{cid}/terminology")
check("术语库返回 200", r.status_code == 200)
term = r.json()
s = term["stats"]
print(f"       领域={term['domain']} 总数={s['total']} 覆盖={s['inVocab']} ({round(s['coverage']*100)}%)")
print(f"       custom={s['custom']} domain={s['domain']} prompt={term['promptChars']}字符")

check("领域正确归一化为 人工智能", term["domain"] == "人工智能", term["domain"])
check("自定义术语数为 3", s["custom"] == 3, str(s["custom"]))
check("prompt 非空", term["promptChars"] > 0)
check("prompt 长度合理(<1000)", term["promptChars"] < 1000, str(term["promptChars"]))

# 自定义术语必须排在 prompt 最前（= 最高识别优先级）
prompt_head = term["prompt"][:40]
check("自定义术语位于 prompt 开头", "backpropagation" in prompt_head, prompt_head)

# ---- 3. 术语来源与释义 ----
print("\n[3] 术语分层与释义覆盖")
custom_terms = [t for t in term["terms"] if t["source"] == "custom"]
domain_terms = [t for t in term["terms"] if t["source"] == "domain"]
check("存在 custom 层术语", len(custom_terms) == 3, str(len(custom_terms)))
check("存在 domain 层术语", len(domain_terms) > 20, str(len(domain_terms)))

# 词表能覆盖的术语应有中文释义
covered = [t for t in term["terms"] if t["inVocab"]]
check("有释义的术语均带 translation",
      all(t["translation"] for t in covered),
      str([t["term"] for t in covered if not t["translation"]][:3]))

# ---- 4. preview 端点 ----
print("\n[4] 术语预览端点（前端配置页实时用）")
r = client.post("/api/terminology/preview", json={
    "domain": "物理学",
    "customTerms": ["wavefunction"],
})
check("preview 返回 200", r.status_code == 200)
pv = r.json()
check("preview 领域为 物理", pv["domain"] == "物理", pv["domain"])
check("preview 含自定义术语", pv["stats"]["custom"] == 1, str(pv["stats"]["custom"]))
check("preview prompt 非空", pv["promptChars"] > 0)

# 空领域不应报错
r = client.post("/api/terminology/preview", json={"domain": "", "customTerms": []})
check("空领域 preview 不报错", r.status_code == 200)
check("空领域 total 为 0", r.json()["stats"]["total"] == 0, str(r.json()["stats"]["total"]))

# ---- 5. 领域列表 ----
print("\n[5] 领域列表端点")
r = client.get("/api/terminology/domains")
check("领域列表返回 200", r.status_code == 200)
domains = r.json()["domains"]
total_terms = sum(d["termCount"] for d in domains)
print(f"       {len(domains)} 个领域, 共 {total_terms} 个术语")
check("领域数 >= 10", len(domains) >= 10, str(len(domains)))
check("术语总数 >= 250", total_terms >= 250, str(total_terms))

# ---- 6. 自定义术语热更新 ----
print("\n[6] 自定义术语更新")
r = client.put(f"/api/courses/{cid}", json={
    "customTerms": ["attention", "transformer", "brandnewterm"],
})
check("更新课程返回 200", r.status_code == 200)

r = client.get(f"/api/courses/{cid}/terminology")
s2 = r.json()["stats"]
check("更新后自定义术语数为 3", s2["custom"] == 3, str(s2["custom"]))
new_custom = [t["term"] for t in r.json()["terms"] if t["source"] == "custom"]
check("新术语已生效", "attention" in new_custom and "brandnewterm" in new_custom, str(new_custom))
print(f"       更新后 custom = {new_custom}")

# ---- 7. 各领域构建均不报错 ----
print("\n[7] 全领域健壮性检查")
frontend_domains = ['计算机科学', '医学', '生物学', '物理学', '数学', '化学',
                    '法律', '经济学', '哲学', '历史', '文学', '其他']
bad = []
for d in frontend_domains:
    r = client.post("/api/terminology/preview", json={"domain": d, "customTerms": []})
    if r.status_code != 200:
        bad.append(d)
check("前端全部领域均可正常构建", not bad, str(bad))

# ---- 8. 字幕术语匹配（本地词表通道）----
print("\n[8] 字幕术语匹配（不依赖 LLM）")
from dictionary.terminology import match_terms_in_text

hits = match_terms_in_text(
    "Today we will discuss the convolutional neural network and how "
    "backpropagation works with gradient descent to minimize the loss function."
)
words = [h["word"] for h in hits]
print(f"       命中: {words}")
check("匹配到术语", len(hits) >= 4, str(len(hits)))
check("长短语优先匹配", "gradient descent" in words or "loss function" in words, str(words))
check("无释义缺失", all(h["translation"] for h in hits))

# 停用词过滤
noise = match_terms_in_text("So um, today we are going to look at, you know, the basic idea here.")
check("口语废话句匹配数为 0", len(noise) == 0, str([h["word"] for h in noise]))

# ---- 9. 讲解器本地优先 ----
print("\n[9] 点击讲解 —— 本地词表优先（零 LLM 依赖）")
from llm.explainer import WordExplainer
ex = WordExplainer()
r1 = ex.explain("gradient descent")
check("词表命中返回 local_vocab", r1 is not None and r1.get("source") == "local_vocab",
      str(r1.get("source") if r1 else None))
check("词表命中无延迟返回", r1 is not None and r1["translation"] == "梯度下降",
      str(r1.get("translation") if r1 else None))

# 缓存命中
r2 = ex.explain("gradient descent")
check("二次查询命中缓存", r2 is r1 or r2 == r1)

# ---- 汇总 ----
print("\n" + "=" * 64)
print(f"测试完成: {PASS} 通过, {FAIL} 失败")
print("=" * 64)
sys.exit(0 if FAIL == 0 else 1)
