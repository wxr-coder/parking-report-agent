import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.services.metrics import MetricsResult


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NarrativeSections:
    payment_and_channel: str
    parking_duration: str
    observations: list[str]
    recommendations: list[str]


def build_narrative(
    metrics: MetricsResult,
    settings: Settings,
    *,
    job_id: str,
    instructions: str | None = None,
) -> NarrativeSections:
    fallback = fallback_narrative(metrics)
    if not settings.openai_api_key:
        logger.info("llm_call_skipped", extra={"event": "llm_call_skipped", "job_id": job_id, "reason": "missing_api_key"})
        return fallback

    messages = [
        {
            "role": "system",
            "content": (
                "You write concise qualitative Chinese management report bullets for a parking operation. "
                "Use only the JSON facts provided by the user. Do not invent or recalculate hard metrics. "
                "Do not include numbers, percentages, dates, amounts, or counts in your text. "
                "Return strict JSON with keys: observations and recommendations. "
                "observations must contain 2-3 strings; recommendations must contain 2-4 strings."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "facts": metrics.to_grounding_dict(),
                    "optional_user_instructions": instructions or "",
                },
                ensure_ascii=False,
            ),
        },
    ]
    payload = {
        "model": settings.openai_model,
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    started = time.perf_counter()
    response_format_retry = False
    try:
        with httpx.Client(timeout=30) as client:
            response = _post_chat_completion(client, settings, payload)
            if _is_unsupported_response_format(response):
                response_format_retry = True
                retry_payload = dict(payload)
                retry_payload.pop("response_format", None)
                response = _post_chat_completion(client, settings, retry_payload)
            response.raise_for_status()
        latency_ms = round((time.perf_counter() - started) * 1000)
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        sections = _parse_qualitative_sections(parsed, fallback)
        logger.info(
            "llm_call",
            extra={
                "event": "llm_call",
                "job_id": job_id,
                "model": settings.openai_model,
                "latency_ms": latency_ms,
                "success": True,
                "response_format_retry": response_format_retry,
            },
        )
        return sections
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000)
        logger.exception(
            "llm_call_failed",
            extra={
                "event": "llm_call",
                "job_id": job_id,
                "model": settings.openai_model,
                "latency_ms": latency_ms,
                "success": False,
                "response_format_retry": response_format_retry,
                "error_type": type(exc).__name__,
            },
        )
        return fallback


def _post_chat_completion(
    client: httpx.Client,
    settings: Settings,
    payload: dict[str, Any],
) -> httpx.Response:
    return client.post(
        f"{settings.openai_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json=payload,
    )


def _is_unsupported_response_format(response: httpx.Response) -> bool:
    if response.status_code != 400:
        return False
    try:
        error = response.json().get("error", {})
    except ValueError:
        return False
    message = str(error.get("message", ""))
    param = str(error.get("param", ""))
    return param == "response_format.type" and "not supported" in message


def fallback_narrative(metrics: MetricsResult) -> NarrativeSections:
    channels = "；".join(f"{name} {count} 笔" for name, count in metrics.payment_channels.items())
    methods = "；".join(f"{name} {count} 笔" for name, count in metrics.payment_methods.items())
    duration = "；".join(f"{bucket} {count} 笔" for bucket, count in metrics.duration_buckets.items())
    longest = (
        f"最长停车 {metrics.longest_stay.hours:.2f} 小时，支付方式为{metrics.longest_stay.payment_method}。"
        if metrics.longest_stay
        else "未发现可计算的停车时长记录。"
    )
    return NarrativeSections(
        payment_and_channel=(
            f"支付方式分布为：{methods}。主要支付方式是{metrics.main_payment_method}"
            f"（{metrics.main_payment_method_count} 笔）。支付渠道分布为：{channels}。"
        ),
        parking_duration=(
            f"平均停车时长约 {metrics.average_parking_hours:.2f} 小时；时长区间分布为：{duration}。{longest}"
        ),
        observations=[
            f"零实收交易 {metrics.zero_paid_transactions} 笔，实际抵扣总额 {metrics.actual_discount_amount:.2f} 元，需要关注优惠和积分核销敞口。",
            f"实收率为 {metrics.collection_rate_percent:.1f}%，应结合支付方式结构复核收入转化与减免策略。",
            f"{metrics.main_payment_method}是交易笔数最高的支付方式，可作为支付入口和运营引导的重点渠道。",
        ],
        recommendations=[
            "定期复核优惠券、会员积分等零实收交易的授权和核销规则。",
            "对长时停车记录建立运营抽查清单，识别长期占位和异常减免风险。",
            "围绕主要支付方式优化入口提示和对账流程，降低人工核对成本。",
        ],
    )


def _parse_qualitative_sections(payload: dict[str, Any], fallback: NarrativeSections) -> NarrativeSections:
    observations = _string_list(payload.get("observations"), min_count=2, max_count=3)
    recommendations = _string_list(payload.get("recommendations"), min_count=2, max_count=4)
    return NarrativeSections(
        payment_and_channel=fallback.payment_and_channel,
        parking_duration=fallback.parking_duration,
        observations=observations,
        recommendations=recommendations,
    )


def _string_list(value: Any, *, min_count: int, max_count: int) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("LLM response list field is not a list")
    items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if not min_count <= len(items) <= max_count:
        raise ValueError("LLM response list field has invalid item count")
    for item in items:
        _reject_hard_metric_text(item)
    return items


def _reject_hard_metric_text(value: str) -> None:
    if any(char.isdigit() for char in value):
        raise ValueError("LLM response contains numeric text")
