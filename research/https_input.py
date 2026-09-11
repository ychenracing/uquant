"""HTTPS-only reads for the registered public research input sources."""
from __future__ import annotations

from http.client import HTTPSConnection
from urllib.parse import urljoin, urlsplit


def read_https(url: str, *, allowed_hosts: tuple[str, ...], timeout: int = 25) -> bytes:
    """Reject non-HTTPS inputs and redirects outside the registered source."""
    for _ in range(6):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in allowed_hosts
                or parsed.port not in {None, 443} or parsed.username is not None
                or parsed.password is not None or parsed.fragment):
            raise ValueError("research input URL is outside its HTTPS source")
        connection = HTTPSConnection(parsed.hostname, timeout=timeout)
        try:
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            connection.request("GET", path)
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ValueError("research input redirect lacks a destination")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f"research input returned HTTP {response.status}")
            return response.read()
        finally:
            connection.close()
    raise ValueError("research input exceeded the HTTPS redirect limit")
