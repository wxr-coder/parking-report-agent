import json
from pathlib import Path

import httpx

from app.config import Settings
from app.services.agent import build_narrative, fallback_narrative
from app.services.metrics import compute_metrics


def test_build_narrative_retries_without_response_format_when_model_rejects_it(
    monkeypatch,
) -> None:
    metrics = compute_metrics(Path("sample/data.csv"))
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": "InvalidParameter",
                        "message": (
                            "The parameter `response_format.type` specified in the request "
                            "are not valid: `json_object` is not supported by this model."
                        ),
                        "param": "response_format.type",
                        "type": "BadRequest",
                    }
                },
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "payment_and_channel": "微信交易占比最高，线上支付为主。",
                                    "parking_duration": "平均停车 3.27 小时，存在长时停车异常。",
                                    "observations": [
                                        "零实收交易较多，需关注优惠敞口。",
                                        "微信和会员积分合计占比较高。",
                                    ],
                                    "recommendations": [
                                        "复核零实收交易授权规则。",
                                        "针对主支付方式优化对账流程。",
                                    ],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
            request=request,
            )

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client
    monkeypatch.setattr(
        "app.services.agent.httpx.Client",
        lambda timeout: original_client(transport=transport, timeout=timeout),
    )

    narrative = build_narrative(
        metrics,
        Settings(openai_api_key="test-key", openai_model="deepseek-v4-flash"),
        job_id="test-job",
    )

    assert requests[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in requests[1]
    fallback = fallback_narrative(metrics)
    assert narrative.payment_and_channel == fallback.payment_and_channel
    assert narrative.parking_duration == fallback.parking_duration
    assert narrative.observations == [
        "零实收交易较多，需关注优惠敞口。",
        "微信和会员积分合计占比较高。",
    ]


def test_build_narrative_falls_back_when_llm_returns_numbers(monkeypatch) -> None:
    metrics = compute_metrics(Path("sample/data.csv"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "observations": [
                                        "零实收交易有 12 笔，需要关注优惠敞口。",
                                        "支付结构集中，需要持续复核。",
                                    ],
                                    "recommendations": [
                                        "复核零实收交易授权规则。",
                                        "优化对账流程。",
                                    ],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client
    monkeypatch.setattr(
        "app.services.agent.httpx.Client",
        lambda timeout: original_client(transport=transport, timeout=timeout),
    )

    narrative = build_narrative(
        metrics,
        Settings(openai_api_key="test-key", openai_model="deepseek-v4-flash"),
        job_id="test-job",
    )

    assert narrative == fallback_narrative(metrics)
