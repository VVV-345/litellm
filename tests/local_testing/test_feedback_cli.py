import json
import os
import subprocess
import sys
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import parse_qs, urlsplit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "feedback.py"
ISSUE = "445f2264-762b-453e-aed9-f7756d04a033"
TOKEN = "test-feedback-credential-do-not-print"


class FeedbackCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.requests: Queue[tuple[str, str, str, object]] = Queue()
        self.responses: Queue[tuple[int, bytes, dict[str, str]]] = Queue()
        requests = self.requests
        responses = self.responses

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.respond()

            def do_POST(self) -> None:
                self.respond()

            def respond(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                requests.put((self.command, self.path, self.headers.get("X-Feedback-Token", ""),
                              json.loads(body) if body else None))
                status, payload, headers = responses.get(timeout=5)
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/deployment"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def invoke(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            env={**os.environ, "FEEDBACK_API_URL": self.base, "FEEDBACK_TOKEN": TOKEN, **(env or {})},
            capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        return result

    def success(self, *args: str) -> object:
        self.responses.put((200, '{"title":"反馈","revision":3}'.encode(), {}))
        result = self.invoke(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, '{"revision":3,"title":"反馈"}\n')
        return json.loads(result.stdout)

    def assert_error(self, result: subprocess.CompletedProcess[str], status: int, code: str) -> None:
        self.assertEqual(result.returncode, status, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout)["error"]["code"], code)

    def test_list_uses_service_root_token_and_unresolved_default(self) -> None:
        self.success("list")
        method, path, token, body = self.requests.get_nowait()
        self.assertEqual(method, "GET")
        self.assertEqual(urlsplit(path).path, "/deployment/account_pool/feedback/agent/issues")
        self.assertEqual(parse_qs(urlsplit(path).query), {"status": ["unresolved"], "limit": ["50"], "offset": ["0"]})
        self.assertEqual(token, TOKEN)
        self.assertIsNone(body)

    def test_list_preserves_filter_query_pagination(self) -> None:
        self.success("list", "--status", "pending", "--q", "页面 & error", "--limit", "12", "--offset", "24")
        query = parse_qs(urlsplit(self.requests.get_nowait()[1]).query)
        self.assertEqual(query, {"status": ["pending"], "q": ["页面 & error"], "limit": ["12"], "offset": ["24"]})

    def test_get_uses_uuid(self) -> None:
        self.success("get", ISSUE)
        self.assertEqual(self.requests.get_nowait()[1], f"/deployment/account_pool/feedback/agent/issues/{ISSUE}")

    def test_mutations_send_exact_action_and_explicit_revision(self) -> None:
        for action in ("claim", "release", "reopen", "note"):
            with self.subTest(action=action):
                self.success(action, ISSUE, "--revision", "3", "--summary", "处理说明")
                method, path, _, body = self.requests.get_nowait()
                self.assertEqual((method, path), ("POST", f"/deployment/account_pool/feedback/agent/issues/{ISSUE}/actions"))
                self.assertEqual(body, {"action": action, "expected_revision": 3, "summary": "处理说明",
                                        "commit": "", "verification": "", "deployed_version": ""})

    def test_submit_fix_reads_utf8_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            evidence = Path(directory) / "verification.txt"
            evidence.write_text("命令通过\nexit=0", encoding="utf-8")
            self.success("submit-fix", ISSUE, "--revision", "4", "--summary", "修复说明", "--commit", "abcdef1",
                         "--verification-file", str(evidence))
        self.assertEqual(self.requests.get_nowait()[3], {
            "action": "submit_fix", "expected_revision": 4, "summary": "修复说明", "commit": "abcdef1",
            "verification": "命令通过\nexit=0", "deployed_version": "",
        })

    def test_resolve_sends_actual_running_version_and_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            evidence = Path(directory) / "verification.txt"
            evidence.write_text("live endpoint verified", encoding="utf-8")
            self.success("resolve", ISSUE, "--revision", "5", "--deployed-version", "v1.2.3@abcdef1",
                         "--verification-file", str(evidence))
        body = self.requests.get_nowait()[3]
        self.assertEqual(body["deployed_version"], "v1.2.3@abcdef1")
        self.assertEqual(body["verification"], "live endpoint verified")

    def test_mutations_without_revision_do_not_send_requests(self) -> None:
        for action in ("claim", "submit-fix", "resolve", "reopen", "release", "note"):
            with self.subTest(action=action):
                self.assert_error(self.invoke(action, ISSUE), 2, "invalid_arguments")
        self.assertTrue(self.requests.empty())

    def test_invalid_arguments_and_missing_evidence_do_not_send_requests(self) -> None:
        for args in (("get", "issue-1"), ("list", "--limit", "101"), ("list", "--offset", "-1"),
                     ("claim", ISSUE, "--revision", "0"), ("submit-fix", ISSUE, "--revision", "3"),
                     ("resolve", ISSUE, "--revision", "3"), ("list", "--token", TOKEN)):
            with self.subTest(args=args[:2]):
                self.assert_error(self.invoke(*args), 2, "invalid_arguments")
        self.assertTrue(self.requests.empty())

    def test_invalid_evidence_file_is_structured(self) -> None:
        self.assert_error(self.invoke("submit-fix", ISSUE, "--revision", "3", "--summary", "fix", "--commit", "abcdef1",
                                      "--verification-file", str(SCRIPT.parent / "missing-evidence.txt")), 2, "invalid_arguments")
        self.assertTrue(self.requests.empty())

    def test_missing_environment_is_structured(self) -> None:
        for env in ({"FEEDBACK_TOKEN": ""}, {"FEEDBACK_API_URL": ""}):
            self.assert_error(self.invoke("list", env=env), 2, "invalid_configuration")

    def test_unsafe_urls_are_rejected_before_credentials_are_sent(self) -> None:
        for url in ("http://example.com", "https://user:password@example.com", f"{self.base}?token=x",
                    f"{self.base}#fragment", "file:///tmp/feedback", "https://example.com:bad"):
            with self.subTest(url=url):
                self.assert_error(self.invoke("list", env={"FEEDBACK_API_URL": url}), 2, "invalid_configuration")
        self.assertTrue(self.requests.empty())

    def test_http_failures_are_structured_and_do_not_echo_server_body(self) -> None:
        for http_status, exit_code, code in ((401, 3, "unauthorized"), (409, 4, "revision_conflict"), (500, 6, "http_error")):
            with self.subTest(http_status=http_status):
                self.responses.put((http_status, TOKEN.encode(), {}))
                result = self.invoke("get", ISSUE)
                self.assert_error(result, exit_code, code)
                self.assertEqual(json.loads(result.stdout)["error"]["status"], http_status)

    def test_redirect_is_never_followed(self) -> None:
        self.responses.put((302, b"", {"Location": f"{self.base}/credential-recipient"}))
        self.assert_error(self.invoke("list"), 6, "redirect_refused")
        self.assertEqual(self.requests.qsize(), 1)

    def test_invalid_json_is_not_a_success(self) -> None:
        for payload in (b"not json", b'"not an object"', b'{"number":NaN}', b"\xff"):
            self.responses.put((200, payload, {}))
            self.assert_error(self.invoke("list"), 7, "invalid_response")

    def test_connection_failure_does_not_report_an_empty_task_list(self) -> None:
        with ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler) as closed:
            address = f"http://127.0.0.1:{closed.server_port}"
        self.assert_error(self.invoke("list", env={"FEEDBACK_API_URL": address}), 5, "connection_failed")

    def test_download_attachment_writes_raw_bytes_without_overwriting(self) -> None:
        content = b"\x89PNG\r\n\x1a\nimage-data"
        with TemporaryDirectory() as directory:
            output = Path(directory) / "capture.png"
            self.responses.put((200, content, {"Content-Type":"image/png"}))
            result = self.invoke("download-attachment", ISSUE, "665f2264-762b-453e-aed9-f7756d04a033",
                                 "--output", str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_bytes(), content)
            self.assertIn(str(output), json.loads(result.stdout)["saved_to"])
            method, path, token, body = self.requests.get_nowait()
            self.assertEqual((method, token, body), ("GET", TOKEN, None))
            self.assertEqual(urlsplit(path).path, f"/deployment/account_pool/feedback/agent/issues/{ISSUE}/attachments/665f2264-762b-453e-aed9-f7756d04a033")

    def test_download_attachment_refuses_to_overwrite_existing_files(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "capture.png"
            output.write_bytes(b"existing")
            self.responses.put((200, b"new", {"Content-Type":"image/png"}))
            result = self.invoke("download-attachment", ISSUE, "665f2264-762b-453e-aed9-f7756d04a033",
                                 "--output", str(output))
            self.assert_error(result, 2, "invalid_arguments")
            self.assertEqual(output.read_bytes(), b"existing")


if __name__ == "__main__":
    unittest.main()
