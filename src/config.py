"""Configuration loading and validation.

Reads config.yaml, validates all fields, and provides typed access
to every setting. Relative paths are resolved against the config file's
own directory so the tool works correctly regardless of CWD.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """Raised when the configuration file is missing, malformed, or
    contains invalid values."""


class Config:
    """Validated, typed view of config.yaml.

    Usage::

        config = Config("config.yaml")
        if config.is_whitelisted("192.168.1.1"):
            ...
    """

    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path).resolve()
        self._data: dict[str, Any] = {}
        self._load()
        self._validate()

    # -- helpers --------------------------------------------------------

    def _load(self) -> None:
        if not self.config_path.exists():
            raise ConfigError(f"配置文件不存在: {self.config_path}")
        try:
            with open(self.config_path, "r", encoding="utf-8") as fh:
                self._data = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"配置文件 YAML 解析失败: {exc}") from exc

    def _validate(self) -> None:
        # --- monitor ---
        monitor = self._data.get("monitor", {})
        self.failure_threshold = self._get_int(monitor, "failure_threshold", 3, ge=1)
        self.time_window_minutes = self._get_int(monitor, "time_window_minutes", 5, ge=1)
        ban_hours = self._get_int(monitor, "ban_duration_hours", 24, ge=0)
        self.ban_duration_hours = ban_hours  # 0 = permanent

        self.monitored_logon_types: set[str] = set()
        for t in monitor.get("monitored_logon_types", [10]):
            self.monitored_logon_types.add(str(int(t)))

        # --- firewall ---
        firewall = self._data.get("firewall", {})
        self.rule_name_prefix = str(firewall.get("rule_name_prefix", "RDP-Auto-Ban")).strip()
        if not self.rule_name_prefix:
            raise ConfigError("firewall.rule_name_prefix 不能为空")
        self.rdp_port = self._get_int(firewall, "rdp_port", 3389, ge=1, le=65535)

        # --- whitelist ---
        whitelist = self._data.get("whitelist", {})
        self.whitelist_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        for entry in whitelist.get("ip_list", []):
            try:
                net = ipaddress.ip_network(str(entry), strict=False)
                self.whitelist_networks.append(net)
            except ValueError as exc:
                raise ConfigError(f"白名单条目无效 '{entry}': {exc}") from exc

        # --- logging ---
        log_cfg = self._data.get("logging", {})
        self.log_level = str(log_cfg.get("level", "INFO")).upper()
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ConfigError(f"无效的日志级别: {self.log_level}")

        self.log_dir = self._resolve_path(log_cfg.get("log_dir", "logs"))
        self.log_max_bytes = self._get_int(log_cfg, "max_bytes", 10_485_760, ge=1024)
        self.log_backup_count = self._get_int(log_cfg, "backup_count", 5, ge=0)

        # --- persistence ---
        persist = self._data.get("persistence", {})
        self.state_file = self._resolve_path(persist.get("state_file", "banned_ips.json"))

    # -- derived properties ---------------------------------------------

    @property
    def time_window_seconds(self) -> float:
        """The sliding window in seconds."""
        return self.time_window_minutes * 60.0

    @property
    def ban_duration_seconds(self) -> float | None:
        """Ban duration in seconds, or ``None`` for permanent bans."""
        if self.ban_duration_hours == 0:
            return None
        return self.ban_duration_hours * 3600.0

    # -- public helpers -------------------------------------------------

    def is_whitelisted(self, ip_str: str) -> bool:
        """Return ``True`` if *ip_str* falls inside any whitelisted network."""
        try:
            ip = ipaddress.ip_address(ip_str.strip())
        except ValueError:
            return False
        return any(ip in net for net in self.whitelist_networks)

    def firewall_rule_name(self, ip_str: str) -> str:
        """Build a deterministic firewall rule name for *ip_str*."""
        return f"{self.rule_name_prefix}-{ip_str}"

    def is_ban_permanent(self) -> bool:
        """Return ``True`` when ban_duration_hours == 0."""
        return self.ban_duration_hours == 0

    # -- internal -------------------------------------------------------

    def _resolve_path(self, value: str) -> Path:
        """Resolve *value* relative to the config file's directory."""
        p = Path(value)
        if p.is_absolute():
            return p
        return (self.config_path.parent / p).resolve()

    @staticmethod
    def _get_int(
        data: dict[str, Any],
        key: str,
        default: int,
        *,
        ge: int | None = None,
        le: int | None = None,
    ) -> int:
        val = data.get(key, default)
        try:
            val = int(val)
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"{key} 必须是整数，当前值: {val}") from exc
        if ge is not None and val < ge:
            raise ConfigError(f"{key} 必须 >= {ge}，当前值: {val}")
        if le is not None and val > le:
            raise ConfigError(f"{key} 必须 <= {le}，当前值: {val}")
        return val
