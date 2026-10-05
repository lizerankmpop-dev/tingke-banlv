"""
生词复习闭环 —— HTTP 接口测试

重点覆盖两件在路由层面极易出错的事：
  1. **路由顺序**：/vocabulary/stats、/vocabulary/review-queue、/vocabulary/export.csv
     都是静态路径，必须比 /vocabulary/{word} 先声明。声明反了不会报错，
     只会静默地把 "stats" 当成一个单词去查、返回 404，非常难发现。
  2. **CSV 的 UTF-8 BOM**：漏了 BOM，Excel 按 GBK 解码，中文释义全是乱码。
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture(scope="module")
def client():
    # 必须用 with / __enter__ 触发 lifespan，否则 dependencies.db 还是 None
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def course(client):
    resp = client.post(
        "/api/courses",
        json={"name": f"生词测试-{uuid.uuid4().hex[:6]}", "domain": "计算机", "language": "en"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_static_routes_not_shadowed_by_word_param(client, course):
    """静态路径不能被 {word} 吃掉"""
    cid = course["id"]

    stats = client.get(f"/api/courses/{cid}/vocabulary/stats")
    assert stats.status_code == 200, stats.text
    assert "due" in stats.json() and "total" in stats.json()

    queue = client.get(f"/api/courses/{cid}/vocabulary/review-queue")
    assert queue.status_code == 200, queue.text
    assert "items" in queue.json()

    csv_resp = client.get(f"/api/courses/{cid}/vocabulary/export.csv")
    assert csv_resp.status_code == 200, csv_resp.text
    assert csv_resp.headers["content-type"].startswith("text/csv")


def test_collect_review_and_stats_flow(client, course):
    cid = course["id"]
    word = f"gradient{uuid.uuid4().hex[:4]}"

    # 划词采集：带上上下文与字幕时间戳
    collected = client.post(
        f"/api/courses/{cid}/vocabulary/{word}/collect",
        json={
            "translation": "梯度",
            "definition": "the slope of a function",
            "contextSentence": "gradient descent minimizes the loss",
            "sourceSrtTime": 12345,
        },
    )
    assert collected.status_code == 200, collected.text
    entry = collected.json()
    assert entry["word"] == word
    assert entry["contextSentence"] == "gradient descent minimizes the loss"
    assert entry["sourceSrtTime"] == 12345
    assert entry["isMemorized"] is False
    assert entry["efactor"] == pytest.approx(2.5)

    # 重复采集同一个词 → 更新而不是新增
    again = client.post(f"/api/courses/{cid}/vocabulary/{word}/collect", json={"translation": "梯度（更新）"})
    assert again.status_code == 200
    assert again.json()["id"] == entry["id"]

    words = [w["word"] for w in client.get(f"/api/courses/{cid}/vocabulary").json()]
    assert words.count(word) == 1

    # 待复习队列里应该能看到它（新建词 next_review_at=0，天然到期）
    queue = client.get(f"/api/courses/{cid}/vocabulary/review-queue?limit=200").json()["items"]
    assert word in [w["word"] for w in queue]

    # 提交评分：前端 SM2 算好排程后传上来
    now = entry["createdAt"]
    reviewed = client.post(
        f"/api/courses/{cid}/vocabulary/{word}/review",
        json={
            "rating": "good",
            "efactor": 2.6,
            "interval": 3.0,
            "repetitions": 2,
            "nextReviewAt": now + 3 * 86_400_000,
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    updated = reviewed.json()
    assert updated["efactor"] == pytest.approx(2.6)
    assert updated["interval"] == pytest.approx(3.0)
    assert updated["repetitions"] == 2
    assert updated["reviewCount"] == 1
    # 已排到未来 → 应离开待复习队列
    queue2 = client.get(f"/api/courses/{cid}/vocabulary/review-queue?limit=200").json()["items"]
    assert word not in [w["word"] for w in queue2]

    stats = client.get(f"/api/courses/{cid}/vocabulary/stats").json()
    assert stats["total"] >= 1
    assert stats["reviewedToday"] >= 1

    # 404 分支
    assert client.post(
        f"/api/courses/{cid}/vocabulary/never-existed/review",
        json={"rating": "good"},
    ).status_code == 404

    assert client.delete(f"/api/courses/{cid}/vocabulary/{word}").json()["ok"] is True
    assert client.delete(f"/api/courses/{cid}/vocabulary/{word}").status_code == 404


def test_csv_has_utf8_bom_and_headers(client, course):
    cid = course["id"]
    word = f"eigen{uuid.uuid4().hex[:4]}"
    client.post(f"/api/courses/{cid}/vocabulary/{word}/collect", json={"translation": "特征"})

    resp = client.get(f"/api/courses/{cid}/vocabulary/export.csv")
    assert resp.status_code == 200
    raw = resp.content
    assert raw.startswith(b"\xef\xbb\xbf"), "缺 UTF-8 BOM，Excel 打开会乱码"

    text = raw.decode("utf-8-sig")
    header = text.splitlines()[0]
    assert "单词" in header and "下次复习" in header
    assert word in text


def test_backup_roundtrip(client):
    """备份导出 → 导入到另一个课程 → 复习进度完整保留"""
    src = client.post("/api/courses", json={"name": f"备份源-{uuid.uuid4().hex[:6]}"}).json()
    dst = client.post("/api/courses", json={"name": f"备份目标-{uuid.uuid4().hex[:6]}"}).json()

    word = f"backup{uuid.uuid4().hex[:4]}"
    entry = client.post(
        f"/api/courses/{src['id']}/vocabulary/{word}/collect",
        json={"translation": "备份词", "contextSentence": "a backup sentence"},
    ).json()
    future = entry["createdAt"] + 30 * 86_400_000
    client.post(
        f"/api/courses/{src['id']}/vocabulary/{word}/review",
        json={"rating": "easy", "efactor": 2.8, "interval": 30, "repetitions": 4,
              "nextReviewAt": future},
    )

    backup = client.get(f"/api/courses/{src['id']}/vocabulary/backup.json").json()
    assert backup["format"] == "tingkebanlv-vocabulary-backup"
    assert backup["version"] == 1
    assert any(i["word"] == word for i in backup["items"])

    imported = client.post(
        f"/api/courses/{dst['id']}/vocabulary/backup.json", json={"items": backup["items"]}
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["created"] >= 1

    restored = client.get(f"/api/courses/{dst['id']}/vocabulary/{word}").json()
    assert restored["efactor"] == pytest.approx(2.8)
    assert restored["interval"] == pytest.approx(30)
    assert restored["repetitions"] == 4
    assert restored["reviewCount"] == 1
    assert restored["nextReviewAt"] == future
    assert restored["contextSentence"] == "a backup sentence"

    # 导入不能删除目标课程里原有的词
    other = f"keep{uuid.uuid4().hex[:4]}"
    client.post(f"/api/courses/{dst['id']}/vocabulary/{other}/collect", json={})
    client.post(f"/api/courses/{dst['id']}/vocabulary/backup.json", json={"items": backup["items"]})
    words = [w["word"] for w in client.get(f"/api/courses/{dst['id']}/vocabulary").json()]
    assert other in words and word in words

    # 空导入要报 400 而不是静默成功
    assert client.post(
        f"/api/courses/{dst['id']}/vocabulary/backup.json", json={"items": []}
    ).status_code == 400


def test_course_not_found(client):
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/courses/{missing}/vocabulary/stats").status_code == 404
    assert client.get(f"/api/courses/{missing}/vocabulary/review-queue").status_code == 404
    assert client.get(f"/api/courses/{missing}/vocabulary/export.csv").status_code == 404
    assert client.get(f"/api/courses/{missing}/vocabulary/backup.json").status_code == 404


# ---- 跨课程待复习概览（启动提醒用）----


def test_due_summary_is_global(client, course):
    """
    启动提醒发生在「还没选课程」时，所以这个端点不能挂在 /courses/{id} 下面。

    这里同时钉死一个容易回退的点：due-summary 是**静态路径**且不含 course_id，
    一旦有人把它挪到 /courses/{course_id}/vocabulary/due-summary，
    未选课程的场景立刻失效（前端会 404），而单元测试不会发现 ——
    因为它测的是「有课程 id」的路径。
    """
    cid = course["id"]
    word = f"duesum{uuid.uuid4().hex[:6]}"
    client.post(f"/api/courses/{cid}/vocabulary/{word}/collect", json={})

    resp = client.get("/api/vocabulary/due-summary")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # 不能断言具体数字（其它测试也在往库里加词），只验证契约与合理性
    for key in ("due", "total", "reviewedToday", "topCourseId", "topCourseDue", "now"):
        assert key in data, f"缺字段 {key}"
    assert data["due"] >= 1, "刚采集的词 next_review_at=now，必须算作待复习"
    assert data["total"] >= 1
    # 有到期词时 topCourseId 必须指向确实有到期词的那门课
    assert data["topCourseId"]
    assert data["topCourseDue"] >= 1

    # 复习后该词不再到期 → due 下降
    before = data["due"]
    reviewed = client.post(
        f"/api/courses/{cid}/vocabulary/{word}/review",
        json={"efactor": 2.5, "interval": 30, "repetitions": 4,
              "nextReviewAt": data["now"] + 30 * 86400000, "rating": "good"},
    )
    assert reviewed.status_code == 200, reviewed.text
    after = client.get("/api/vocabulary/due-summary").json()
    assert after["due"] == before - 1


def test_due_summary_excludes_memorized(client, course):
    """标记「已掌握」的词必须立刻退出待复习统计，否则提醒会一直响"""
    cid = course["id"]
    word = f"duechk{uuid.uuid4().hex[:6]}"
    entry = client.post(f"/api/courses/{cid}/vocabulary/{word}/collect", json={}).json()
    client.put(f"/api/courses/{cid}/vocabulary/{entry['id']}", json={"isMemorized": True})

    summary = client.get("/api/vocabulary/due-summary").json()
    # 该词不应出现在 due 里：把整库待复习词拉出来确认它不在（避免依赖全局计数）
    queue_ids = {
        item["id"] for c in client.get("/api/courses").json()
        for item in client.get(f"/api/courses/{c['id']}/vocabulary/review-queue?limit=200").json()["items"]
    }
    assert entry["id"] not in queue_ids
    assert summary["due"] == len(queue_ids), "两端统计口径必须一致"
