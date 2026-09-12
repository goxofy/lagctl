# lagctl

[English](README.md) | [简体中文](README.zh-CN.md)

`lagctl` 是一个轻量级 macOS LaunchAgent 管理工具，同时提供适合脚本调用的 CLI 和交互式终端界面。它通过系统 `launchd` 服务创建和管理当前用户的任务，不需要 `sudo`，也不会运行自己的常驻后台守护进程。

项目适用于 Python、Node.js、Shell 脚本、本地 Web 服务、隧道、Worker、定时任务，以及其他需要脱离终端会话持续运行或登录后自动启动的进程。

## 功能亮点

- 创建、编辑、启动、停止、重启、立即触发、检查和删除用户级 LaunchAgent。
- 基于 Textual 的交互式终端仪表盘，支持键盘和鼠标操作。
- 支持始终保持运行、失败后重启、单次运行和固定间隔运行。
- 分离 stdout 和 stderr 日志，支持实时查看、过滤、暂停和清空。
- 展示 PID、最近退出码、进程树、TCP 监听端口和网络流量统计。
- 自动识别 Python、Node.js、sh、bash 和 zsh 脚本解释器。
- 保存当前 `PATH`，让任务能够找到 Homebrew、虚拟环境、Node.js 和其他工具。
- 原子更新 plist，并在新配置加载失败时尝试回滚。
- 提供 zsh 和 bash 补全，包括已管理任务名称补全。
- 基础 CLI 没有第三方运行时依赖。Textual 是可选依赖，仅 TUI 需要安装。

## 管理范围

`lagctl` 只管理当前用户下列标签命名空间中的 LaunchAgent：

```text
local.launch-agent-manager.*
```

它不管理系统级 LaunchDaemon、任意第三方 plist、Docker 服务或日历式 launchd 计划。

## 系统要求

- macOS 14 Sonoma 或更新版本。
- Python 3.9 或更新版本。
- 系统工具：
  - `/bin/launchctl`
  - `/usr/bin/tail`
  - `/usr/sbin/lsof`，用于发现监听端口
  - `/usr/bin/nettop`，用于采样网络流量
- 可选 TUI 需要 Textual `0.70` 至 `<1.0`。

检查本机环境：

```bash
./bin/lagctl doctor
```

## 安装

### 从源码运行

```bash
git clone https://github.com/goxofy/lagctl.git
cd lagctl
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e '.[tui]'
make test
```

直接从仓库运行：

```bash
./bin/lagctl tui
./bin/lagctl list
```

### 安装到 `~/.local`

```bash
make install
```

确保 `~/.local/bin` 已加入 `PATH`：

```bash
export PATH="$HOME/.local/bin:$PATH"
lagctl doctor
```

`make install` 会复制程序和补全文件，但不会安装 Textual。安装后的启动器所调用的 `python3` 必须能够导入 Textual，`lagctl tui` 才能运行。如果使用项目虚拟环境，请在执行安装后的命令前激活该虚拟环境。

安装到其他前缀：

```bash
make install PREFIX="$HOME/bin-tools"
```

卸载程序文件：

```bash
make uninstall
```

卸载不会删除已有 LaunchAgent、日志或被管理的脚本。

## 快速开始

创建一个持续运行的 Python Worker：

```bash
lagctl add worker -- /absolute/path/to/worker.py
```

创建带工作目录和环境变量的 Node.js 服务：

```bash
lagctl add api \
  --cwd /Users/me/projects/api \
  --env NODE_ENV=production \
  --env PORT=3000 \
  -- /opt/homebrew/bin/node server.js
```

检查任务：

```bash
lagctl list
lagctl status api
lagctl network api
lagctl logs -f api
```

打开交互式仪表盘：

```bash
lagctl tui
```

执行 `add` 时必须使用 `--`。分隔符右侧的所有内容都会作为被管理程序的命令和参数保存。

## 终端界面

TUI 支持：

- 按任务名称搜索。
- 按全部、运行中、已停止或错误状态过滤。
- 使用完整配置表单创建和编辑任务。
- 在后台 Worker 中启动、停止、重启和立即触发任务。
- 独立的详情、日志、删除确认和活动历史页面。
- 终端宽度小于 100 列时自动切换为纵向布局。
- 详情内容支持鼠标选择，并可使用 `Ctrl+C` 复制。

### 主界面

| 按键 | 操作 |
|---|---|
| `Up` / `Down` | 选择任务 |
| `Enter` | 打开任务详情 |
| `a` | 添加任务 |
| `e` | 编辑选中任务 |
| `s` | 启动或停止选中任务 |
| `R` | 重启选中任务 |
| `x` | 立即触发选中任务 |
| `l` | 打开日志 |
| `d` | 删除选中任务 |
| `h` | 打开最近活动历史 |
| `r` | 刷新 |
| `q` | 退出 |

### 日志页面

日志页面默认进入命令模式，因此字母会被解释为快捷键，而不是搜索文本。

| 按键 | 操作 |
|---|---|
| `o` | 显示 stdout |
| `e` | 显示 stderr |
| `a` | 同时显示两个流 |
| `/` | 进入日志搜索模式 |
| `Enter` / `Esc` | 退出搜索模式 |
| `p` | 暂停或恢复刷新 |
| `f` | 开关自动滚动 |
| `c` | 清空当前选中的日志流 |
| `+` / `-` | 增加或减少日志行数限制 |
| `q` / `Esc` | 返回上一个页面 |

默认显示最近 200 行，可在 50 至 2000 行之间调整。日志读取和清空操作均在后台 Worker 中执行。

## 任务模式

### 始终保持运行

默认模式会在进程退出后重新启动：

```bash
lagctl add collector -- /Users/me/apps/collector.py
```

设置 launchd 重启节流时间：

```bash
lagctl add collector --throttle 30 -- /Users/me/apps/collector.py
```

### 失败后重启

```bash
lagctl add importer --mode on-failure -- /Users/me/apps/importer.py
```

退出码为 `0` 时任务停止，非零退出码会让 launchd 重新启动任务。

### 固定间隔

```bash
lagctl add sync --interval 900 -- /Users/me/apps/sync.py
```

禁止任务加载时立即运行：

```bash
lagctl add sync --interval 900 --no-run-at-load -- /Users/me/apps/sync.py
```

使用 `--interval` 且未指定 `--mode` 时，会自动采用单次运行语义。如果显式指定 `--mode`，只能使用 `once`。

### 只创建、不加载

```bash
lagctl add worker --no-start -- /Users/me/apps/worker.py
lagctl start worker
```

### 旧式后台启动脚本

如果旧式脚本使用 `nohup ... &` 启动子进程后立即退出：

```bash
lagctl add legacy-service \
  --mode once \
  --allow-background-children \
  --cwd /Users/me/apps/legacy-service \
  -- /bin/bash /Users/me/apps/legacy-service/run.sh start
```

这会写入 `AbandonProcessGroup=true`。该选项只能与 `--mode once` 搭配，不能与 `--interval` 一起使用。

这种模式只能监督启动脚本。脱离后的进程可能不会出现在 `lagctl status` 中，可能无法通过 `lagctl stop` 停止，也无法可靠计入进程树、端口或流量统计。条件允许时，应让启动脚本使用 `exec` 替换自身并以前台方式运行实际进程。

## 更新任务

使用 `--force` 替换已管理的任务：

```bash
lagctl add worker --force --env APP_ENV=production -- /Users/me/apps/worker.py
```

已加载的旧任务会先停止，plist 会被原子替换，然后加载新配置。如果新配置加载失败，`lagctl` 会尝试恢复之前的 plist 和加载状态。

TUI 编辑表单使用相同的更新流程。

## 生命周期命令

```bash
lagctl start worker
lagctl stop worker
lagctl restart worker
lagctl run worker
```

- `stop` 使用 `launchctl bootout`，因此始终保持运行的任务也会保持停止。
- `start` 会启用并 bootstrap 任务。
- `restart` 和 `run` 使用 `kickstart -k`。
- 对未加载任务执行 `restart` 时，会先 bootstrap，再立即触发，即使 `RunAtLoad=false` 也会运行。

## 日志

```bash
lagctl logs worker
lagctl logs -f worker
lagctl logs --stream stderr -n 200 worker
```

默认日志路径：

```text
~/Library/Logs/LaunchAgentManager/<name>.stdout.log
~/Library/Logs/LaunchAgentManager/<name>.stderr.log
```

日志由 launchd 持续追加。`lagctl` 当前不会自动轮转、压缩或限制日志文件大小，高输出任务应配置外部日志轮转策略。

删除任务但保留日志：

```bash
lagctl remove worker
```

同时删除任务和日志：

```bash
lagctl remove --purge-logs worker
```

## 进程与网络指标

对于运行中的任务，TUI 会显示：

- 主进程 PID 和所有后代进程 PID。
- 通过 `lsof` 获取的 TCP 监听地址。
- 通过 `nettop` 获取的累计接收和发送字节数。
- 通过约 1 秒 `nettop` 差值采样得到的接收和发送速率。

CLI 也可以获取相同的快照：

```bash
lagctl network NAME
```

所有运行中的任务会在一次后台刷新中统一采样。没有 PID 的任务不会调用 `nettop`。采集错误会显示在 `Network error` 字段中，不会阻止任务生命周期操作。

限制：

- 只显示 TCP LISTEN Socket，不显示 UDP Socket 和主动连接的远端地址。
- 流量根据 launchd 进程树归属。
- 无法可靠地把脱离的旧式后台进程归属到原任务。
- 短生命周期进程可能在采样完成前退出。
- 显示的速率是采样值，不是长期平均值。

## 文件与 launchd 操作

生成的 plist 文件：

```text
~/Library/LaunchAgents/local.launch-agent-manager.<name>.plist
```

工具使用的现代 launchctl 操作：

```text
加载     launchctl bootstrap gui/$UID <plist>
停止     launchctl bootout gui/$UID/<label>
重启     launchctl kickstart -k gui/$UID/<label>
状态     launchctl print gui/$UID/<label>
```

命令会直接保存在 `ProgramArguments` 中，`lagctl` 不使用 `sh -c`。管道、重定向或 Shell 变量展开应写入脚本文件，而不是作为普通参数传入。

## Shell 补全

临时启用 zsh 补全：

```bash
source <(lagctl completion zsh)
```

临时启用 bash 补全：

```bash
source <(lagctl completion bash)
```

`make install` 也会将补全文件复制到：

```text
~/.local/share/zsh/site-functions/_lagctl
~/.local/share/bash-completion/completions/lagctl
```

如果存在 Oh My Zsh 自定义目录，安装器也会把 `_lagctl` 安装到该目录。

## 安全说明

- 通过 `--env` 传入的值会以明文形式保存在 plist 中。
- `--env KEY=VALUE` 也可能保留在 Shell 历史或进程参数中。
- 不要使用这种方式保存密码、API Token 或其他敏感凭据。
- 应优先使用 macOS Keychain，或让被管理进程读取权限受限的文件。
- 被管理的 plist 文件使用 `0600` 权限写入。
- TUI 只显示用户显式提供的环境变量；自动继承的 `PATH` 不会显示在详情中。

## 故障排查

### 终端中可以运行，但 launchd 下失败

```bash
lagctl status NAME
lagctl logs --stream stderr NAME
```

`launchd` 不会读取 `.zshrc`、Shell alias、函数或交互式 Shell 初始化配置。请使用绝对路径，显式设置必要的环境变量，并选择正确的工作目录。

### 检查原始配置

```bash
lagctl show NAME
launchctl print "gui/$UID/local.launch-agent-manager.NAME"
plutil -lint "$HOME/Library/LaunchAgents/local.launch-agent-manager.NAME.plist"
```

### 修改脚本代码

修改脚本内容后不需要重新创建任务，只需执行：

```bash
lagctl restart NAME
```

只有命令、参数、环境变量、工作目录或启动策略发生变化时，才需要使用 `add --force` 或 TUI 编辑表单。

## 开发

运行基础测试：

```bash
make test
```

运行包括 Textual 测试在内的完整测试：

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

测试使用临时目录、假的 `launchctl` 和注入的网络采集器，不会修改真实 LaunchAgent。可以在本地 macOS 主机上另外执行只读 Smoke Test，以验证 `lsof` 和 `nettop`。

隔离开发实例：

```bash
LAGCTL_AGENT_DIR=/tmp/lagctl-agents \
LAGCTL_LOG_DIR=/tmp/lagctl-logs \
./bin/lagctl list
```

项目结构：

```text
lagctl.py              核心 CLI 和 LaunchAgent 服务层
lagctl_tui/            可选 Textual 界面
bin/lagctl             源码和安装后的启动器
completions/           zsh 和 bash 补全
tests/                 CLI、服务层和 TUI 测试
Makefile               测试、安装和卸载命令
pyproject.toml         Python 元数据和可选 TUI 依赖
```

## 致谢

最初的工作流设计参考了 [LaunchManager](https://github.com/Sean10000/LaunchManager)。
