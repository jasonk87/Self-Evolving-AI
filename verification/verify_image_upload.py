
from playwright.sync_api import sync_playwright
import time
import os

def run_verification():
    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # We need the Flask app running. Assuming it is running on port 5000 from a background process.
        # But wait, I need to start it first.
        # The instructions say "Start the local development server".
        # I will do that in the bash session.

        try:
            page.goto("http://localhost:5000")

            # Wait for chat input
            page.wait_for_selector("#chat-input")

            # Check for Image Upload Button
            upload_btn = page.query_selector("#image-upload-btn")
            if not upload_btn:
                print("FAILED: Image upload button not found.")
                return

            print("Image upload button found.")

            # Mock file upload interaction (harder in headless without an actual file interaction)
            # But we can verify the UI element exists and screenshot it.

            # Take screenshot of the input area
            input_area = page.query_selector(".input-area")
            if input_area:
                input_area.screenshot(path="verification/input_area_with_upload.png")
                print("Screenshot saved to verification/input_area_with_upload.png")
            else:
                print("FAILED: Input area not found.")

        except Exception as e:
            print(f"Verification failed: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    run_verification()
