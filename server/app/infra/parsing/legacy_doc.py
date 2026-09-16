from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from app.domain.shared.errors import ParsingError
from app.infra.parsing.docx import docx_to_markdown

_CANDIDATES = (
    "soffice",
    "libreoffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
)


def find_soffice() -> str | None:
    """定位 LibreOffice；对齐现有 findLibreOfficeCommand（convert.mjs:1256）。"""
    for candidate in _CANDIDATES:
        found = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if found:
            return found
    return None


def doc_to_docx(path: Path, output_dir: Path) -> Path:
    soffice = find_soffice()
    if not soffice:
        raise ParsingError(
            "本机未安装 LibreOffice，无法解析 .doc 文件；请安装后重试，或将文件另存为 .docx",
            code="libreoffice-missing",
            path=str(path),
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [soffice, "--headless", "--convert-to", "docx", "--outdir", str(output_dir), str(path)],
        capture_output=True, text=True, timeout=180, check=False,
    )
    produced = output_dir / (path.stem + ".docx")
    if completed.returncode != 0 or not produced.exists():
        raise ParsingError(
            "LibreOffice 转换 .doc 失败",
            code="office-conversion-failed",
            path=str(path),
        )
    return produced


def doc_to_markdown(path: Path) -> str:
    """.doc → LibreOffice → docx → markdown。对齐 withLegacyWordDocxFile。"""
    path = Path(path)
    with tempfile.TemporaryDirectory(prefix="yibiao-doc-") as tmp:
        produced = doc_to_docx(path, Path(tmp))
        return docx_to_markdown(produced)
