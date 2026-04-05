# Gmail Search CLI

This project uses the official Gmail API Python client to search your mailbox.

## Setup

1. In Google Cloud, enable the Gmail API.
2. Create an OAuth client for a desktop app.
3. Download the OAuth client JSON and place it in this folder as `credentials.json`.

## Run

```bash
source .venv/bin/activate
uv run main.py 'from:alerts@example.com newer_than:7d'
```

Optional flags:

```bash
uv run main.py 'label:inbox is:unread' --limit 5
```

Inspect raw HTML and extract tables:

```bash
uv run main.py 'from:alerts@example.com subject:"statement"' --limit 1 --raw-html --extract-tables --debug-mime
```

Raw HTML bodies are also saved to `html_output/` by default.

On the first run, a browser window opens for Google sign-in and consent. The app stores the resulting token in `token.json`.
