from __future__ import annotations

import json
import logging
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


class OllamaClient:
    def __init__(self, base_url: str, model: str, temperature: float = 0.3):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self._session = requests.Session()

    def generate(self, prompt: str, expect_json: bool = False) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": self.temperature},
        }
        if expect_json:
            payload["format"] = "json"

        try:
            resp = self._session.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json()["response"].strip()
        except requests.RequestException as e:
            logger.error("Ollama request failed: %s", e)
            raise

    def generate_json(self, prompt: str) -> dict[str, Any]:
        raw = self.generate(prompt, expect_json=True)
        # strip any accidental markdown fences
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)

    def health_check(self) -> bool:
        try:
            resp = self._session.get(f"{self.base_url}/api/tags", timeout=5)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def model_available(self) -> bool:
        try:
            resp = self._session.get(f"{self.base_url}/api/tags", timeout=5)
            tags = resp.json().get("models", [])
            return any(m.get("name", "").startswith(self.model.split(":")[0]) for m in tags)
        except Exception:
            return False
