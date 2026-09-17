"""结构等价验收（spec §8.5）。基准 = 生成器声明的结构（裁定 R27）。

每个样本都由本文件按已知规格生成，断言直接对那份规格：
标题数与相对层级、表格数与行列数、段落数量级、页数与页序、编码往返。
不追求与现有 Node 转换器逐字节或逐结构一致——那需要驱动 client/ 的
Node 依赖，脆弱且越界；那部分属一次性人工核对。
"""

from docx import Document
from openpyxl import Workbook

from app.infra.parsing.router import parse_document
from app.infra.parsing.service import save_uploaded_document
from tests.parsing_fixtures import build_docx

# --- 样本规格（断言只引用这些常量，不硬编码魔法数）----------------------
H1_COUNT, H2_COUNT, H3_COUNT = 1, 2, 3
PARAGRAPH_COUNT = 40
TABLE_SHAPES = ((2, 3), (3, 2))          # (行, 列)
SHEETS = (("报价", 2, 3), ("工期", 3, 2), ("备注", 1, 2))  # (表名, 行, 列)
# 页文本用 ASCII：PyMuPDF 的 insert_text 默认 base-14（Helvetica）字体不含
# CJK 字形，中文会被渲染成占位点（·····），PDF 实际不含该文本，get_text 取回
# 的也是点而非原文——那样断言「中文文本在 markdown 里」与解析器无关地必败。
# 这里只验证结构（页数、页序、页间 --- 分隔、无伪影），故改用可渲染的 ASCII。
PDF_PAGES = ("PAGE ONE BODY", "PAGE TWO BODY", "PAGE THREE BODY")

ARTIFACTS = ("base64", "<w:", "xmlns", "&lt;!--TABLE", "<!--TABLE:")


def _build_docx_with_tables(path):
    """按规格造一份含多级标题、多张表、定长段落序列的 docx。

    `build_docx` 只支持单表，故此处直接用 python-docx；规格常量与本文件共享。
    """
    doc = Document()
    for index in range(H1_COUNT):
        doc.add_heading(f"一级标题 {index}", level=1)
    for index in range(H2_COUNT):
        doc.add_heading(f"二级标题 {index}", level=2)
    for index in range(H3_COUNT):
        doc.add_heading(f"三级标题 {index}", level=3)
    for rows, cols in TABLE_SHAPES:
        table = doc.add_table(rows=rows, cols=cols)
        for r in range(rows):
            for c in range(cols):
                table.cell(r, c).text = f"r{r}c{c}"
    doc.add_paragraph("正文块 " * 20)
    for index in range(PARAGRAPH_COUNT - 1):
        doc.add_paragraph(f"段落 {index}")
    doc.save(path)
    return path


def _build_xlsx(path):
    wb = Workbook()
    first = wb.active
    first.title = SHEETS[0][0]
    for name, rows, cols in SHEETS:
        ws = first if name == SHEETS[0][0] else wb.create_sheet(title=name)
        for r in range(rows):
            for c in range(cols):
                ws.cell(row=r + 1, column=c + 1, value=f"{name}{r}{c}")
    wb.save(path)
    return path


def _assert_no_artifacts(markdown: str) -> None:
    for bad in ARTIFACTS:
        assert bad not in markdown, f"解析产物里出现了伪影：{bad}"


def test_docx_structure_survives_parsing(tmp_path):
    source = _build_docx_with_tables(tmp_path / "spec.docx")

    markdown = parse_document(source, provider="local").markdown
    lines = markdown.splitlines()

    # 标题：数量与相对层级。层级深度可与生成器不同，相对顺序必须一致。
    h1 = [line for line in lines if line.startswith("# ")]
    h2 = [line for line in lines if line.startswith("## ")]
    h3 = [line for line in lines if line.startswith("### ")]
    assert len(h1) == H1_COUNT
    assert len(h2) == H2_COUNT
    assert len(h3) == H3_COUNT

    # 表格：数量与列数（GFM 分隔行列数 == 表头列数）
    # 仅当本行含 `|` 且字符全属 {"|","-",":"," "} 才算 GFM 分隔行。
    # 注意：`set(line) <= set("|-: ")` 会把空行也算进来（空集是任何集合的子集），
    # 那样段落之间的空行会被误判成分隔行、列数算成 -1。必须加 `"|" in line` 约束。
    separators = [line for line in lines if line and set(line) <= set("|-: ") and "|" in line]
    assert len(separators) == len(TABLE_SHAPES)
    assert [line.count("|") - 1 for line in separators] == [cols for _, cols in TABLE_SHAPES]

    # 段落：同一量级（±10%）
    body = [line for line in lines if line and not line.startswith(("#", "|"))]
    assert abs(len(body) - PARAGRAPH_COUNT) <= PARAGRAPH_COUNT * 0.10

    _assert_no_artifacts(markdown)


def test_xlsx_structure_survives_parsing(tmp_path):
    source = _build_xlsx(tmp_path / "spec.xlsx")

    markdown = parse_document(source, provider="local").markdown
    lines = markdown.splitlines()

    headings = [line for line in lines if line.startswith("## ")]
    assert len(headings) == len(SHEETS)
    assert [line[3:].strip() for line in headings] == [name for name, _, _ in SHEETS]

    # 仅当本行含 `|` 且字符全属 {"|","-",":"," "} 才算 GFM 分隔行。
    # 注意：`set(line) <= set("|-: ")` 会把空行也算进来（空集是任何集合的子集），
    # 那样段落之间的空行会被误判成分隔行、列数算成 -1。必须加 `"|" in line` 约束。
    separators = [line for line in lines if line and set(line) <= set("|-: ") and "|" in line]
    assert [line.count("|") - 1 for line in separators] == [cols for _, _, cols in SHEETS]

    _assert_no_artifacts(markdown)


def test_pdf_structure_survives_parsing(tmp_path):
    import fitz

    source = tmp_path / "spec.pdf"
    doc = fitz.open()
    for text in PDF_PAGES:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=12)
    doc.save(source)
    doc.close()

    markdown = parse_document(source, provider="local").markdown

    for text in PDF_PAGES:
        assert text in markdown
    # 页序：每页文本出现位置递增，且页间有分隔符
    positions = [markdown.index(text) for text in PDF_PAGES]
    assert positions == sorted(positions)
    assert markdown.count("---") >= len(PDF_PAGES) - 1
    _assert_no_artifacts(markdown)


def test_text_encodings_round_trip(tmp_path):
    """GBK / UTF-8 / 带 BOM 的 UTF-8 都要原样往返（spec §8.5 最后一条）。"""
    gbk_text = "投标文件正文：本项目采用公开招标方式，投标人须按招标文件要求编制投标文件。"
    samples = {
        "gbk.txt": (gbk_text, "gbk"),
        "utf8.txt": ("UTF-8 编码的正文内容。", "utf-8"),
    }
    for name, (text, encoding) in samples.items():
        source = tmp_path / name
        source.write_bytes(text.encode(encoding))
        assert parse_document(source, provider="local").markdown.strip() == text

    bom = tmp_path / "bom.txt"
    bom.write_bytes(b"\xef\xbb\xbf" + "带 BOM 的正文。".encode("utf-8"))
    assert parse_document(bom, provider="local").markdown.strip() == "带 BOM 的正文。"


def test_saved_markdown_matches_parsed_markdown(tmp_path):
    """落盘不得改变内容：文件里的结构断言必须与直接解析同源（spec §8.5 的落盘侧）。"""
    source = _build_docx_with_tables(tmp_path / "spec.docx")
    data_root = tmp_path / "data"

    direct = parse_document(source, provider="local").markdown
    saved = save_uploaded_document(
        data_root=data_root, project_id="p1", source_path=source, role="tender"
    )
    on_disk = (data_root / "projects" / "p1" / "documents" / f"{saved.document_id}.md").read_text(
        encoding="utf-8"
    )

    assert on_disk == direct
    _assert_no_artifacts(on_disk)


def test_docx_heading_depth_is_not_swallowed(tmp_path):
    """退化情形：只有标题、没有正文时，标题不能被当成正文吞掉。"""
    source = build_docx(tmp_path / "heads.docx", blocks=[("h1", "唯一标题")])

    markdown = parse_document(source, provider="local").markdown

    assert any(line.startswith("# ") for line in markdown.splitlines())
