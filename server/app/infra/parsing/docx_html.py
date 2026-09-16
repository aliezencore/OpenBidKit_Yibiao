from __future__ import annotations

import mammoth


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
        html = result.value
    # mammoth 的图片转换器只产出空 src 占位，仍会留下 <img>；统一剥掉，避免空链接。
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for img in soup.find_all("img"):
        img.decompose()
    return str(soup)
