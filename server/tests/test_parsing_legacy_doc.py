import pytest

from app.infra.parsing import ParsingError
from app.infra.parsing.legacy_doc import find_soffice, doc_to_markdown


def test_find_soffice_returns_none_or_str():
    # 不能假设测试机装了 LibreOffice；契约是「要么找到要么 None」。
    assert find_soffice() is None or isinstance(find_soffice(), str)


# 裁定 R20：**不要**写「造一个不存在的 .doc 路径再 skip」的用例——它自己构造路径、
# 自己判定不存在、再跳过自己，在任何机器上都不会执行到断言，属零信息量的死用例。
# .doc → docx 的真实转换需要一份真实的 .doc 样本，而本机（以及多数 CI）没有
# LibreOffice，产不出样本，因此**该路径无法在自动化里验证**，如实记为已知缺口
# （见本计划「完成标准」下方），由装了 LibreOffice 的机器手工验证。


def test_missing_libreoffice_raises_parsing_error(tmp_path, monkeypatch):
    monkeypatch.setattr("app.infra.parsing.legacy_doc.find_soffice", lambda: None)
    p = tmp_path / "a.doc"
    p.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 32)

    with pytest.raises(ParsingError) as info:
        doc_to_markdown(p)

    assert info.value.code == "libreoffice-missing"
