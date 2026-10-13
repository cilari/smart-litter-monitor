import argparse
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from python import export_petkit_csv as monitor


class ArchitectureAnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.timezone = ZoneInfo("Australia/Sydney")
        self.now = datetime(2026, 10, 13, 12, 0, tzinfo=self.timezone)

    def row(
        self,
        event_time: datetime,
        *,
        event_name: str,
        pet_name: str = "",
        duration: float | str = "",
        litter_percent: float | None = None,
    ) -> dict[str, object]:
        content = {}
        if litter_percent is not None:
            content["litterPercent"] = litter_percent
        return {
            "event_time_epoch": event_time.timestamp(),
            "event_time_local": event_time.isoformat(),
            "device_id": "device-1",
            "event_name": event_name,
            "pet_name": pet_name,
            "duration_seconds": duration,
            "raw_content_json": json.dumps(content),
        }

    def test_pet_metrics_use_the_rolling_window_and_selected_pet(self) -> None:
        rows = [
            self.row(
                self.now - timedelta(hours=2),
                event_name="pet_out",
                pet_name="Cookie",
                duration=60,
            ),
            self.row(
                self.now - timedelta(hours=1),
                event_name="pet_out",
                pet_name="Cookie",
                duration=90,
            ),
            self.row(
                self.now - timedelta(hours=25),
                event_name="pet_out",
                pet_name="Cookie",
                duration=120,
            ),
            self.row(
                self.now - timedelta(minutes=30),
                event_name="pet_out",
                pet_name="Mila",
                duration=30,
            ),
        ]

        visits, average = monitor.pet_metrics(
            rows,
            "Cookie",
            self.now - timedelta(hours=24),
            self.now,
            self.timezone,
        )

        self.assertEqual(visits, 2)
        self.assertEqual(average, 75)

    def test_environment_file_loads_petkit_values_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / ".env"
            path.write_text(
                "PETKIT_REGION='NZ'\n"
                "PETKIT_MQTT_HOST=localhost\n"
                "UNRELATED_VALUE=ignored\n",
                encoding="utf-8",
            )
            previous_region = os.environ.get("PETKIT_REGION")
            previous_host = os.environ.get("PETKIT_MQTT_HOST")
            try:
                os.environ["PETKIT_REGION"] = "AU"
                os.environ.pop("PETKIT_MQTT_HOST", None)
                monitor.load_environment_file(path)
                self.assertEqual(os.environ["PETKIT_REGION"], "AU")
                self.assertEqual(os.environ["PETKIT_MQTT_HOST"], "localhost")
                self.assertNotIn("UNRELATED_VALUE", os.environ)
            finally:
                if previous_region is None:
                    os.environ.pop("PETKIT_REGION", None)
                else:
                    os.environ["PETKIT_REGION"] = previous_region
                if previous_host is None:
                    os.environ.pop("PETKIT_MQTT_HOST", None)
                else:
                    os.environ["PETKIT_MQTT_HOST"] = previous_host

    def test_consumption_rate_ignores_refill_increases(self) -> None:
        measurements = [
            (self.now - timedelta(hours=24), 80),
            (self.now - timedelta(hours=18), 78),
            (self.now - timedelta(hours=12), 95),
            (self.now, 91),
        ]

        rate, samples = monitor.litter_consumption_rate(
            measurements,
            self.now - timedelta(hours=24),
            self.now,
        )

        self.assertEqual(samples, 4)
        self.assertEqual(rate, 6)

    def test_architecture_cycle_builds_seven_sensors_and_deduplicates_alerts(
        self,
    ) -> None:
        rows = [
            self.row(
                self.now - timedelta(days=8),
                event_name="clean_over",
                litter_percent=100,
            ),
            self.row(
                self.now - timedelta(days=4),
                event_name="clean_over",
                litter_percent=96,
            ),
            self.row(
                self.now - timedelta(days=1),
                event_name="clean_over",
                litter_percent=93,
            ),
            self.row(
                self.now - timedelta(hours=12),
                event_name="clean_over",
                litter_percent=91,
            ),
            self.row(
                self.now,
                event_name="clean_over",
                litter_percent=89,
            ),
            self.row(
                self.now - timedelta(hours=1),
                event_name="pet_out",
                pet_name="Cookie",
                duration=60,
            ),
        ]
        status = {
            "device_id": "device-1",
            "device_name": "Test Pura X",
            "sand_percent": "15",
            "sand_lack": "true",
            "box_full": "true",
        }

        with tempfile.TemporaryDirectory() as temporary_directory:
            state_path = Path(temporary_directory) / "alert-state.json"
            args = argparse.Namespace(
                timezone="Australia/Sydney",
                analysis_hours=24.0,
                baseline_days=7.0,
                fast_consumption_ratio=1.10,
                tracked_pets=["Cookie", "Mila"],
                alert_state=state_path,
            )
            messages, state = monitor.architecture_messages(
                args, rows, status, self.now
            )
            monitor.save_alert_state(state_path, state)
            repeated_messages, _ = monitor.architecture_messages(
                args, rows, status, self.now + timedelta(minutes=5)
            )

        sensor_topics = [
            topic
            for topic, _ in messages
            if topic.startswith("litter/sensor/")
        ]
        alert_types = {
            payload["alert_type"]
            for topic, payload in messages
            if topic.startswith("litter/alerts/")
        }
        repeated_alerts = [
            payload
            for topic, payload in repeated_messages
            if topic.startswith("litter/alerts/")
        ]

        self.assertEqual(len(sensor_topics), 7)
        self.assertEqual(
            alert_types,
            {
                "fast_consumption",
                "litter_lacking",
                "waste_bin_full",
                "no_visit_24h",
            },
        )
        no_visit_alerts = [
            payload
            for topic, payload in messages
            if topic == "litter/alerts/urgent"
            and payload.get("alert_type") == "no_visit_24h"
        ]
        self.assertEqual(
            [payload["pet_name"] for payload in no_visit_alerts],
            ["Mila"],
        )
        self.assertEqual(repeated_alerts, [])


if __name__ == "__main__":
    unittest.main()
