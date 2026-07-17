import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import httpcore
import pytest

from vietnam_calendar.config import Settings
from vietnam_calendar.infrastructure.feeds.rss import (
    FeedTooLarge, InvalidFeed, PinnedHTTPTransport, SafeFeedClient, TotalFetchTimeout, TransientFeedError, UnsafeFeedUrl,
    normalize_article_url, parse_feed, parse_retry_after, validate_feed_url,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "feeds" / "tuoitre-home.xml"


async def public_resolver(host: str, port: int) -> list[str]: return ["93.184.216.34"]
async def private_resolver(host: str, port: int) -> list[str]: return ["127.0.0.1"]


def settings(**changes) -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://unused", **changes)


@pytest.mark.asyncio
async def test_conditional_headers_and_304_are_preserved():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == '"old"'
        assert request.headers["if-modified-since"] == "Wed, 16 Jul 2025 00:00:00 GMT"
        return httpx.Response(304, headers={"etag": '"new"'}, request=request)
    client = SafeFeedClient(settings(), transport=httpx.MockTransport(handler), resolver=public_resolver)
    result = await client.fetch("https://news.tuoitre.vn/home.rss", etag='"old"', last_modified="Wed, 16 Jul 2025 00:00:00 GMT")
    await client.aclose()
    assert result.status_code == 304 and result.body is None and result.etag == '"new"'


@pytest.mark.asyncio
async def test_redirect_target_is_validated_on_every_hop():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://localhost/private"}, request=request)
    client = SafeFeedClient(settings(rss_allowed_hosts="news.tuoitre.vn,localhost"), transport=httpx.MockTransport(handler), resolver=private_resolver)
    with pytest.raises(UnsafeFeedUrl): await client.fetch("https://news.tuoitre.vn/home.rss")
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://news.tuoitre.vn/home.rss", "https://u:p@news.tuoitre.vn/x", "https://news.tuoitre.vn:444/x", "https://example.com/x"])
async def test_unsafe_urls_are_rejected(url):
    with pytest.raises(UnsafeFeedUrl): await validate_feed_url(url, frozenset({"news.tuoitre.vn"}), public_resolver)


@pytest.mark.asyncio
async def test_decompressed_stream_size_is_bounded():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 2000, request=request))
    client = SafeFeedClient(settings(rss_max_body_bytes=1024), transport=transport, resolver=public_resolver)
    with pytest.raises(FeedTooLarge): await client.fetch("https://news.tuoitre.vn/home.rss")
    await client.aclose()


@pytest.mark.asyncio
async def test_429_is_transient_and_retry_after_is_capped():
    transport = httpx.MockTransport(lambda request: httpx.Response(429, headers={"retry-after": "9999"}, request=request))
    client = SafeFeedClient(settings(), transport=transport, resolver=public_resolver)
    with pytest.raises(TransientFeedError) as caught: await client.fetch("https://news.tuoitre.vn/home.rss")
    await client.aclose()
    assert caught.value.retryable and caught.value.retry_after == 3600 and caught.value.http_status == 429


@pytest.mark.asyncio
async def test_5xx_is_transient():
    transport = httpx.MockTransport(lambda request: httpx.Response(503, request=request))
    client = SafeFeedClient(settings(), transport=transport, resolver=public_resolver)
    with pytest.raises(TransientFeedError) as caught: await client.fetch("https://news.tuoitre.vn/home.rss")
    await client.aclose()
    assert caught.value.http_status == 503


@pytest.mark.asyncio
async def test_content_length_is_rejected_before_streaming():
    transport=httpx.MockTransport(lambda request:httpx.Response(200,headers={"content-length":"9999"},content=b"x",request=request))
    client=SafeFeedClient(settings(rss_max_body_bytes=1024),transport=transport,resolver=public_resolver)
    with pytest.raises(FeedTooLarge): await client.fetch("https://news.tuoitre.vn/home.rss")
    await client.aclose()


@pytest.mark.asyncio
async def test_timeout_is_transient():
    def handler(request: httpx.Request): raise httpx.ReadTimeout("slow", request=request)
    client = SafeFeedClient(settings(), transport=httpx.MockTransport(handler), resolver=public_resolver)
    with pytest.raises(TransientFeedError): await client.fetch("https://news.tuoitre.vn/home.rss")
    await client.aclose()


def test_tuoitre_fixture_normalizes_date_guidless_identity_and_enclosure():
    parsed = parse_feed(FIXTURE.read_bytes(), datetime(2025, 7, 16, tzinfo=UTC))
    assert parsed.warning is None and parsed.accepted == 1 and parsed.rejected == 0
    entry = parsed.entries[0]
    assert entry.source_guid is None
    assert entry.published_at == datetime(2025, 7, 16, 2, 30, tzinfo=UTC)
    assert entry.image_url == "https://cdn.tuoitre.vn/sample-image.jpg"
    assert len(entry.identity_key) == 64


def test_invalid_xml_without_entries_fails_but_partial_feed_is_accepted():
    with pytest.raises(InvalidFeed): parse_feed(b"<rss><broken>", datetime.now(UTC))
    partial = b"<rss><channel><item><title>One</title><link>https://news.tuoitre.vn/one</link></item>"
    parsed = parse_feed(partial, datetime.now(UTC))
    assert parsed.accepted == 1 and parsed.warning


def test_url_normalization_removes_tracking_and_fragment():
    assert normalize_article_url("HTTPS://NEWS.TUOITRE.VN:443/a?utm_source=x&id=2#part") == "https://news.tuoitre.vn/a?id=2"


def test_retry_after_parsing():
    assert parse_retry_after("15") == 15
    assert parse_retry_after("nonsense") is None


@pytest.mark.asyncio
async def test_total_deadline_includes_dns():
    async def slow_resolver(host, port): await asyncio.sleep(0.05); return ["93.184.216.34"]
    client = SafeFeedClient(settings(rss_total_timeout=0.01), transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"", request=r)), resolver=slow_resolver)
    with pytest.raises(TotalFetchTimeout): await client.fetch("https://news.tuoitre.vn/home.rss")
    await client.aclose()


class SlowStream(httpx.AsyncByteStream):
    async def __aiter__(self): await asyncio.sleep(0.05); yield b"x"


@pytest.mark.asyncio
async def test_total_deadline_includes_streaming():
    transport=httpx.MockTransport(lambda request:httpx.Response(200,stream=SlowStream(),request=request))
    client=SafeFeedClient(settings(rss_total_timeout=0.01),transport=transport,resolver=public_resolver)
    with pytest.raises(TotalFetchTimeout): await client.fetch("https://news.tuoitre.vn/home.rss")
    await client.aclose()


class RecordingStream(httpcore.AsyncNetworkStream):
    def __init__(self): self.reads=0; self.sni=None; self.written=b""
    async def read(self,max_bytes,timeout=None):
        self.reads+=1
        return b"HTTP/1.1 304 Not Modified\r\nContent-Length: 0\r\n\r\n" if self.reads==1 else b""
    async def write(self,buffer,timeout=None): self.written+=buffer
    async def aclose(self): pass
    async def start_tls(self,ssl_context,server_hostname=None,timeout=None): self.sni=server_hostname; return self
    def get_extra_info(self,info): return None


class RecordingBackend(httpcore.AsyncNetworkBackend):
    def __init__(self): self.target=None; self.stream=RecordingStream()
    async def connect_tcp(self,host,port,timeout=None,local_address=None,socket_options=None): self.target=(host,port); return self.stream
    async def connect_unix_socket(self,*args,**kwargs): raise AssertionError
    async def sleep(self,seconds): pass


@pytest.mark.asyncio
async def test_pinned_transport_uses_validated_ip_but_preserves_host_and_sni():
    backend=RecordingBackend(); transport=PinnedHTTPTransport("news.tuoitre.vn","93.184.216.34",backend=backend)
    async with httpx.AsyncClient(transport=transport) as client:
        response=await client.get("https://news.tuoitre.vn/home.rss")
    assert response.status_code==304
    assert backend.target == ("93.184.216.34",443)
    assert backend.stream.sni == "news.tuoitre.vn"
    assert b"Host: news.tuoitre.vn" in backend.stream.written


def test_parser_counts_mixed_entries_and_caps_total_work():
    raw=b"<rss><channel><item><title>ok</title><link>https://news.tuoitre.vn/ok</link></item><item><title>bad</title></item><item><title>extra</title><link>https://news.tuoitre.vn/extra</link></item></channel></rss>"
    parsed=parse_feed(raw,datetime.now(UTC),max_entries=2)
    assert (parsed.total,parsed.accepted,parsed.rejected)==(3,1,2)


def test_parser_rejects_oversized_raw_entry():
    raw=("<rss><channel><item><title>ok</title><link>https://news.tuoitre.vn/ok</link><description>"+"x"*2000+"</description></item></channel></rss>").encode()
    with pytest.raises(InvalidFeed): parse_feed(raw,datetime.now(UTC),max_raw_entry_bytes=1024)


def test_all_invalid_preserves_parse_counts():
    raw=b"<rss><channel><item><title>missing link</title></item><item><link>https://news.tuoitre.vn/x</link></item></channel></rss>"
    with pytest.raises(InvalidFeed) as caught: parse_feed(raw,datetime.now(UTC))
    assert (caught.value.total,caught.value.rejected)==(2,2)


@pytest.mark.asyncio
@pytest.mark.parametrize("address",["127.0.0.1","10.0.0.1","169.254.1.1","224.0.0.1","0.0.0.0","::1","fe80::1","fc00::1","ff02::1","::"])
async def test_all_non_public_ip_categories_are_rejected(address):
    async def resolver(host,port): return [address]
    with pytest.raises(UnsafeFeedUrl): await validate_feed_url("https://news.tuoitre.vn/x",frozenset({"news.tuoitre.vn"}),resolver)


@pytest.mark.asyncio
async def test_public_multihop_redirects_are_revalidated():
    seen=[]
    async def resolver(host,port): seen.append(host); return ["93.184.216.34"]
    def handler(request):
        path=request.url.path
        if path=="/one": return httpx.Response(302,headers={"location":"https://cdn.tuoitre.vn/two"},request=request)
        if path=="/two": return httpx.Response(307,headers={"location":"https://news.tuoitre.vn/final"},request=request)
        return httpx.Response(304,request=request)
    client=SafeFeedClient(settings(rss_allowed_hosts="news.tuoitre.vn,cdn.tuoitre.vn"),transport=httpx.MockTransport(handler),resolver=resolver)
    assert (await client.fetch("https://news.tuoitre.vn/one")).status_code==304
    await client.aclose(); assert seen==["news.tuoitre.vn","cdn.tuoitre.vn","news.tuoitre.vn"]


def test_http_date_retry_after():
    now=datetime(2025,1,1,tzinfo=UTC)
    assert parse_retry_after("Wed, 01 Jan 2025 00:01:00 GMT",now)==60
