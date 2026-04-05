from __future__ import annotations

import argparse
import base64
import html
import re
from pathlib import Path

from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search Gmail messages with the Gmail API."
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
        "--debug-mime",
        action="store_true",
        help="Print the MIME structure for each matched message.",
    )
    parser.add_argument(
        "--raw-html",
        action="store_true",
        help="Print the raw HTML body when an HTML part is present.",
    )
    parser.add_argument(
        "--save-html-dir",
        default="html_output",
        help="Directory where raw HTML bodies are written when HTML is present.",
    )
    parser.add_argument(
        "--extract-tables",
        action="store_true",
        help="Extract HTML tables with BeautifulSoup and print them as rows.",
    )
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        help="Specific extracted field to print, for example: --field statement_balance",
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


def html_to_text(html_body: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html_body)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?i)</div\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_body_text(payload: dict) -> tuple[str, str]:
    plain_text = extract_mime_body(payload, "text/plain")
    if plain_text:
        return plain_text, "plain"

    html_body = extract_mime_body(payload, "text/html")
    if html_body:
        return html_to_text(html_body), "html"

    return "", "none"


def extract_raw_html(payload: dict) -> str:
    return extract_mime_body(payload, "text/html")


def sanitize_filename(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return sanitized or "message"


def save_raw_html(output_dir: Path, message_id: str, subject: str, raw_html: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{sanitize_filename(subject)[:80]}_{message_id}.html"
    path = output_dir / filename
    path.write_text(raw_html, encoding="utf-8")
    return path


def extract_tables_from_html(raw_html: str) -> list[list[list[str]]]:
    soup = BeautifulSoup(raw_html, "html.parser")
    tables: list[list[list[str]]] = []

    for table in soup.find_all("table"):
        rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [
                cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])
            ]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)

    return tables


def normalize_field_name(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", label.strip().lower()).strip("_")
    return cleaned


def extract_statement_fields(tables: list[list[list[str]]]) -> tuple[dict[str, str], dict[str, str]]:
    flattened_values: list[str] = []
    for table in tables:
        for row in table:
            row_text = " ".join(cell.strip() for cell in row if cell.strip()).strip()
            if row_text:
                flattened_values.append(row_text)

    fields: dict[str, str] = {}
    labels: dict[str, str] = {}
    index = 0
    while index < len(flattened_values) - 1:
        current = flattened_values[index]
        next_value = flattened_values[index + 1]

        if current.endswith(":"):
            key = normalize_field_name(current[:-1])
            if key and key not in fields:
                fields[key] = next_value
                labels[key] = current
            index += 2
            continue

        index += 1

    return fields, labels


def resolve_requested_fields(
    requested_fields: list[str], available_fields: dict[str, str]
) -> tuple[list[str], list[str]]:
    if not requested_fields:
        return list(available_fields.keys()), []

    resolved: list[str] = []
    missing: list[str] = []

    for requested in requested_fields:
        key = normalize_field_name(requested.rstrip(":"))
        if key in available_fields:
            resolved.append(key)
        else:
            missing.append(requested)

    return resolved, missing


def should_print_fields_only(args: argparse.Namespace) -> bool:
    return bool(args.field)


def print_mime_structure(payload: dict, indent: int = 0) -> None:
    prefix = " " * indent
    mime_type = payload.get("mimeType", "(unknown)")
    filename = payload.get("filename", "")
    body = payload.get("body", {})
    size = body.get("size", 0)
    has_data = "yes" if body.get("data") else "no"

    line = f"{prefix}- mimeType={mime_type} size={size} has_data={has_data}"
    if filename:
        line += f" filename={filename}"
    print(line)

    for part in payload.get("parts", []):
        print_mime_structure(part, indent + 2)


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

    for index, message in enumerate(messages, start=1):
        headers = header_map(message)
        payload = message.get("payload", {})
        subject = headers.get("Subject", "(no subject)")
        raw_html = extract_raw_html(payload)
        fields_only = should_print_fields_only(args)
        if raw_html:
            html_path = save_raw_html(
                Path(args.save_html_dir), message["id"], subject, raw_html
            )
        if not fields_only:
            print(f"[{index}] {headers.get('Subject', '(no subject)')}")
            print(f"    From: {headers.get('From', '(unknown sender)')}")
            print(f"    Date: {headers.get('Date', '(unknown date)')}")
            print(f"    Snippet: {message.get('snippet', '')}")
            if raw_html:
                print(f"    Saved HTML: {html_path}")
            if args.raw_html:
                print("    Body (html-raw):")
                print(raw_html or "    (no text/html body found)")
            else:
                body, body_format = extract_body_text(payload)
                print(f"    Body ({body_format}):")
                print(body or "    (no readable body found)")
        if args.extract_tables:
            tables = extract_tables_from_html(raw_html) if raw_html else []
            if tables:
                statement_fields, statement_labels = extract_statement_fields(tables)
                if statement_fields:
                    keys_to_print, missing_fields = resolve_requested_fields(
                        args.field, statement_fields
                    )
                    if fields_only:
                        for key in keys_to_print:
                            value = statement_fields[key]
                            label = statement_labels.get(key, key)
                            print(f"{label} {value}")
                        for requested in missing_fields:
                            print(f"{requested} (not found)")
                    else:
                        print(f"    Extracted tables: {len(tables)}")
                        for table_index, table in enumerate(tables, start=1):
                            print(f"    Table {table_index}:")
                            for row in table:
                                print(f"      {row}")
                        print("    Extracted fields:")
                        for key in keys_to_print:
                            value = statement_fields[key]
                            label = statement_labels.get(key, key)
                            print(f"      {label} {value}")
                        for requested in missing_fields:
                            print(f"      {requested} (not found)")
            elif not fields_only:
                print("    Extracted tables: 0")
        if args.debug_mime:
            print("    MIME structure:")
            print_mime_structure(payload, indent=6)
        if not fields_only:
            print(f"    Message ID: {message['id']}")
            print()


if __name__ == "__main__":
    main()
