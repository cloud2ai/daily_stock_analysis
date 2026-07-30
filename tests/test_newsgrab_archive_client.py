import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from research.prototype.newsgrab_archive.client import NewsGrabClient
from research.prototype.newsgrab_archive.models import ArchiveRequest


class _NewsGrabHandler(BaseHTTPRequestHandler):
    post_payloads = []
    poll_payloads = []

    def do_POST(self):  # noqa: N802 - HTTP handler API
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.__class__.post_payloads.append(payload)
        self._write_json({"job_id": "job-123"})

    def do_GET(self):  # noqa: N802 - HTTP handler API
        payload = self.__class__.poll_payloads.pop(0)
        self._write_json(payload)

    def log_message(self, format, *args):  # noqa: A003 - suppress test server logs
        return

    def _write_json(self, payload):
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@contextmanager
def newsgrab_server(*poll_payloads):
    _NewsGrabHandler.post_payloads = []
    _NewsGrabHandler.poll_payloads = list(poll_payloads)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _NewsGrabHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", _NewsGrabHandler
    finally:
        server.shutdown()
        thread.join()


def request() -> ArchiveRequest:
    return ArchiveRequest(query="600519", scope="company", language="zh-CN", region="CN")


def test_client_submits_google_news_job_and_returns_done_articles():
    with newsgrab_server(
        {"job_id": "job-123", "status": "running", "result": None, "error": None},
        {
            "job_id": "job-123",
            "status": "done",
            "result": [
                {
                    "title": "Verified article",
                    "content": "Full text",
                    "url": "https://example.com/story",
                    "source": "example.com",
                    "published_date": "2026-07-28T07:30:00Z",
                }
            ],
            "error": None,
        },
    ) as (base_url, handler):
        result = NewsGrabClient(base_url, poll_interval_seconds=0).collect(request())

    assert handler.post_payloads == [
        {
            "backend": "google_news",
            "query": "600519",
            "params": {"max_results": 10, "days": 1, "language": "zh-CN", "region": "CN"},
        }
    ]
    assert result.job_id == "job-123"
    assert result.error is None
    assert result.articles[0]["title"] == "Verified article"


def test_client_returns_explicit_error_for_failed_job():
    with newsgrab_server(
        {"job_id": "job-123", "status": "failed", "result": None, "error": "upstream timeout"}
    ) as (base_url, _):
        result = NewsGrabClient(base_url, poll_interval_seconds=0).collect(request())

    assert result.job_id == "job-123"
    assert result.error == "newsgrab_failed: upstream timeout"


def test_client_returns_explicit_error_for_protocol_without_job_id():
    class MissingJobIdHandler(_NewsGrabHandler):
        def do_POST(self):  # noqa: N802 - HTTP handler API
            self._write_json({})

    server = ThreadingHTTPServer(("127.0.0.1", 0), MissingJobIdHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = NewsGrabClient(
            f"http://127.0.0.1:{server.server_port}", poll_interval_seconds=0
        ).collect(request())
    finally:
        server.shutdown()
        thread.join()

    assert result.job_id is None
    assert result.error == "protocol_error: missing_job_id"
