"""Run new browser acceptance tests even in CI lanes without Playwright's download.

GitHub's Ubuntu image also provides Chrome/Chromium. No test is skipped if a
browser is missing: the normal launch error remains a failing prerequisite.
"""

import shutil
from pathlib import Path


async def launch_chromium(pw):
    options = {"headless": True}
    if not Path(pw.chromium.executable_path).is_file():
        installed = next((path for name in ("google-chrome", "chromium", "chromium-browser")
                          if (path := shutil.which(name))), None)
        if installed:
            options["executable_path"] = installed
    return await pw.chromium.launch(**options)
