import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_WORD_RE = re.compile(r"[\w.-]+", re.UNICODE)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    body: str

    @property
    def trigger_text(self) -> str:
        return f"{self.name} {self.description}".lower()


def _parse_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    metadata: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = _parse_scalar(value)

    return metadata, text[match.end():].strip()


class SkillRegistry:
    """Loads Codex-style skills from SKILL.md files and selects relevant ones."""

    def __init__(
        self,
        roots: Iterable[str],
        include_system: bool = True,
        max_body_chars: int = 20000,
    ):
        self._roots = [Path(root).expanduser() for root in roots if root]
        self._include_system = include_system
        self._max_body_chars = max_body_chars
        self._skills: list[Skill] = []
        self._loaded = False

    def load(self) -> list[Skill]:
        if self._loaded:
            return self._skills

        skills: list[Skill] = []
        seen_paths: set[Path] = set()
        for root in self._roots:
            if not root.exists():
                logger.info("Skill root does not exist: %s", root)
                continue
            if not root.is_dir():
                logger.warning("Skill root is not a directory: %s", root)
                continue
            for skill_file in self._iter_skill_files(root):
                resolved = skill_file.resolve()
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                skill = self._load_skill(skill_file)
                if skill:
                    skills.append(skill)

        self._skills = skills
        self._loaded = True
        logger.info("Loaded %d skills", len(skills))
        return self._skills

    def select(self, message: str, limit: int = 3) -> list[Skill]:
        query_terms = self._terms(message)
        if not query_terms:
            return []

        scored: list[tuple[int, Skill]] = []
        for skill in self.load():
            score = self._score(skill, query_terms, message.lower())
            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda item: (-item[0], item[1].name))
        return [skill for _, skill in scored[:limit]]

    def build_context(self, skills: list[Skill]) -> str:
        if not skills:
            return ""

        blocks = [
            "<skills>",
            "The following Codex-style skills matched the current task. Use their instructions when relevant. "
            "Load referenced files only when needed, and prefer bundled scripts/assets over recreating them.",
        ]
        for skill in skills:
            body = skill.body[: self._max_body_chars]
            if len(skill.body) > self._max_body_chars:
                body += "\n\n[Skill body truncated by configured limit.]"
            blocks.append(
                f"\n<skill name=\"{skill.name}\" path=\"{skill.path}\">\n"
                f"Description: {skill.description}\n\n"
                f"{body}\n"
                "</skill>"
            )
        blocks.append("</skills>")
        return "\n".join(blocks)

    def _iter_skill_files(self, root: Path) -> Iterable[Path]:
        for skill_file in root.glob("*/SKILL.md"):
            yield skill_file
        if self._include_system:
            for skill_file in root.glob(".system/*/SKILL.md"):
                yield skill_file

    def _load_skill(self, skill_file: Path) -> Optional[Skill]:
        try:
            text = skill_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = skill_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Failed to read skill file %s: %s", skill_file, exc)
            return None

        metadata, body = _parse_frontmatter(text)
        name = metadata.get("name") or skill_file.parent.name
        description = metadata.get("description", "")
        if not description:
            logger.warning("Skill %s has no description; it will be harder to trigger", skill_file)

        return Skill(
            name=name,
            description=description,
            path=skill_file,
            body=body,
        )

    def _score(self, skill: Skill, query_terms: set[str], lowered_message: str) -> int:
        score = 0
        skill_name = skill.name.lower()
        if skill_name in lowered_message:
            score += 20
        if skill.name == "cad-drawing-analysis" and self._is_cad_request(lowered_message):
            score += 30
        for trigger in self._literal_triggers(skill.trigger_text):
            if trigger in lowered_message:
                score += 8

        trigger_terms = self._terms(skill.trigger_text)
        overlap = query_terms & trigger_terms
        score += len(overlap)

        # Longer, domain-specific terms are stronger than generic words.
        score += sum(1 for term in overlap if len(term) >= 6)
        return score

    @staticmethod
    def _is_cad_request(message: str) -> bool:
        cad_keywords = [
            "cad",
            "dxf",
            "dwg",
            "制图",
            "画图",
            "图纸",
            "机械图",
            "工程图",
            "流程图",
            "原理图",
            "安装板",
            "孔位",
            "圆角",
            "开孔",
            "通孔",
            "长圆孔",
            "电气控制",
            "工艺流程",
            "控制原理",
        ]
        return any(keyword in message for keyword in cad_keywords)

    @staticmethod
    def _literal_triggers(text: str) -> set[str]:
        triggers: set[str] = set()
        for token in re.split(r"[,;，；、\s]+", text.lower()):
            normalized = token.strip(" ._-")
            if len(normalized) >= 3:
                triggers.add(normalized)
        return triggers

    @staticmethod
    def _terms(text: str) -> set[str]:
        stopwords = {
            "the", "and", "for", "with", "when", "that", "this", "from", "into",
            "use", "using", "asks", "user", "need", "needs", "skill", "skills",
            "file", "files", "create", "update", "build", "make", "help",
        }
        terms: set[str] = set()
        for token in _WORD_RE.findall(text):
            normalized = token.strip("._-").lower()
            if len(normalized) >= 3 and normalized not in stopwords:
                terms.add(normalized)
            for part in re.split(r"[._-]+", normalized):
                if len(part) >= 3 and part not in stopwords:
                    terms.add(part)
        return terms
