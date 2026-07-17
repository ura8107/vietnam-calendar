"""Safe, bounded RSS retrieval and deterministic entry normalization."""

from __future__ import annotations

import asyncio
import calendar
import hashlib
import html
import ipaddress
import json
import re
import socket
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import feedparser
import httpcore
import httpx

from ...config import Settings
from .tuoitre import normalize_tuoitre_rss_dates

Resolver = Callable[[str, int], Awaitable[list[str]]]
TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


class FeedError(Exception):
    code = "feed_error"
    retryable = False

    def __init__(self, message: str, *, retry_after: int | None = None, http_status: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.http_status = http_status


class UnsafeFeedUrl(FeedError): code = "unsafe_feed_url"
class FeedTooLarge(FeedError): code = "feed_too_large"
class InvalidFeed(FeedError):
    code = "invalid_feed"
    def __init__(self, message: str, *, total: int = 0, rejected: int = 0):
        super().__init__(message); self.total = total; self.rejected = rejected
class TransientFeedError(FeedError):
    code = "feed_transient"
    retryable = True


class TotalFetchTimeout(TransientFeedError): code = "feed_total_timeout"


@dataclass(frozen=True)
class FetchResult:
    status_code: int
    body: bytes | None
    etag: str | None
    last_modified: str | None
    final_url: str


@dataclass(frozen=True)
class NormalizedEntry:
    source_guid: str | None
    source_url: str
    normalized_url: str
    identity_key: str
    title_raw: str
    summary_raw: str | None
    author_raw: str | None
    title_normalized: str
    summary_text: str | None
    image_url: str | None
    published_at: datetime | None
    updated_at_source: datetime | None
    date_source: str
    content_hash: str
    raw_entry: dict[str, Any]


@dataclass(frozen=True)
class ParseResult:
    entries: list[NormalizedEntry]
    total: int
    accepted: int
    rejected: int
    warning: str | None


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({item[4][0] for item in infos})


def _is_global_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not any((address.is_private, address.is_loopback, address.is_link_local,
                    address.is_multicast, address.is_reserved, address.is_unspecified))


async def validate_feed_url(url: str, allowed_hosts: frozenset[str], resolver: Resolver = system_resolver) -> list[str]:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username is not None or parts.password is not None:
        raise UnsafeFeedUrl("feed URL must be credential-free HTTPS")
    try:
        port = parts.port or 443
    except ValueError as exc:
        raise UnsafeFeedUrl("feed URL has an invalid port") from exc
    host = parts.hostname.lower().rstrip(".")
    if host not in allowed_hosts or port != 443:
        raise UnsafeFeedUrl("feed host or port is not allowlisted")
    try:
        addresses = await resolver(host, port)
    except (OSError, socket.gaierror) as exc:
        raise TransientFeedError("feed hostname could not be resolved") from exc
    if not addresses or any(not _is_global_address(address) for address in addresses):
        raise UnsafeFeedUrl("feed hostname resolved to a non-public address")
    return addresses


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Pin TCP to a validated IP; TLS still receives the original hostname/SNI."""
    def __init__(self, hostname: str, address: str, delegate: httpcore.AsyncNetworkBackend | None = None):
        self.hostname, self.address = hostname.lower().rstrip("."), address
        self.delegate = delegate or httpcore.AnyIOBackend()

    async def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                          local_address: str | None = None, socket_options=None):
        if host.lower().rstrip(".") != self.hostname:
            raise httpcore.ConnectError("network backend refused an unvalidated hostname")
        return await self.delegate.connect_tcp(self.address, port, timeout=timeout,
                                               local_address=local_address, socket_options=socket_options)

    async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options=None):
        raise httpcore.ConnectError("unix sockets are disabled for RSS")

    async def sleep(self, seconds: float) -> None: await self.delegate.sleep(seconds)


class _CoreResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream: Any): self.stream = stream
    async def __aiter__(self):
        async for part in self.stream: yield part
    async def aclose(self) -> None: await self.stream.aclose()


class PinnedHTTPTransport(httpx.AsyncBaseTransport):
    """HTTPX transport backed by httpcore's supported custom network backend."""
    def __init__(self, hostname: str, address: str, *, max_connections: int = 10,
                 backend: httpcore.AsyncNetworkBackend | None = None):
        network = PinnedNetworkBackend(hostname, address, backend)
        self.pool = httpcore.AsyncConnectionPool(
            ssl_context=httpcore.default_ssl_context(), max_connections=max_connections,
            max_keepalive_connections=0, network_backend=network,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(scheme=request.url.raw_scheme, host=request.url.raw_host,
                             port=request.url.port, target=request.url.raw_path),
            headers=request.headers.raw, content=request.stream, extensions=request.extensions,
        )
        try: response = await self.pool.handle_async_request(core_request)
        except httpcore.TimeoutException as exc: raise httpx.TimeoutException(str(exc), request=request) from exc
        except (httpcore.NetworkError, httpcore.ProtocolError) as exc: raise httpx.TransportError(str(exc), request=request) from exc
        return httpx.Response(response.status, headers=response.headers,
                              stream=_CoreResponseStream(response.stream), extensions=response.extensions,
                              request=request)

    async def aclose(self) -> None: await self.pool.aclose()


def parse_retry_after(value: str | None, now: datetime | None = None) -> int | None:
    if not value:
        return None
    try:
        seconds = int(value)
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None: target = target.replace(tzinfo=UTC)
            seconds = int((target - (now or datetime.now(UTC))).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
    return min(3600, max(0, seconds))


class SafeFeedClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None,
                 resolver: Resolver = system_resolver):
        self.settings = settings
        self.resolver = resolver
        timeout = httpx.Timeout(connect=settings.rss_connect_timeout, read=settings.rss_read_timeout,
                                write=settings.rss_write_timeout, pool=settings.rss_pool_timeout)
        self.timeout = timeout
        self.transport = transport
        self.client = (httpx.AsyncClient(timeout=timeout, limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                                         follow_redirects=False, trust_env=False, transport=transport)
                       if transport is not None else None)

    async def aclose(self) -> None:
        if self.client is not None: await self.client.aclose()

    async def fetch(self, url: str, *, etag: str | None = None, last_modified: str | None = None) -> FetchResult:
        try:
            async with asyncio.timeout(self.settings.rss_total_timeout):
                return await self._fetch(url, etag=etag, last_modified=last_modified)
        except TimeoutError as exc:
            raise TotalFetchTimeout("feed exceeded total fetch deadline") from exc

    async def _fetch(self, url: str, *, etag: str | None = None, last_modified: str | None = None) -> FetchResult:
        headers = {"User-Agent": self.settings.rss_user_agent + (f" ({self.settings.rss_contact})" if self.settings.rss_contact else ""),
                   "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.1"}
        if etag: headers["If-None-Match"] = etag
        if last_modified: headers["If-Modified-Since"] = last_modified
        current = url
        for redirects in range(4):
            addresses = await validate_feed_url(current, self.settings.allowed_rss_hosts, self.resolver)
            parts = urlsplit(current); hostname = parts.hostname or ""
            owned = self.client is None
            client = self.client or httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=False, trust_env=False,
                transport=PinnedHTTPTransport(hostname, addresses[0]))
            try:
                async with client.stream("GET", current, headers=headers) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if redirects == 3: raise UnsafeFeedUrl("feed exceeded three redirects")
                        location = response.headers.get("location")
                        if not location: raise UnsafeFeedUrl("feed redirect omitted Location")
                        current = urljoin(str(response.url), location)
                        continue
                    if response.status_code == 304:
                        return FetchResult(304, None, response.headers.get("etag"), response.headers.get("last-modified"), str(response.url))
                    if response.status_code == 429 or 500 <= response.status_code <= 599:
                        raise TransientFeedError("feed source temporarily unavailable", retry_after=parse_retry_after(response.headers.get("retry-after")), http_status=response.status_code)
                    if response.status_code != 200:
                        raise FeedError("feed source returned a permanent HTTP error", http_status=response.status_code)
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > self.settings.rss_max_body_bytes:
                        raise FeedTooLarge("feed response exceeded configured size")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self.settings.rss_max_body_bytes:
                            raise FeedTooLarge("feed response exceeded configured size")
                    return FetchResult(200, bytes(body), response.headers.get("etag"), response.headers.get("last-modified"), str(response.url))
            except httpx.TimeoutException as exc:
                raise TransientFeedError("feed request timed out") from exc
            except httpx.TransportError as exc:
                raise TransientFeedError("feed transport failed") from exc
            finally:
                if owned: await client.aclose()
        raise AssertionError("unreachable")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None: super().__init__(convert_charrefs=True); self.parts: list[str] = []
    def handle_data(self, data: str) -> None: self.parts.append(data)


def html_to_text(value: str | None) -> str | None:
    if not value: return None
    parser = _TextExtractor()
    try: parser.feed(value)
    except Exception: return None
    result = " ".join("".join(parser.parts).split())
    return result or None


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", html.unescape(value)).split())


def normalize_article_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("article URL is invalid")
    host = parts.hostname.lower().rstrip(".")
    port = parts.port
    netloc = host if port is None or (parts.scheme.lower() == "https" and port == 443) or (parts.scheme.lower() == "http" and port == 80) else f"{host}:{port}"
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not k.lower().startswith("utm_") and k.lower() not in TRACKING_KEYS])
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict): return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None: return value
    return str(value)


def _parsed_date(entry: Any, key: str) -> datetime | None:
    value = entry.get(f"{key}_parsed")
    if not value: return None
    try: return datetime.fromtimestamp(calendar.timegm(value), UTC)
    except (ValueError, OverflowError, TypeError): return None


def parse_feed(raw: bytes, fetched_at: datetime, *, max_entries: int = 500,
               max_raw_entry_bytes: int = 65536) -> ParseResult:
    parsed = feedparser.parse(normalize_tuoitre_rss_dates(raw))
    entries = list(parsed.entries)
    bozo_message = str(parsed.get("bozo_exception"))[:500] if parsed.get("bozo") else None
    if parsed.get("bozo") and not entries:
        raise InvalidFeed("feed XML was invalid and yielded no entries", total=0, rejected=0)
    normalized: list[NormalizedEntry] = []; rejected = max(0, len(entries) - max_entries)
    for entry in entries[:max_entries]:
        try:
            title = str(entry.get("title") or "").strip()
            link = str(entry.get("link") or "").strip()
            if not title or not link or len(title) > 1000 or len(link) > 4096:
                rejected += 1; continue
            normalized_url = normalize_article_url(link)
            guid = (str(entry.get("id") or "").strip() or None)
            if guid: guid = guid[:4096]
            identity_source = f"guid:{guid}" if guid else f"url:{normalized_url}"
            identity_key = hashlib.sha256(identity_source.encode()).hexdigest()
            summary_raw = str(entry.get("summary") or entry.get("description") or "").strip() or None
            if summary_raw: summary_raw = summary_raw[:20000]
            summary_text = html_to_text(summary_raw)
            published, updated = _parsed_date(entry, "published"), _parsed_date(entry, "updated")
            date_source = "published" if published else ("updated" if updated else "fetched")
            enclosures = entry.get("enclosures") or []
            image_url = next((str(item.get("href")) for item in enclosures if item.get("href") and str(item.get("type", "")).startswith("image/")), None)
            title_normalized = normalize_text(html_to_text(title) or title)
            author = str(entry.get("author") or "").strip()[:500] or None
            raw_entry = _json_safe(dict(entry)); raw_json = json.dumps(raw_entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            if len(raw_json) > max_raw_entry_bytes:
                rejected += 1; continue
            fingerprint = dict(source_guid=guid, source_url=link, normalized_url=normalized_url,
                               title_raw=title, summary_raw=summary_raw, author_raw=author,
                               title_normalized=title_normalized, summary_text=summary_text,
                               image_url=image_url, published_at=published.isoformat() if published else None,
                               updated_at_source=updated.isoformat() if updated else None,
                               date_source=date_source, raw_entry=raw_entry)
            content_hash = hashlib.sha256(json.dumps(fingerprint, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            normalized.append(NormalizedEntry(guid, link, normalized_url, identity_key, title, summary_raw,
                                              author, title_normalized,
                                              summary_text, image_url, published, updated, date_source,
                                              content_hash, raw_entry))
        except (TypeError, ValueError):
            rejected += 1; continue
    if not normalized and entries:
        raise InvalidFeed("feed yielded no valid entries", total=len(entries), rejected=rejected)
    return ParseResult(normalized, len(entries), len(normalized), rejected, bozo_message)
