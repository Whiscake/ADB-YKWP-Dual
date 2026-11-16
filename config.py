"""Application constants. User-editable values are read from settings.json."""
import settings as _settings

_s = _settings.load()

DEVICE_A = _s["device_a"]
DEVICE_B = _s["device_b"]
BRIDGE_TOKEN = _s["token"]
BRIDGE_PORT = _s["port"]
NTFY_URL = _s["ntfy_url"]
GAME_PACKAGE = _s["game_package"]
LOGIN_BUTTON_COORDS = (_s["login_button_x"], _s["login_button_y"])

BRIDGE_HOST = "0.0.0.0"
INITIAL_POLL_DELAY_SECONDS = 18.0
STUCK_TIMEOUT_SECONDS = 40.0
ESCALATION_TIMEOUT_SECONDS = 240.0
ALERT_REPEAT_SECONDS = 300.0
BOTH_AT_LOGIN_WINDOW_SECONDS = 6.0
LOGIN_RETRY_SECONDS = 8.0
HOLD_NOTIFY = True
HOLD_GAP_SECONDS = 60.0
STAGE_CLEAR_TAP_DELAY_SECONDS = 0.20
TAP_JITTER_PIXELS = 8
