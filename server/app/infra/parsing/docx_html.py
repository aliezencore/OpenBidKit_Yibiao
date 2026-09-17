from __future__ import annotations

import mammoth

from app.domain.shared.errors import ParsingError


def docx_to_html(path) -> str:
    """docx → HTML。mammoth 保留语义（标题/列表/加粗），丢弃样式与图片。

    裁定 R14：不带 `include_images` 参数。P2a 里 `docx_to_markdown` 对
    `include_images=True` 已显式拒绝（R2），带上该参数会让 True 分支成为
    不可达死代码。P2b 做真实图片占位时再按需要加参数。
    """
    with open(path, "rb") as handle:
        result = mammoth.convert_to_html(
            handle,
            convert_image=mammoth.images.img_element(lambda image: {"src": ""}),
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
    # mammoth 的图片转换器只产出空 src 占位，仍会留下 <img>；统一剥掉，避免空链接。
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for img in soup.find_all("img"):
        img.decompose()
    return str(soup)
