"""Capture the real openMAINT UI in headless CI, closing every owned page."""
import argparse
import os
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def diagnostic(page, output, phase):
    # Never collect field values, cookies, headers or the full DOM.
    snapshot = dict(phase=phase, title=page.title(), fragment=page.url.split('#', 1)[-1] if '#' in page.url else '', text=page.locator('body').inner_text()[:4000], inputs=page.locator('input').evaluate_all("nodes => nodes.slice(0, 20).map(node => ({id: node.id, name: node.name, type: node.type, testid: node.getAttribute('data-testid')}))"))
    (output / ('ui-' + phase + '.json')).write_text(json.dumps(snapshot, indent=2) + '\n')
    print(json.dumps(snapshot))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='artifacts/maintenance')
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    base = os.getenv('MAINT_URL', 'http://127.0.0.1:8090/cmdbuild')
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(channel=os.getenv('MAINT_BROWSER_CHANNEL') or None, headless=True)
        context = browser.new_context(viewport={'width': 1280, 'height': 900})
        page = context.new_page()
        try:
            page.goto(base + '/ui/', wait_until='networkidle', timeout=120000)
            page.screenshot(path=str(output / '00-ui-initial.png'))
            diagnostic(page, output, 'initial')
            user = page.get_by_test_id('login-inputusername').locator('input')
            user.wait_for(timeout=60000)
            page.screenshot(path=str(output / '01-login.png'))
            user.fill(os.getenv('MAINT_USER', 'admin'))
            page.get_by_test_id('login-inputpassword').locator('input[type="password"]').fill(os.getenv('MAINT_PASSWORD', 'admin'))
            page.get_by_test_id('login-btnlogin').click()
            # The login handler loads the class store and then selects a homepage.
            # Wait for that shell before routing, so its redirect cannot win a race.
            page.get_by_text('All items', exact=True).wait_for(timeout=60000)
            page.wait_for_function("() => Boolean(window.CMDBuildUI && CMDBuildUI.util.helper.ModelHelper.getClassFromName('FleetAsset'))", timeout=60000)
            diagnostic(page, output, 'authenticated')
            page.evaluate("route => CMDBuildUI.util.Utilities.redirectTo(route)", 'classes/FleetAsset/cards')
            page.get_by_text('TUG-01', exact=True).first.wait_for(timeout=60000)
            page.screenshot(path=str(output / '02-fleet-assets.png'))
            page.evaluate("route => CMDBuildUI.util.Utilities.redirectTo(route)", 'classes/FleetWorkOrder/cards')
            page.get_by_text('ENGINE-OIL', exact=False).first.wait_for(timeout=60000)
            page.screenshot(path=str(output / '03-work-orders.png'))
        except Exception:
            diagnostic(page, output, 'failure')
            page.screenshot(path=str(output / '00-ui-failure.png'))
            raise
        finally:
            page.close()
            context.close()
            browser.close()


if __name__ == '__main__':
    main()
