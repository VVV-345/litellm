import argparse
import ipaddress
import json
import os
import re
import sys
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from typing import Final, NoReturn
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024
ATTACHMENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "application/pdf", "text/plain"})


@dataclass(frozen=True, slots=True)
class Failure:
    code: str
    message: str
    exit_status: int
    status: int | None = None


def emit(value: object) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    payload: Final = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    sys.stdout.write(payload + "\n")


def fail(failure: Failure) -> NoReturn:
    emit({"error": {"code": failure.code, "message": failure.message, "status": failure.status}})
    raise SystemExit(failure.exit_status)


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        fail(Failure("invalid_arguments", "Invalid arguments; use --help for usage", 2))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        return None


def arguments() -> argparse.Namespace:
    parser: Final = Parser(description="Read and update feedback using a scoped environment token")
    commands: Final = parser.add_subparsers(dest="command", required=True)
    listing: Final = commands.add_parser("list")
    listing.add_argument("--status", default="unresolved", choices=(
        "all", "unresolved", "pending", "in_progress", "pending_verification", "resolved",
    ))
    listing.add_argument("--q", default="")
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--offset", type=int, default=0)
    commands.add_parser("get").add_argument("issue_id", type=UUID)
    for action in ("claim", "submit-fix", "resolve", "reopen", "release", "note"):
        mutation: Final = commands.add_parser(action)
        mutation.add_argument("issue_id", type=UUID)
        mutation.add_argument("--revision", type=int, required=True)
        mutation.add_argument("--summary", default="")
        mutation.add_argument("--commit", default="")
        mutation.add_argument("--verification-file", type=Path)
        mutation.add_argument("--deployed-version", default="")
    attachment: Final = commands.add_parser("download-attachment")
    attachment.add_argument("issue_id", type=UUID)
    attachment.add_argument("attachment_id", type=UUID)
    attachment.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def service_url(value: str) -> str | Failure:
    try:
        parsed: Final = urlsplit(value)
        port: Final = parsed.port
        if (
            not parsed.hostname or parsed.scheme not in ("https", "http")
            or parsed.username is not None or parsed.password is not None
            or "?" in value or "#" in value
            or any(character.isspace() or ord(character) < 32 for character in value)
            or (port is not None and port < 1)
        ):
            return Failure("invalid_configuration", "FEEDBACK_API_URL must be a safe service base URL", 2)
        if parsed.scheme == "http" and parsed.hostname.lower() != "localhost":
            if not ipaddress.ip_address(parsed.hostname).is_loopback:
                return Failure("invalid_configuration", "HTTP is restricted to loopback hosts", 2)
        return value.rstrip("/") + "/account_pool/feedback/agent"
    except ValueError:
        return Failure("invalid_configuration", "FEEDBACK_API_URL must be HTTPS or loopback HTTP", 2)


def mutation_body(args: argparse.Namespace) -> dict[str, str | int] | Failure:
    if args.revision < 1:
        return Failure("invalid_arguments", "Revision must be positive", 2)
    try:
        verification: Final = args.verification_file.read_text(encoding="utf-8") if args.verification_file else ""
    except (OSError, UnicodeError):
        return Failure("invalid_arguments", "Cannot read verification file as UTF-8", 2)
    if args.command == "submit-fix" and (
        not args.summary.strip() or not re.fullmatch(r"[0-9a-fA-F]{7,64}", args.commit) or not verification.strip()
    ):
        return Failure("invalid_arguments", "submit-fix requires summary, commit and verification file", 2)
    if args.command == "resolve" and (not args.deployed_version.strip() or not verification.strip()):
        return Failure("invalid_arguments", "resolve requires deployed version and verification file", 2)
    return {
        "action": args.command.replace("-", "_"), "expected_revision": args.revision,
        "summary": args.summary, "commit": args.commit, "verification": verification,
        "deployed_version": args.deployed_version,
    }


def http_failure(status: int) -> Failure:
    if status == 401:
        return Failure("unauthorized", "Feedback credential is invalid, expired or revoked", 3, status)
    if status == 409:
        return Failure("revision_conflict", "Issue changed; get its current revision before retrying", 4, status)
    if 300 <= status < 400:
        return Failure("redirect_refused", "Redirect refused; configure the final service URL", 6, status)
    return Failure("http_error", "Feedback service rejected the request", 6, status)


def reject_constant(value: str) -> NoReturn:
    raise ValueError("Non-finite JSON number")


def request(url: str, token: str, body: dict[str, str | int] | None) -> object | Failure:
    data: Final = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    outgoing: Final = Request(url, data=data, headers={
        "X-Feedback-Token": token, "Accept": "application/json", "Content-Type": "application/json",
    })
    try:
        with build_opener(NoRedirect()).open(outgoing, timeout=30) as response:
            payload: Final = response.read()
    except HTTPError as error:
        error.close()
        return http_failure(error.code)
    except (URLError, OSError, HTTPException, ValueError):
        return Failure("connection_failed", "Cannot contact feedback service; task state is unknown", 5)
    try:
        value: Final = json.loads(payload.decode("utf-8"), parse_constant=reject_constant)
        if not isinstance(value, dict):
            return Failure("invalid_response", "Feedback service did not return a JSON object", 7)
        return value
    except (ValueError, UnicodeError):
        return Failure("invalid_response", "Feedback service returned malformed JSON", 7)


def download_attachment(url: str, token: str, output: Path) -> dict[str, str | int] | Failure:
    outgoing: Final = Request(url, headers={"X-Feedback-Token": token, "Accept": "application/octet-stream"})
    try:
        with build_opener(NoRedirect()).open(outgoing, timeout=30) as response:
            content: Final = response.read(MAX_ATTACHMENT_SIZE + 1)
            content_type: Final = response.headers.get_content_type()
    except HTTPError as error:
        error.close()
        return http_failure(error.code)
    except (URLError, OSError, HTTPException, ValueError):
        return Failure("connection_failed", "Cannot contact feedback service; task state is unknown", 5)
    if len(content) > MAX_ATTACHMENT_SIZE or content_type not in ATTACHMENT_TYPES:
        return Failure("invalid_response", "Feedback service returned an invalid attachment", 7)
    try:
        with output.open("xb") as attachment:
            attachment.write(content)
    except OSError:
        return Failure("invalid_arguments", "Cannot write attachment without overwriting a file", 2)
    return {"saved_to": str(output.resolve()), "bytes": len(content), "content_type": content_type}


def main() -> None:
    args: Final = arguments()
    base: Final = service_url(os.environ.get("FEEDBACK_API_URL", ""))
    if isinstance(base, Failure):
        fail(base)
    token: Final = os.environ.get("FEEDBACK_TOKEN", "")
    if not token or any(ord(character) < 33 or ord(character) > 126 for character in token):
        fail(Failure("invalid_configuration", "FEEDBACK_TOKEN must contain a scoped feedback credential", 2))
    if args.command == "list":
        if not 1 <= args.limit <= 100 or args.offset < 0:
            fail(Failure("invalid_arguments", "Limit must be 1..100 and offset nonnegative", 2))
        query: Final = urlencode({"status": args.status, "q": args.q, "limit": args.limit, "offset": args.offset})
        result: Final = request(f"{base}/issues?{query}", token, None)
    elif args.command == "get":
        result = request(f"{base}/issues/{args.issue_id}", token, None)
    elif args.command == "download-attachment":
        result = download_attachment(
            f"{base}/issues/{args.issue_id}/attachments/{args.attachment_id}", token, args.output,
        )
    else:
        body: Final = mutation_body(args)
        if isinstance(body, Failure):
            fail(body)
        result = request(f"{base}/issues/{args.issue_id}/actions", token, body)
    if isinstance(result, Failure):
        fail(result)
    emit(result)


if __name__ == "__main__":
    main()
