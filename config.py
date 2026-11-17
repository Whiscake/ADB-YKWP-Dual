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
INITIAL_POLL_DELAY_SECONDS = _s["min_stage_seconds"]
STUCK_TIMEOUT_SECONDS = _s["stuck_after_seconds"]
ESCALATION_TIMEOUT_SECONDS = _s["alert_after_seconds"]
ALERT_REPEAT_SECONDS = _s["alert_repeat_seconds"]
BOTH_AT_LOGIN_WINDOW_SECONDS = _s["both_at_login_window_seconds"]
LOGIN_RETRY_SECONDS = _s["login_retry_seconds"]
HOLD_NOTIFY = _s["hold_alerts"]
HOLD_GAP_SECONDS = _s["hold_gap_seconds"]
STAGE_CLEAR_TAP_DELAY_SECONDS = _s["stage_clear_tap_delay_seconds"]
TAP_JITTER_PIXELS = _s["tap_jitter_pixels"]
