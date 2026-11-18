"""Sends a REAL notification to the ntfy topic in settings.json. Skipped unless LIVE_NTFY=1, so normal runs never ping.

    LIVE_NTFY=1 PYTHONPATH=. python3 -m unittest tests.test_ntfy_live -v

The message goes through notify.send (the same code and certificate setup the app uses), then the topic is read back to
confirm the ntfy server received it. Whether your phone then shows it is the part to check by eye.
Override the settings file with DUAL_LOGIN_SETTINGS=/path/to/settings.json (e.g. the one beside the built binary).
"""
import json
import os
import sys
import time
import types
import unittest
import urllib.request
import uuid
from pathlib import Path
from unittest import mock

# notify.py imports config, which would read the real settings; stub it and inject the real URL only inside the test
sys.modules.setdefault("settings", types.SimpleNamespace(find_adb=lambda: "adb"))
sys.modules.setdefault("config", types.SimpleNamespace(TAP_JITTER_PIXELS=0, NTFY_URL=""))

import notify  # noqa: E402

SETTINGS = Path(os.environ.get("DUAL_LOGIN_SETTINGS",
                               Path.home() / "Library/Application Support/dual-login/settings.json"))


@unittest.skipUnless(os.environ.get("LIVE_NTFY") == "1", "set LIVE_NTFY=1 to send a real test notification")
class LiveNtfyTest(unittest.TestCase):
    def test_send_reaches_the_ntfy_server(self):
        url = json.loads(SETTINGS.read_text()).get("ntfy_url", "")
        self.assertTrue(url, f"no ntfy_url in {SETTINGS}")
        marker = f"dual-login test {uuid.uuid4().hex[:8]}"

        # notify.send never raises; a failed send is logged as a warning, so no warning means it was accepted
        with mock.patch.object(notify.config, "NTFY_URL", url), self.assertNoLogs("coordinator", "WARNING"):
            notify.send(marker)

        seen = False
        deadline = time.time() + 10
        while time.time() < deadline and not seen:
            with urllib.request.urlopen(url.rstrip("/") + "/json?poll=1&since=2m", timeout=10,
                                        context=notify._ssl_context()) as resp:
                lines = resp.read().decode().splitlines()
            seen = any(marker in json.loads(line).get("message", "") for line in lines if line.strip())
            if not seen:
                time.sleep(1)
        self.assertTrue(seen, "the ntfy server never showed the test message")
        print(f"\nsent and confirmed on the server: '{marker}'. Now check your phone.")


if __name__ == "__main__":
    unittest.main()
