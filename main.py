from __future__ import annotations

import argparse
import base64
import shutil
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search Gmail messages and save their HTML bodies."
    )
    parser.add_argument(
        "query",
        help="Gmail search query, for example: from:alerts@example.com newer_than:7d",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of messages to return.",
    )
    parser.add_argument(
        "--credentials",
        default="credentials.json",
        help="Path to the OAuth client secret JSON downloaded from Google Cloud.",
    )
    parser.add_argument(
        "--token",
        default="token.json",
        help="Path to the cached OAuth token JSON.",
    )
    parser.add_argument(
        "--save-html-dir",
        default="saved_htmls",
        help="Directory where HTML bodies are written.",
    )
    return parser.parse_args()


def load_credentials(credentials_path: Path, token_path: Path) -> Credentials:
    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
        creds = flow.run_local_server(port=0)

    token_path.write_text(creds.to_json())
    return creds


def header_map(message: dict) -> dict[str, str]:
    headers = message.get("payload", {}).get("headers", [])
    return {header["name"]: header["value"] for header in headers}


def decode_base64url(data: str) -> str:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding).decode("utf-8", errors="replace")


def extract_mime_body(payload: dict, target_mime_type: str) -> str:
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")
    parts = payload.get("parts", [])

    if mime_type == target_mime_type and body_data:
        return decode_base64url(body_data)

    for part in parts:
        if (
            part.get("mimeType") == target_mime_type
            and part.get("body", {}).get("data")
        ):
            return decode_base64url(part["body"]["data"])

        nested_parts = part.get("parts", [])
        if nested_parts:
            nested_body = extract_mime_body(part, target_mime_type)
            if nested_body:
                return nested_body

    return ""


def extract_raw_html(payload: dict) -> str:
    return extract_mime_body(payload, "text/html")


def sanitize_filename(value: str) -> str:
    sanitized = "".join(
        char if char.isalnum() or char in "._-" else "_"
        for char in value.strip()
    ).strip("._")
    return sanitized or "message"


def save_raw_html(output_dir: Path, message_id: str, subject: str, raw_html: str) -> Path:
    filename = f"{sanitize_filename(subject)[:80]}_{message_id}.html"
    path = output_dir / filename
    path.write_text(raw_html, encoding="utf-8")
    return path


def reset_output_dir(output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def search_messages(
    query: str,
    limit: int,
    credentials_path: Path,
    token_path: Path,
) -> list[dict]:
    creds = load_credentials(credentials_path, token_path)
    service = build("gmail", "v1", credentials=creds)

    response = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=limit)
        .execute()
    )
    messages = response.get("messages", [])

    results: list[dict] = []
    for item in messages:
        message = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=item["id"],
                format="full",
            )
            .execute()
        )
        results.append(message)
    return results


def main() -> None:
    args = parse_args()
    credentials_path = Path(args.credentials)
    token_path = Path(args.token)

    if not credentials_path.exists():
        raise SystemExit(
            "Missing credentials.json. Create a Google Cloud OAuth client for desktop "
            "apps, enable the Gmail API, and place the downloaded file here."
        )

    messages = search_messages(
        query=args.query,
        limit=args.limit,
        credentials_path=credentials_path,
        token_path=token_path,
    )

    if not messages:
        print("No matching messages found.")
        return

    output_dir = Path(args.save_html_dir)
    reset_output_dir(output_dir)

    saved_count = 0
    for message in messages:
        headers = header_map(message)
        payload = message.get("payload", {})
        subject = headers.get("Subject", "(no subject)")
        raw_html = extract_raw_html(payload)
        if not raw_html:
            continue

        html_path = save_raw_html(output_dir, message["id"], subject, raw_html)
        saved_count += 1
        print(html_path)

    if saved_count == 0:
        print(f"No HTML bodies were found. Output directory: {output_dir}")


if __name__ == "__main__":
    main()
