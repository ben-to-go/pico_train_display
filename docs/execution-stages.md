# Application Execution Lifecycle & Features

This document explains what happens at every stage of the display's execution lifecycle—from cold boot to steady-state dual-core rendering, non-fatal runtime error recovery, and hardware reset—and details all the features active at each step.

---

## 🗺️ Complete Execution Lifecycle & Architecture Flowchart

The following unified flowchart maps the entire runtime lifecycle of the application:

```
                                         [POWER ON / COLD BOOT]
                                                    |
                                                    v
                                    +-------------------------------+
                                    | STAGE 1: BOOT & CONFIG LOADER |
                                    | - Read & validate config.json |
                                    | - Purge corrupted .tmp files  |
                                    | - Init Flash WAL (wal.log)    |
                                    | - Create boot Run ID (otel)   |
                                    +---------------+---------------+
                                                    |
                                       Config Valid?|
                               +--------------------+--------------------+
                               |                                         |
                            (No / Corrupt)                            (Yes)
                               |                                         |
                               v                                         v
            +--------------------------------------+   +-----------------------------------+
            | STAGE 2: CAPTIVE SETUP PORTAL (AP)   |   | STAGE 3: WI-FI & TIME SYNC        |
            | - Show AP SSID & URL on OLED         |   | - Join Wi-Fi (15s timeout)        |
            | - Scan & rank SSIDs by RSSI          |   | - Lock power mode (PM_NONE)       |
            | - Serve web portal (192.168.4.1)     |   | - NTP sync to UTC                 |
            | - Write config.json & reboot         |   | - Calculate UK BST clock offsets  |
            +------------------+-------------------+   +-----------------+-----------------+
                               |                                         |
                               +--------------------+                    v
                                                    |  +-----------------------------------+
                                                    |  | STAGE 4: INITIAL DEPARTURE FETCH  |
                                                    |  | - Fetch first live departures     |
                                                    |  | - If offline: Load Baked-in       |
                                                    |  |   Stoke Mandeville snapshot       |
                                                    |  | - Ship startup boot telemetry     |
                                                    |  +-----------------+-----------------+
                                                    |                    |
                                                    v                    v
+-------------------------------------------------------------------------------------------------------------------+
|                                 STAGE 5: RUNNING STATE (DUAL-CORE ARCHITECTURE)                                   |
|                                                                                                                   |
|  CORE 1: HIGH-PRIORITY RENDER ENGINE (60 FPS)             CORE 0: ORCHESTRATION, POLLING & RECOVERY (120s)        |
|  --------------------------------------------             ------------------------------------------------        |
|                                                                                   |                               |
|  +-----------------------------------------+                             [Sleep update_interval]                  |
|  | Loop: every 16.7ms (60 Hz timer)        |                                      |                               |
|  | 1. Read UK time (RTC + BST offset)      |                             [Check Wi-Fi Link]                       |
|  | 2. Fetch latest BoardState snapshot     |                                      |                               |
|  | 3. Render departures & calling points   |                     +----------------+----------------+              |
|  | 4. Update live clock & blinking seconds |                     |                                 |              |
|  | 5. Find dirty row span                  |                Link Lost?                         Link OK            |
|  | 6. Partial flush to SSD1322 parallel bus|                     |                                 |              |
|  |    (1,152B vs 8,192B full frame)        |              (Raise _RadioIsGone)            [Fetch Departures]      |
|  +-----------------------------------------+                     |                                 |              |
|                       ^                                          |                      +----------+----------+   |
|                       | Atomic Snapshot                          |                      |                     |   |
|                       | StateController.swap()                   |                   Success?               Error |
|                       v                                          |                      |                     |   |
|  +-----------------------------------------+                     |            [Update BoardState]             |   |
|  | Active BoardState:                      |                     |            [Clear Stale Dot]               |   |
|  | - Station & Destination                 |                     |            [Replay Flash WAL]              |   |
|  | - Services 1..3 (Time, Dest, Platform)  |                     |                      |                     |   |
|  | - Calling points text                   |                     |                      v                     |   |
|  | - Stale dot flag (True/False)           |                     |            [Loop to next cycle]            |   |
|  +-----------------------------------------+                     |                                            |   |
|                                                                  |       +------------------------------------+   |
|                                                                  |       | Non-Fatal Runtime Error Handling       |
|                                                                  |       | (See docs/fallback.md for details)     |
|                                                                  |       v                                        v
|                                                                  |  [429 Rate Limit]                    [API 5xx/404/DNS]
|                                                                  |  - Read Retry-After                  - Keep old departures
|                                                                  |  - Sleep retry_after s               - Keep clock ticking
|                                                                  |  - NO REBOOT                         - Set Stale Dot = True
|                                                                  |  - NO UI INTERRUPTION                - Retry in 120s
|                                                                  |       |                              - NO REBOOT
|                                                                  |       +-------------------+--------------------+
|                                                                  |                           |
|                                                                  |                           v
|                                                                  |                 [Loop to next cycle]
|                                                                  |
+------------------------------------------------------------------|------------------------------------------------+
                                                                   |
                                                                   | (Fatal Radio Loss / Socket Lockup)
                                                                   v
                                               +---------------------------------------+
                                               | STAGE 6: GRACEFUL SHUTDOWN & RESET    |
                                               | - Flush crash log to flash WAL        |
                                               | - Arm hardware watchdog (machine.WDT) |
                                               | - machine.reset() (reboots in 1.2s)   |
                                               +---------------------------------------+
```

---

## Stage-by-Stage Feature Breakdown

### Stage 1: Cold Boot & Configuration Discovery

When power is applied to the Pico 2 W, `main.main()` executes on **Core 0**:

| Feature | Where | What it does |
| :--- | :--- | :--- |
| **Config Loader & Validation** | `src/config.py` | Reads `config.json`, validates types and value bounds. If missing or corrupt, branches to **Stage 2: Setup Portal**. |
| **Baked-in Build Tokens** | `src/baked.py` | Allows pre-embedding `RTT_TOKEN` or `OTEL_HEADERS` into firmware so devices can be gifted without manual credential entry. |
| **Flash Write-Ahead Log (WAL)** | `src/wal.py` | Mounts flash filesystem, auto-purges corrupted/orphaned `.tmp` files, and prepares `wal.log` for persistent logging. |
| **Observability Initialization** | `src/otel.py` | Configures OpenTelemetry exporter, creates a unique boot Run ID, and records startup memory capacity. See [docs/observability.md](observability.md). |

---

### Stage 2: Provisioning & Captive Portal (`setup`)

If `config.json` is missing or unreadable, the board enters configuration mode:

| Feature | Where | What it does |
| :--- | :--- | :--- |
| **OLED Status Display** | `src/widgets.py` | Drives the SSD1322 screen to display the setup SSID (`Pico Train Display`), password (`12345678`), and URL (`http://192.168.4.1`). |
| **Live SSID Discovery** | `src/services/wifi.py` | Scans nearby Wi-Fi networks in the background, sorts by signal strength (RSSI), and deduplicates mesh network nodes sharing the same SSID. |
| **Async Web Server** | `src/setup/server.py` | Serves an interactive mobile-friendly web page allowing the user to select their Wi-Fi, enter their password, and pick their home/destination stations. |
| **Clean Connection Teardown** | `src/setup/server.py` | Flushes the HTTP 200 response to the browser, pauses for client disassociation, deactivates the AP, and resets into the new config. |

---

### Stage 3: Network Connection & Time Synchronization

Once valid configuration is loaded, `main.run()` establishes network connectivity:

| Feature | Where | What it does |
| :--- | :--- | :--- |
| **Wi-Fi Association** | `src/services/wifi.py` | Connects to the configured SSID with a 15-second timeout and animates connection progress dots on the OLED display. |
| **Power Management Lockdown (`PM_NONE`)** | `src/services/wifi.py` | Forces `wlan.config(pm=0xa11140)` on every connection to stop the CYW43 Wi-Fi chip from entering DTIM sleep between router beacons (preventing ~90% of radio drops). |
| **NTP Network Time Sync** | `src/services/ntp.py` | Synchronizes the RP2350 hardware RTC to UTC via standard Network Time Protocol servers. |
| **UK Daylight Saving Time Rules** | `src/utils.py` | Computes British Summer Time (BST) offsets automatically so the clock matches real-world platform clocks year-round. |

---

### Stage 4: Initial Departure Fetch & Fallback Safety

Before launching the display render engine, the system fetches the first set of live train departures:

| Feature | Where | What it does |
| :--- | :--- | :--- |
| **Synchronous First Fetch** | `src/trains.py` | Contacts Realtime Trains to retrieve initial departures and calling points. |
| **Baked-In Snapshot Fallback** | `src/fallback.py` | If the network or API fails on initial boot, immediately loads a pre-captured real-world weekday morning timetable (Stoke Mandeville to London Marylebone) so the screen is never blank. See [docs/fallback.md](fallback.md). |
| **Boot Telemetry Delivery** | `src/otel.py` | Flushes startup and memory logs to Grafana Cloud Loki before entering the main loop. |

---

### Stage 5: The Running State (Dual-Core Engine & Non-Fatal Recovery)

`main.run()` orchestrates steady-state execution across both CPU cores:

#### Core 1: Dedicated 60 FPS Render Engine
| Feature | Where | What it does |
| :--- | :--- | :--- |
| **Dual-Core State Isolation** | `src/state.py` | Uses atomic snapshot swapping (`StateController`) so Core 1 draws immutable copies of board data without lock contention with Core 0. |
| **8080 8-Bit Parallel Bus** | `src/parallel8080.py` | Bit-bangs the 256x64 SSD1322 OLED over an 8-bit parallel bus using MicroPython `viper` assembly, matching hardware timing requirements ($\ge 300\text{ns}$ cycle, $\ge 60\text{ns}$ strobe pulse). |
| **Partial Row Flushing** | `src/ssd1322.py` | Compares previous and current framebuffers and transmits **only modified row spans** (e.g. 9 rows = 1,152 bytes vs 8,192 bytes full frame), reducing bus traffic by **86%** and eliminating visual tearing. |
| **Zero-Allocation Rendering** | `src/widgets.py` | All framebuffers, command arrays, and row buffers are pre-allocated. Eliminates per-frame `gc.collect()` in the render thread for stutter-free 60 FPS output. |
| **Smooth Text Scrolling** | `src/widgets.py` | Scrolls long calling-point lists using precise microsecond clock offsets rather than frame-step increments for buttery-smooth motion. |
| **Authentic Rail Typography** | `src/fonts.py` | Draws departures using a faithful dot-matrix typeface with destination, scheduled time, expected time, platform badges, and ticking seconds indicator. See [docs/display-format.md](display-format.md). |

#### Core 0: Scheduled Polling & Runtime Recovery
| Feature | Where | What it does |
| :--- | :--- | :--- |
| **Request Budgeting (120s Pace)** | `src/main.py` | Polls Realtime Trains every 120 seconds (32 requests/hour), staying well inside the 100 req/hr and 1,000 req/day API rate limits. |
| **Dynamic Calling Points** | `src/services/rtt.py` | Detects when the next departing train changes and automatically fetches its full calling points. |
| **401 Token Auto-Renewal** | `src/services/rtt.py` | Automatically exchanges expired OAuth/access tokens in <1 second without interrupting display output. |
| **429 Rate Limit Backoff** | `src/main.py` | Automatically backs off for `error.retry_after` seconds without rebooting or spamming the server. |
| **API Outage Resilience (5xx/404/DNS)** | `src/main.py` | If Realtime Trains is down or DNS fails while Wi-Fi is connected, the board **does not reboot**. The clock keeps ticking, the last valid departures stay visible, and the **1-pixel Stale Dot** illuminates. Details in [docs/fallback.md](fallback.md). |
| **Persistent WAL Replay** | `src/otel.py` | Replays any offline back-buffered logs from flash memory (`wal.log`) to Grafana Cloud Loki upon reconnection. |
| **Heap Memory Monitoring** | `src/main.py` | Tracks free/allocated heap memory (typically ~22% utilized, ~356 KB free) and triggers proactive garbage collection on Core 0 during idle polling windows. |

---

### Stage 6: Graceful Shutdown & Hardware Reset

When an unrecoverable radio error occurs (such as the CYW43 Wi-Fi chip locking up with `STAT_CONNECTING`, `EHOSTUNREACH`, or persistent socket timeouts):

| Feature | Where | What it does |
| :--- | :--- | :--- |
| **Fast Radio Loss Detection** | `src/main.py` | Detects link loss or hardware socket failure and raises `_RadioIsGone` immediately, bypassing futile software reconnects. |
| **Shutdown Watchdog** | `src/main._arm_shutdown_watchdog` | Arms a hardware watchdog timer (`machine.WDT`) to guarantee that the chip resets even if network socket cleanup hangs. |
| **Flash WAL Flush** | `src/wal.py`, `src/otel.py` | Writes any unsent log entries to flash memory so crash reasons and stack traces survive the reboot. |
| **Fast 1.2s Hardware Reset** | `src/main.main` `finally` | Calls `machine.reset()`, power-cycling the RP2350 CPU and CYW43 radio registers and returning the board to full operation in **~10 seconds**. |
