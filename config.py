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
FORCE_STOP_AFTER_SECONDS = _s["force_stop_after_seconds"]
BOTH_AT_LOGIN_WINDOW_SECONDS = _s["both_at_login_window_seconds"]
LOGIN_RETRY_SECONDS = _s["login_retry_seconds"]
STAGE_CLEAR_TAP_DELAY_SECONDS = _s["stage_clear_tap_delay_seconds"]
TAP_JITTER_PIXELS = _s["tap_jitter_pixels"]

POINTS_PER_CLEAR = _s["points_per_clear"]
FORECAST_TARGET_DATETIME = _s["forecast_target_datetime"]
STATS_INTERVAL_SECONDS = _s["stats_interval_seconds"]
STATS_CLEAR_SAMPLE_SIZE = _s["stats_clear_sample_size"]
STATS_UPTIME_WINDOW_HOURS = _s["stats_uptime_window_hours"]
