# Ubuntu Migration — MT5 Bridge on Linux

**Date:** 2026-11-20  
**Goal:** Move the MT5 bridge from a Windows Server (native) to an Ubuntu VPS using Wine + Docker, eliminating the Windows Server licensing cost.

---

## What This Is and Why

Our platform has one Windows-only component: `services/mt5-bridge`. It uses the `MetaTrader5` Python package, which communicates with the MT5 terminal via **Windows named pipes (IPC)**. This cannot run natively on Linux.

The solution: run MT5 terminal + Windows Python inside **Wine** (a Windows compatibility layer), expose it over a network socket using **mt5linux** (an RPyC bridge library), and have our native Linux `mt5-bridge` talk to it over localhost.

```
┌─────────────────────────────────────────────────────────┐
│  Docker Container: gmag11/metatrader5_vnc               │
│                                                         │
│  MT5 terminal64.exe  ←── Windows IPC ───  Python 3.9   │
│       (Wine)                               (Wine)       │
│                                            mt5linux      │
│                                            RPyC server  │
│                                            port 8001    │
└──────────────────────────────┬──────────────────────────┘
                               │ TCP localhost:8001
┌──────────────────────────────▼──────────────────────────┐
│  mt5-bridge (Docker, native Linux Python 3.11)          │
│                                                         │
│  from mt5linux import MetaTrader5                       │
│  mt5 = MetaTrader5(host='mt5', port=8001)               │
│  mt5.initialize()  → mt5.order_send()  → ...            │
└─────────────────────────────────────────────────────────┘
```

---

## Server Specs (srv)

| Item | Value |
|---|---|
| OS | Ubuntu 22.04.5 LTS |
| CPU | 4 vCPU |
| RAM | 7.8 GB |
| Disk | 73 GB (32 GB free) |
| Docker | 29.5.2 |
| Compose | v5.1.4 |

---

## What We Tried First (and Why It Failed)

Before finding the Docker approach, we tried installing Wine manually on the Ubuntu host:

1. Installed WineHQ stable 11.0 on the host OS
2. Created a Wine64 prefix at `/opt/mt5`
3. Installed Python 3.11 for Windows inside Wine
4. Installed `MetaTrader5` and `rpyc` in Wine Python
5. Tried to install MT5 terminal using `mt5setup.exe`

**Result:** MT5 installer blocked with:
> "A debugger has been found running in your system. Please unload it from memory and restart your program."

**Why:** MT5's installer has anti-tamper protection that calls `IsDebuggerPresent()`. Wine's kernel always returns `TRUE` for this call because Wine acts as a compatibility layer. No Wine flag (`WINEDEBUG=-all`, etc.) can suppress this — it's implemented at the kernel level.

**Cleanup:** All manual Wine installation was removed — Wine packages purged, `/opt/mt5` prefix deleted, temp files removed. The server was returned to a clean state.

---

## The Correct Approach: gmag11/metatrader5_vnc Docker Image

The Docker image `gmag11/metatrader5_vnc:latest` pre-installs everything and handles the MT5 installer anti-debug check internally (by running the installer in a controlled Wine environment during the image build).

**What the image provides:**
- Wine with a pre-configured prefix
- MT5 terminal64.exe installed inside Wine
- Python 3.9 (Windows, 32-bit) inside Wine with `MetaTrader5` package
- KasmVNC browser-based desktop on port 3000 (for initial GUI login)
- mt5linux RPyC server on port 8001

---

## Bugs Found and Fixed During Testing

### Bug 1: mt5linux 1.1.1 is incompatible with Python 3.11

**What happened:** The container's first-boot script (`/Metatrader/start.sh`) runs `pip install mt5linux` without a version pin. In 2026, mt5linux released version 1.1.1 which uses Python 3.12 syntax (multi-line f-strings, PEP 701). The container runs Python 3.11, which does not support this syntax.

**Error seen:**
```
[7/7] Starting the mt5linux server...
SyntaxError: unterminated string literal (detected at line 1755)
  File "mt5linux/metatrader5.py", line 1755
    code = f'mt5.copy_rates_from("{symbol}", {timeframe}, {
           ^
[7/7] Failed to start the mt5linux server on port 8001.
```

**Fix:** Pin `mt5linux==0.1.9` in `/Metatrader/start.sh`:
```bash
# Line 102 — Wine Python install
# BEFORE: "mt5linux>=0.1.9"
# AFTER:
"mt5linux==0.1.9"

# Line 114 — Linux Python install
# BEFORE: mt5linux
# AFTER:
"mt5linux==0.1.9"
```

---

### Bug 2: Stale compiled bytecode (__pycache__)

**What happened:** After downgrading mt5linux to 0.1.9, Python still loaded the old 1.1.1 logic. The `__pycache__` directory contained a compiled `.pyc` file from the previous 1.1.1 install. Python loads `.pyc` files preferentially over `.py` source files if the cache exists.

**Fix:** Delete the stale cache:
```bash
docker exec -u abc mt5-test rm -rf /config/.local/lib/python3.11/site-packages/mt5linux/__pycache__
```

---

### Bug 3: Wine prefix ownership — server must run as user `abc`

**What happened:** The linuxserver.io base image stores the Wine prefix at `/config/.wine`, owned by user `abc` (uid 911). The startup script runs as root. When root tries to call `wine python.exe`, Wine refuses:
```
wine: '/config/.wine' is not owned by you
```

**Fix:** Launch the mt5linux RPyC server as user `abc`:
```bash
# In start.sh line 126, run as abc not root
su abc -c "python3 -m mt5linux --host 0.0.0.0 -p 8001 -w wine python.exe" &
```
Or via docker exec manually:
```bash
docker exec -u abc mt5-test python3 -m mt5linux --host 0.0.0.0 -p 8001 -w wine python.exe &
```

---

### Bug 4: /tmp/mt5linux/server.py owned by root

**What happened:** mt5linux 0.1.9 generates a `server.py` script and writes it to `/tmp/mt5linux/` before launching it inside Wine Python. A previous root-level test run had already created this file owned by root. When the server launched as `abc`, it couldn't overwrite the file.

**Error:**
```
PermissionError: [Errno 13] Permission denied: '/tmp/mt5linux/server.py'
```

**Fix:**
```bash
docker exec mt5-test chown abc /tmp/mt5linux
docker exec mt5-test rm /tmp/mt5linux/server.py  # remove stale root-owned file
```

---

### Bug 5: rpyc version mismatch between client and server

**What happened:** The RPyC server runs inside Wine Python 3.9, which has `rpyc 5.0.1` (installed when mt5linux was first set up in 2021). The Linux Python client connects using the latest `rpyc 6.0.2`. Versions 5 and 6 are protocol-incompatible.

**Error:**
```
ValueError: invalid message type: 18
```

**Fix:** Pin `rpyc==5.0.1` on the Linux client side (mt5-bridge's requirements):
```
mt5linux==0.1.9
rpyc==5.0.1
```

---

## Final Verified Working Configuration

After all fixes, the full chain was confirmed working:

| Component | Version | Location |
|---|---|---|
| Docker image | `gmag11/metatrader5_vnc:latest` | Container |
| MT5 terminal | terminal64.exe | Wine inside container |
| Wine Python | 3.9.13 (32-bit) | Wine inside container |
| mt5linux (server-side) | 0.1.9 | Wine Python site-packages |
| rpyc (server-side) | 5.0.1 | Wine Python site-packages |
| Linux Python | 3.11 | Container system |
| mt5linux (client-side) | 0.1.9 | Linux Python site-packages |
| rpyc (client-side) | **must be 5.0.1** | mt5-bridge requirements.txt |
| RPyC port | 8001 | localhost only |
| VNC port | 3000 | localhost only (SSH tunnel) |

**Test result:** `mt5.initialize()` returns `False` with `No IPC connection` — expected until the one-time broker login is done via VNC. RPyC protocol handshake succeeds cleanly.

---

## One-Time Broker Login (VNC)

The MT5 terminal needs a manual login to cache the broker server certificates. This is done once via the browser VNC interface and persists in the `/config` volume forever after.

### How to access VNC

**What `ssh -L 3000:127.0.0.1:3000 srv` does:**

Port 3000 on the server is only accessible from the server itself (bound to `127.0.0.1`, not the public internet). The SSH command creates a secure tunnel: your local `localhost:3000` is forwarded through the SSH connection to the server's private `127.0.0.1:3000`. Without this tunnel, there is no way to reach port 3000 from outside the server.

```
Your Browser           SSH Tunnel           Server
localhost:3000  ──────────────────────►  127.0.0.1:3000
                                              │
                                         Docker container
                                         KasmVNC desktop
```

### Steps

1. Open a terminal and run:
   ```bash
   ssh -L 3000:127.0.0.1:3000 srv
   ```
   Keep this terminal open.

2. Open `http://localhost:3000` in your browser.

3. Sign in with:
   - **Username:** `admin`
   - **Password:** `testpass`

4. You will see the MT5 terminal desktop. Inside MT5:
   - `File` → `Login to Trade Account`
   - Enter: Login = `26396767`, Password = your Vantage password, Server = Vantage Markets server
   - Tick **Save password**
   - Click OK

5. `Tools` → `Options` → `Expert Advisors` → tick **Allow algorithmic trading** → OK

6. Close the browser tab. The terminal keeps running in the background.

After this, `mt5.initialize()` will return `True` and `mt5.account_info().login` will return `26396767`.

---

## Production docker-compose.yml Changes Needed

When integrating into the main platform stack, two changes are required:

### 1. Add the mt5 container

```yaml
  mt5:
    image: gmag11/metatrader5_vnc:latest
    container_name: mt5-terminal
    restart: unless-stopped
    ports:
      - "127.0.0.1:3000:3000"   # VNC — SSH tunnel only, never expose publicly
      - "127.0.0.1:8001:8001"   # RPyC bridge
    volumes:
      - ./mt5_config:/config    # persists login, Wine prefix, everything
    environment:
      - CUSTOM_USER=admin
      - PASSWORD=changeme_vnc   # change this before deploying
    networks: [mt5-net]
    restart: unless-stopped
```

### 2. Update mt5-bridge

`services/mt5-bridge/main.py`:
```python
# BEFORE (Windows):
import MetaTrader5 as mt5

# AFTER (Ubuntu):
from mt5linux import MetaTrader5
mt5 = MetaTrader5(host='mt5', port=8001)  # 'mt5' = container name on mt5-net
```

`services/mt5-bridge/requirements.txt` — add:
```
mt5linux==0.1.9
rpyc==5.0.1
```

---

## Remaining Cleanup in the Test Container

The current `mt5-test` container on `srv` has manual fixes applied. For a clean production deployment, all fixes need to be baked into a patched `start.sh` mounted at startup or built into a custom Dockerfile. See the **Production Hardening** section below.

### start.sh patches needed (lines to change)

| Line | Before | After |
|---|---|---|
| 102 | `"mt5linux>=0.1.9"` | `"mt5linux==0.1.9"` |
| 114 | `--no-deps mt5linux` | `--no-deps "mt5linux==0.1.9"` |
| 126 | `python3 -m mt5linux ...` | `su abc -c "python3 -m mt5linux ..."` |

Also add after line 126:
```bash
chown abc /tmp/mt5linux 2>/dev/null || true
```

---

## Production Hardening Checklist

- [ ] Change VNC password from `testpass` to something strong in `.env`
- [ ] Mount patched `start.sh` as a volume override or build a custom Dockerfile
- [ ] Add Sunday cron restart: `0 2 * * 0 docker restart mt5-terminal`
- [ ] Via VNC: close all charts, set `Tools → Options → Charts → Max bars = 5000`
- [ ] Never expose ports 3000 or 8001 to the public internet
- [ ] Keep `./mt5_config` volume backed up — it holds your broker login cache
