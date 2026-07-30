import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from research.prototype.newsgrab_archive.runner import main


class _RunnerHandler(BaseHTTPRequestHandler):
    terminal_status = "done"

    def do_POST(self):  # noqa: N802 - HTTP handler API
        self._write_json({"job_id": "job-123"})

    def do_GET(self):  # noqa: N802 - HTTP handler API
        if self.__class__.terminal_status == "done":
            payload = {
                "job_id": "job-123",
                "status": "done",
                "result": [
                    {
                        "title": "Archived by CLI",
                        "content": "Full text",
                        "url": "https://example.com/story",
                        "source": "example.com",
                        "published_date": "2026-07-28T07:30:00Z",
                    }
                ],
                "error": None,
            }
        else:
            payload = {"job_id": "job-123", "status": "failed", "result": None, "error": "offline"}
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
def runner_server(terminal_status="done"):
    _RunnerHandler.terminal_status = terminal_status
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RunnerHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


def cli_args(base_url, archive_root):
    return [
        "--base-url",
        base_url,
        "--archive-root",
        str(archive_root),
        "--query",
        "600519",
        "--scope",
        "company",
        "--poll-interval-seconds",
        "0",
    ]


def test_cli_archives_a_done_job_to_requested_root(tmp_path):
    with runner_server() as base_url:
        exit_code = main(cli_args(base_url, tmp_path))

    assert exit_code == 0
    manifests = list(tmp_path.glob("*/*/manifest.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())["run_status"] == "completed"


def test_cli_writes_failure_manifest_and_returns_nonzero(tmp_path):
    with runner_server("failed") as base_url:
        exit_code = main(cli_args(base_url, tmp_path))

    assert exit_code == 1
    manifests = list(tmp_path.glob("*/*/manifest.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())["run_status"] == "failed"
