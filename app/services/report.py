from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from app.services.agent import NarrativeSections
from app.services.metrics import MetricsResult, money_str

if TYPE_CHECKING:
    from docx.document import Document as DocxDocument


def render_report(
    *,
    template_path: Path,
    output_path: Path,
    metrics: MetricsResult,
    narrative: NarrativeSections,
    chart_path: Path,
) -> Path:
    from docx import Document

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = Document(template_path)
    _fill_key_metrics_table(document, metrics)
    _replace_paragraphs(document, metrics, narrative)
    _insert_chart(document, chart_path)
    document.save(output_path)
    return output_path


def _fill_key_metrics_table(document: "DocxDocument", metrics: MetricsResult) -> None:
    values = {
        "1. 总交易笔数": f"{metrics.total_transactions} 笔",
        "2. 应收总金额（元）": f"¥ {money_str(metrics.expected_amount)}",
        "3. 实收总金额（元）": f"¥ {money_str(metrics.paid_amount)}",
        "4. 实际抵扣总额（元）": f"¥ {money_str(metrics.actual_discount_amount)}",
        "5. 实收率（%）": f"{metrics.collection_rate_percent:.1f}%",
        "6. 主要支付方式": f"{metrics.main_payment_method} ×{metrics.main_payment_method_count}",
    }
    for table in document.tables:
        for row in table.rows:
            if not row.cells:
                continue
            key = row.cells[0].text.strip()
            if key in values and len(row.cells) > 1:
                row.cells[1].text = values[key]


def _replace_paragraphs(
    document: "DocxDocument",
    metrics: MetricsResult,
    narrative: NarrativeSections,
) -> None:
    report_period = _report_period(metrics)
    replacements = {
        "数据周期：【起始日期 – 结束日期】": f"数据周期：{report_period}",
        "生成时间：【时间戳】": f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        "【叙述：各支付方式与支付渠道（线上支付 / 出口贴码）的分布情况，指出占主导的方式与渠道。】": narrative.payment_and_channel,
        "【占位图 —— 须由智能体替换为依据实际数据生成的图表。报告至少须包含一张真实图表。】": "",
        "【根据“收费时间 − 进车时间”推算停车时长。报告平均值 / 分布情况及明显的长时停车异常项。可选择再加入一张图表（按小时或时长区间）。】": narrative.parking_duration,
        "【智能体生成的观察内容置于此处 —— \n2\n 至 \n3\n 条简洁、有数据支撑、与管理决策相关的要点。\n读者对象：负责经营本停车业务的管理者。本节由智能体撰写，不设固定公式。应基于数据给出对决策有价值的洞察 —— 例如收入流失、免费 / 优惠敞口、渠道结构变化、异常情况或时段规律等。】": _bullet_text(narrative.observations),
        "【依据上述分析给出 2 至 4 条可执行的建议。\n如无建议，请说明。\n】": _bullet_text(narrative.recommendations),
    }
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        replacement = replacements.get(text)
        if replacement is not None:
            _set_paragraph_text(paragraph, replacement)


def _insert_chart(document: "DocxDocument", chart_path: Path) -> None:
    from docx.shared import Inches

    for paragraph in document.paragraphs:
        if "支付方式分布图" in paragraph.text:
            run = paragraph.insert_paragraph_before().add_run()
            run.add_picture(str(chart_path), width=Inches(5.8))
            return
    document.add_picture(str(chart_path), width=Inches(5.8))


def _set_paragraph_text(paragraph, text: str) -> None:
    paragraph.clear()
    if "\n" not in text:
        paragraph.add_run(text)
        return
    lines = text.splitlines()
    paragraph.add_run(lines[0])
    for line in lines[1:]:
        paragraph.add_run().add_break()
        paragraph.add_run(line)


def _bullet_text(items: list[str]) -> str:
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1))


def _report_period(metrics: MetricsResult) -> str:
    if metrics.paid_at_min and metrics.paid_at_max:
        return f"{metrics.paid_at_min:%Y-%m-%d} 至 {metrics.paid_at_max:%Y-%m-%d}"
    return "未识别"
