# Features & Architecture by Stage

This document details all features and architectural capabilities in `pico_train_display`, organized by subsystem and development stage.

---

## 🏛️ System Architecture Overview

The system runs on the **Raspberry Pi Pico 2 W** (RP2350 microcontroller), using its dual-core ARM Cortex-M33 architecture to strictly separate networking from display rendering:

```
+------------------------------------------+  +------------------------------------------+
|                 CORE 0                   |  |                 CORE 1                   |
|       (Orchestration & Network)          |  |         (60 FPS Render Engine)           |
+------------------------------------------+  +------------------------------------------+
| - Wi-Fi Management (CYW43 Driver)        |  | - Dedicated 60 Hz Render Thread          |
| - Realtime Trains API (HTTP / TLS / NTP) |  | - Pixel-Accurate Dot-Matrix Typography   |
| - OpenTelemetry Exporter & Flash WAL     |  | - Smooth Sub-Pixel Text Scrolling        |
| - State Management (Immutable Snapshots) |  | - SSD1322 8-bit Parallel Bus Bit-Bang    |
+------------------------------------------+  +------------------------------------------+
                     \                              /
                      \                            /
                       +--------------------------+
                       |    Hardware & Peripherals|
                       | - 256x64 SSD1322 OLED    |
                       | - CYW43439 Wi-Fi Radio   |
                       | - On-Board Flash (WAL)   |
                       +--------------------------+
```

---

## 1. High-Performance Display Engine

### ⚡ 8080 8-Bit Parallel Bus (`src/parallel8080.py`)
- **Fast Bit-Banging**: Replaced slow SPI with an 8-bit parallel bus driven by MicroPython's native `viper` code emitter.
- **Hardware-Accurate Timing**: Calibrates write strobe pulse widths to match the SSD1322 datasheet ($\ge 300\text{ns}$ write cycles, $\ge 60\text{ns}$ low pulses) across varying CPU clock frequencies.

### 🖼️ Partial Row Flushing (`src/ssd1322.py`)
- **86% Bus Traffic Reduction**: Instead of transmitting the full 8,192-byte framebuffer on every frame, the driver detects modified row ranges.
- **Tearing Elimination**: Text scrolling updates only 9 rows (1,152 bytes) and the live clock updates only 9 rows, shrinking the bus collision window and eliminating visual tearing.

### 🚫 Zero-Allocation Render Loop
- **Eliminated Render GC**: Removed per-frame `gc.collect()` calls from the render loop.
- **Zero Jitter**: Pre-allocated command and row buffers prevent garbage collection pauses on Core 1 while the display is scanning out.

### 🔤 Authentic UK Railway Typography (`src/widgets.py`, `src/fonts.py`)
- **Dot-Matrix Font**: Custom pixel-perfect reproduction of UK National Rail platform displays.
- **Live Platform Layout**: Multi-row display with calling points, live clock with blinking seconds dot, delay reasons, and status badges.

---

## 2. API Integration & Request Budgeting

### 🚆 Realtime Trains Next-Gen API (`src/services/rtt.py`, `src/trains.py`)
- **Live Station Boards**: Fetches up-to-the-minute departures, destinations, platforms, delays, and cancellations.
- **Calling Points**: Dynamically pulls calling points for the next departing train.
- **Automatic Token Exchange**: Handles periodic token expiration (HTTP 401) seamlessly in under 1 second.

### ⏱️ Strict Rate Limit Budgeting (`tests/test_rate_limit.py`)
- **Paced Intervals**: Defaults to a 120-second update cycle (32 requests/hour), well within API rate limits (100/hr, 1,000/day).
- **HTTP 429 Exponential Backoff**: Automatically reads `Retry-After` headers and pauses requests without restarting or wedging the board.

---

## 3. Resiliency, Self-Healing & Error Handling

### ⚡ Fast Hardware Reset on Radio Loss (`src/main.py`)
- **Immediate Recovery**: When the CYW43 Wi-Fi chip locks up (`STAT_CONNECTING` / `EHOSTUNREACH`), the board triggers `machine.reset()` immediately.
- **10-Second Recovery**: Reboots and re-associates with Wi-Fi in **1.2 seconds**, cutting recovery time from 30–45 minutes down to ~10 seconds.

### 🛡️ Outage-Proof Behavior (`src/fallback.py`)
- **Baked-in Fallback**: If the network is down at cold boot, displays a pre-captured real-world station snapshot.
- **Stale Data Retention**: If an API outage occurs while running, keeps the last loaded departures and ticking clock on screen.
- **The Stale Dot**: Illuminates a discrete 1-pixel indicator in the bottom-right corner when data is out-of-date.
- **No Reboot Loops on API Errors**: 5xx server errors, 404s, and DNS lookup failures do not reboot the board.

### 📡 Wi-Fi Power Management (`src/services/wifi.py`)
- **Disabled DTIM Sleep (`PM_NONE`)**: Prevents the CYW43 radio from entering deep power-saving mode between beacons, eliminating sleep-wake desyncs.

---

## 4. On-Device Provisioning & Setup Portal

### 📱 Captive Portal Web Server (`src/setup/server.py`)
- **First-Boot Access Point**: Automatically broadcasts `Pico Train Display` (Password: `12345678`) if `config.json` is missing or invalid.
- **In-Browser Form**: Allows setting station, destination, and Wi-Fi credentials from any smartphone or laptop at `http://192.168.4.1`.

### 🔍 Live SSID Discovery
- **Signal-Strength Ordering**: Scans nearby Wi-Fi networks and orders suggestions by RSSI.
- **Mesh Deduplication**: Deduplicates mesh networks sharing the same SSID across multiple BSSIDs.

### 🎁 Zero-Solder Giveaway Mode
- **Baked-In API Tokens**: Build firmware with pre-embedded API tokens so recipients only need to enter their home Wi-Fi details.

---

## 5. Observability & Write-Ahead Telemetry

### 📝 Write-Ahead Log (`src/wal.py`)
- **Zero Log Loss**: Appends every log line to flash memory (`wal.log`).
- **Post-Reboot Replay**: Even during a 30-minute Wi-Fi outage or unexpected reboot, logs are preserved on flash and replayed to Loki upon reconnection.

### 📊 OpenTelemetry & Grafana Cloud Loki (`src/otel.py`)
- **Structured Telemetry**: Ships application logs, boot run identifiers, API latencies, and memory stats to Grafana Cloud.
- **Live Memory Monitoring**: Tracks heap utilization and fragmentation on every cycle.

### 🛠️ CLI Query Tool (`scripts/loki.py`)
- **Terminal Log Viewer**: Command-line tool to query, filter, and tail live logs directly from Grafana Loki.

---

## 6. Developer Tooling & Build Pipeline

### 🖥️ Desktop Terminal Simulator (`sim/panel.py`)
- **Hardware-Free Development**: Runs unmodified MicroPython application code in a terminal, drawing the amber OLED display in ASCII/ANSI.

### ⚡ Rapid Build & Flash Commands (`Makefile`)
- `make firmware`: Incremental multi-core compilation of MicroPython and frozen bytecode in ~10 seconds.
- `make f`: One-key firmware flashing to connected Pico in BOOTSEL mode.
- `make fn`: Flash-nuke and clean-slate firmware flash.
- `make test`: Runs 162 unit tests covering all drivers, rate limits, and network state machines.
