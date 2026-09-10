from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlsplit
from typing import Any, Dict, List, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from .base import BaseAction, interpolate_template

logger = logging.getLogger("file_event_automator.actions.network")


class _SSRFSafeHTTPConnection(HTTPConnection):
    def _new_conn(self):
        sock = super()._new_conn()
        peer_ip = sock.getpeername()[0]
        ip = ipaddress.ip_address(peer_ip)
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
            sock.close()
            raise PermissionError(
                f"Bloqueo SSRF / DNS Rebinding: Conexión establecida a IP privada rechazada ({peer_ip})"
            )
        return sock


class _SSRFSafeHTTPSConnection(HTTPSConnection):
    def _new_conn(self):
        sock = super()._new_conn()
        peer_ip = sock.getpeername()[0]
        ip = ipaddress.ip_address(peer_ip)
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
            sock.close()
            raise PermissionError(
                f"Bloqueo SSRF / DNS Rebinding: Conexión establecida a IP privada rechazada ({peer_ip})"
            )
        return sock


class _SSRFSafeHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = _SSRFSafeHTTPConnection


class _SSRFSafeHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _SSRFSafeHTTPSConnection


class SSRFSafeAdapter(HTTPAdapter):
    """Adaptador HTTP que intercepta sockets TCP para bloquear IPs privadas post-resolución (Anti-Rebinding)."""
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme["http"] = _SSRFSafeHTTPConnectionPool
        self.poolmanager.pool_classes_by_scheme["https"] = _SSRFSafeHTTPSConnectionPool


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
        session = requests.Session()
        if not self.allow_private_networks:
            session.trust_env = False
            adapter = SSRFSafeAdapter()
            session.mount("http://", adapter)
            session.mount("https://", adapter)

        try:
            resp = session.request(
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
        except requests.exceptions.ConnectionError as ce:
            # Desempaquetar PermissionError si se originó en el adaptador anti-rebinding
            cur: Any = ce
            while cur is not None:
                if isinstance(cur, PermissionError):
                    raise cur
                if getattr(cur, "__cause__", None) and isinstance(cur.__cause__, PermissionError):
                    raise cur.__cause__
                found = False
                for arg in getattr(cur, "args", ()):
                    if isinstance(arg, PermissionError):
                        raise arg
                    if isinstance(arg, tuple):
                        for subarg in arg:
                            if isinstance(subarg, PermissionError):
                                raise subarg
                    if isinstance(arg, Exception):
                        cur = arg
                        found = True
                        break
                if not found:
                    break
            raise
        finally:
            session.close()

