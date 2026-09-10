"""Untrusted source links must fail before any network request."""
import pytest

from research.https_input import read_https


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://q.stock.sohu.com/history",
    "http://q.stock.sohu.com/history",
    "https://unregistered.example/history",
    "https://q.stock.sohu.com:444/history",
    "https://name:password@q.stock.sohu.com/history",
    "https://q.stock.sohu.com/history#fragment",
])
def test_input_source_rejects_unregistered_transport(url):
    with pytest.raises(ValueError, match="outside its HTTPS source"):
        read_https(url, allowed_hosts=("q.stock.sohu.com",))
