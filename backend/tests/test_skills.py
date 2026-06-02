from pathlib import Path

from app.domain.services.skills import SkillRegistry


def write_skill(root: Path, name: str, description: str, body: str, system: bool = False) -> Path:
    skill_dir = root / (".system" if system else "") / name
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}",
        encoding="utf-8",
    )
    return skill_file


def test_loads_codex_style_skills(tmp_path: Path):
    write_skill(
        tmp_path,
        "doc-to-cad-dxf",
        "Convert engineering documents into CAD-importable DXF files.",
        "# Doc To CAD\n\nUse ezdxf.",
    )
    write_skill(
        tmp_path,
        "openai-docs",
        "Use official OpenAI documentation for API questions.",
        "# OpenAI Docs\n\nSearch docs first.",
        system=True,
    )

    registry = SkillRegistry([str(tmp_path)])

    skills = registry.load()

    assert {skill.name for skill in skills} == {"doc-to-cad-dxf", "openai-docs"}
    assert all(skill.path.name == "SKILL.md" for skill in skills)


def test_selects_relevant_skills_by_name_and_description(tmp_path: Path):
    write_skill(
        tmp_path,
        "doc-to-cad-dxf",
        "Convert document-based engineering assignments into editable CAD-importable DXF files.",
        "# Doc To CAD\n\nDXF workflow.",
    )
    write_skill(
        tmp_path,
        "skill-creator",
        "Create or update Codex skills.",
        "# Skill Creator\n\nSkill workflow.",
        system=True,
    )

    registry = SkillRegistry([str(tmp_path)])

    selected = registry.select("请把这个工程文档转换成 CAD DXF 图纸")

    assert [skill.name for skill in selected] == ["doc-to-cad-dxf"]


def test_build_context_includes_only_selected_skill_body(tmp_path: Path):
    write_skill(
        tmp_path,
        "doc-to-cad-dxf",
        "Convert engineering documents into DXF.",
        "# Doc To CAD\n\nImportant DXF instructions.",
    )
    write_skill(
        tmp_path,
        "imagegen",
        "Generate raster images.",
        "# Imagegen\n\nImage instructions.",
        system=True,
    )
    registry = SkillRegistry([str(tmp_path)])

    selected = registry.select("make a dxf from this cad assignment")
    context = registry.build_context(selected)

    assert "Important DXF instructions" in context
    assert "Image instructions" not in context
    assert '<skill name="doc-to-cad-dxf"' in context
