import subprocess
import sys
import types
import unittest
import urllib.error
from unittest import mock

# adb.py and notify.py import config/settings, which read the real settings.json. Stubbing them means the tests never
# touch your setup, your devices or your ntfy topic.
sys.modules["settings"] = types.SimpleNamespace(find_adb=lambda: "adb")
sys.modules["config"] = types.SimpleNamespace(TAP_JITTER_PIXELS=0, NTFY_URL="https://example.invalid/topic")

import adb  # noqa: E402
import notify  # noqa: E402


def devices_text(*rows):
    return "List of devices attached\n" + "".join(f"{serial}\t{state}\n" for serial, state in rows)


def done(cmd, out=""):
    return subprocess.CompletedProcess(cmd, 0, out, "")


class FakeAdb:
    """Stands in for the adb binary. Commands to a serial fail until that serial has been connected."""

    def __init__(self, attached=(), connect_works=True):
        self.attached = set(attached)
        self.connect_works = connect_works
        self.calls = []

    def __call__(self, cmd, **kwargs):
        args = cmd[1:]
        self.calls.append(args)
        if args == ["devices"]:
            return done(cmd, devices_text(*[(s, "device") for s in sorted(self.attached)]))
        if args[0] == "connect":
            if self.connect_works:
                self.attached.add(args[1])
            return done(cmd, "connected")
        if args[0] == "-s" and args[1] not in self.attached:
            raise subprocess.CalledProcessError(1, cmd)
        return done(cmd)


class AdbTests(unittest.TestCase):
    def test_attached_ignores_offline_and_unauthorized(self):
        text = devices_text(("a", "device"), ("b", "offline"), ("c", "unauthorized"))
        with mock.patch("adb.subprocess.run", return_value=done([], text)):
            self.assertEqual(adb.attached(), {"a"})

    def test_reconnect_missing_only_touches_missing_devices(self):
        fake = FakeAdb(attached=["a"])
        with mock.patch("adb.subprocess.run", fake):
            self.assertEqual(adb.reconnect_missing(["a", "b"]), {"b": True})
        self.assertNotIn(["connect", "a"], fake.calls)

    def test_reconnect_missing_reports_failure(self):
        fake = FakeAdb(connect_works=False)
        with mock.patch("adb.subprocess.run", fake):
            self.assertEqual(adb.reconnect_missing(["a"]), {"a": False})

    def test_command_reconnects_and_retries_once(self):
        fake = FakeAdb()  # device starts dropped
        with mock.patch("adb.subprocess.run", fake):
            adb.force_stop("a", "pkg")  # first attempt fails, reconnect, second succeeds
        self.assertIn("a", fake.attached)
        self.assertEqual(sum(1 for c in fake.calls if c[:2] == ["-s", "a"]), 2)

    def test_command_raises_when_device_cannot_be_reconnected(self):
        fake = FakeAdb(connect_works=False)
        with mock.patch("adb.subprocess.run", fake), self.assertRaises(subprocess.CalledProcessError):
            adb.force_stop("a", "pkg")

    def test_healthy_device_is_not_reconnected(self):
        fake = FakeAdb(attached=["a"])
        with mock.patch("adb.subprocess.run", fake):
            adb.force_stop("a", "pkg")
        self.assertNotIn(["connect", "a"], fake.calls)


class NotifyTests(unittest.TestCase):
    def test_failed_send_is_logged_and_does_not_raise(self):
        err = urllib.error.URLError("certificate verify failed")
        with mock.patch("notify.urllib.request.urlopen", side_effect=err), \
                self.assertLogs("coordinator", "WARNING") as logs:
            notify.send("hello")  # must not raise
        self.assertIn("certificate verify failed", logs.output[0])

    def test_no_url_means_no_request(self):
        with mock.patch.object(notify.config, "NTFY_URL", ""), \
                mock.patch("notify.urllib.request.urlopen") as urlopen:
            notify.send("hello")
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
