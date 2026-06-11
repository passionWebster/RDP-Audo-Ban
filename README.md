# RDP Auto-Ban

Windows 后台服务 —— 实时监控登录失败事件，自动将攻击 IP 列入防火墙黑名单。

## 功能概览

- **实时监控**：轮询 Windows Security 事件日志，捕获 Event ID 4625（登录失败）
- **多登录类型**：支持 Network/NTLM (LogonType 3) 和 RDP (LogonType 10)，可配置
- **全局阈值 + 批量封禁**：时间窗口内总失败数达到阈值后，一次性封禁窗口中所有攻击 IP，有效应对分布式 IP 轮换攻击
- **自动过期**：支持设置封禁时长，到期自动解封并清理防火墙规则
- **永久封禁**：可配置为永不自动解封
- **IP 白名单**：支持 CIDR 网段，本地网络不会被误封
- **重启恢复**：服务启动时自动重建缺失的防火墙规则，清理过期封禁
- **Windows 服务**：以后台服务方式运行，开机自启

## 需求

- Windows 10 / 11 或 Windows Server（64 位）
- Python 3.9+
- **管理员权限**（读取 Security 日志 + 操作防火墙）
- 审核策略已启用：`auditpol /get /category:"Logon/Logoff"` 中 "Failure" 为 "已启用"

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/passionWebster/RDP-Audo-Ban.git
cd RDP-Auto-Ban

# 2. 创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 3. 下载 NSSM（服务包装器，约 400KB）
curl -L -o nssm.zip https://nssm.cc/release/nssm-2.24.zip
# 解压 nssm-2.24.zip，把 win64\nssm.exe 放到项目根目录

# 4. 修改配置（按需调整阈值、白名单等）
notepad config.yaml

# 5. 前台测试运行（Ctrl+C 停止）
.venv\Scripts\python rdp_auto_ban.py --console

# 6. 确认无误后安装为 Windows 服务（以管理员运行）
.\install_service.bat
```

## 配置说明

编辑 `config.yaml`，修改后需重启服务生效：

```yaml
monitor:
  failure_threshold: 3       # 触发封禁的全局失败次数阈值
  time_window_minutes: 5     # 统计时间窗口（分钟）
  ban_duration_hours: 24     # 封禁时长（0 = 永久封禁）
  monitored_logon_types: [3, 10]  # 3=Network/NTLM, 10=RDP

firewall:
  rule_name_prefix: "RDP-Auto-Ban"  # 防火墙规则名前缀
  rdp_port: 3389                     # RDP 监听端口

whitelist:
  ip_list:
    - "127.0.0.1"
    - "192.168.0.0/16"
    - "10.0.0.0/8"
    - "172.16.0.0/12"

logging:
  level: "INFO"              # DEBUG / INFO / WARNING / ERROR
  log_dir: "logs"
  max_bytes: 10485760        # 单文件最大 10MB
  backup_count: 5            # 保留的历史日志文件数

persistence:
  state_file: "banned_ips.json"  # 封禁状态持久化文件
```

### 关于 LogonType

| 值 | 含义 | 说明 |
|---|---|---|
| 3 | Network | NTLM/SMB 暴力破解，是最常见的攻击类型 |
| 10 | RemoteInteractive | RDP 远程桌面 |

> **注意**：Windows 10/11 客户端版本在 Event 4625 的 IpAddress 字段可能为空，建议同时监控 LogonType 3 和 10。Server 版本默认会记录 IP。

## 封禁策略

采用**全局阈值 + 批量封禁**模型：

```
事件流 → 白名单过滤 → 已封禁过滤 → IpTracker 记录
                                      ↓
                          全局失败数 ≥ threshold？
                                      ↓ 是
                          封禁窗口中所有 IP → 重置计数器
```

- 3 个不同 IP 各失败 1 次 → 3 个全被封禁
- 1 个 IP 失败 3 次 → 该 IP 被封禁
- 分布式攻击（IP 轮换）也能被捕获，只要总次数达标

## 架构

```
rdp_auto_ban.py          # 主入口：RdpAutoBan 编排器 + Windows Service + CLI
config.yaml              # 配置文件
src/
  config.py              # YAML 加载/校验、CIDR 白名单匹配
  logger.py              # 日志系统（RotatingFileHandler + 控制台）
  event_watcher.py       # EvtQuery 轮询 Security 日志、解析 4625 XML
  ip_tracker.py          # 滑动窗口全局失败计数
  firewall.py            # netsh advfirewall 封装 + banned_ips.json 持久化
```

**数据流**：`EventWatcher` 轮询（2 秒间隔）→ 解析 XML → 过滤 LogonType → 回调 `_on_rdp_failure()` → 白名单检查 → `IpTracker` 记录 → 全局阈值判断 → `FirewallManager` 批量封禁 + 持久化。

## 服务管理

本工具使用 [NSSM](https://nssm.cc/)（Non-Sucking Service Manager）将控制台程序包装为 Windows 服务，
避免 pywin32 的 `pythonservice.exe` DLL 兼容性问题。

```bash
# 安装并启动服务
.\install_service.bat

# 卸载服务
.\uninstall_service.bat

# 手动控制（通过 NSSM）
nssm start RDP-Auto-Ban          # 启动
nssm stop RDP-Auto-Ban           # 停止
nssm restart RDP-Auto-Ban        # 重启
nssm status RDP-Auto-Ban         # 查看状态
nssm edit RDP-Auto-Ban           # 图形界面修改配置

# 查看 Windows 服务
services.msc  → 找到 "RDP Auto Ban Service"
```

## 日志

日志输出到 `logs/rdp_auto_ban.log`，自动轮转（默认保留 5 个历史文件，每个最大 10MB）。

```bash
# 实时查看
Get-Content -Path logs\rdp_auto_ban.log -Wait
```

## 常见问题

### 安装服务时报 "拒绝访问"

请以**管理员身份**运行 `install_service.bat`（右键 → 以管理员身份运行）。

### 服务安装成功但未检测到攻击

1. 检查审核策略：`auditpol /get /category:"Logon/Logoff"` — "Failure" 应为 "成功" 或 "已启用"
2. 检查 `monitored_logon_types` 是否包含攻击事件对应的 LogonType
3. 查看日志文件确认 EventWatcher 是否正常启动
4. 在 Windows 10/11 客户端版本上，4625 事件的 IpAddress 可能为空，这是系统限制

### 如何手动解封某个 IP

```bash
# 删除防火墙规则
netsh advfirewall firewall delete rule name="RDP-Auto-Ban-<IP地址>"

# 编辑 banned_ips.json，删除对应条目
notepad banned_ips.json
```

### 端口不是 3389 怎么办

编辑 `config.yaml` 中的 `firewall.rdp_port` 为你实际的 RDP 端口号。

## 技术决策

- **Polling 而非 EvtSubscribe**：某些 pywin32 版本在 subscription handle 上调用 EvtNext 会报 error 6（句柄无效），EvtQuery 轮询是稳定替代方案
- **XML 解析用两步查找**：`iterfind` 在部分 CPython 构建中存在命名空间递归 bug，改为 `find(EventData)` + 迭代子元素
- **全局阈值**：不区分 IP，总量达标即封禁，有效应对分布式攻击
- **单 IP 单规则**：规则命名 `RDP-Auto-Ban-<IP>`，方便单独管理

## 许可证

MIT License
