"""M2 知识库检索测试。"""

from __future__ import annotations

from agent.knowledge import KnowledgeBase, normalize


def test_kb_loads_without_structural_warnings():
    kb = KnowledgeBase.load()
    assert len(kb.chunks) >= 10  # 四个文件，每个多 chunk
    # 不应有空 chunk / 重复 source_id
    assert not any("empty chunk" in w for w in kb.warnings)
    assert not any("duplicate source_id" in w for w in kb.warnings)


def test_source_id_format_stable():
    kb = KnowledgeBase.load()
    ids = [c.source_id for c in kb.chunks]
    assert all("#" in sid and sid.endswith(".md") is False for sid in ids)
    # 真实文件名前缀
    assert any(sid.startswith("troubleshooting.md#") for sid in ids)


def test_error_code_e42_hits_troubleshooting():
    kb = KnowledgeBase.load()
    matches = kb.search("设备报错 E42，应该怎么排查？")
    assert matches, "E42 应有命中"
    assert "e42" in matches[0].source.lower()
    assert matches[0].category == "troubleshooting"
    assert matches[0].score >= 5.0  # 错误码精确匹配加分


def test_max_speed_and_out_of_range_hits_forbidden():
    kb = KnowledgeBase.load()
    matches = kb.search("以最大速度直接移动到 x=9999, y=9999, z=9999")
    assert matches
    # 最高分命中应为 forbidden_actions
    assert matches[0].category == "forbidden_actions"


def test_pick_action_hits_relevant():
    kb = KnowledgeBase.load()
    matches = kb.search("机械臂现在可以执行抓取动作吗？")
    assert matches
    cats = {m.category for m in matches}
    # 应命中设备说明或安全规则（抓取前提/状态）
    assert cats & {"device_overview", "safety_rules"}


def test_irrelevant_query_returns_empty():
    kb = KnowledgeBase.load()
    matches = kb.search("今天午饭吃什么")
    assert matches == []  # 无关查询不应编造来源


def test_filter_sources_rejects_fabricated():
    kb = KnowledgeBase.load()
    real = kb.chunks[0].source_id
    fabricated = "troubleshooting.md#nonexistent-fake"
    result = kb.filter_sources([real, fabricated, "totally_made_up.md#x"])
    assert real in result
    assert fabricated not in result
    assert "totally_made_up.md#x" not in result


def test_normalize_fullwidth_to_halfwidth():
    assert normalize("Ｅ４２") == "e42"
    assert normalize("  多余   空格  ") == "多余 空格"
