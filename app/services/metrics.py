import csv
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


MONEY_COLUMNS = ("应收金额", "实收金额(元)", "实际抵扣额(元)")
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class LongStay:
    hours: Decimal
    entry_time: datetime
    paid_at: datetime
    payment_method: str


@dataclass(frozen=True)
class MetricsResult:
    total_transactions: int
    expected_amount: Decimal
    paid_amount: Decimal
    actual_discount_amount: Decimal
    collection_rate_percent: Decimal
    main_payment_method: str
    main_payment_method_count: int
    payment_methods: dict[str, int]
    payment_channels: dict[str, int]
    zero_paid_transactions: int
    average_parking_hours: Decimal
    longest_stay: LongStay | None
    duration_buckets: dict[str, int]
    paid_at_min: datetime | None
    paid_at_max: datetime | None
    entry_time_min: datetime | None
    entry_time_max: datetime | None

    def to_grounding_dict(self) -> dict[str, Any]:
        return {
            "total_transactions": self.total_transactions,
            "expected_amount": money_str(self.expected_amount),
            "paid_amount": money_str(self.paid_amount),
            "actual_discount_amount": money_str(self.actual_discount_amount),
            "collection_rate_percent": f"{self.collection_rate_percent:.1f}",
            "main_payment_method": self.main_payment_method,
            "main_payment_method_count": self.main_payment_method_count,
            "payment_methods": self.payment_methods,
            "payment_channels": self.payment_channels,
            "zero_paid_transactions": self.zero_paid_transactions,
            "average_parking_hours": f"{self.average_parking_hours:.2f}",
            "longest_stay": {
                "hours": f"{self.longest_stay.hours:.2f}",
                "entry_time": self.longest_stay.entry_time.isoformat(sep=" "),
                "paid_at": self.longest_stay.paid_at.isoformat(sep=" "),
                "payment_method": self.longest_stay.payment_method,
            }
            if self.longest_stay
            else None,
            "duration_buckets": self.duration_buckets,
            "paid_at_range": _range_text(self.paid_at_min, self.paid_at_max),
            "entry_time_range": _range_text(self.entry_time_min, self.entry_time_max),
        }


def compute_metrics(csv_path: Path) -> MetricsResult:
    payment_methods: Counter[str] = Counter()
    payment_channels: Counter[str] = Counter()
    duration_buckets: Counter[str] = Counter()
    total_transactions = 0
    expected_amount = Decimal("0")
    paid_amount = Decimal("0")
    actual_discount_amount = Decimal("0")
    zero_paid_transactions = 0
    total_parking_hours = Decimal("0")
    duration_count = 0
    longest_stay: LongStay | None = None
    paid_times: list[datetime] = []
    entry_times: list[datetime] = []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        _validate_headers(reader.fieldnames)
        for row in reader:
            total_transactions += 1
            expected = parse_decimal(row["应收金额"])
            paid = parse_decimal(row["实收金额(元)"])
            discount = parse_decimal(row["实际抵扣额(元)"])
            expected_amount += expected
            paid_amount += paid
            actual_discount_amount += discount
            if paid == 0:
                zero_paid_transactions += 1

            method = clean_value(row.get("支付方式")) or "未标记"
            channel = clean_value(row.get("支付渠道")) or "未标记"
            payment_methods[method] += 1
            payment_channels[channel] += 1

            paid_at = parse_datetime(row.get("收费时间"))
            entry_time = parse_datetime(row.get("进车时间"))
            if paid_at:
                paid_times.append(paid_at)
            if entry_time:
                entry_times.append(entry_time)
            if paid_at and entry_time and paid_at >= entry_time:
                hours = Decimal(str((paid_at - entry_time).total_seconds())) / Decimal("3600")
                total_parking_hours += hours
                duration_count += 1
                duration_buckets[_duration_bucket(hours)] += 1
                if longest_stay is None or hours > longest_stay.hours:
                    longest_stay = LongStay(
                        hours=hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                        entry_time=entry_time,
                        paid_at=paid_at,
                        payment_method=method,
                    )

    collection_rate = (
        (paid_amount / expected_amount * Decimal("100")).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
        if expected_amount
        else Decimal("0.0")
    )
    average_hours = (
        (total_parking_hours / duration_count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if duration_count
        else Decimal("0.00")
    )
    main_method, main_count = payment_methods.most_common(1)[0] if payment_methods else ("", 0)

    ordered_buckets = {bucket: duration_buckets.get(bucket, 0) for bucket in bucket_order()}
    return MetricsResult(
        total_transactions=total_transactions,
        expected_amount=expected_amount,
        paid_amount=paid_amount,
        actual_discount_amount=actual_discount_amount,
        collection_rate_percent=collection_rate,
        main_payment_method=main_method,
        main_payment_method_count=main_count,
        payment_methods=dict(payment_methods.most_common()),
        payment_channels=dict(payment_channels.most_common()),
        zero_paid_transactions=zero_paid_transactions,
        average_parking_hours=average_hours,
        longest_stay=longest_stay,
        duration_buckets=ordered_buckets,
        paid_at_min=min(paid_times) if paid_times else None,
        paid_at_max=max(paid_times) if paid_times else None,
        entry_time_min=min(entry_times) if entry_times else None,
        entry_time_max=max(entry_times) if entry_times else None,
    )


def bucket_order() -> tuple[str, ...]:
    return ("<1h", "1-2h", "2-4h", "4-8h", "8-24h", ">=24h")


def clean_value(value: str | None) -> str:
    return (value or "").strip()


def parse_decimal(value: str | None) -> Decimal:
    text = clean_value(value)
    return Decimal(text) if text else Decimal("0")


def parse_datetime(value: str | None) -> datetime | None:
    text = clean_value(value)
    return datetime.strptime(text, TIME_FORMAT) if text else None


def money_str(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{rounded:.2f}"


def _duration_bucket(hours: Decimal) -> str:
    if hours < 1:
        return "<1h"
    if hours < 2:
        return "1-2h"
    if hours < 4:
        return "2-4h"
    if hours < 8:
        return "4-8h"
    if hours < 24:
        return "8-24h"
    return ">=24h"


def _range_text(start: datetime | None, end: datetime | None) -> str | None:
    if start is None or end is None:
        return None
    return f"{start:%Y-%m-%d %H:%M:%S} 至 {end:%Y-%m-%d %H:%M:%S}"


def _validate_headers(fieldnames: list[str] | None) -> None:
    if not fieldnames:
        raise ValueError("CSV is empty or missing headers")
    missing = [column for column in (*MONEY_COLUMNS, "支付方式", "支付渠道", "收费时间", "进车时间") if column not in fieldnames]
    if missing:
        raise ValueError(f"CSV missing required columns: {', '.join(missing)}")
