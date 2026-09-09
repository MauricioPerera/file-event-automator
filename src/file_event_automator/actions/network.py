from __future__ import annotations

import logging
from typing import Any, Dict, Optional
import requests
from .base import BaseAction, interpolate_template

logger = logging.getLogger("file_event_automator.actions.network")


class WebhookAction(BaseAction):
    def __init__(
        self,
        url_template: str,
        method: str = "POST",
        headers: Optional[Dict[str, str]] = None,
        json_payload: Optional[Any] = None,
        timeout: float = 10.0
    ):
        self.url_template = url_template
        self.method = method.upper()
        self.headers = headers or {}
        self.json_payload = json_payload
        self.timeout = timeout

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        url = interpolate_template(self.url_template, context)
        headers = interpolate_template(self.headers, context)
        json_data = interpolate_template(self.json_payload, context) if self.json_payload is not None else None

        logger.info(f"Enviando Webhook {self.method} a {url}")
        resp = requests.request(
            method=self.method,
            url=url,
            headers=headers,
            json=json_data,
            timeout=self.timeout
        )

        resp.raise_for_status()
        logger.info(f"Webhook {url} respondió exitosamente (HTTP {resp.status_code})")
        context["last_webhook_status"] = resp.status_code
        return context
