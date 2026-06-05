from decimal import Decimal
from pathlib import Path

from app.services.metrics import compute_metrics


def test_compute_metrics_required_hard_numbers(tmp_path: Path) -> None:
    csv_path = tmp_path / "data.csv"
    csv_path.write_text(
        "\ufeff应收金额,实收金额(元),免费金额(元),充值卡扣费(元),抵扣金额(元),抵扣时长(小时),实际抵扣额(元),支付方式,支付渠道,收费时间,进车时间\n"
        "30,30,0,0,0,0,0,微信,线上支付,2026-04-01 10:00:00,2026-04-01 08:00:00\n"
        "45,0,0,0,45,0,45,会员积分,线上支付,2026-04-01 12:00:00,2026-04-01 09:00:00\n"
        "15,10,0,0,5,0,5,微信,出口贴码,2026-04-01 13:30:00,2026-04-01 13:00:00\n",
        encoding="utf-8",
    )

    metrics = compute_metrics(csv_path)

    assert metrics.total_transactions == 3
    assert metrics.expected_amount == Decimal("90")
    assert metrics.paid_amount == Decimal("40")
    assert metrics.actual_discount_amount == Decimal("50")
    assert metrics.collection_rate_percent == Decimal("44.4")
    assert metrics.main_payment_method == "微信"
    assert metrics.main_payment_method_count == 2


def test_fixture_metrics_match_research_baseline() -> None:
    metrics = compute_metrics(Path("sample/data.csv"))

    assert metrics.total_transactions == 3674
    assert metrics.expected_amount == Decimal("105795")
    assert metrics.paid_amount == Decimal("57397.5")
    assert metrics.actual_discount_amount == Decimal("48285")
    assert metrics.collection_rate_percent == Decimal("54.3")
    assert metrics.main_payment_method == "微信"
    assert metrics.main_payment_method_count == 1435
