import logging
import ssl
import urllib.request

import config

log = logging.getLogger("coordinator")


def _ssl_context() -> ssl.SSLContext | None:
    """A PyInstaller build has no system certificate store, so HTTPS fails verification unless certifi's bundle is used.
    Falls back to Python's default context where certifi isn't installed (running from source)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def send(message: str, priority: str = "default") -> None:
    if not config.NTFY_URL:
        return
    try:
        req = urllib.request.Request(config.NTFY_URL, data=message.encode(), headers={"Priority": priority})
        urllib.request.urlopen(req, timeout=10, context=_ssl_context()).close()
    except Exception as exc:
        # never raise: an alert failing must not break the handoff. Logged so a broken ntfy setup is visible
        log.warning("ntfy send failed: %s: %s", type(exc).__name__, exc)
