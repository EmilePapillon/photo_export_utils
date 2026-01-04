from pathlib import Path

import click
from playwright.sync_api import sync_playwright

DEFAULT_STATE_FILE = "storage_state.json"


def save_facebook_login_state(out_path: Path, headless: bool = False) -> None:
    """Open a browser for manual Facebook login and save the session cookies."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
        print("Log in manually in the opened browser window.")
        print("When you see your feed/home page, return to this terminal and press Enter.")
        input()

        context.storage_state(path=str(out_path))
        print(f"Saved session to {out_path}")
        browser.close()


@click.command()
@click.option(
    "--out",
    "out_path",
    type=click.Path(path_type=Path),
    default=DEFAULT_STATE_FILE,
    show_default=True,
    help="Where to store the Playwright storage state JSON.",
)
@click.option(
    "--headless/--no-headless",
    default=False,
    show_default=True,
    help="Run Chromium headlessly while logging in.",
)
def main(out_path: Path, headless: bool) -> None:
    """CLI entry point for persisting a Facebook login session."""
    save_facebook_login_state(out_path, headless=headless)


if __name__ == "__main__":
    main()
