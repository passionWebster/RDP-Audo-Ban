"""Windows Security Event Log monitor (polling via EvtQuery).

Uses ``EvtQuery`` + ``EvtNext`` to retrieve Event ID 4625 (failed
logon) from the Security channel, parses the XML, filters for
configured LogonType values (e.g. 3 = Network/NTLM, 10 = RDP), and
invokes a user-supplied callback for each match.

**Why polling instead of EvtSubscribe?**  Some pywin32 builds cannot
call ``EvtNext`` on a subscription handle (error 6 / invalid handle).
``EvtQuery`` is the reliable fallback — it re-issues a fresh query on
every cycle and tracks the highest ``EventRecordID`` seen to avoid
processing the same event twice.

Requires Administrator privileges to read the Security log.
"""

from __future__ import annotations

import logging
import threading
import time
import xml.etree.ElementTree as ET
from typing import Callable

import win32evtlog

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# XPath filter — only Event ID 4625.
QUERY_4625 = "*[System[(EventID=4625)]]"

# Query flags: "Security" is a channel path, newest events first.
_QUERY_FLAGS = win32evtlog.EvtQueryChannelPath | win32evtlog.EvtQueryReverseDirection

# XML namespace used by the Windows Event schema.
_EVT_NS = "http://schemas.microsoft.com/win/2004/08/events/event"

# Max events to pull in one EvtNext call.
_BATCH_SIZE = 20

# EvtNext timeout, ms.
_POLL_TIMEOUT_MS = 500

# Interval between poll cycles, seconds.
_POLL_INTERVAL_S = 2.0

# Delay after a transient error, seconds.
_RECONNECT_DELAY_S = 5


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------


def _parse_event_xml(xml_str: str) -> dict[str, str]:
    """Extract ``{Name: text}`` from ``<Data>`` children of a 4625 event.

    Uses a two-step approach — ``EventData`` → iterate ``Data`` children —
    because ``root.iterfind("...Data")`` returns 0 results on some CPython /
    pywin32 builds (ElementTree XPath recursion bug).
    """
    data: dict[str, str] = {}
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return data
    event_data = root.find(f"{{{_EVT_NS}}}EventData")
    if event_data is not None:
        for elem in event_data:
            name = elem.get("Name")
            if name:
                data[name] = (elem.text or "").strip()
    return data


def _is_local_ip(ip: str) -> bool:
    """Return ``True`` when *ip* is a loopback / empty placeholder."""
    return ip in ("-", "", "::1", "127.0.0.1", "0.0.0.0")


def _extract_record_id(xml_str: str) -> int:
    """Read ``<EventRecordID>`` from the event XML."""
    try:
        root = ET.fromstring(xml_str)
        sys_elem = root.find(f"{{{_EVT_NS}}}System")
        if sys_elem is not None:
            rid_elem = sys_elem.find(f"{{{_EVT_NS}}}EventRecordID")
            if rid_elem is not None and rid_elem.text:
                return int(rid_elem.text)
    except (ET.ParseError, ValueError, AttributeError):
        pass
    return 0


# ---------------------------------------------------------------------------
# EventWatcher
# ---------------------------------------------------------------------------


class EventWatcher:
    """Polling-based watcher for the Windows Security event log.

    Parameters
    ----------
    on_rdp_failure:
        Callback ``f(ip: str, username: str, status: str)`` invoked for
        every logon failure matching the configured LogonType values.
    log:
        A :mod:`logging` logger instance.
    monitored_logon_types:
        Set of LogonType strings to watch (e.g. ``{"3", "10"}``).
        Defaults to ``{"10"}`` (RDP only).
    """

    def __init__(
        self,
        on_rdp_failure: Callable[[str, str, str], None],
        log: logging.Logger,
        monitored_logon_types: set[str] | None = None,
    ) -> None:
        self._callback = on_rdp_failure
        self._log = log
        self._monitored_logon_types = monitored_logon_types or {"10"}

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        # Highest EventRecordID we have seen — prevents double-processing.
        self._last_record_id: int = 0

    # -- public API -----------------------------------------------------

    def start(self) -> None:
        """Launch the watcher on a daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            self._log.warning("EventWatcher 已在运行中，忽略重复启动")
            return

        # Seed the cursor so we don't replay the entire log on first run.
        self._last_record_id = self._query_max_record_id()

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="evt-watcher", daemon=True
        )
        self._thread.start()
        self._log.info(
            "EventWatcher 已启动 | 轮询 Security 日志 EventID=4625 | "
            "起始 RecordID=%d",
            self._last_record_id,
        )

    def stop(self) -> None:
        """Signal stop and block until the watcher thread exits (max 10 s)."""
        self._log.info("正在停止 EventWatcher …")
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=10)
            self._thread = None
        self._log.info("EventWatcher 已停止")

    # -- main loop ------------------------------------------------------

    def _run(self) -> None:
        """Poll loop: query → process → sleep; reconnect on error."""
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception:
                self._log.exception(
                    "EventWatcher 异常，%d 秒后重试…", _RECONNECT_DELAY_S
                )
                self._sleep_interruptible(_RECONNECT_DELAY_S)
            else:
                self._sleep_interruptible(_POLL_INTERVAL_S)

    def _poll_once(self) -> None:
        """Execute one query and dispatch new (unseen) events."""
        query = win32evtlog.EvtQuery("Security", _QUERY_FLAGS, QUERY_4625)
        try:
            events = list(
                win32evtlog.EvtNext(query, _BATCH_SIZE, _POLL_TIMEOUT_MS, 0)
            )
        finally:
            # EvtClose is not exported by this pywin32 build — the handle
            # is released when ``query`` goes out of scope.
            del query

        for handle in events:
            if self._stop_event.is_set():
                break
            self._process_event(handle)

    # -- event processing -----------------------------------------------

    def _process_event(self, handle: int) -> None:
        """Render, parse, and conditionally dispatch a single event."""
        try:
            xml_str = win32evtlog.EvtRender(handle, win32evtlog.EvtRenderEventXml)
        except Exception:
            self._log.debug("EvtRender 失败，跳过事件")
            return

        # Skip events we've already seen.
        record_id = _extract_record_id(xml_str)
        if record_id and record_id <= self._last_record_id:
            return
        if record_id > self._last_record_id:
            self._last_record_id = record_id

        data = _parse_event_xml(xml_str)
        if not data:
            return

        logon_type = data.get("LogonType", "")
        ip = data.get("IpAddress", "")
        username = data.get("TargetUserName", "")
        status = data.get("Status", "")

        # Only monitored logon types (e.g. 3 = Network/NTLM, 10 = RDP).
        if logon_type not in self._monitored_logon_types:
            return

        # Skip local / empty IPs.
        if _is_local_ip(ip):
            return

        self._log.info(
            "登录失败 | LogonType=%s | IP=%s | 用户=%s | 状态=%s",
            logon_type, ip, username, status,
        )
        self._callback(ip, username, status)

    # -- helpers --------------------------------------------------------

    def _query_max_record_id(self) -> int:
        """Ask the log for the highest EventRecordID right now.

        Returns 0 on failure (safe fallback — may cause a few duplicate
        events on first run but never misses anything).
        """
        try:
            query = win32evtlog.EvtQuery(
                "Security", _QUERY_FLAGS, QUERY_4625
            )
            try:
                events = list(
                    win32evtlog.EvtNext(query, 1, 100, 0)
                )
                if events:
                    xml_str = win32evtlog.EvtRender(
                        events[0], win32evtlog.EvtRenderEventXml
                    )
                    return _extract_record_id(xml_str)
            finally:
                del query
        except Exception:
            self._log.debug("查询初始 RecordID 失败，从 0 开始")
        return 0

    def _sleep_interruptible(self, seconds: float) -> None:
        """Sleep that wakes early when ``stop()`` is called."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not self._stop_event.is_set():
            time.sleep(min(0.5, deadline - time.monotonic()))
