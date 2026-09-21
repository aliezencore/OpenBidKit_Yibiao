from __future__ import annotations

import mammoth

from app.domain.shared.errors import ParsingError
from app.infra.parsing.asset_store import asset_target


def _asset_placeholder(image) -> dict:
    """mammoth 的图片回调：读字节 → `assets/<sha256>` 占位 src（P2a↔P2b 接线）。

    `image.open()` 返回的是 **`contextlib.closing` 包装**，必须 `with` 使用——
    直接 `.read()` 会 `AttributeError: 'closing' object has no attribute 'read'`
    （实测踩过）。读到的字节与 `docx_assets.list_media_files` 读出的
    `word/media/*` 是同一份（都经关系表指向同一个 zip 条目），两侧都用 `sha256`
    → **markdown 的 target 与落盘文件名按构造对齐**（裁定 R-DW-2）。

    `alt` 由 mammoth 的 `img_element` 自行补（`image.alt_text` 为 None 时不加），
    所以典型形态是 `![](assets/<hash>)`。
    """
    with image.open() as stream:
        data = stream.read()
    return {"src": asset_target(data)}


def docx_to_html(path, *, include_images: bool = False) -> str:
    """docx → HTML。mammoth 保留语义（标题/列表/加粗）。

    - `include_images=False`（默认）：图片一律丢弃（P2a 原行为）。
    - `include_images=True`：图片保留为 `<img src="assets/<sha256>">` 占位
      （裁定 R-DW-1/2）——target 由 `asset_store.asset_target` 生成，与
      `save_docx_assets` 落盘的文件名同一哈希。

    裁定史：P2a 的 R14 曾「不带 `include_images` 参数」，理由是 R2 已在
    `docx_to_markdown` 层显式拒绝、带上参数会让 True 分支成为不可达死代码。
    R2 的拒绝前提（P2b 未落地）已消失，故 P2a↔P2b 接线撤销该拒绝并补上参数。
    """
    with open(path, "rb") as handle:
        result = mammoth.convert_to_html(
            handle,
            convert_image=(
                mammoth.images.img_element(_asset_placeholder) if include_images
                else mammoth.images.img_element(lambda image: {"src": ""})
            ),
        )
        # mammoth 对损坏/畸形 docx 不会抛异常，只在 messages 里报告 error；
        # 若不拦截，这里会返回空/近空 HTML，下游查重/废标项会在空文本上静默运行。
        # 警告（非 error）是合法的——mammoth 会对不支持的样式发出 warning。
        if any(getattr(message, "type", None) == "error" for message in result.messages):
            raise ParsingError(
                f"docx 转换失败：{result.messages!r}",
                code="docx-conversion-failed",
                path=str(path),
            )
        html = result.value

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    if not include_images:
        # 图片模式下 `<img>` 已是真占位（src = assets/<hash>），必须保留；
        # 只有不带图的模式才剥掉 mammoth 留下的空 src 占位，避免空链接。
        for img in soup.find_all("img"):
            img.decompose()
    return str(soup)
