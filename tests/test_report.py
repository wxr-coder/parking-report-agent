from pathlib import Path
from zipfile import ZipFile

from docx import Document

from app.services.agent import NarrativeSections
from app.services.charts import create_payment_method_chart
from app.services.metrics import compute_metrics
from app.services.report import render_report


def test_render_report_replaces_template_chart_and_llm_sections(tmp_path: Path) -> None:
    metrics = compute_metrics(Path("sample/data.csv"))
    chart_path = create_payment_method_chart(metrics, tmp_path / "payment_methods.png")
    output_path = tmp_path / "generated_report.docx"
    narrative = NarrativeSections(
        payment_and_channel="微信支付占主导，出口贴码交易集中。",
        parking_duration="平均停车时长 2.10 小时，长时停车记录需要复核。",
        observations=[
            "零实收交易形成可核查的收入流失敞口。",
            "主支付方式集中度较高，适合重点优化入口体验。",
        ],
        recommendations=[
            "建立零实收交易抽查清单。",
            "按支付方式拆分对账异常并每周复盘。",
        ],
    )

    render_report(
        template_path=Path("sample/停车明细分析报告_模板.docx"),
        output_path=output_path,
        metrics=metrics,
        narrative=narrative,
        chart_path=chart_path,
    )

    document = Document(output_path)
    report_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    media_files = [
        name for name in ZipFile(output_path).namelist() if name.startswith("word/media/")
    ]

    assert len(document.inline_shapes) == 1
    assert len(media_files) == 1
    assert "【占位图" not in report_text
    assert "【智能体生成的观察内容置于此处" not in report_text
    assert "【依据上述分析给出" not in report_text
    assert "1. 零实收交易形成可核查的收入流失敞口。" in report_text
    assert "2. 主支付方式集中度较高，适合重点优化入口体验。" in report_text
    assert "1. 建立零实收交易抽查清单。" in report_text
    assert "2. 按支付方式拆分对账异常并每周复盘。" in report_text
