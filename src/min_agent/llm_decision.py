from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

import requests

from min_agent.models import TradeDecision

Transport = Callable[[str, dict[str, Any], int], dict[str, Any]]


def parse_decision_json(text: str) -> TradeDecision:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM response did not contain a JSON object")

    payload = json.loads(text[start : end + 1])
    return TradeDecision.model_validate(payload)


class OllamaDecisionEngine:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        gpu_devices: str,
        transport: Transport | None = None,
        timeout: int = 120,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.gpu_devices = gpu_devices
        self.transport = transport or self._requests_transport
        self.timeout = timeout

    def launch_environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env["CUDA_VISIBLE_DEVICES"] = self.gpu_devices
        return env

    def decide(self, context: dict[str, Any]) -> TradeDecision:
        prompt = (
            "You are a paper-trading decision engine. Return exactly one JSON object with keys: "
            "symbol, action, quantity, confidence, rationale. action must be BUY, SELL, or HOLD. "
            "Do not include any order outside the supplied symbol and risk context.\n"
            f"Context: {json.dumps(context, sort_keys=True)}"
        )
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1},
        }
        response = self.transport(f"{self.base_url}/api/generate", payload, self.timeout)
        return parse_decision_json(str(response.get("response", "")))

    @staticmethod
    def _requests_transport(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        return response.json()
