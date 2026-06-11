# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RDP-Auto-Ban is a Windows background service that monitors Security event log for failed logon attempts (Event ID 4625) and automatically blocks attacking IPs via Windows Firewall (`netsh advfirewall`). Uses Python 3 + PyWin32 + PyYAML.

## Commands

```bash
# Run in foreground (console mode) — requires Administrator
.venv\Scripts\python rdp_auto_ban.py --console

# Install & start the Windows service
.\install_service.bat              # must run as Administrator

# Uninstall the Windows service
.\uninstall_service.bat            # must run as Administrator

# Manual service control
.venv\Scripts\python rdp_auto_ban.py install
.venv\Scripts\python rdp_auto_ban.py start
.venv\Scripts\python rdp_auto_ban.py stop
.venv\Scripts\python rdp_auto_ban.py remove

# Quick test of core modules (no service required)
.venv\Scripts\python -c "
import sys; sys.path.insert(0, '.')
from src.config import Config; c = Config('config.yaml')
print(f'Whitelist networks: {len(c.whitelist_networks)}')
print(f'Monitored LogonTypes: {c.monitored_logon_types}')
"
```

## Architecture

```
rdp_auto_ban.py          # Main entry: RdpAutoBan orchestrator + RdpAutoBanService + CLI
config.yaml              # All settings (thresholds, whitelist, logging, etc.)
src/
  config.py              # YAML loading, validation, CIDR whitelist checks
  logger.py              # RotatingFileHandler + StreamHandler (UTF-8 forced)
  event_watcher.py       # EvtQuery polling loop for Security log EventID 4625
  ip_tracker.py          # Sliding-window failure counter (global count, not per-IP)
  firewall.py            # netsh advfirewall wrapper + banned_ips.json persistence
```

**Data flow:** EventWatcher polls Security log → parses XML with `_parse_event_xml()` → filters by configured `monitored_logon_types` → callback `_on_rdp_failure()` → whitelist check → records failure → if **global** failure count ≥ threshold within the window, bans **all** IPs that appeared in that window.

## Key Design Decisions

- **Polling, not EvtSubscribe.** Some pywin32 builds throw error 6 ("句柄无效") when calling `EvtNext` on a subscription handle. The solution uses `EvtQuery` with a fresh query per cycle, deduplicating via `EventRecordID`.
- **`_parse_event_xml` uses two-step lookup** (find `EventData` → iterate children). `root.iterfind("{NS}Data")` returns 0 results on some CPython builds — an ElementTree namespace recursion quirk.
- **Global threshold ban strategy.** Total failures across *all* IPs are counted together. When the total reaches `failure_threshold` within `time_window_minutes`, every IP that appeared in the window is banned in one batch. This catches distributed attacks where attackers rotate IPs.
- **Single IP per firewall rule.** Rule name format: `{rule_name_prefix}-{IP}` (e.g. `RDP-Auto-Ban-1.2.3.4`). Allows individual unblock.
- **Startup reconciliation.** On boot the service: (a) removes expired bans from state, (b) re-creates any missing firewall rules for active state entries.
- **Atomic state writes.** `banned_ips.json` is written via `.tmp` + `os.replace()` to prevent corruption.
- **Windows-specific imports guarded** with `if sys.platform == "win32"` so the module can be imported for linting on other platforms.

## Configuration

`config.yaml` — edit then restart the service:

- `monitor.failure_threshold` (3) — global failures in the window that trigger a ban wave
- `monitor.time_window_minutes` (5) — sliding window size
- `monitor.ban_duration_hours` (24, 0=permanent)
- `monitor.monitored_logon_types` ([3, 10]) — 3=Network/NTLM, 10=RemoteInteractive/RDP
- `whitelist.ip_list` — CIDR entries (127.0.0.1, 192.168.0.0/16, etc.)
- `firewall.rdp_port` (3389) — the port blocked by firewall rules

## Constraints

- **Must run as Administrator** (reading Security log + modifying firewall)
- **Windows only** (relies on pywin32, netsh, win32serviceutil)
- Audit policy must be enabled: `auditpol /get /category:"Logon/Logoff"` — "Failure" must be audited
- On Windows 10/11 client SKUs, 4625 IpAddress may be empty; Server SKUs default to recording it
