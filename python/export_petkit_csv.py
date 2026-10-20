"""Collect, analyse, export, and publish PETKIT Pura X (T3) data."""

from __future__ import annotations

import argparse
import asyncio
import csv
import getpass
import hashlib
import json
import math
import os
import sys
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from http import HTTPMethod
from itertools import pairwise
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from pypetkitapi.client import PetKitClient
from pypetkitapi.litter_container import Litter, LitterRecord

CSV_FIELDS = [
    "record_date",
    "event_time_local",
    "event_time_epoch",
    "device_id",
    "device_name",
    "pet_id",
    "pet_name",
    "event_id",
    "event_type",
    "event_name",
    "time_in_local",
    "time_in_epoch",
    "time_out_local",
    "time_out_epoch",
    "duration_seconds",
    "pet_weight_raw",
    "auto_clear",
    "toilet_detection",
    "raw_content_json",
]

STATUS_FIELDS = [
    "captured_at_local",
    "device_id",
    "device_name",
    "sand_percent",
    "sand_weight_raw",
    "sand_lack",
    "box_full",
    "box_state",
    "used_times",
    "deodorant_left_days",
    "spray_left_days",
    "power",
    "error_code",
    "error_message",
]

ARCHITECTURE_SENSOR_PREFIX = "litter/sensor"
ARCHITECTURE_URGENT_TOPIC = "litter/alerts/urgent"
ARCHITECTURE_MAINTENANCE_TOPIC = "litter/alerts/maintenance"
ARCHITECTURE_HEARTBEAT_TOPIC = "litter/health/heartbeat"


def load_environment_file(path: Path) -> None:
    """Load PETKIT_* values from a local .env file without executing it."""
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RuntimeError(f"Could not read environment file {path}: {exc}") from exc

    for line_number, original_line in enumerate(lines, start=1):
        line = original_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise ValueError(f"Invalid .env entry on line {line_number}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key.startswith("PETKIT_"):
            continue
        if value.startswith(("'", '"')):
            quote = value[0]
            if len(value) < 2 or value[-1] != quote:
                raise ValueError(
                    f"Unterminated quoted .env value on line {line_number}"
                )
            value = value[1:-1]
        os.environ.setdefault(key, value)


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Date must use YYYY-MM-DD format, not {value!r}"
        ) from exc


def iter_dates(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def epoch_to_local(value: Any, tz: ZoneInfo) -> str:
    if value in (None, ""):
        return ""
    try:
        epoch = float(value)
        if epoch > 10_000_000_000:
            epoch /= 1000
        return datetime.fromtimestamp(epoch, UTC).astimezone(tz).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def duration_seconds(content: dict[str, Any], record: dict[str, Any]) -> Any:
    time_in = content.get("timeIn")
    time_out = content.get("timeOut")
    try:
        if time_in is not None and time_out is not None:
            return int(time_out) - int(time_in)
    except (TypeError, ValueError):
        pass
    return record.get("duration", "")


def flatten_record(
    record: dict[str, Any],
    requested_date: date,
    device_id: int,
    device_name: str,
    tz: ZoneInfo,
) -> dict[str, Any]:
    content = record.get("content")
    if not isinstance(content, dict):
        content = {}

    timestamp = record.get("timestamp")
    time_in = content.get("timeIn")
    time_out = content.get("timeOut")

    return {
        "record_date": requested_date.isoformat(),
        "event_time_local": epoch_to_local(timestamp, tz),
        "event_time_epoch": timestamp if timestamp is not None else "",
        "device_id": device_id,
        "device_name": device_name,
        "pet_id": record.get("petId", ""),
        "pet_name": record.get("petName", ""),
        "event_id": record.get("eventId", ""),
        "event_type": record.get("eventType", ""),
        "event_name": record.get("enumEventType", ""),
        "time_in_local": epoch_to_local(time_in, tz),
        "time_in_epoch": time_in if time_in is not None else "",
        "time_out_local": epoch_to_local(time_out, tz),
        "time_out_epoch": time_out if time_out is not None else "",
        "duration_seconds": duration_seconds(content, record),
        "pet_weight_raw": content.get("petWeight", ""),
        "auto_clear": content.get("autoClear", ""),
        "toilet_detection": content.get(
            "toiletDetection", record.get("toiletDetection", "")
        ),
        "raw_content_json": json.dumps(
            content, ensure_ascii=False, separators=(",", ":")
        ),
    }


def flatten_status(litter: Litter, tz: ZoneInfo) -> dict[str, Any]:
    device = litter.device_nfo
    state = litter.state
    return {
        "captured_at_local": datetime.now(UTC).astimezone(tz).isoformat(),
        "device_id": device.device_id if device else litter.id,
        "device_name": (
            (device.device_name if device else None) or litter.name or "Pura X"
        ),
        "sand_percent": getattr(state, "sand_percent", "") if state else "",
        "sand_weight_raw": getattr(state, "sand_weight", "") if state else "",
        "sand_lack": getattr(state, "sand_lack", "") if state else "",
        "box_full": getattr(state, "box_full", "") if state else "",
        "box_state": getattr(state, "box_state", "") if state else "",
        "used_times": getattr(state, "used_times", "") if state else "",
        "deodorant_left_days": (
            getattr(state, "deodorant_left_days", "") if state else ""
        ),
        "spray_left_days": (
            getattr(state, "spray_left_days", "") if state else ""
        ),
        "power": getattr(state, "power", "") if state else "",
        "error_code": getattr(state, "error_code", "") if state else "",
        "error_message": getattr(state, "error_msg", "") if state else "",
    }


def parse_args() -> argparse.Namespace:
    # Use the PetKit time zone, not the host clock: containers default to UTC,
    # which put "today" a day behind Sydney every morning.
    today = datetime.now(
        ZoneInfo(os.getenv("PETKIT_TIMEZONE", "Australia/Sydney"))
    ).date()
    parser = argparse.ArgumentParser(
        description=(
            "Export PETKIT Pura X history to CSV and optionally publish the "
            "sensor and alert pipeline described in docs/architecture.png."
        )
    )
    parser.add_argument(
        "--start",
        type=parse_date,
        # PetKit rejects queries older than 30 days (inclusive), error code 720.
        default=today - timedelta(days=29),
        help="Start date in YYYY-MM-DD format; defaults to 29 days ago.",
    )
    parser.add_argument(
        "--end",
        type=parse_date,
        default=today,
        help="End date in YYYY-MM-DD format; defaults to today.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("petkit_pura_x.csv"),
        help="History CSV output path.",
    )
    parser.add_argument(
        "--status-output",
        type=Path,
        default=Path("petkit_pura_x_status.csv"),
        help="Current litter-box status CSV output path.",
    )
    parser.add_argument(
        "--device-id",
        type=int,
        help="When more than one T3 exists, select this device ID.",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("PETKIT_REGION", "AU"),
        help="PETKIT account region; defaults to PETKIT_REGION or AU.",
    )
    parser.add_argument(
        "--timezone",
        default=os.getenv("PETKIT_TIMEZONE", "Australia/Sydney"),
        help=(
            "IANA time zone; defaults to PETKIT_TIMEZONE or "
            "Australia/Sydney."
        ),
    )
    parser.add_argument(
        "--mqtt-host",
        default=os.getenv("PETKIT_MQTT_HOST", "localhost"),
        help="MQTT broker; defaults to PETKIT_MQTT_HOST or localhost.",
    )
    parser.add_argument(
        "--mqtt-port",
        type=int,
        default=int(os.getenv("PETKIT_MQTT_PORT", "1883")),
        help="MQTT broker port; defaults to PETKIT_MQTT_PORT or 1883.",
    )
    parser.add_argument(
        "--publish-architecture",
        action="store_true",
        help=(
            "After exporting, calculate and publish the seven sensor streams, "
            "typed alerts and heartbeat defined in docs/architecture.png."
        ),
    )
    parser.add_argument(
        "--tracked-pets",
        default=os.getenv("PETKIT_TRACKED_PETS", "Cookie,Mila"),
        help=(
            "Comma-separated pet names for per-pet sensor topics; defaults to "
            "Cookie,Mila."
        ),
    )
    parser.add_argument(
        "--analysis-hours",
        type=float,
        default=24.0,
        help="Current consumption and visit window in hours; defaults to 24.",
    )
    parser.add_argument(
        "--baseline-days",
        type=float,
        default=7.0,
        help="Historical litter-consumption baseline in days; defaults to 7.",
    )
    parser.add_argument(
        "--fast-consumption-ratio",
        type=float,
        default=1.10,
        help=(
            "Fast-consumption multiplier; 1.10 means strictly more than 10%% "
            "above baseline."
        ),
    )
    parser.add_argument(
        "--alert-state",
        type=Path,
        default=Path(".petkit_alert_state.json"),
        help="Persistent state for edge-triggered architecture alerts.",
    )
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("--start cannot be later than --end")
    if args.analysis_hours <= 0:
        parser.error("--analysis-hours must be greater than zero")
    if args.baseline_days <= 0:
        parser.error("--baseline-days must be greater than zero")
    if args.fast_consumption_ratio <= 1:
        parser.error("--fast-consumption-ratio must be greater than 1")
    args.tracked_pets = [
        item.strip() for item in args.tracked_pets.split(",") if item.strip()
    ]
    if args.publish_architecture and not args.tracked_pets:
        parser.error("--tracked-pets must contain at least one pet name")
    return args


def publish_mqtt_messages(
    args: argparse.Namespace,
    messages: Sequence[tuple[str, dict[str, Any]]],
) -> None:
    """Publish JSON messages over one MQTT connection."""
    try:
        from paho.mqtt import publish
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "paho-mqtt is not installed. Run: "
            ".venv/bin/python -m pip install -r requirements.txt"
        ) from exc

    mqtt_messages = [
        {
            "topic": topic,
            "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "qos": 1,
            "retain": False,
        }
        for topic, payload in messages
    ]
    publish.multiple(
        mqtt_messages,
        hostname=args.mqtt_host,
        port=args.mqtt_port,
    )
    print(
        f"Published {len(messages)} MQTT message(s) to "
        f"{args.mqtt_host}:{args.mqtt_port}."
    )
    for topic, payload in messages:
        print(f"{topic}: {json.dumps(payload, ensure_ascii=False)}")


def parse_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def parse_boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalised = value.strip().lower()
        if normalised in {"true", "1", "yes", "on"}:
            return True
        if normalised in {"false", "0", "no", "off"}:
            return False
    return None


def parse_record_time(row: dict[str, Any], tz: ZoneInfo) -> datetime | None:
    epoch = parse_number(row.get("event_time_epoch"))
    if epoch is not None:
        if epoch > 10_000_000_000:
            epoch /= 1000
        try:
            return datetime.fromtimestamp(epoch, UTC).astimezone(tz)
        except (OSError, OverflowError, ValueError):
            pass

    local_value = row.get("event_time_local")
    if not local_value:
        return None
    try:
        parsed = datetime.fromisoformat(str(local_value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def topic_name(value: str) -> str:
    """Convert a pet name into a safe, readable MQTT topic component."""
    cleaned = "".join(
        character.lower() if character.isalnum() else "-"
        for character in value.strip()
    )
    return "-".join(part for part in cleaned.split("-") if part) or "unknown"


def pet_metrics(
    rows: Sequence[dict[str, Any]],
    pet_name: str,
    window_start: datetime,
    window_end: datetime,
    tz: ZoneInfo,
) -> tuple[int, float | None]:
    durations: list[float] = []
    visits = 0
    expected_name = pet_name.casefold()
    for row in rows:
        if row.get("event_name") != "pet_out":
            continue
        if str(row.get("pet_name", "")).casefold() != expected_name:
            continue
        event_time = parse_record_time(row, tz)
        if event_time is None or not window_start <= event_time <= window_end:
            continue
        visits += 1
        duration = parse_number(row.get("duration_seconds"))
        if duration is not None and duration >= 0:
            durations.append(duration)
    average = sum(durations) / len(durations) if durations else None
    return visits, average


def litter_measurements(
    rows: Sequence[dict[str, Any]],
    tz: ZoneInfo,
) -> list[tuple[datetime, float]]:
    measurements: set[tuple[datetime, float]] = set()
    for row in rows:
        if row.get("event_name") != "clean_over":
            continue
        try:
            content = json.loads(str(row.get("raw_content_json") or "{}"))
        except json.JSONDecodeError:
            continue
        if not isinstance(content, dict):
            continue
        value = parse_number(content.get("litterPercent"))
        event_time = parse_record_time(row, tz)
        if value is None or event_time is None or not 0 <= value <= 100:
            continue
        measurements.add((event_time, value))
    return sorted(measurements, key=lambda item: item[0])


def litter_consumption_rate(
    measurements: Sequence[tuple[datetime, float]],
    window_start: datetime,
    window_end: datetime,
) -> tuple[float | None, int]:
    """Return percentage-points consumed per day within one time window.

    Only downward changes count as consumption. Upward changes are treated as
    refills or device noise and therefore cannot inflate the rate.
    """
    selected = [
        item for item in measurements if window_start <= item[0] <= window_end
    ]
    if len(selected) < 2:
        return None, len(selected)
    elapsed_days = (selected[-1][0] - selected[0][0]).total_seconds() / 86_400
    if elapsed_days <= 0:
        return None, len(selected)
    consumed = sum(
        max(previous[1] - current[1], 0.0)
        for previous, current in pairwise(selected)
    )
    return consumed / elapsed_days, len(selected)


def load_alert_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"active": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("active"), dict):
            return payload
    except (OSError, json.JSONDecodeError):
        print(f"Warning: could not read {path}; starting with no active alerts.")
    return {"active": {}}


def save_alert_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(UTC).isoformat()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def alert_event_id(
    device_id: str,
    alert_type: str,
    qualification_time: str,
    scope: str = "",
) -> str:
    identity = (
        f"{device_id}|{alert_type}|{scope}|{qualification_time}".encode()
    )
    return f"alert-{hashlib.sha256(identity).hexdigest()[:20]}"


def architecture_messages(
    args: argparse.Namespace,
    rows: Sequence[dict[str, Any]],
    status_row: dict[str, Any],
    now: datetime,
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    """Build the seven sensor streams, edge-triggered alerts, and heartbeat."""
    tz = ZoneInfo(args.timezone)
    now = now.astimezone(tz)
    device_id = str(status_row.get("device_id", ""))
    device_name = str(status_row.get("device_name") or "Pura X")
    device_rows = [
        row for row in rows if str(row.get("device_id", "")) == device_id
    ]
    common = {
        "timestamp": now.isoformat(),
        "device_id": device_id,
        "device_name": device_name,
        "source": "petkit",
    }

    sand_percent = parse_number(status_row.get("sand_percent"))
    sand_lack = parse_boolean(status_row.get("sand_lack"))
    box_full = parse_boolean(status_row.get("box_full"))
    sensor_values: list[tuple[str, str, Any, str]] = [
        ("sand_percent", "sand_percent", sand_percent, "percent"),
        ("box_full", "box_full", box_full, "boolean"),
        ("sand_lack", "sand_lack", sand_lack, "boolean"),
    ]

    analysis_window = timedelta(hours=args.analysis_hours)
    current_start = now - analysis_window
    metrics_by_pet: dict[str, tuple[int, float | None]] = {}
    for pet_name in args.tracked_pets:
        visits, average_duration = pet_metrics(
            device_rows, pet_name, current_start, now, tz
        )
        metrics_by_pet[pet_name] = (visits, average_duration)
        pet_topic = topic_name(pet_name)
        sensor_values.extend(
            [
                (
                    f"{pet_topic}/visit_rate",
                    "visit_rate",
                    visits,
                    f"visits/{args.analysis_hours:g}h",
                ),
                (
                    f"{pet_topic}/avg_duration",
                    "avg_duration",
                    (
                        round(average_duration, 2)
                        if average_duration is not None
                        else None
                    ),
                    "seconds",
                ),
            ]
        )

    messages: list[tuple[str, dict[str, Any]]] = []
    for topic_suffix, metric, value, unit in sensor_values:
        messages.append(
            (
                f"{ARCHITECTURE_SENSOR_PREFIX}/{topic_suffix}",
                {**common, "metric": metric, "value": value, "unit": unit},
            )
        )

    measurements = litter_measurements(device_rows, tz)
    baseline_start = current_start - timedelta(days=args.baseline_days)
    current_rate, current_samples = litter_consumption_rate(
        measurements, current_start, now
    )
    baseline_rate, baseline_samples = litter_consumption_rate(
        measurements, baseline_start, current_start
    )
    fast_consumption: bool | None = None
    if (
        current_rate is not None
        and baseline_rate is not None
        and baseline_rate > 0
    ):
        fast_consumption = (
            current_rate > baseline_rate * args.fast_consumption_ratio
        )
    threshold_percent = (args.fast_consumption_ratio - 1) * 100

    conditions: list[
        tuple[str, str, bool | None, str, str, dict[str, Any]]
    ] = [
        (
            "fast_consumption",
            "",
            fast_consumption,
            ARCHITECTURE_URGENT_TOPIC,
            (
                "Urgent litter consumption trend: the current rate is more "
                f"than {threshold_percent:g} percent above the "
                f"{args.baseline_days:g}-day baseline. This is not a "
                "veterinary diagnosis."
            ),
            {
                "current_rate": current_rate,
                "baseline_rate": baseline_rate,
                "rate_unit": "percentage_points_per_day",
                "threshold_multiplier": args.fast_consumption_ratio,
                "current_sample_count": current_samples,
                "baseline_sample_count": baseline_samples,
                "analysis_window_start": current_start.isoformat(),
                "baseline_window_start": baseline_start.isoformat(),
            },
        ),
        (
            "litter_lacking",
            "",
            sand_lack,
            ARCHITECTURE_MAINTENANCE_TOPIC,
            (
                "Maintenance alert: PETKIT reports that the litter is low. "
                "Please inspect and refill the litter box."
            ),
            {"sand_lack": sand_lack, "sand_percent": sand_percent},
        ),
        (
            "waste_bin_full",
            "",
            box_full,
            ARCHITECTURE_MAINTENANCE_TOPIC,
            (
                "Maintenance alert: PETKIT reports that the waste bin is "
                "full. Please empty the waste bin."
            ),
            {"box_full": box_full},
        ),
    ]
    for pet_name, (visits, _) in metrics_by_pet.items():
        conditions.append(
            (
                "no_visit_24h",
                pet_name,
                visits == 0,
                ARCHITECTURE_URGENT_TOPIC,
                (
                    f"Urgent health-pattern alert: {pet_name} has not used the "
                    f"litter box in the last {args.analysis_hours:g} hours. "
                    "This is not a veterinary diagnosis."
                ),
                {
                    "pet_name": pet_name,
                    "visit_count": visits,
                    "window_hours": args.analysis_hours,
                    "analysis_window_start": current_start.isoformat(),
                    "analysis_window_end": now.isoformat(),
                },
            )
        )

    state = load_alert_state(args.alert_state)
    active = dict(state.get("active", {}))
    for alert_type, scope, condition, topic, notification, evidence in conditions:
        key = f"{device_id}:{alert_type}"
        if scope:
            key = f"{key}:{scope.casefold()}"
        if condition is True and key not in active:
            qualified_at = now.isoformat()
            event_id = alert_event_id(
                device_id, alert_type, qualified_at, scope=scope.casefold()
            )
            event = {
                **common,
                "event_id": event_id,
                "event_type": "alert_opened",
                "alert_type": alert_type,
                "qualification_timestamp": qualified_at,
                "notification": notification,
                "evidence": evidence,
            }
            if scope:
                event["pet_name"] = scope
            messages.append((topic, event))
            active[key] = {
                "event_id": event_id,
                "opened_at": qualified_at,
            }
        elif condition is False:
            active.pop(key, None)

    state["active"] = active
    messages.append(
        (
            ARCHITECTURE_HEARTBEAT_TOPIC,
            {
                **common,
                "status": "ok",
                "history_record_count": len(device_rows),
                "litter_measurement_count": len(measurements),
                "active_alerts": sorted(active),
            },
        )
    )
    return messages, state


def credentials() -> tuple[str, str]:
    username = os.getenv("PETKIT_USERNAME") or input("PETKIT login email: ").strip()
    password = os.getenv("PETKIT_PASSWORD") or getpass.getpass("PETKIT password: ")
    if not username or not password:
        raise ValueError("The login email and password cannot be empty.")
    return username, password


async def fetch_day(
    client: PetKitClient,
    litter: Litter,
    requested_date: date,
) -> list[dict[str, Any]]:
    device = litter.device_nfo
    if device is None:
        return []

    params = LitterRecord.query_param(
        device, request_date=requested_date.strftime("%Y%m%d")
    )
    response = await client.req.request(
        method=HTTPMethod.POST,
        url=f"{device.device_type}/{LitterRecord.get_endpoint(device.device_type)}",
        params=params,
        headers=await client.get_session_id(),
    )

    if isinstance(response, dict):
        response = response.get("list", [])
    if not isinstance(response, list):
        raise TypeError(
            f"{requested_date.isoformat()} returned an unsupported data type: "
            f"{type(response).__name__}"
        )
    return [item for item in response if isinstance(item, dict)]


async def export(args: argparse.Namespace) -> int:
    username, password = credentials()
    local_tz = ZoneInfo(args.timezone)
    rows: list[dict[str, Any]] = []
    status_rows: list[dict[str, Any]] = []

    async with aiohttp.ClientSession() as session:
        client = PetKitClient(
            username=username,
            password=password,
            region=args.region,
            timezone=args.timezone,
            session=session,
        )
        await client.get_devices_data()

        litter_boxes = [
            entity
            for entity in client.petkit_entities.values()
            if isinstance(entity, Litter)
            and entity.device_nfo is not None
            and entity.device_nfo.device_type == "t3"
            and (
                args.device_id is None
                or entity.device_nfo.device_id == args.device_id
            )
        ]
        if not litter_boxes:
            print(
                "No Pura X/T3 was found. Check the account region, device "
                "sharing, and --device-id.",
                file=sys.stderr,
            )
            return 2

        status_rows = [
            flatten_status(litter, local_tz) for litter in litter_boxes
        ]

        total_days = (args.end - args.start).days + 1
        for litter in litter_boxes:
            device = litter.device_nfo
            assert device is not None
            device_name = device.device_name or litter.name or "Pura X"
            print(
                f"Reading {total_days} day(s) from {device_name} "
                f"({device.device_id})..."
            )
            for requested_date in iter_dates(args.start, args.end):
                records = await fetch_day(client, litter, requested_date)
                rows.extend(
                    flatten_record(
                        record,
                        requested_date,
                        device.device_id,
                        device_name,
                        local_tz,
                    )
                    for record in records
                )

    rows.sort(
        key=lambda row: (
            str(row["event_time_epoch"]),
            str(row["device_id"]),
            str(row["event_id"]),
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    args.status_output.parent.mkdir(parents=True, exist_ok=True)
    with args.status_output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=STATUS_FIELDS)
        writer.writeheader()
        writer.writerows(status_rows)

    print(f"History: {args.output.resolve()} ({len(rows)} records)")
    print(f"Current status: {args.status_output.resolve()} ({len(status_rows)} devices)")

    if args.publish_architecture:
        if len(status_rows) != 1:
            raise ValueError(
                "Architecture publishing requires exactly one Pura X/T3. "
                "Select it with --device-id."
            )
        analysis_time = datetime.now(UTC).astimezone(local_tz)
        required_start = (
            analysis_time
            - timedelta(hours=args.analysis_hours)
            - timedelta(days=args.baseline_days)
        ).date()
        if args.start > required_start or args.end < analysis_time.date():
            raise ValueError(
                "Architecture publishing requires history from at least "
                f"{required_start.isoformat()} through "
                f"{analysis_time.date().isoformat()}."
            )
        messages, next_alert_state = architecture_messages(
            args,
            rows,
            status_rows[0],
            analysis_time,
        )
        publish_mqtt_messages(args, messages)
        save_alert_state(args.alert_state, next_alert_state)
        sensor_count = 3 + (2 * len(args.tracked_pets))
        print(
            f"Architecture cycle complete: {sensor_count} sensor streams, "
            f"{len(messages) - sensor_count - 1} new alert(s), and one heartbeat."
        )
    return 0


def main() -> int:
    load_environment_file(Path(__file__).resolve().parent.parent / ".env")
    args = parse_args()
    try:
        return asyncio.run(export(args))
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports library failures.
        print(f"Operation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
