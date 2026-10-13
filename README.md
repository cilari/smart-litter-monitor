# Smart Litter Monitor

A real-time Cyber-Physical System that monitors a PetKit Pura X smart litter box, calculates rule-based alerts, and sends automated announcements through Node-RED and Alexa.

![Architecture](docs/architecture.png)

## How it works

- **Python** fetches PetKit data, calculates 7 sensor streams and 4 rule-based alerts, and publishes them to MQTT
- **Mosquitto** brokers all MQTT messages locally
- **Node-RED** validates and deduplicates alerts, applies quiet hours, and routes announcements
- **InfluxDB + Grafana** support future time-series storage and dashboards; this branch is currently disabled
- **AWS IoT Core** is reserved for future cloud integration
- **Alexa** announces urgent alerts via an Echo device

## Alert rules

| Alert | Rule |
|---|---|
| No visit | A tracked cat has no litter-box visit during the latest 24-hour window |
| Fast litter consumption | Current 24-hour consumption is more than 10% above the preceding 7-day baseline |
| Low litter | PetKit reports that litter is low |
| Waste box full | PetKit reports that the waste box is full |

Visit-completion notifications are intentionally excluded. The current rules are deterministic and do not require AI. AI may be added later for combined frequency, duration, and weight anomalies.

## Sensor streams

| Topic | Description |
|---|---|
| `litter/sensor/sand_percent` | Litter level (0–100%) |
| `litter/sensor/box_full` | Waste bin full status |
| `litter/sensor/sand_lack` | Litter low warning |
| `litter/sensor/cookie/visit_rate` | Cookie's visits per 24h |
| `litter/sensor/mila/visit_rate` | Mila's visits per 24h |
| `litter/sensor/cookie/avg_duration` | Cookie's average visit duration (s) |
| `litter/sensor/mila/avg_duration` | Mila's average visit duration (s) |

## Getting started

```bash
# 1. Clone the repo
git clone https://github.com/cilari/smart-litter-monitor.git
cd smart-litter-monitor

# 2. Set up environment
cp .env.example .env
# Fill in your PetKit credentials and config in .env

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install Mosquitto on macOS
brew install mosquitto

# 5. Start the local MQTT broker from the repository root
mosquitto -c mosquitto/mosquitto.conf
```

In another terminal, install and start Node-RED:

```bash
npm install -g --unsafe-perm node-red
cd ~/.node-red
npm install node-red-contrib-alexa-remote2-applestrudel node-red-contrib-influxdb
node-red
```

Open `http://127.0.0.1:1880`, import
`node-red/smart_litter_complete.json`, and configure your own Alexa account and
Echo device in the announcement node. The repository flow contains no Alexa
credentials or device identifiers.

Run the Python publisher from the repository root:

```bash
python python/export_petkit_csv.py --publish-architecture
```

## Project structure

```
smart-litter-monitor/
├── .github/workflows/   CI/CD — lint + tests on every push
├── python/              PetKit data collection and alert rules
├── tests/               Unit tests for alert logic
├── node-red/            Node-RED alert workflow export
├── grafana/             Dashboard config
├── mosquitto/           MQTT broker config
├── aws/                 IoT Core policy
└── requirements.txt     Python dependencies
```

## CI status

![CI](https://github.com/cilari/smart-litter-monitor/actions/workflows/ci.yml/badge.svg)
