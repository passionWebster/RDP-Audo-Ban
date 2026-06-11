#!/usr/bin/env python3
"""RDP Auto-Ban — main entry point.

Monitors Windows Security event log for RDP (LogonType=10) login
failures and automatically blocks attacking IPs via Windows Firewall.

Usage::

    python rdp_auto_ban.py --console       Run in foreground (Ctrl+C to stop)
    python rdp_auto_ban.py install         Register as Windows service
    python rdp_auto_ban.py start           Start the service
    python rdp_auto_ban.py stop            Stop the service
    python rdp_auto_ban.py remove          Unregister the service
    python rdp_auto_ban.py debug           Run service in debug mode

Requires: Administrator privileges, pywin32, pyyaml.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

# Ensure the project root is on sys.path so ``from src.xxx`` works
# regardless of the CWD when the script is launched (important for
# the Windows service host).
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from src.config import Config, ConfigError  # noqa: E402
from src.logger import setup_logging, get_logger  # noqa: E402
from src.ip_tracker import IpTracker  # noqa: E402
from src.firewall import FirewallManager  # noqa: E402
from src.event_watcher import EventWatcher  # noqa: E402

# Windows-specific imports — only available on Windows.
if sys.platform == "win32":
    import win32serviceutil
    import win32service
    import win32event
    import servicemanager
else:
    # Allow importing for linting / IDE support on other platforms.
    win32serviceutil = None  # type: ignore[assignment]
    win32service = None  # type: ignore[assignment]
    win32event = None  # type: ignore[assignment]
    servicemanager = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = "config.yaml"

# Interval for housekeeping (seconds).
_HOUSEKEEPING_INTERVAL_S = 60


# ---------------------------------------------------------------------------
# Core application logic (service / console agnostic)
# ---------------------------------------------------------------------------


class RdpAutoBan:
    """Orchestrates event watching, IP tracking, firewall blocking,
    and state persistence.

    Parameters
    ----------
    config_path:
        Path to ``config.yaml``.
    """

    def __init__(self, config_path: str = DEFAULT_CONFIG) -> None:
        # -- config & logging -------------------------------------------
        self.config = Config(config_path)
        self.log = setup_logging(self.config)
        self.log.info("RDP Auto-Ban 初始化完成")
        self.log.info(
            "配置: 阈值=%d次/%d分钟 | 封禁=%d小时 | 端口=%d | 监控登录类型=%s",
            self.config.failure_threshold,
            self.config.time_window_minutes,
            self.config.ban_duration_hours,
            self.config.rdp_port,
            ",".join(sorted(self.config.monitored_logon_types)),
        )

        # -- sub-systems ------------------------------------------------
        self.ip_tracker = IpTracker(
            self.config.time_window_seconds,
            self.config.failure_threshold,
        )
        self.firewall = FirewallManager(self.config, self.log)
        self.event_watcher = EventWatcher(
            on_rdp_failure=self._on_rdp_failure,
            log=self.log,
            monitored_logon_types=self.config.monitored_logon_types,
        )

        # -- runtime state -----------------------------------------------
        self.state: dict[str, dict] = {}
        self._stop_event = threading.Event()
        self._housekeeping_thread: threading.Thread | None = None

    # -- public API -----------------------------------------------------

    def start(self) -> None:
        """Launch event monitoring and periodic housekeeping."""
        # 1. Load & reconcile persisted state.
        self.state = self.firewall.load_state()
        self.log.info("已加载封禁状态: %d 条记录", len(self.state))
        self._reconcile_on_startup()

        # 2. Start the event watcher (blocks on its own daemon thread).
        self.event_watcher.start()

        # 3. Start housekeeping.
        self._housekeeping_thread = threading.Thread(
            target=self._housekeeping_loop,
            name="housekeeping",
            daemon=True,
        )
        self._housekeeping_thread.start()

    def stop(self) -> None:
        """Gracefully shut down all components."""
        self.log.info("RDP Auto-Ban 正在停止…")
        self._stop_event.set()
        self.event_watcher.stop()
        if self._housekeeping_thread is not None:
            self._housekeeping_thread.join(timeout=15)
        self.log.info("RDP Auto-Ban 已停止")

    # -- event callback -------------------------------------------------

    def _on_rdp_failure(self, ip: str, _username: str, _status: str) -> None:
        """Handle a single RDP logon-failure event.

        All failures are accumulated globally.  Once the total number of
        failures (from any IP) reaches *failure_threshold* within the
        time window, **every** IP that appeared in the window is banned
        in one batch.
        """
        # 1. Whitelist check.
        if self.config.is_whitelisted(ip):
            self.log.debug("IP 在白名单中，跳过: %s", ip)
            return

        # 2. Already banned?
        if ip in self.state:
            self.log.debug("IP 已被封禁，跳过: %s", ip)
            return

        # 3. Record this failure.
        self.ip_tracker.record_failure(ip)
        total = self.ip_tracker.total_failure_count()

        # 4. Global threshold reached → ban every IP in the window.
        if total >= self.config.failure_threshold:
            targets = [
                t for t in self.ip_tracker.get_all_ips()
                if t not in self.state
            ]
            self.log.warning(
                "全局阈值触发: %d 次失败 / %d 分钟 | 封禁 %d 个 IP",
                total, self.config.time_window_minutes, len(targets),
            )
            for target_ip in targets:
                if self.firewall.block_ip(target_ip):
                    self.state = self.firewall.record_ban(self.state, target_ip)
                    self.log.info(">>> 已封禁 IP: %s", target_ip)
                else:
                    self.log.error("封禁失败: %s", target_ip)

            # Reset the tracker so the next wave starts fresh.
            self.ip_tracker.reset_all()

    # -- housekeeping ---------------------------------------------------

    def _housekeeping_loop(self) -> None:
        """Periodic cleanup thread."""
        self.log.debug("Housekeeping 线程已启动（间隔 %d 秒）", _HOUSEKEEPING_INTERVAL_S)
        while not self._stop_event.is_set():
            self._stop_event.wait(_HOUSEKEEPING_INTERVAL_S)
            if self._stop_event.is_set():
                break
            try:
                self._do_housekeeping()
            except Exception:
                self.log.exception("Housekeeping 异常")

    def _do_housekeeping(self) -> None:
        """Run one round of cleanup."""
        # Purge idle IP trackers.
        removed = self.ip_tracker.cleanup()
        if removed:
            self.log.debug("清理了 %d 个过期IP跟踪记录", removed)

        # Expire timed-out bans.
        expired = self.firewall.find_expired(self.state)
        for ip in expired:
            self.log.info("封禁已到期，解封 IP: %s", ip)
            if self.firewall.unblock_ip(ip):
                self.state = self.firewall.remove_ban(self.state, ip)

    # -- startup reconciliation -----------------------------------------

    def _reconcile_on_startup(self) -> None:
        """Ensure firewall rules match the persisted state.

        Re-creates missing rules and removes state entries whose ban
        has expired (and removes orphaned rules if possible).
        """
        if not self.state:
            return

        # Remove expired entries from state.
        expired = self.firewall.find_expired(self.state)
        for ip in expired:
            self.log.info("启动时发现过期封禁: %s，移除规则", ip)
            self.firewall.unblock_ip(ip)
            self.state = self.firewall.remove_ban(self.state, ip)

        # Ensure every active state entry has a corresponding rule.
        for ip in list(self.state):
            rule_name = self.config.firewall_rule_name(ip)
            if not self.firewall.rule_exists(rule_name):
                self.log.warning("启动时发现缺失的防火墙规则，重新创建: %s", rule_name)
                if not self.firewall.block_ip(ip):
                    self.log.error(
                        "无法恢复封禁规则: %s，从状态中移除", ip
                    )
                    self.state = self.firewall.remove_ban(self.state, ip)

    # -- helpers --------------------------------------------------------


# ---------------------------------------------------------------------------
# Windows Service wrapper
# ---------------------------------------------------------------------------

if sys.platform == "win32":

    class RdpAutoBanService(win32serviceutil.ServiceFramework):
        """Windows Service that hosts :class:`RdpAutoBan`."""

        _svc_name_ = "RDP-Auto-Ban"
        _svc_display_name_ = "RDP Auto Ban Service"
        _svc_description_ = (
            "监控 RDP 登录失败事件，自动将攻击 IP 列入 Windows 防火墙黑名单"
        )

        def __init__(self, args: list) -> None:
            super().__init__(args)
            self._app: RdpAutoBan | None = None
            # Create an event that SvcStop signals so SvcDoRun knows to exit.
            self._stop_event = win32event.CreateEvent(None, 0, 0, None)

        def SvcDoRun(self) -> None:
            """Service entry point — blocks until the service is stopped."""
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ""),
            )
            try:
                self._app = RdpAutoBan(
                    os.path.join(_PROJECT_DIR, DEFAULT_CONFIG)
                )
                self._app.start()
                # Wait indefinitely until SvcStop signals us.
                win32event.WaitForSingleObject(self._stop_event, win32event.INFINITE)
            except Exception:
                servicemanager.LogMsg(
                    servicemanager.EVENTLOG_ERROR_TYPE,
                    servicemanager.PYS_SERVICE_STOPPED,
                    (self._svc_name_, f"异常: {sys.exc_info()[1]}"),
                )
                raise

        def SvcStop(self) -> None:
            """Called by the SCM when the service is requested to stop."""
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            if self._app:
                self._app.stop()
            win32event.SetEvent(self._stop_event)
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STOPPED,
                (self._svc_name_, ""),
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_console() -> None:
    """Run the application in the foreground (Ctrl+C to exit)."""
    config_path = os.path.join(_PROJECT_DIR, DEFAULT_CONFIG)
    app = RdpAutoBan(config_path)
    app.start()

    print("RDP Auto-Ban 正在运行中（控制台模式），按 Ctrl+C 停止…")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n正在停止…")
        app.stop()
        print("已退出。")


def _configure_service() -> None:
    """Set the service to auto-start and configure recovery actions.

    Recovery policy: restart the service after 60 s on failure, up to
    3 times in a rolling 86400 s (1 day) window.

    Uses ``sc`` (Service Control) CLI, which is built into every Windows
    installation — simpler and more reliable than pywin32 structs.
    """
    svc_name = RdpAutoBanService._svc_name_

    # 1. Auto-start on boot.
    subprocess.run(
        ["sc", "config", svc_name, "start=auto"],
        check=True,
        capture_output=True,
        encoding="utf-8", errors="replace",
        timeout=15,
    )

    # 2. Restart on failure: 3 retries, 60 s delay, reset counter after 1 day.
    subprocess.run(
        ["sc", "failure", svc_name,
         "reset=86400",
         "actions=restart/60000/restart/60000/restart/60000"],
        check=True,
        capture_output=True,
        encoding="utf-8", errors="replace",
        timeout=15,
    )


def main() -> None:
    """Parse command line and dispatch to console or service mode."""
    if sys.platform != "win32":
        sys.exit("错误: RDP Auto-Ban 仅支持 Windows 平台")

    if "--console" in sys.argv:
        _run_console()
    else:
        # Delegate to win32serviceutil (handles install / start / stop /
        # remove / debug).
        win32serviceutil.HandleCommandLine(RdpAutoBanService)

        # When installing, also configure auto-start + recovery.
        if "install" in sys.argv:
            print("[配置] 设置自启动 + 异常恢复…")
            _configure_service()
            print("[配置] 完成 — 服务将开机自启，异常退出后 60 秒自动重启")


if __name__ == "__main__":
    main()
