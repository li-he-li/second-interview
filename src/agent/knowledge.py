"""知识库加载与 lexical 检索。

- 按 ``## 标题`` 切分 chunk，生成稳定 ``source_id = <file>#<slug>``。
- 检索采用规则评分（错误码 +5 / 标题 +3 / 安全词 +3 / 普通词 +1 / 同义词 +0.5），
  中文用子串包含匹配，不依赖外部分词或向量库。
- 资料冲突时按优先级：禁止动作 > 安全规则 > 故障排查 > 设备说明。
- 检索不到时返回空，绝不编造来源。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

KNOWLEDGE_DIR = Path("knowledge")

# 冲突优先级：数字越大越优先。
CATEGORY_PRIORITY: dict[str, int] = {
    "forbidden_actions": 4,
    "safety_rules": 3,
    "troubleshooting": 2,
    "device_overview": 1,
}

# 同义词扩展（规范词 -> 别名列表）。
SYNONYMS: dict[str, list[str]] = {
    "机械臂": ["机械手", "robot arm", "机器人"],
    "抓取": ["夹取", "取件", "pick", "grip"],
    "急停": ["e-stop", "estop", "紧急停止", "emergency_stop"],
    "移动": ["move", "运动", "位移"],
    "故障": ["报错", "错误", "error"],
}

# 安全/禁止类加权词。
SAFETY_TERMS = ["最大速度", "越界", "禁止", "绕过", "急停", "高风险", "审批", "危险", "强制执行", "禁用保护"]


@dataclass
class Chunk:
    source_id: str
    category: str
    title: str
    text: str
    keywords: list[str]
    file: str
    priority: int


@dataclass
class Match:
    source: str
    text: str
    score: float
    category: str


def normalize(text: str) -> str:
    """全角转半角、小写、合并空白。"""

    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    return re.sub(r"\s+", " ", text).strip()


def _slug(title: str) -> str:
    s = normalize(title)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^\w\-]", "", s)  # \w 在 Python3 默认含中文
    return s or "section"


def _infer_category(path: Path) -> str:
    return path.stem  # 文件名即 category


def _parse_file(path: Path) -> tuple[list[Chunk], list[str]]:
    category = _infer_category(path)
    priority = CATEGORY_PRIORITY.get(category, 0)
    content = path.read_text(encoding="utf-8")
    chunks: list[Chunk] = []
    warnings: list[str] = []

    parts = re.split(r"^##\s+", content, flags=re.MULTILINE)
    for part in parts[1:]:
        lines = part.splitlines()
        if not lines:
            continue
        title = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        keywords: list[str] = []
        m = re.search(r"^keywords:\s*(.+)$", body, flags=re.MULTILINE)
        if m:
            keywords = [k.strip() for k in m.group(1).split(",") if k.strip()]
            body = re.sub(r"^keywords:\s*.+$", "", body, flags=re.MULTILINE).strip()
        if not title or not body:
            warnings.append(f"kb_warning: empty chunk in {path.name}")
            continue
        chunks.append(
            Chunk(
                source_id=f"{path.name}#{_slug(title)}",
                category=category,
                title=title,
                text=body,
                keywords=keywords,
                file=path.name,
                priority=priority,
            )
        )
    return chunks, warnings


def load_kb(kb_dir: Path = KNOWLEDGE_DIR) -> tuple[list[Chunk], list[str]]:
    """加载知识库；缺失/空/重复 source_id 记录 warning，不抛异常。"""

    chunks: list[Chunk] = []
    warnings: list[str] = []
    if not kb_dir.exists():
        warnings.append(f"kb_warning: knowledge dir missing ({kb_dir})")
        return chunks, warnings
    for md in sorted(kb_dir.glob("*.md")):
        file_chunks, file_warnings = _parse_file(md)
        chunks.extend(file_chunks)
        warnings.extend(file_warnings)
    seen: set[str] = set()
    for c in chunks:
        if c.source_id in seen:
            warnings.append(f"kb_warning: duplicate source_id {c.source_id}")
        seen.add(c.source_id)
    if not chunks:
        warnings.append("kb_warning: knowledge base empty")
    return chunks, warnings


def _score(chunk: Chunk, norm_query: str, error_codes: set[str]) -> float:
    text = normalize(chunk.text)
    kw_norm = [normalize(k) for k in chunk.keywords]
    blob = " ".join([text, normalize(chunk.title), *kw_norm])
    score = 0.0

    for ec in error_codes:
        if ec in blob:
            score += 5.0
    title_norm = normalize(chunk.title)
    if title_norm and title_norm in norm_query:
        score += 3.0
    for term in SAFETY_TERMS:
        if term in blob and term in norm_query:
            score += 3.0
    for k in kw_norm:
        if k and k in norm_query:
            score += 1.0
    for canon, syns in SYNONYMS.items():
        canon_n = normalize(canon)
        syns_n = [normalize(s) for s in syns]
        in_query = canon_n in norm_query or any(s in norm_query for s in syns_n)
        in_blob = canon_n in blob or any(s in blob for s in syns_n)
        if in_query and in_blob:
            score += 0.5
    return score


def _unknown_model_codes(norm_query: str, chunks: list[Chunk]) -> list[str]:
    """查询明确包含未收录设备型号时，避免用通用资料硬匹配。"""

    codes = set(re.findall(r"\b[a-z]{2,}-?\d{2,}\b", norm_query))
    if not codes:
        return []
    blob = " ".join(normalize(" ".join([c.title, c.text, *c.keywords])) for c in chunks)
    return [code for code in codes if code not in blob]


def search(
    query: str,
    chunks: list[Chunk],
    top_k: int = 3,
    min_score: float = 1.0,
) -> list[Match]:
    """lexical 评分检索；低于阈值不返回，绝不编造。"""

    norm = normalize(query)
    if _unknown_model_codes(norm, chunks):
        return []
    error_codes = set(re.findall(r"e\d+", norm))
    scored = [(c, _score(c, norm, error_codes)) for c in chunks]
    scored = [(c, s) for c, s in scored if s > 0]
    scored.sort(key=lambda x: (-x[1], -x[0].priority))
    return [
        Match(source=c.source_id, text=c.text, score=round(s, 2), category=c.category)
        for c, s in scored[:top_k]
        if s >= min_score
    ]


class KnowledgeBase:
    """知识库句柄：持有 chunks，提供检索与 source_id 过滤。"""

    def __init__(self, chunks: list[Chunk], warnings: list[str]) -> None:
        self.chunks = chunks
        self.warnings = warnings
        self._valid = {c.source_id for c in chunks}

    @classmethod
    def load(cls, kb_dir: Path = KNOWLEDGE_DIR) -> "KnowledgeBase":
        chunks, warnings = load_kb(kb_dir)
        return cls(chunks, warnings)

    def search(self, query: str, top_k: int = 3, min_score: float = 1.0) -> list[Match]:
        return search(query, self.chunks, top_k, min_score)

    def filter_sources(self, sources: list[str]) -> list[str]:
        """过滤掉 LLM 编造的 source_id，只保留真实存在的。"""

        return [s for s in sources if s in self._valid]
