from __future__ import annotations

import hashlib
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.domain.shared.errors import ParsingError
from app.infra.parsing.router import parse_document

# 安全单段路径：大小写字母、数字、`. _ -`，非空。`.`/`..` 为特殊路径分量，
# 会触发目录穿越，单独排除（裁定 P2a 终审 F1）。
_SAFE_PROJECT_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_SPECIAL_PATH_COMPONENTS = frozenset({"", ".", ".."})


@dataclass(frozen=True)
class SavedDocument:
    """落盘结果。字段与域表要写的列一一对应（spec §5.2），由调用方落库。"""

    document_id: str
    project_id: str
    role: str
    source: str
    file_name: str
    source_path: str
    markdown_path: str
    content_hash: str
    content_chars: int
    parser_label: str
    format_kind: str
    extension: str


def save_uploaded_document(
    *,
    data_root: Path,
    project_id: str,
    source_path: Path,
    role: str,
    source: str = "upload",
    provider: str = "local",
) -> SavedDocument:
    """解析 → 落盘 markdown → 复制原件 → 返回值对象。**不写数据库**（裁定 R24）。

    顺序很关键（裁定 R24）：**先解析、后落盘**。解析失败时抛 `ParsingError`，
    此时一个字节都没写过，因此不会留下半成品目录——`test_parse_failure_leaves_no_artifacts`
    钉住这一点。若反过来先建目录再解析，失败就会留下空目录与    已复制的原件。
    """
    _validate_project_id(project_id)
    data_root = Path(data_root)
    source_path = Path(source_path)

    # 1) 解析（失败即抛，此时尚未写任何文件）
    parsed = parse_document(source_path, provider=provider)

    # 2) 生成 id 与目标路径
    document_id = uuid.uuid4().hex[:12]
    project_dir = data_root / "projects" / project_id
    documents_dir = project_dir / "documents"
    uploads_dir = project_dir / "uploads"
    documents_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    markdown_path = documents_dir / f"{document_id}.md"
    copied_path = uploads_dir / f"{document_id}{parsed.extension}"

    # 3) 落盘：正文与原件副本
    markdown_path.write_text(parsed.markdown, encoding="utf-8", newline="\n")
    shutil.copyfile(source_path, copied_path)

    return SavedDocument(
        document_id=document_id,
        project_id=project_id,
        role=role,
        source=source,
        file_name=source_path.name,
        source_path=str(copied_path),
        markdown_path=str(markdown_path),
        content_hash=hashlib.sha256(parsed.markdown.encode("utf-8")).hexdigest(),
        content_chars=len(parsed.markdown),
        parser_label=parsed.parser_label,
        format_kind=parsed.format_kind,
        extension=parsed.extension,
    )


def _validate_project_id(project_id: str) -> None:
    """project_id 必须是一个安全的单段路径，不得触发目录穿越（裁定 P2a 终审 F1）。

    P3/P4 的上传端点会把 URL 路径参数喂进来，这里进行一次性的净化，而不是指望
    每个未来端点都记得。拒绝：空串、含 `..`/分隔符/绝对路径的非法字符、以及 `.`/`..`
    这类特殊路径分量。不静默改写 id。
    """
    if not isinstance(project_id, str) or project_id in _SPECIAL_PATH_COMPONENTS:
        raise ParsingError(
            f"非法的 project_id：{project_id!r}",
            code="invalid-project-id",
        )
    if not _SAFE_PROJECT_ID.match(project_id):
        raise ParsingError(
            f"非法的 project_id：{project_id!r}（只允许字母、数字、. _ -）",
            code="invalid-project-id",
        )
