import httpx
import pytest

from events_bot.core.fetch import FetchError, check_content

from .conftest import FIXTURES, mock_fetcher

RSS = (FIXTURES / "rbi" / "pressreleases_rss_2026-10-03.xml").read_bytes()


def test_check_content():
    assert check_content(RSS, "xml") is None                     # BOM tolerated
    assert check_content(b"<!DOCTYPE html><html>", "xml") == "not XML"
    assert check_content(b"%PDF-1.7 ...", "pdf") is None
    assert check_content(b"<html>Request Rejected support ID 123</html>", "pdf")
    assert check_content(b"<html>Request Rejected. Your support ID is 1</html>", "html") == "bot/block page"
    assert check_content(b'{"a": 1}', "json") is None


def test_conditional_get_archive_and_304(make_app):
    app = make_app()
    seen_headers = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(req.headers))
        if req.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=RSS, headers={"etag": '"v1"', "content-type": "text/xml"})

    f = mock_fetcher(app, handler)
    url = "https://rbi.org.in/pressreleases_rss.xml"
    r1 = f.get(url, source_id="rbi_pr", doc_type="rss:press_releases", expect="xml")
    assert r1.status == 200 and r1.document_id and r1.sha256
    assert app.archive.get(f"{r1.sha256[:2]}/{r1.sha256}") == RSS
    r2 = f.get(url, source_id="rbi_pr", doc_type="rss:press_releases", expect="xml")
    assert r2.not_modified
    assert seen_headers[1].get("if-none-match") == '"v1"'
    assert app.db.one("select count(*) as n from documents")["n"] == 1


def test_same_bytes_same_document(make_app):
    app = make_app()
    f = mock_fetcher(app, lambda req: httpx.Response(200, content=RSS))
    a = f.get("https://x.test/feed", source_id="rbi_pr", doc_type="rss", expect="xml")
    b = f.get("https://x.test/feed", source_id="rbi_pr", doc_type="rss", expect="xml")
    assert a.document_id == b.document_id


def test_http_200_bot_page_is_an_error_and_not_archived(make_app):
    app = make_app()
    f = mock_fetcher(app, lambda req: httpx.Response(200, content=b"<html>Please enable JavaScript. support ID 9</html>"))
    with pytest.raises(FetchError) as ei:
        f.get("https://rbidocs.rbi.org.in/x.PDF", source_id="rbi_pr", doc_type="pdf", expect="pdf")
    assert ei.value.kind == "bad_content"
    assert app.db.one("select count(*) as n from documents")["n"] == 0


def test_http_error(make_app):
    app = make_app()
    f = mock_fetcher(app, lambda req: httpx.Response(403))
    with pytest.raises(FetchError) as ei:
        f.get("https://x.test/a", source_id="rbi_pr", doc_type="rss")
    assert ei.value.status == 403
