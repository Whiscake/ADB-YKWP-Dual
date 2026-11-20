"""Application constants. User-editable values are read from settings.json; reload() picks up saved changes."""
import settings as _settings

BRIDGE_HOST = "127.0.0.1"


def reload() -> None:
    s = _settings.load()
    single = s["mode"] == "single"
    names = {s["device_a"]: s["device_a_name"] or s["device_a"], s["device_b"]: s["device_b_name"] or s["device_b"]}
    names.update({serial: s["single_names"].get(serial) or serial for serial in s["single_devices"]})
    globals().update(
        MODE="single" if single else "dual",
        DEVICE_A=s["device_a"],
        DEVICE_B=s["device_b"],
        DEVICES=tuple(s["single_devices"]) if single else (s["device_a"], s["device_b"]),
        DEVICE_NAMES={serial: name for serial, name in names.items() if serial},
        BRIDGE_TOKEN=s["token"],
        BRIDGE_PORT=s["port"],
        NTFY_URL=s["ntfy_url"],
        GAME_PACKAGE=s["game_package"],
        LOGIN_BUTTON_COORDS=(s["login_button_x"], s["login_button_y"]),
        INITIAL_POLL_DELAY_SECONDS=s["min_stage_seconds"],
        STUCK_TIMEOUT_SECONDS=s["stuck_after_seconds"],
        ESCALATION_TIMEOUT_SECONDS=s["alert_after_seconds"],
        ALERT_REPEAT_SECONDS=s["alert_repeat_seconds"],
        FORCE_STOP_AFTER_SECONDS=s["force_stop_after_seconds"],
        BOTH_AT_LOGIN_WINDOW_SECONDS=s["both_at_login_window_seconds"],
        LOGIN_RETRY_SECONDS=s["login_retry_seconds"],
        STAGE_CLEAR_TAP_DELAY_SECONDS=s["stage_clear_tap_delay_seconds"],
        TAP_JITTER_PIXELS=s["tap_jitter_pixels"],
        POINTS_PER_CLEAR=s["points_per_clear"],
        ROOM_POINTS=s["room_points"],
        FORECAST_TARGET_DATETIME=s["forecast_target_datetime"],
        STATS_INTERVAL_SECONDS=s["stats_interval_seconds"],
        STATS_CLEAR_SAMPLE_SIZE=s["stats_clear_sample_size"],
        STATS_UPTIME_WINDOW_HOURS=s["stats_uptime_window_hours"],
        RESTART_EVERY_MINUTES=s["restart_every_minutes"] if s["restart_enabled"] else 0,
        RESTART_BOOT_TIMEOUT_SECONDS=s["restart_boot_timeout_seconds"],
        RESCUE_RESTART_SECONDS=s["rescue_restart_seconds"],
        CONFIGS=s["single_configs"] if single else s["dual_configs"],
        WRITE_LOGS=s["write_logs"],
    )


reload()
