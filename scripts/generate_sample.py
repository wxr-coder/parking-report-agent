from pathlib import Path

from app.config import Settings
from app.services.agent import build_narrative
from app.services.charts import create_payment_method_chart
from app.services.metrics import compute_metrics
from app.services.report import render_report


def main() -> None:
    sample_dir = Path("sample")
    data_path = sample_dir / "data.csv"
    template_path = sample_dir / "停车明细分析报告_模板.docx"
    chart_path = sample_dir / "payment_methods.png"
    output_path = sample_dir / "sample_report.docx"

    metrics = compute_metrics(data_path)
    create_payment_method_chart(metrics, chart_path)
    narrative = build_narrative(
        metrics,
        Settings(openai_api_key=None),
        job_id="sample",
        instructions="生成用于提交作业的样例报告。",
    )
    render_report(
        template_path=template_path,
        output_path=output_path,
        metrics=metrics,
        narrative=narrative,
        chart_path=chart_path,
    )
    print(f"Generated {output_path}")


if __name__ == "__main__":
    main()
