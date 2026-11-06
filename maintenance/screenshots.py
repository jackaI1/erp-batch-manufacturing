"""Capture the real openMAINT UI in headless CI, closing every owned page."""
import argparse
import os
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='artifacts/maintenance')
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    base = os.getenv('MAINT_URL', 'http://127.0.0.1:8090/cmdbuild')
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1280, 'height': 900})
        page = context.new_page()
        try:
            page.goto(base + '/ui/', wait_until='networkidle', timeout=120000)
            user = page.locator('input[name="username"]')
            user.wait_for(timeout=60000)
            page.screenshot(path=str(output / '01-login.png'))
            user.fill(os.getenv('MAINT_USER', 'admin'))
            page.locator('input[name="password"]').fill(os.getenv('MAINT_PASSWORD', 'admin'))
            page.get_by_text('Login', exact=True).last.click()
            page.wait_for_timeout(3000)
            page.goto(base + '/ui/#classes/FleetAsset/cards', wait_until='networkidle', timeout=120000)
            page.get_by_text('TUG-01', exact=True).first.wait_for(timeout=60000)
            page.screenshot(path=str(output / '02-fleet-assets.png'))
            page.goto(base + '/ui/#classes/FleetWorkOrder/cards', wait_until='networkidle', timeout=120000)
            page.get_by_text('ENGINE-OIL', exact=False).first.wait_for(timeout=60000)
            page.screenshot(path=str(output / '03-work-orders.png'))
        finally:
            page.close()
            context.close()
            browser.close()


if __name__ == '__main__':
    main()
