import socket
import pytest

_orig_getaddrinfo = socket.getaddrinfo

@pytest.fixture(autouse=True)
def mock_dns_for_test_domains(monkeypatch):
    def patched_getaddrinfo(host, port, *args, **kwargs):
        # Mapeo de dominios de prueba ficticios a una IP publica segura (ej. example.com 93.184.216.34)
        if isinstance(host, str) and (host.endswith('.test') or host in ('api.empresa.com', 'test.webhook.local-public')):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', port or 443))]
        return _orig_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, 'getaddrinfo', patched_getaddrinfo)
