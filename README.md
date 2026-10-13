# Smart Litter Monitor

A real-time Cyber-Physical System that monitors a PetKit Pura X smart litter box, calculates rule-based alerts, and sends automated announcements through Node-RED and Alexa.

![Architecture](docs/architecture.png)

## How it works

- **Python** fetches PetKit data, calculates 7 sensor streams and 4 rule-based alerts, and publishes them to MQTT
- **Mosquitto** brokers all MQTT messages locally
- **Node-RED** validates and deduplicates alerts, applies quiet hours, and routes announcements plus Alexa App push notifications
- **InfluxDB + Grafana** store every sensor reading, alert, and heartbeat as time-series data for the live dashboard
- **AWS IoT Core** is reserved for future cloud integration
- **Alexa** announces alerts via an Echo device and sends a mobile push notification through the Alexa App

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

The whole stack runs in containers, so no local Python, Mosquitto, or Node-RED
install is needed. These steps use [Finch](https://github.com/runfinch/finch);
Docker Desktop works the same with `docker compose` in place of `finch compose`.

### 1. Install Finch (one time)

```bash
brew install --cask finch
finch vm init
```

If a local Mosquitto or Node-RED is already running, stop it first; the
containers need ports 1883 and 1880.

### 2. Clone and configure

```bash
git clone https://github.com/cilari/smart-litter-monitor.git
cd smart-litter-monitor
cp .env.example .env
```

Fill in the PetKit credentials in `.env` (shared privately by the team, never
committed).

### 3. Start the stack

```bash
finch compose up -d --build
finch compose ps
```

The first run downloads images and takes a few minutes. All five services should
show `running`:

| Service | Purpose | URL |
|---|---|---|
| `collector` | Runs `export_petkit_csv.py` every 5 minutes | — |
| `mosquitto` | MQTT broker | `localhost:1883` |
| `node-red` | Validation, alerts, InfluxDB writes, Alexa | http://localhost:1880 |
| `influxdb` | Time-series storage | http://localhost:8086 |
| `grafana` | Dashboard | http://localhost:3000 |

### 4. One-time Node-RED setup

Credentials are stored outside the repository, so enter them once in Node-RED:

1. Open http://localhost:1880
2. Double-click **Store smart-litter points**, click the pencil next to
   **Server**, and set the token to the `INFLUXDB_TOKEN` value from `.env`
3. Configure your own Alexa account and Echo device in the announcement node,
   then select the same account in **Push alert to Alexa app**
4. Click **Deploy**

Node-RED encrypts these with `NODE_RED_CREDENTIAL_SECRET`, so they persist
across restarts.

### Daily use

| Action | Command |
|---|---|
| Start | `finch vm start` then `finch compose up -d` |
| Stop | `finch compose down` |
| Collector output | `finch logs -f slm-collector` |
| Run one collection now | `finch exec slm-collector python python/export_petkit_csv.py --publish-architecture` |

## Project structure

```
smart-litter-monitor/
├── .github/workflows/   CI — lint + tests on every push
├── python/              PetKit data collection and alert rules
├── tests/               Unit tests for alert logic
├── node-red/            Flow export, image with required nodes, settings
├── grafana/             Datasource and dashboard provisioning
├── mosquitto/           MQTT broker config
├── aws/                 IoT Core policy
├── Dockerfile           Collector image
├── docker-compose.yml   Full stack
└── requirements.txt     Python dependencies
```

## CI status

![CI](https://github.com/cilari/smart-litter-monitor/actions/workflows/ci.yml/badge.svg)
