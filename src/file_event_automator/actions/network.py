from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlsplit
from typing import Any, Dict, List, Optional
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
        timeout: float = 10.0,
        allowed_domains: Optional[List[str]] = None,
        allow_private_networks: bool = False
    ):
        self.url_template = url_template
        self.method = method.upper()
        self.headers = headers or {}
        self.json_payload = json_payload
        self.timeout = timeout
        self.allowed_domains = [d.lower() for d in allowed_domains] if allowed_domains else None
        self.allow_private_networks = allow_private_networks

    def _validate_url_security(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Esquema de URL no soportado: '{parsed.scheme}'. Solo http y https son permitidos.")

        hostname = parsed.hostname
        if not hostname:
            raise ValueError(f"URL inválida sin hostname: '{url}'")

        # 1. Validar allowlist de dominios si está configurada
        if self.allowed_domains:
            matched = any(
                hostname.lower() == domain or hostname.lower().endswith("." + domain)
                for domain in self.allowed_domains
            )
            if not matched:
                raise PermissionError(
                    f"Dominio '{hostname}' no permitido. Permitidos: {self.allowed_domains}"
                )

        # 2. Protección contra SSRF (IPs privadas, loopback, metadatos en la nube)
        if not self.allow_private_networks:
            lowered = hostname.lower()
            if lowered == "localhost" or lowered.endswith(".localhost"):
                raise PermissionError(f"Bloqueo SSRF: Petición a localhost rechazada.")

            try:
                ip = ipaddress.ip_address(hostname)
                if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
                    raise PermissionError(
                        f"Bloqueo SSRF: Petición a dirección IP interna o privada ({hostname}) rechazada."
                    )
                return
            except ValueError:
                pass

            try:
                addr_info = socket.getaddrinfo(hostname, None)
                if not addr_info:
                    raise ConnectionError(f"Bloqueo SSRF: No se obtuvieron direcciones para '{hostname}'.")
                for info in addr_info:
                    ip_str = info[4][0]
                    ip = ipaddress.ip_address(ip_str)
                    if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
                        raise PermissionError(
                            f"Bloqueo SSRF: Petición a dirección IP interna o privada ({ip_str}) rechazada."
                        )
            except socket.gaierror as e:
                raise ConnectionError(
                    f"Bloqueo SSRF (Fail-Closed): No se pudo resolver de forma segura el host '{hostname}': {e}"
                )

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        url = interpolate_template(self.url_template, context)
        self._validate_url_security(url)

        headers = interpolate_template(self.headers, context)
        json_data = interpolate_template(self.json_payload, context) if self.json_payload is not None else None

        # Inyectar Idempotency-Key automáticamente si no está presente
        if "Idempotency-Key" not in headers:
            event_id = context.get("event_id", "evt")
            action_idx = context.get("action_index", 0)
            headers["Idempotency-Key"] = f"{event_id}_{action_idx}"

        logger.info(f"Enviando Webhook {self.method} a {url} (Idempotency-Key: {headers['Idempotency-Key']})")
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

