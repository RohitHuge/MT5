# MT5 Platform — Ubuntu Migration Plan

**Date:** 2026-11-20  
**Goal:** Run the entire MT5 trading platform on the Ubuntu server (`srv`) with zero Windows
dependency. MT5 terminal runs via the official MetaQuotes Linux package (their own patched Wine,
no anti-debug issues). All other services run in Docker.

---

## Architecture (Final State)

```
srv (Ubuntu 22.04)
│
├── HOST PROCESSES
│   ├── Xvfb :99          ← virtual framebuffer (fake screen, systemd service)
│   └── MT5 terminal       ← official MetaQuotes Linux package (systemd service)
│           └── MT5Bridge.mq5 EA  ← always loaded on chart, TCP :8002
│
└── DOCKER (mt5-net)
    ├── mt5-bridge          ← calls host:8002 via host.docker.internal
    ├── candle-feed
    ├── strategy-ema-scalp
    ├── strategy-liq-sweep
    ├── strategy-ob-fvg
    ├── executor
    ├── shadow-tracker
    ├── redis
    ├── postgres
    └── dashboard
```

**Data flow:**
```
Strategies → mt5-bridge → HTTP :8002 → MT5Bridge EA → MT5 native API → broker
```

---

## What Is Already Done

| Phase | Task | Status |
|---|---|---|
| 1 | `experts/MT5Bridge.mq5` — MQL5 EA with HTTP server on :8002 | ✅ Done |
| 2 | `services/mt5-bridge/main.py` — replaced Python MT5 package with httpx | ✅ Done |
| 2 | `services/mt5-bridge/requirements.txt` — removed MetaTrader5, added httpx | ✅ Done |
| 2 | `services/mt5-bridge/Dockerfile` — pure Python 3.11, no Windows deps | ✅ Done |
| 3 | `docker-compose.yml` — mt5-bridge added as Docker service | ✅ Done |
| 3 | `.env` / `.env.example` — EA_URL added, MT5_BRIDGE_URL updated | ✅ Done |

All code changes are committed. The remaining work is **infrastructure setup on `srv`** and
**one-time manual login**.

---

## Why the Official MetaQuotes Package (Not Community Wine)

We spent a session trying to run MT5 terminal inside `gmag11/metatrader5_vnc` (community Wine).
It failed because MT5 builds from 2025+ have aggressive anti-tamper checks (`IsDebuggerPresent`,
`NtQueryInformationProcess`) that block startup in unpatched Wine.

The official MetaQuotes Linux package ships its own private Wine build patched specifically for
MT5. The anti-debug check never triggers. From the server's perspective it is an opaque binary —
run the installer, MT5 terminal appears.

---

## Phase 4 — Install MT5 on `srv` (Ubuntu Official Package)

### Task 4.1 — Install Xvfb and x11vnc on the Ubuntu host

Xvfb creates a virtual screen in RAM so MT5 has somewhere to render.
x11vnc is only needed for the one-time login — it can be removed after.

```bash
sudo apt-get update
sudo apt-get install -y xvfb x11vnc
```

### Task 4.2 — Start Xvfb on display :99

```bash
Xvfb :99 -screen 0 1280x800x24 &
export DISPLAY=:99
```

Verify it is running:
```bash
ps aux | grep Xvfb
```

### Task 4.3 — Download and run the official MetaQuotes MT5 installer

```bash
wget https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.sh
chmod +x mt5setup.sh
DISPLAY=:99 ./mt5setup.sh
```

The installer runs against the virtual display. It downloads MT5 terminal and their bundled
Wine runtime. This takes 2–5 minutes depending on connection speed.

MT5 installs to `~/.local/share/metatrader5/` by default.

### Task 4.4 — Start MT5 terminal against the virtual display

```bash
DISPLAY=:99 ~/.local/share/metatrader5/mt5 &
sleep 10
```

### Task 4.5 — One-time broker login via VNC

This is the only manual GUI step. It is done once and never repeated.

**On your laptop — open SSH tunnel:**
```bash
ssh -L 5900:127.0.0.1:5900 srv
```
Keep this terminal open.

**On `srv` — start x11vnc pointing at display :99:**
```bash
x11vnc -display :99 -localhost -nopw -forever &
```

**On your laptop — connect with any VNC viewer:**
- Address: `localhost:5900`
- No password

You will see the MT5 terminal window. Inside MT5:
1. `File` → `Login to Trade Account`
2. Login: `26396767`
3. Password: your Vantage Markets password
4. Server: select Vantage Markets server from dropdown
5. Tick **Save password**
6. Click OK — MT5 connects and loads charts

After login stop x11vnc on the server:
```bash
pkill x11vnc
```

### Task 4.6 — Load MT5Bridge.mq5 during the same GUI session

While still in the VNC session (before stopping x11vnc):

1. Copy the EA to MT5's Experts folder:
   ```bash
   cp /path/to/project/experts/MT5Bridge.mq5 \
      ~/.local/share/metatrader5/MQL5/Experts/
   ```
2. In MT5: open MetaEditor (`Tools → MetaEditor` or F4)
3. Open `MT5Bridge.mq5` and compile (F7) — must show 0 errors, 0 warnings
4. Back in MT5 terminal: open any chart (e.g. XAUUSD M5)
5. Drag `MT5Bridge` from the Navigator panel onto the chart
6. In the EA settings dialog: tick **Allow live trading** and **Allow DLL imports**
7. Click OK — the EA loads, smiley face appears on the chart
8. `Tools` → `Options` → `Expert Advisors` → tick **Allow algorithmic trading** → OK

Verify port 8002 is open from within the VNC terminal:
```bash
ss -tlnp | grep 8002
```
Should show a listening socket.

Now stop x11vnc and close the VNC viewer. MT5 continues running headlessly.

### Task 4.7 — Verify MT5 is accessible from Docker

From the server (not VNC), confirm the EA responds:
```bash
curl http://localhost:8002/health
curl http://localhost:8002/account
```

Expected `/account` response: `{"login":26396767, "balance":...}`

---

## Phase 5 — Systemd Services (Survive Reboots)

After confirming everything works, make Xvfb and MT5 auto-start on boot.

### Task 5.1 — Systemd service for Xvfb

Create `/etc/systemd/system/xvfb.service`:
```ini
[Unit]
Description=Xvfb virtual framebuffer
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/Xvfb :99 -screen 0 1280x800x24
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable xvfb
sudo systemctl start xvfb
```

### Task 5.2 — Systemd service for MT5 terminal

Create `/etc/systemd/system/mt5.service` (replace `YOUR_USER` with the actual username):
```ini
[Unit]
Description=MetaTrader 5 terminal
After=xvfb.service
Requires=xvfb.service

[Service]
Type=simple
User=YOUR_USER
Environment=DISPLAY=:99
ExecStart=/home/YOUR_USER/.local/share/metatrader5/mt5
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable mt5
sudo systemctl start mt5
```

### Task 5.3 — Verify services survive reboot

```bash
sudo reboot
# wait ~60 seconds
ssh srv
systemctl status xvfb mt5
curl http://localhost:8002/health
```

---

## Phase 6 — Start Docker Stack and Validate

### Task 6.1 — Start the full platform

```bash
cd /path/to/project
docker-compose --profile platform up -d
```

All 10 containers should come up healthy.

### Task 6.2 — Smoke test every bridge endpoint

```bash
curl http://localhost:8000/health
curl http://localhost:8000/account
curl http://localhost:8000/symbol/XAUUSD
curl "http://localhost:8000/candles?symbol=XAUUSD&timeframe=M5&count=10"
curl http://localhost:8000/positions
```

### Task 6.3 — Place and close a test order on the demo account

```bash
curl -X POST http://localhost:8000/order \
  -H "Content-Type: application/json" \
  -d '{"symbol":"XAUUSD","direction":"BUY","lots":0.01,"sl":1800.0,"tp":2500.0}'
```

Note the ticket number in the response, then close it:
```bash
curl -X POST http://localhost:8000/close/<ticket>
```

Verify the trade appears and closes in the MT5 terminal.

### Task 6.4 — Verify Redis candle stream

```bash
docker exec mt5-redis redis-cli subscribe candles:XAUUSD:M5
```

Should receive a JSON candle every time a new M5 bar closes (up to 5 minutes wait).

### Task 6.5 — Verify strategy signals flow end-to-end

Check shadow_signals table in Postgres has rows from all three strategies:
```bash
docker exec mt5-postgres psql -U trader -d mt5 \
  -c "SELECT strategy_name, COUNT(*) FROM shadow_signals GROUP BY strategy_name;"
```

---

## Security Checklist Before Going Live

- [ ] Port 8002 (EA) bound to `127.0.0.1` only — not reachable from outside the server
- [ ] Port 8000 (mt5-bridge) exposed only on `127.0.0.1` or behind a reverse proxy
- [ ] `EA_URL` in `.env` uses `host.docker.internal` — not a public IP
- [ ] MT5 terminal password saved in MT5 only — never in any config file or env var
- [ ] `x11vnc` stopped after login — not running permanently
- [ ] `.env` is gitignored — confirm with `git status`
- [ ] Add weekly cron restart for MT5: `0 2 * * 0 systemctl restart mt5`

---

## Full Task Checklist

### Already done (code)
- [x] `experts/MT5Bridge.mq5` written and committed
- [x] `services/mt5-bridge/main.py` updated to use httpx
- [x] `services/mt5-bridge/Dockerfile` created
- [x] `docker-compose.yml` updated with mt5-bridge service
- [x] `.env` / `.env.example` updated with EA_URL

### Remaining (infrastructure on `srv`)
- [ ] 4.1 — Install Xvfb + x11vnc on host
- [ ] 4.2 — Start Xvfb on :99
- [ ] 4.3 — Run official MT5 installer
- [ ] 4.4 — Start MT5 against :99
- [ ] 4.5 — VNC login to Vantage Markets (account 26396767)
- [ ] 4.6 — Compile + load MT5Bridge.mq5 EA on a chart
- [ ] 4.7 — Verify curl http://localhost:8002/health responds
- [ ] 5.1 — Systemd service for Xvfb
- [ ] 5.2 — Systemd service for MT5
- [ ] 5.3 — Reboot test
- [ ] 6.1 — docker-compose --profile platform up -d
- [ ] 6.2 — Smoke test all 5 bridge endpoints
- [ ] 6.3 — Place + close test order on demo
- [ ] 6.4 — Verify Redis candle stream
- [ ] 6.5 — Verify strategy signals in Postgres
