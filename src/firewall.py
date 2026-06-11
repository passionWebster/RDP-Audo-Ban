"""Windows Firewall rule management via netsh advfirewall.

All rule operations are synchronous and log failures explicitly.
State (banned_ips.json) is maintained as the authoritative record of
what this tool has blocked so that restarts survive cleanly.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# netsh wrappers
# ---------------------------------------------------------------------------

def _run_netsh(args: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    """Thin wrapper around ``netsh``; raises on non-zero exit."""
    return subprocess.run(
        ["netsh", "advfirewall", "firewall"] + args,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# FirewallManager
# ---------------------------------------------------------------------------


class FirewallManager:
    """Create / delete Windows Firewall rules and persist ban state.

    Parameters
    ----------
    config:
        Application config (see ``src.config.Config``).
    log:
        Pre-configured logger.
    """

    def __init__(self, config: Any, log: logging.Logger) -> None:
        self._cfg = config
        self._log = log

    # -- firewall rule CRUD ---------------------------------------------

    def block_ip(self, ip: str) -> bool:
        """Add an inbound block rule for *ip* on the configured RDP port.

        Idempotent — returns ``True`` if the rule already exists.
        """
        rule_name = self._cfg.firewall_rule_name(ip)

        if self.rule_exists(rule_name):
            self._log.debug("规则已存在，跳过添加: %s", rule_name)
            return True

        self._log.info("正在封禁 IP: %s …", ip)
        try:
            _run_netsh([
                "add", "rule",
                f"name={rule_name}",
                "dir=in",
                "action=block",
                f"remoteip={ip}",
                "protocol=tcp",
                f"localport={self._cfg.rdp_port}",
            ])
        except subprocess.CalledProcessError as exc:
            self._log.error("netsh 添加规则失败: %s | %s", rule_name, exc.stderr.strip())
            return False
        except OSError as exc:
            self._log.error("调用 netsh 失败（PATH 或权限问题）: %s", exc)
            return False

        self._log.info("防火墙规则已添加: %s", rule_name)
        return True

    def unblock_ip(self, ip: str) -> bool:
        """Remove the firewall rule for *ip*.

        Idempotent — returns ``True`` if the rule doesn't exist.
        """
        rule_name = self._cfg.firewall_rule_name(ip)

        if not self.rule_exists(rule_name):
            self._log.debug("规则已不存在，跳过删除: %s", rule_name)
            return True

        self._log.info("正在解封 IP: %s …", ip)
        try:
            _run_netsh(["delete", "rule", f"name={rule_name}"])
        except subprocess.CalledProcessError as exc:
            self._log.error("netsh 删除规则失败: %s | %s", rule_name, exc.stderr.strip())
            return False
        except OSError as exc:
            self._log.error("调用 netsh 失败: %s", exc)
            return False

        self._log.info("防火墙规则已删除: %s", rule_name)
        return True

    def rule_exists(self, rule_name: str) -> bool:
        """Check whether a firewall rule named *rule_name* exists."""
        try:
            _run_netsh(["show", "rule", f"name={rule_name}"])
            return True
        except subprocess.CalledProcessError:
            return False
        except OSError:
            return False

    # -- state persistence ----------------------------------------------

    def load_state(self) -> dict[str, dict]:
        """Read ``banned_ips.json``, returning ``{}`` on any error."""
        path = Path(self._cfg.state_file)
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            self._log.warning("读取封禁状态文件失败，重置为空: %s", exc)
            return {}

    def save_state(self, state: dict[str, dict]) -> None:
        """Atomically write *state* to ``banned_ips.json``."""
        path = Path(self._cfg.state_file)
        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2, ensure_ascii=False)
            tmp.replace(path)
        except OSError as exc:
            self._log.error("写入封禁状态文件失败: %s", exc)

    def record_ban(self, state: dict[str, dict], ip: str) -> dict[str, dict]:
        """Stamp *ip* into the state dict and persist.

        Returns the mutated *state* for convenience.
        """
        now = datetime.now(timezone.utc)
        record: dict[str, Any] = {"banned_at": now.isoformat()}

        ban_secs = self._cfg.ban_duration_seconds
        if ban_secs is not None:
            record["expires_at"] = (now + timedelta(seconds=ban_secs)).isoformat()
        else:
            record["expires_at"] = None  # permanent

        state[ip] = record
        self.save_state(state)
        return state

    def remove_ban(self, state: dict[str, dict], ip: str) -> dict[str, dict]:
        """Remove *ip* from the state dict and persist."""
        if ip in state:
            del state[ip]
            self.save_state(state)
        return state

    def find_expired(self, state: dict[str, dict]) -> list[str]:
        """Return IPs whose ban has expired (``expires_at`` is in the past).

        Permanent bans (``expires_at`` is ``None``) never expire.
        """
        now = datetime.now(timezone.utc)
        expired: list[str] = []
        for ip, record in state.items():
            expires_str = record.get("expires_at")
            if expires_str is None:
                continue  # permanent ban
            try:
                expires_at = datetime.fromisoformat(expires_str)
            except (ValueError, TypeError):
                self._log.warning("封禁状态中过期时间格式异常: %s=%s", ip, expires_str)
                continue
            if now >= expires_at:
                expired.append(ip)
        return expired
