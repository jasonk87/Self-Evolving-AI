import os

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("WEEBO_BASE_URL", "http://127.0.0.1:5000")


def verify_weebo_ui_overhaul():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            desktop = browser.new_page(viewport={"width": 1440, "height": 950})
            desktop_errors = []
            desktop.on("console", lambda msg: desktop_errors.append(msg.text) if msg.type == "error" else None)
            desktop.on("pageerror", lambda err: desktop_errors.append(str(err)))

            desktop.goto(BASE_URL, wait_until="networkidle", timeout=30000)
            desktop.locator(".weebo-command-center").wait_for(state="visible", timeout=10000)
            desktop.wait_for_timeout(2500)

            assert desktop.locator(".weebo-card").is_visible()
            assert "Weebo" in desktop.locator(".weebo-card").inner_text()
            assert desktop.locator("#normal-current-work").is_visible()
            assert desktop.locator("#normal-approvals-list").is_visible()
            assert desktop.locator("#normal-tools-count").inner_text().strip() != "--"
            assert not desktop.locator("#mission-debug-workbench").is_visible()

            desktop.locator("#mission-debug-toggle").check(force=True)
            desktop.locator("#mission-debug-workbench").wait_for(state="visible", timeout=5000)

            desktop.locator('[data-target="view-chat"]').first.click()
            desktop.locator(".chat-home-card").wait_for(state="visible", timeout=5000)
            assert "Weebo is ready" in desktop.locator(".chat-home-card").inner_text()

            desktop.screenshot(path="output-playwright-mission-desktop.png", full_page=True)
            assert desktop_errors == []

            mobile = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
            mobile_errors = []
            mobile.on("console", lambda msg: mobile_errors.append(msg.text) if msg.type == "error" else None)
            mobile.on("pageerror", lambda err: mobile_errors.append(str(err)))

            mobile.goto(BASE_URL, wait_until="networkidle", timeout=30000)
            mobile.locator(".weebo-command-center").wait_for(state="visible", timeout=10000)
            mobile.wait_for_timeout(2000)

            assert mobile.locator(".weebo-command-center > *").count() == 4
            has_horizontal_overflow = mobile.evaluate(
                "() => document.documentElement.scrollWidth > window.innerWidth + 2"
            )
            assert not has_horizontal_overflow

            mobile.locator('.mobile-nav-item[data-target="view-chat"]').click()
            mobile.locator(".chat-home-card").wait_for(state="visible", timeout=5000)
            assert "Weebo is ready" in mobile.locator(".chat-home-card").inner_text()

            mobile.screenshot(path="output-playwright-mission-mobile.png", full_page=True)
            assert mobile_errors == []
        finally:
            browser.close()


if __name__ == "__main__":
    verify_weebo_ui_overhaul()
    print("Weebo UI overhaul verification passed.")
