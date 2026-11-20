"""Reads Smart AutoClicker backups and adds the stage-clear / login-screen ADB actions to the chosen events."""
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

LOGIN_REPEAT_PAUSE_MS = 2000


@dataclass
class Scenario:
    folder: str
    name: str
    events: list[tuple[int, str]]


def read(path: Path) -> list[Scenario]:
    with zipfile.ZipFile(path) as archive:
        scenarios = []
        for entry in archive.namelist():
            if not entry.endswith(".json"):
                continue
            try:
                data = json.loads(archive.read(entry))["scenario"]
                events = sorted(data["events"], key=lambda e: e["event"].get("priority", 0))
                scenarios.append(Scenario(entry.split("/", 1)[0], data["scenario"]["name"] or "",
                                          [(e["event"]["id"], e["event"]["name"] or "") for e in events]))
            except (KeyError, TypeError, ValueError):
                continue  # not a scenario (another file in the backup), or one this program can't read
        return sorted(scenarios, key=lambda s: s.name.lower())


def _adb_action(action_id: int, event_id: int, name: str, command: str, priority: int) -> dict:
    return {"action": {"id": action_id, "eventId": event_id, "name": name, "priority": priority, "type": "ADB",
                       "adbHost": "", "adbPort": 0, "adbCommand": command}, "intentExtras": []}


def _pause_action(action_id: int, event_id: int, priority: int) -> dict:
    return {"action": {"id": action_id, "eventId": event_id, "name": "Wait", "priority": priority, "type": "PAUSE",
                       "pauseDuration": LOGIN_REPEAT_PAUSE_MS}, "intentExtras": []}


def _append(event: dict, action: dict) -> None:
    action["action"]["priority"] = 1 + max((a["action"].get("priority", 0) for a in event["actions"]), default=-1)
    event["actions"] = sorted(event["actions"], key=lambda a: a["action"].get("priority", 0)) + [action]


def prepare(source: Path, target: Path, scenario: Scenario, clear_event: int | None, login_event: int | None,
            serial: str, name: str) -> None:
    with zipfile.ZipFile(source) as archive:
        json_entry = next(e for e in archive.namelist() if e.startswith(f"{scenario.folder}/") and e.endswith(".json"))
        backup = json.loads(archive.read(json_entry))
        data = backup["scenario"]
        next_id = 1 + max((a["action"]["id"] for e in data["events"] for a in e["actions"]), default=0)
        for event in data["events"]:
            event_id = event["event"]["id"]
            if event_id == clear_event:
                _append(event, _adb_action(next_id, event_id, "Stage clear", f"stage-clear {serial}", 0))
                next_id += 1
            if event_id == login_event:
                event["actions"] = [_adb_action(next_id, event_id, "Login screen", f"login-screen {serial}", 0),
                                    _pause_action(next_id + 1, event_id, 1)]
                next_id += 2
        data["scenario"]["name"] = name
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
            for entry in archive.namelist():
                if entry == json_entry:
                    out.writestr(entry, json.dumps(backup))
                elif entry.startswith(f"{scenario.folder}/"):
                    out.writestr(entry, archive.read(entry))
