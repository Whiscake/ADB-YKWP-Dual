import urllib.request

import config


def send(message: str, priority: str = "default") -> None:
    if not config.NTFY_URL:
        return
    try:
        req = urllib.request.Request(config.NTFY_URL, data=message.encode(), headers={"Priority": priority})
        urllib.request.urlopen(req, timeout=10).close()
    except Exception:
        pass
