"""Unit-тесты SSRF-защиты fetch_url (denylist + резолв IP)."""

import pytest

from src.tools import (
    _block_reason_for_ip,
    _parse_numeric_host,
    _resolve_host_ips,
    fetch_url,
    is_url_blocked,
)

# Публичные хосты для «не блокируется» — резолвятся через DNS.
PUBLIC_URLS = [
    "https://example.com/",
    "https://en.wikipedia.org/wiki/Main_Page",
]


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://127.0.0.1/",
        "http://0.0.0.0/",
        "http://10.0.0.5/",
        "http://172.16.0.1/",
        "http://172.31.255.254/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # AWS metadata
        "http://metadata.google.internal/",
        "http://[::1]/",
    ],
)
def test_denylist_prefixes_blocked(url):
    assert is_url_blocked(url) is not None


@pytest.mark.parametrize(
    "url",
    [
        "http://127.1/",            # короткая форма loopback
        "http://2130706433/",       # decimal IPv4 loopback
        "http://0x7f000001/",       # hex IPv4 loopback
        "http://0177.0.0.1/",       # octal IPv4 loopback
        "http://[0:0:0:0:0:0:0:1]/",  # полный IPv6 loopback
    ],
)
def test_obfuscated_loopback_blocked(url):
    assert is_url_blocked(url) is not None


@pytest.mark.parametrize("url", PUBLIC_URLS)
def test_public_urls_allowed(url):
    assert is_url_blocked(url) is None


def test_fetch_url_blocks_ssrf_target():
    text, status = fetch_url("http://127.0.0.1:80/")
    assert status == "ERROR"
    assert "заблокирован" in text


def test_fetch_url_rejects_non_http():
    text, status = fetch_url("file:///etc/passwd")
    assert status == "ERROR"


def test_resolve_host_ips_loopback_variants():
    assert any(_block_reason_for_ip(ip) for ip in _resolve_host_ips("127.0.0.1"))


@pytest.mark.parametrize(
    "host,expected",
    [
        ("127.0.0.1", "127.0.0.1"),
        ("127.1", "127.0.0.1"),
        ("127.0.1", "127.0.0.1"),
        ("2130706433", "127.0.0.1"),
        ("0x7f000001", "127.0.0.1"),
        ("0177.0.0.1", "127.0.0.1"),
        ("0x7f.0.0.1", "127.0.0.1"),
        ("8.8.8.8", "8.8.8.8"),
    ],
)
def test_parse_numeric_host(host, expected):
    assert _parse_numeric_host(host) == expected


def test_parse_numeric_host_rejects_hostnames():
    assert _parse_numeric_host("example.com") is None
    assert _parse_numeric_host("localhost") is None
