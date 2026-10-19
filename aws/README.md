# AWS IoT Core Integration

Node-RED publishes live data to **AWS IoT Core** over MQTT with TLS on port 8883.
There are two publishing paths:

1. **Sensor data** → topic `cps/sensor/data`
2. **Alerts** → topics `cps/alerts/urgent` and `cps/alerts/maintenance`, which an
   IoT Rule forwards to **SNS** for email notification.

```
                                  ┌─► cps/sensor/data                         (view in MQTT test client)
collector → Mosquitto → Node-RED ─┤
         (TLS 8883, X.509 certs)  └─► cps/alerts/urgent | cps/alerts/maintenance
                                            │
                                            └─► IoT Rule → SNS → email
```

## Published message formats

### Sensor (`cps/sensor/data`)
Each of the 7 sensor streams is reshaped into the assessment schema:
```json
{
  "device_id": "100020991",
  "sensor": "sand_percent",
  "value": 57,
  "timestamp": "2026-10-19T14:44:23+11:00"
}
```
Booleans (`box_full`, `sand_lack`) become `1`/`0`; pet-scoped topics become names
like `cookie_visit_rate`.

### Alert (`cps/alerts/urgent` | `cps/alerts/maintenance`)
The collector's alert messages are forwarded unchanged (topic rewritten only),
preserving the urgent/maintenance split:
```json
{
  "timestamp": "2026-10-19T14:30:00+11:00",
  "device_id": "100020991",
  "device_name": "Pura X",
  "event_id": "alert-...",
  "event_type": "alert_opened",
  "alert_type": "waste_bin_full",
  "notification": "Maintenance alert: PETKIT reports that the waste bin is full...",
  "evidence": { "box_full": true }
}
```

## Files in this folder

| File | Purpose |
|---|---|
| `iot-policy.json` | Least-privilege IoT policy (connect + publish to the sensor and alert topics only). Templated — replace `REGION` and `ACCOUNT_ID` before applying in AWS. |
| `certs/` | Device certificate, private key, and Amazon Root CA. **Gitignored — never committed.** |
| `screenshots/` | Evidence: IoT Core data, test alerts, SNS topic, SNS email. |

## One-time AWS console setup (manual)

Done in the AWS console (region `ap-southeast-2` / Sydney) by the account owner.

### 1. Create a Thing
IoT Core → **Manage → Things → Create things** → single thing → name it
`smart-litter-nodered` → defaults → Next.

### 2. Create and download a certificate
- Choose **Auto-generate a new certificate** → Next.
- Download all three files (the private key is shown only once) into `aws/certs/`:
  - device certificate → `*-certificate.pem.crt`
  - private key → `*-private.pem.key`
  - **Amazon Root CA 1** → `AmazonRootCA1.pem`
- **Activate** the certificate.

### 3. Create the policy
- IoT Core → **Security → Policies → Create policy**, name it `smart-litter-policy`.
- Paste the contents of `iot-policy.json`, replacing `REGION` (e.g. `ap-southeast-2`)
  and `ACCOUNT_ID` (your 12-digit account ID).
- The policy allows only:
  - `iot:Connect` as client `smart-litter-nodered`
  - `iot:Publish` to `cps/sensor/data`
  - `iot:Publish` to `cps/alerts/urgent` and `cps/alerts/maintenance`
- Attach the policy to the certificate from step 2.

> Note: the alert publish permissions are required. With a sensor-only policy, AWS
> rejects the alert publish and drops the MQTT connection.

### 4. Get the endpoint
IoT Core → **Settings** → copy the **Device data endpoint** (the `-ats` one):
```
xxxxxxxxxxxxxx-ats.iot.ap-southeast-2.amazonaws.com
```

## Local wiring (Node-RED)

1. Put the three certificate files in `aws/certs/` (gitignored).
2. The `node-red` service in `docker-compose.yml` mounts `./aws/certs` read-only at
   `/data/certs`. Restart to apply:
   ```bash
   finch compose up -d
   ```
3. **AWS IoT Core broker config** (one broker, reused by both publish nodes):
   - **Server:** your `-ats` endpoint, **Port:** `8883`, MQTT V3.1.1
   - **Use TLS:** on. In the TLS config, use file **paths** (`certType: files`):
     - Certificate: `/data/certs/<id>-certificate.pem.crt`
     - Private Key: `/data/certs/<id>-private.pem.key`
     - CA Certificate: `/data/certs/AmazonRootCA1.pem`
   - **Client ID:** `smart-litter-nodered` (must match the policy; must be unique)

4. **Sensor branch** — function node builds the sensor schema, mqtt-out publishes
   to `cps/sensor/data`:
   ```javascript
   // taps litter/sensor/..., skips alerts/heartbeat
   const parts = String(msg.topic || "").split("/");
   if (parts[0] !== "litter" || parts[1] !== "sensor") return null;
   const value = typeof msg.payload.value === "boolean"
       ? (msg.payload.value ? 1 : 0) : Number(msg.payload.value);
   const sensorName = parts.length >= 4 ? parts[2] + "_" + msg.payload.metric
                                        : msg.payload.metric;
   msg.payload = JSON.stringify({
       device_id: String(msg.payload.device_id || "petkit_pura_x"),
       sensor: sensorName, value,
       timestamp: msg.payload.timestamp || new Date().toISOString()
   });
   msg.topic = "cps/sensor/data";
   return msg;
   ```

5. **Alert branch** — function node forwards alerts, preserving severity; mqtt-out
   has an **empty Topic** so it uses `msg.topic`:
   ```javascript
   // taps litter/alerts/..., rewrites to cps/alerts/<severity>
   const parts = String(msg.topic || "").split("/");
   if (parts[0] !== "litter" || parts[1] !== "alerts") return null;
   const severity = parts[2] || "general";          // urgent | maintenance
   msg.topic = "cps/alerts/" + severity;
   if (typeof msg.payload !== "string") msg.payload = JSON.stringify(msg.payload);
   return msg;
   ```

6. **Deploy.** The broker node should show a green "connected" dot.

## SNS email notification (alerts)

### Create the SNS topic + subscription
1. SNS → **Topics → Create topic** → Standard → name `litter-alerts`.
2. **Create subscription** → Protocol **Email** → your address →
   **confirm the link** in the email (status must become **Confirmed**).

### Create the IoT Rule
1. IoT Core → **Message routing → Rules → Create rule**, name `litter_alerts_to_sns`.
2. SQL — surfaces the readable text as `headline` while keeping all fields:
   ```sql
   SELECT notification AS headline, * FROM 'cps/alerts/#'
   ```
3. Action: **Simple Notification Service (SNS)** → topic `litter-alerts`,
   message format **RAW**, create role `iot-sns-publish-role`.

## Verify

**Sensors:** MQTT test client → subscribe `cps/sensor/data` → trigger a collection:
```bash
finch exec slm-collector python python/export_petkit_csv.py --publish-architecture
```

**Alerts:** publish a test alert (flows through the real path to SNS → email):
```bash
finch exec slm-mosquitto mosquitto_pub -t 'litter/alerts/maintenance' \
  -m '{"device_id":"100020991","alert_type":"waste_bin_full","notification":"Maintenance alert: the waste bin is full","event_type":"alert_opened"}'
```
Check: the message appears in the test client on `cps/alerts/#`, and an email
arrives with `headline` as the first field.

See `screenshots/` for captured evidence.

## Security notes

- **Least-privilege:** the policy grants only connect + publish to the exact topics
  used (`cps/sensor/data`, `cps/alerts/urgent`, `cps/alerts/maintenance`) — no
  subscribe, no wildcard publish. A publish to any other topic is rejected and the
  connection dropped (observed and intended behaviour).
- **Certificates/keys** live only in `aws/certs/` and are gitignored
  (`*.pem`, `*.key`, `*.crt`) — never committed.
- `iot-policy.json` is templated, so no account ID is stored in git.
- **SNS uses email** (free tier), not SMS.
- For production: scope `iot:Connect` per device, rotate certificates, and add an
  alert cooldown to prevent notification storms.

## Cost

All services used (IoT Core, IoT Rules, SNS email) are within the AWS Free Tier at
this volume (~2,300 messages/day). Effective cost: ~$0.
