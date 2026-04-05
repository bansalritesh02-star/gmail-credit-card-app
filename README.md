# Gmail HTML Saver

This project uses the official Gmail API Python client to search your mailbox and save matching HTML email bodies to disk.

## Setup

1. In Google Cloud, enable the Gmail API.
2. Create an OAuth client for a desktop app.
3. Download the OAuth client JSON and place it in this folder as `credentials.json`.

## Run

```bash
source .venv/bin/activate
uv run main.py 'from:alerts@example.com newer_than:7d'
```

By default, each run clears `saved_htmls/` and writes one `.html` file per matching email that contains a `text/html` body.

Use a different output folder if needed:

```bash
uv run main.py 'label:inbox is:unread' --limit 5 --save-html-dir my_html_output
python main.py "from:citicards@info6.citi.com newer_than:10d" 
```

On the first run, a browser window opens for Google sign-in and consent. The app stores the resulting token in `token.json`.
