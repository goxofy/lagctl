# Launch Agent Manager (`lagctl`)

`lagctl` 是一个面向 macOS 14 Sonoma 的轻量命令行工具，用来让 Python、Node、Shell 等脚本通过系统自带的 `launchd` 持续运行。它参考了 [LaunchManager](https://github.com/Sean10000/LaunchManager) 的核心工作流，但当前版本只管理当前用户的 LaunchAgent，不需要管理员权限，也不需要让本工具本身常驻。

## 第一版功能

- 创建用户级 LaunchAgent，并在登录时自动启动。
- 支持始终重启、仅失败后重启、单次运行、固定间隔运行。
- 支持不修改旧式后台启动脚本，让其派生进程在启动脚本退出后继续运行。
- 支持 zsh 和 bash 的子命令、选项与任务名称自动补全。
- 启动、停止、重启和立即触发任务。
- 提供可选的 Textual 终端仪表盘，用于浏览任务和查看详情。
- 查看任务状态、PID、最近退出码和实际 plist。
- 分别记录 stdout、stderr，并支持持续追踪日志。
- 自动识别 `.py`、`.js`、`.mjs`、`.cjs`、`.sh`、`.zsh` 和 `.bash` 脚本的解释器。
- 将当前终端的 `PATH` 固化到任务中，避免 `launchd` 找不到 Homebrew、Node 或 Python。
- 原子写入 plist；只操作 `local.launch-agent-manager.*` 标签下的任务。

当前版本暂不支持 GUI、系统级 LaunchDaemon、日历式复杂计划、监听端口发现、Docker 服务管理和编辑任意第三方 plist。

## 要求

- macOS 14 Sonoma 或更新版本。
- Python 3.9 或更新版本。运行 Python 脚本的主机通常已经具备此条件；可用 `python3 --version` 检查。
- 系统自带的 `/bin/launchctl`。

TUI 是可选功能，需要额外安装 Textual：

```bash
python3 -m pip install -e '.[tui]'
```

## 快速开始

在仓库中直接运行：

```bash
./bin/lagctl doctor
./bin/lagctl add my-worker -- /absolute/path/to/worker.py
./bin/lagctl list
./bin/lagctl logs -f my-worker
```

启动终端仪表盘：

```bash
./bin/lagctl tui
```

仪表盘当前支持任务列表、状态/PID/退出码/命令详情、自动刷新、任务启停、重启、立即触发、日志查看和删除确认。使用以下快捷键：

```text
↑/↓       选择任务
Enter     刷新详情
s         启动或停止选中任务
R         重启选中任务
x         立即触发选中任务
l         查看日志
d         删除选中任务
r         手动刷新
q         退出
```

日志页面默认每 2 秒重新读取最近 200 行。使用 `o` 查看 stdout、`e` 查看 stderr、`a` 查看两者，使用 `q` 或 `Esc` 返回。

如果脚本路径包含空格，按普通 shell 规则加引号：

```bash
./bin/lagctl add my-worker -- "/Users/me/My Project/worker.py" --port 8080
```

也可以显式给出解释器和参数：

```bash
./bin/lagctl add api \
  --cwd /Users/me/projects/api \
  --env NODE_ENV=production \
  --env PORT=3000 \
  -- /opt/homebrew/bin/node server.js
```

`--` 用来明确分隔 `lagctl` 自身的选项与被管理程序的命令，是必需的。分隔符右侧的所有内容都会原样作为任务命令及参数保存。

`--interval` 未指定 `--mode` 时使用单次触发语义；如果显式指定 `--mode`，只能使用 `once`。例如 `--interval 900 --mode keep-alive` 会被拒绝，避免重启策略被静默忽略。

## 安装

默认安装到 `~/.local`，不会使用 `sudo`：

```bash
make test
make install
```

确认 `~/.local/bin` 已加入 shell 的 `PATH`：

```bash
export PATH="$HOME/.local/bin:$PATH"
lagctl doctor
```

也可以指定其他前缀：

```bash
make install PREFIX="$HOME/bin-tools"
```

卸载默认位置中的 `lagctl`：

```bash
make uninstall
```

卸载命令只删除 `lagctl` 程序文件，不会删除已经创建的 LaunchAgent、任务日志或脚本。

## Tab 自动补全

安装包包含 zsh 和 bash 补全脚本，可补全子命令、常用选项以及当前已经管理的任务名称。例如输入：

```text
lagctl sta<Tab>       -> lagctl status
lagctl status tg<Tab> -> lagctl status tg-lite-listener
lagctl logs --str<Tab> -> lagctl logs --stream
```

临时为当前 zsh 会话启用：

```bash
source <(lagctl completion zsh)
```

临时为当前 bash 会话启用：

```bash
source <(lagctl completion bash)
```

标准安装还会把补全文件放到 `~/.local/share/zsh/site-functions` 和 `~/.local/share/bash-completion/completions`。对应目录需要存在于 shell 的补全搜索路径。如果检测到 Oh My Zsh 的 `~/.oh-my-zsh/custom/completions` 目录，安装器会自动同步 `_lagctl` 到该目录，新开终端后即可生效。`make uninstall` 会一并删除上述补全文件。

## 常用命令

```text
lagctl add NAME [选项] -- COMMAND [ARGUMENT ...]
lagctl list
lagctl status NAME
lagctl show NAME
lagctl start NAME
lagctl stop NAME
lagctl restart NAME
lagctl run NAME
lagctl logs [-f] [-n LINES] [--stream all|stdout|stderr] NAME
lagctl remove [--purge-logs] NAME
lagctl doctor
```

### 持续运行 Python 脚本

默认模式为 `keep-alive`。脚本退出后，无论退出码是什么，`launchd` 都会再次启动它：

```bash
lagctl add collector -- /Users/me/apps/collector/main.py
```

默认重启节流为 10 秒，可避免异常脚本形成高频重启循环：

```bash
lagctl add collector --throttle 30 -- /Users/me/apps/collector/main.py
```

### 仅异常退出后重启

```bash
lagctl add importer --mode on-failure -- /Users/me/apps/importer.py
```

脚本正常退出（退出码 `0`）后不重启，非零退出后重启。

### 固定间隔运行

下面的任务加载时先运行一次，之后大约每 15 分钟触发一次；如果上一次仍在运行，`launchd` 不会并发启动同一任务的第二个实例：

```bash
lagctl add sync --interval 900 -- /Users/me/apps/sync.py
```

如不希望加载或登录时立即执行，加上 `--no-run-at-load`：

```bash
lagctl add sync --interval 900 --no-run-at-load -- /Users/me/apps/sync.py
```

### 只创建、不立即加载

```bash
lagctl add worker --no-start -- /Users/me/apps/worker.py
lagctl start worker
```

### 兼容会自行后台化的启动脚本

如果旧式 `run.sh` 使用 `nohup ... &` 启动后台进程并立即退出，可使用：

```bash
lagctl add legacy-service \
  --mode once \
  --allow-background-children \
  --cwd /Users/me/apps/legacy-service \
  -- /bin/bash /Users/me/apps/legacy-service/run.sh start
```

该选项会在 plist 中设置 `AbandonProcessGroup=true`，禁止 `launchd` 在启动脚本退出时清理同一进程组中的后台子进程。为防止重复创建后台进程，它只能与 `--mode once` 搭配，不能与 `keep-alive`、`on-failure` 或 `--interval` 一起使用。

这种兼容模式只能监督启动脚本，不能监督启动脚本派生后自行运行的后台进程。因此 `lagctl status` 在启动完成后不会显示后台服务 PID，`lagctl stop` 也不能保证终止已脱离的后台进程。状态、停止和重启操作应继续使用原脚本提供的 `status`、`stop` 和 `restart` 子命令；长期方案仍然是让启动脚本以前台 `exec` 方式运行实际服务。

### 更新已有任务

`--force` 会先停止已加载的旧任务，原子替换 plist，再加载新配置：

```bash
lagctl add worker --force --env APP_ENV=production -- /Users/me/apps/worker.py
```

如果新配置加载失败，工具会尝试恢复旧 plist，并在旧任务原先已加载时重新加载旧任务。如果恢复也失败，会同时报告原始错误和恢复错误；此时应检查 plist 和 `launchctl` 状态。

### 停止与立即运行

```bash
lagctl stop worker
lagctl start worker
lagctl restart worker
lagctl run worker
```

`stop` 使用 `bootout` 卸载任务，所以 `keep-alive` 任务不会被立即拉起。`start` 重新加载任务。`restart` 和 `run` 会使用 `kickstart -k`；如果任务正在运行，会先终止当前进程再启动新进程。对于尚未加载的任务，`restart` 会先加载 plist，再执行 `kickstart -k`，因此即使设置了 `--no-run-at-load` 也会立即触发一次。

### 日志

```bash
lagctl logs worker
lagctl logs -f worker
lagctl logs --stream stderr -n 200 worker
```

默认日志目录：

```text
~/Library/Logs/LaunchAgentManager/<name>.stdout.log
~/Library/Logs/LaunchAgentManager/<name>.stderr.log
```

日志由 `launchd` 持续追加，工具目前不提供自动轮转、压缩或大小限制。高输出任务应自行配置日志轮转，并定期检查磁盘空间。

### 删除任务

```bash
lagctl remove worker
lagctl remove --purge-logs worker
```

默认只停止任务并删除 plist，保留日志。只有显式使用 `--purge-logs` 才会一并删除两份日志。

## 文件位置与工作原理

生成的 plist 位于：

```text
~/Library/LaunchAgents/local.launch-agent-manager.<name>.plist
```

工具对应使用以下 Sonoma 支持的现代 `launchctl` 操作：

- 加载：`launchctl bootstrap gui/$UID <plist>`
- 停止并卸载：`launchctl bootout gui/$UID/<label>`
- 立即重启：`launchctl kickstart -k gui/$UID/<label>`
- 查询：`launchctl print gui/$UID/<label>`

plist 中直接保存命令参数数组，不经过 `sh -c`。因此参数不会被二次展开，包含空格的参数也不会产生歧义。如果确实需要管道、重定向或 shell 变量展开，请把这些逻辑写进一个 `.sh`/`.zsh` 脚本，再让 `lagctl` 管理该脚本。

## 常见问题

### 终端里能运行，LaunchAgent 却失败

先看错误日志：

```bash
lagctl status NAME
lagctl logs --stream stderr NAME
```

`launchd` 不读取 `.zshrc`，不要依赖 shell alias、函数或交互式初始化逻辑。`lagctl` 默认保存创建任务时的 `PATH`，并把识别到的解释器转换为绝对路径；其他配置仍应通过 `--env KEY=VALUE` 明确传入。

`--env` 中的值会明文保存到 plist，并且可能出现在 shell 历史和进程参数中。不要用它保存密码、API token 或其他高敏感凭据；敏感信息应改用 macOS Keychain 或由脚本在运行时读取的受限文件。

### 修改代码后需要重建任务吗

不需要。plist 指向原脚本路径，修改脚本后执行 `lagctl restart NAME` 即可。只有命令、参数、环境变量、工作目录或运行策略变化时，才需要使用 `lagctl add --force ...` 更新配置。

### 如何检查 launchd 的原始配置

```bash
lagctl show NAME
launchctl print "gui/$UID/local.launch-agent-manager.NAME"
plutil -lint "$HOME/Library/LaunchAgents/local.launch-agent-manager.NAME.plist"
```

## 开发与测试

测试全部在临时目录内运行，并使用假的 `launchctl`，不会加载或删除本机任务：

```bash
make test
```

也可通过环境变量把开发实例隔离到自定义目录：

```bash
LAGCTL_AGENT_DIR=/tmp/lagctl-agents \
LAGCTL_LOG_DIR=/tmp/lagctl-logs \
./bin/lagctl list
```
