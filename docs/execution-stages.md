# Application Execution Lifecycle & Features

This document explains what happens at every stage of the display's execution lifecycle—from cold boot to steady-state rendering, error recovery, and shutdown—and details all the features active at each step.

---

## 🗺️ Lifecycle Flowchart

```
 [Cold Boot / Power On]
           |
           v
 [Stage 1: Boot & Config] -----> (No config or invalid?) -----> [Stage 2: Setup Portal AP]
           |                                                              |
           | (Config Valid)                                               v
           v                                                     (Save & Reset into Config)
 [Stage 3: Wi-Fi & NTP Clock]
           |
           v
 [Stage 4: Initial Departure Fetch] (Fails? -> Use Baked-in Snapshot)
           |
           v
 +-------------------------------------------------------------------------+
 |                         RUNNING STATE (DUAL-CORE)                       |
 |                                                                         |
 |   [Stage 5: Core 1 Render Loop]           [Stage 6: Core 0 Update Loop] |
 |   - 60 Hz timer loop                      - 120s polling interval       |
 |   - Dot-matrix typography                 - Realtime Trains API query   |
 |   - Smooth text scrolling                 - Calling points fetch        |
 |   - Partial row SSD1322 flush             - 401 token auto-renewal      |
 |   - Zero-allocation render                - WAL replay to Grafana Loki  |
 +-------------------------------------------------------------------------+
           |
           +-----> [Stage 7: Error Handling & Self-Healing]
           |       - 429 Rate limit backoff (No reboot)
           |       - API 5xx / DNS outage -> Keep clock, stale dot (No reboot)
           |       - Radio/Wi-Fi wedge -> Fast 1.2s hardware reboot
           v
 [Stage 8: Graceful Shutdown & Crash Watchdog]
           |
           v
    [Hardware Reset]
```

---

## Stage 1: Cold Boot & Configuration Discovery

When power is applied to the Pico 2 W, `main.main()` runs first on **Core 0**:

| Feature | Where | What it does |
| :--- | :--- | :--- |
| **Config Loader & Validation** | `src/config.py` | Reads `config.json`, validates types and value bounds. If missing or corrupt, branches to **Stage 2: Setup Portal**. |
| **Baked-in Build Tokens** | `src/baked.py` | Allows pre-embedding `RTT_TOKEN` or `OTEL_HEADERS` into firmware so devices can be gifted without manual credential entry. |
| **Flash Write-Ahead Log (WAL)** | `src/wal.py` | Mounts flash filesystem, auto-purges corrupted/orphaned `.tmp` files, and prepares `wal.log` for persistent logging. |
| **Observability Initialization** | `src/otel.py` | Configures the OpenTelemetry client, creates a unique boot Run ID, and logs startup memory capacity. |

---

## Stage 2: Provisioning & Captive Portal (`setup`)

If `config.json` is missing or unreadable, the board enters configuration mode:

| Feature | Where | What it does |
| :--- | :--- | :--- |
| **OLED Status Display** | `src/widgets.py` | Drives the SSD1322 screen to display the setup SSID (`Pico Train Display`), password (`12345678`), and URL (`http://192.168.4.1`). |
| **Live SSID Discovery** | `src/services/wifi.py` | Scans nearby Wi-Fi networks in the background, sorts by signal strength (RSSI), and deduplicates mesh network nodes sharing the same SSID. |
| **Async Web Server** | `src/setup/server.py` | Serves an interactive mobile-friendly web page allowing the user to select their Wi-Fi, enter their password, and pick their home/destination stations. |
| **Clean Connection Teardown** | `src/setup/server.py` | Flushes the HTTP 200 response to the browser, pauses for client disassociation, deactivates the AP, and resets into the new config. |

---

## Stage 3: Network Connection & Time Synchronization

Once valid configuration is loaded, `main.run()` establishes network connectivity:

| Feature | Where | What it does |
| :--- | :--- | :--- |
| **Wi-Fi Association** | `src/services/wifi.py` | Connects to the configured SSID with a 15-second timeout and animates connection progress dots on the OLED display. |
| **Power Management Lockdown (`PM_NONE`)** | `src/services/wifi.py` | Forces `wlan.config(pm=0xa11140)` on every connection to stop the CYW43 Wi-Fi chip from entering DTIM sleep between router beacons (preventing ~90% of radio drops). |
| **NTP Network Time Sync** | `src/services/ntp.py` | Synchronizes the RP2350 hardware RTC to UTC via standard Network Time Protocol servers. |
| **UK Daylight Saving Time Rules** | `src/utils.py` | Computes British Summer Time (BST) offsets automatically so the clock matches real-world platform clocks year-round. |

---

## Stage 4: Initial Departure Fetch & Fallback Safety

Before launching the display render engine, the system fetches the first set of live train departures:

| Feature | Where | What it does |
| :--- | :--- | :--- |
| **Synchronous First Fetch** | `src/trains.py` | Contacts Realtime Trains to retrieve initial departures and calling points. |
| **Baked-In Snapshot Fallback** | `src/fallback.py` | If the network or API fails on initial boot, immediately loads a pre-captured real-world weekday morning timetable (Stoke Mandeville to London Marylebone) so the screen is never blank. |
| **Boot Telemetry Delivery** | `src/otel.py` | Flushes startup and memory logs to Grafana Cloud Loki before entering the main loop. |

---

## Stage 5: Dual-Core Display Render Engine (Core 1)

`main.run()` spawns a dedicated high-priority render thread on **Core 1** (`_render_thread`):

| Feature | Where | What it does |
| :--- | :--- | :--- |
| **Dual-Core State Isolation** | `src/state.py` | Uses atomic snapshot swapping (`StateController`) so Core 1 draws immutable copies of board data without lock contention with Core 0. |
| **8080 8-Bit Parallel Bus** | `src/parallel8080.py` | Bit-bangs the 256x64 SSD1322 OLED over an 8-bit parallel bus using MicroPython `viper` assembly, matching hardware timing requirements ($\ge 300\text{ns}$ cycle, $\ge 60\text{ns}$ strobe pulse). |
| **Partial Row Flushing** | `src/ssd1322.py` | Compares previous and current framebuffers and transmits **only modified row spans** (e.g. 9 rows = 1,152 bytes vs 8,192 bytes full frame), reducing bus traffic by **86%** and eliminating visual tearing. |
| **Zero-Allocation Rendering** | `src/widgets.py` | All framebuffers, command arrays, and row buffers are pre-allocated. Eliminates per-frame `gc.collect()` in the render thread for stutter-free 60 FPS output. |
| **Smooth Text Scrolling** | `src/widgets.py` | Scrolls long calling-point lists using precise microsecond clock offsets rather than frame-step increments for buttery-smooth motion. |
| **Authentic Rail Typography** | `src/fonts.py` | Draws departures using a faithful dot-matrix typeface with destination, scheduled time, expected time, platform badges, and ticking seconds indicator. |

---

## Stage 6: Steady-State Polling & Token Management (Core 0)

While Core 1 renders at 60 Hz, Core 0 runs the scheduled background update loop:

| Feature | Where | What it does |
| :--- | :--- | :--- |
| **Request Budgeting (120s Pace)** | `src/main.py` | Polls Realtime Trains every 120 seconds (32 requests/hour), staying well inside the 100 req/hr and 1,000 req/day API rate limits. |
| **Dynamic Calling Points** | `src/services/rtt.py` | Detects when the next departing train changes and automatically fetches its full calling points. |
| **401 Token Auto-Renewal** | `src/services/rtt.py` | Automatically exchanges expired OAuth/access tokens in <1 second without interrupting display output. |
| **Persistent WAL Replay** | `src/otel.py` | Replays any offline back-buffered logs from flash memory (`wal.log`) to Grafana Cloud Loki upon reconnection. |
| **Heap Memory Monitoring** | `src/main.py` | Tracks free/allocated heap memory (typically ~22% utilized, ~356 KB free) and triggers proactive garbage collection on Core 0 during idle polling windows. |

---

## Stage 7: Self-Healing & Error Handling

When network, API, or hardware faults occur, the error handling pipeline routes them intelligently:

```
                            Departure Update Failed
                                       |
             +-------------------------+-------------------------+
             |                                                   |
     RateLimitError (429)                               Other Exception
             |                                                   |
  Wait error.retry_after (e.g. 120s)              +--------------+--------------+
      (NO REBOOT)                                 |                             |
                                          Socket/Radio Error?            API Server Error?
                                     (EHOSTUNREACH, ETIMEDOUT,         (500, 502, 503, 404,
                                      isconnected() == False)           DNS lookup failure)
                                                  |                             |
                                             REBOOT NOW                 Wi-Fi is healthy!
                                          (machine.reset())             Mark board stale,
                                                                       keep clock ticking,
                                                                       wait update_interval
                                                                             (120s)
                                                                           (NO REBOOT)
```

| Feature | Where | What it does |
| :--- | :--- | :--- |
| **429 Rate Limit Backoff** | `src/main.py` | Backs off for `retry_after` seconds without rebooting or spamming the server. |
| **API Outage Resilience (5xx/404/DNS)** | `src/main.py` | If Realtime Trains is down or DNS fails while Wi-Fi is connected, the board **does not reboot**. The clock keeps ticking, the last valid departures stay visible, and the **1-pixel Stale Dot** illuminates. |
| **Fast Hardware Reset on Radio Wedge** | `src/main.py` | When the CYW43 Wi-Fi chip locks up (`STAT_CONNECTING` / `EHOSTUNREACH` / socket timeout), the device raises `_RadioIsGone` and reboots immediately. The Pico resets and re-associates in **1.2 seconds**, cutting recovery time from 30–45 minutes down to ~10 seconds. |

---

## Stage 8: Graceful Shutdown & Crash Watchdog

When a reboot is required (either from a wedged radio or unhandled exception):

| Feature | Where | What it does |
| :--- | :--- | :--- |
| **Shutdown Watchdog** | `src/main._arm_shutdown_watchdog` | Arms a hardware watchdog timer (`machine.WDT`) to guarantee that the chip resets even if network socket cleanup hangs. |
| **Flash WAL Flush** | `src/wal.py`, `src/otel.py` | Writes any unsent log entries to flash memory so crash reasons and stack traces survive the reboot. |
| **Hardware Reset** | `src/main.main` `finally` | Calls `machine.reset()` for a clean hardware power-cycle of the RP2350 CPU and CYW43 radio registers. |
