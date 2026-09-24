"""P4d：废标检查结果 Excel 导出（签名失效判定 + 五工作表 + 未能识别的图片区块）。

真值来源 `client/electron/services/checkResultExportService.cjs`：
`selectCurrentRejectionResults` :112-137、`buildRejectionWorkbook` :217-334、
`exportRejectionExcel` :506-517（「没有可导出的当前废标检查结果」:511 逐字）。
"""

from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.config import Settings
from app.main import create_app

DOCX_MIME = ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document")


def _docx(tmp_path: Path, name: str, text: str) -> bytes:
    from tests.parsing_fixtures import build_docx

    return build_docx(tmp_path / name, blocks=[("p", text)]).read_bytes()


import pytest


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(_env_file=None, data_root=tmp_path / "data")
    with TestClient(create_app(settings)) as test_client:
        from tests.ai_fakes import ScriptedAI

        # ① 提取 ② 正文定稿 ③ 印章内容识别 ④ 角色判断（按调用顺序排队）
        test_client.app.state.yibiao.ai = ScriptedAI([
            "# 招标文件中明确提到的\n\n## 无效投标\n- 资质不符",
            '{"findings":[]}',
            '{"images":[]}',
            '{"positions":[]}',
        ])
        project = test_client.post("/projects", json={"name": "导出"}).json()["id"]
        yield test_client, project


def _run_flow(client, project: str, tmp_path: Path) -> None:
    client.post(f"/projects/{project}/rejection/documents",
                data={"role": "tender"},
                files=[("files", ("招标.docx", _docx(tmp_path, "t.docx", "招标正文"), DOCX_MIME))])
    client.post(f"/projects/{project}/rejection/documents",
                data={"role": "bid"},
                files=[("files", ("投标.docx", _docx(tmp_path, "b.docx", "投标正文"), DOCX_MIME))])
    from tests.ai_fakes import ScriptedAI

    def _wait(task_id: str, *, rounds: int = 400) -> None:
        for _ in range(rounds):
            if client.get(f"/tasks/{task_id}").json()["status"] in ("success", "error"):
                return

    extract = client.post(f"/projects/{project}/rejection/extract")
    _wait(extract.json()["taskId"])
    # 主体识别：换独立假件（响应形状与队尾不同）
    client.app.state.yibiao.ai = ScriptedAI([
        '{"subject_summary":[],"role_summary":[],"candidates":['
        '{"official_name":"甲公司"}]}'])
    identify = client.post(f"/projects/{project}/rejection/subject/identify")
    _wait(identify.json()["taskId"])
    # 恢复主队列：正文定稿 findings → 印章 images → 角色判断 positions
    client.app.state.yibiao.ai = ScriptedAI([
        '{"findings":[]}',
        '{"images":[]}',
        '{"positions":[]}'])
    client.post(f"/projects/{project}/rejection/subject/confirm",
                json={"officialName": "甲公司"})
    run = client.post(f"/projects/{project}/rejection/run", json={})
    _wait(run.json()["taskId"])


def test_export_without_results_rejected(client):
    client, project = client
    response = client.get(f"/projects/{project}/rejection/export")
    assert response.status_code == 400
    assert response.json()["detail"] == "没有可导出的当前废标检查结果"


def test_export_after_full_flow_returns_five_sheets(client, tmp_path):
    client, project = client
    _run_flow(client, project, tmp_path)

    response = client.get(f"/projects/{project}/rejection/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    workbook = load_workbook(io.BytesIO(response.content))
    assert workbook.sheetnames == ["检查概览", "废标项", "错别字", "逻辑问题", "投标主体"]

    overview = workbook["检查概览"]
    texts = [row[0] for row in overview.iter_rows(values_only=True) if row and row[0]]
    assert "导出时间" in texts and "检查类型" in texts and "主体检查统计项" in texts

    typo_sheet = workbook["错别字"]
    rows = list(typo_sheet.iter_rows(values_only=True))
    assert rows[0][:4] == ("序号", "投标文件序号", "投标文件名", "错误文本")
    # 表头行后为数据行或空（AI 假件返回 {"findings":[]} → 仅表头 + 空行由
    # 「检查概览」的说明列承载），此处只钉结构
    assert len(rows) >= 1

    subject_sheet = workbook["投标主体"]
    assert list(subject_sheet.iter_rows(values_only=True))[0][3] == "来源"


def test_export_stale_after_bid_document_change(client, tmp_path):
    client, project = client
    """跑完检查后换投标文件 → 结果签名失效 → 不可导出（参考 :102-111 的 valid 语义）。"""
    _run_flow(client, project, tmp_path)
    assert client.get(f"/projects/{project}/rejection/export").status_code == 200

    client.post(f"/projects/{project}/rejection/documents",
                data={"role": "bid"},
                files=[("files", ("新投标.docx", _docx(tmp_path, "n.docx", "新投标正文"), DOCX_MIME))])

    response = client.get(f"/projects/{project}/rejection/export")
    assert response.status_code == 400
    assert response.json()["detail"] == "没有可导出的当前废标检查结果"
