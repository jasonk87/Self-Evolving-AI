from playwright.sync_api import sync_playwright
import time
import sys
import subprocess
import os

def verify_navigation():
    # Kill any existing on 5000
    os.system("kill $(lsof -t -i :5000) 2>/dev/null || true")

    print("Starting web app...")
    # Use setsid to easily kill the whole process group later if needed
    process = subprocess.Popen([sys.executable, "web_app.py"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    time.sleep(10) # Give it plenty of time

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            print("Navigating to home...")
            try:
                page.goto("http://localhost:5000")
            except Exception as e:
                print(f"Failed to load page: {e}")
                return

            page.wait_for_selector("#chat-input", timeout=10000)
            page.screenshot(path="/home/jules/verification/before_nav.png")

            print("Sending chat message...")
            page.fill("#chat-input", "Navigate to dashboard")
            page.click("#send-btn")

            print("Waiting for navigation...")
            try:
                page.wait_for_selector("#view-mission-control.active", timeout=10000)
                print("Navigation successful!")
            except Exception as e:
                print(f"Navigation timed out or failed: {e}")

            # Take screenshot regardless
            page.screenshot(path="/home/jules/verification/verification.png")

    finally:
        print("Stopping web app...")
        process.terminate()
        process.wait()

if __name__ == "__main__":
    verify_navigation()
