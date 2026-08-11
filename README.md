# Guarded Agent

## 项目简介

Guarded Agent 是一个治理优先、仅在本地运行的 Coding Agent Harness。LLM 只提出结构化动作；确定性代码负责工作区围栏、命令风险分类、一次性审批、工具执行、验证反馈、审计和停机判断。CLI 与本地 WebUI 共用同一个应用服务；默认 Mock provider 可离线演示，无需 API Key 或网络。

目标且受支持的源码运行时为 **Python 3.12.x**。项目不使用现成的 Agent 编排框架。

## 安装

当前可用的源码安装方式如下（在仓库根目录执行）：

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

此项目没有定义单独的 console-script 入口；安装后使用 `python -m guarded_agent`。可先确认命令面：

```bash
python -m guarded_agent --help
python -m guarded_agent credential --help
python -m guarded_agent memory --help
```

> **注意（venv 可见性）**：`guarded_agent` 只安装在本仓库的 `.venv` 中，不会装进系统 Python。下面所有 `python -m guarded_agent` 都要求在存在该 venv 的 shell 中执行（通常先 `. .venv/bin/activate`）。一旦 `cd` 到其他目录——例如凭据管理一节要求进入的私有目录——当前 shell 会丢失激活状态，直接运行 `python -m guarded_agent` 会落到系统解释器并报 `No module named guarded_agent`。此时改用 venv 解释器的绝对路径即可（把 `<repo>` 换成仓库实际路径），或在当前终端重新激活：
>
> ```bash
> <repo>/.venv/bin/python -m guarded_agent --help
> source <repo>/.venv/bin/activate
> ```

## 运行

先在受信任项目的根目录创建允许的验收命令；命令必须以 argv 数组写入配置，运行时只能从这里选择：

```toml
[validation]
commands = [["python", "-m", "pytest", "-q"]]
```

以下命令均为当前 CLI 的实际子命令：

```bash
# 离线机制演示：危险操作拦截、失败反馈修正、审批参数篡改拦截
python -m guarded_agent demo

# 创建一次任务。默认 provider 是 mock；真实模型需显式选择 provider。
python -m guarded_agent run \
  --workspace /absolute/path/to/trusted-project \
  --goal '修复一个可验证的问题' \
  --accept '["python", "-m", "pytest", "-q"]'

# 仅本机 WebUI，默认端口为 8000；不得改为公网监听。
python -m guarded_agent web \
  --workspace /absolute/path/to/trusted-project

# DeepSeek 本地持续对话（启动时输入一次 vault 密码）
python -m guarded_agent web \
  --workspace /absolute/path/to/trusted-project \
  --provider openai-compatible --model deepseek-chat

# 记忆管理
python -m guarded_agent memory add \
  --workspace /absolute/path/to/trusted-project \
  --category convention --content '测试使用 pytest'
python -m guarded_agent memory search \
  --workspace /absolute/path/to/trusted-project --query pytest

# 查看已安装版本
python -m guarded_agent version
```

WebUI 只允许绑定 `127.0.0.1`，访问地址为 `http://127.0.0.1:8000`。它固定在启动时给定的一个工作区；不接受主密码、任意 shell 输入或公网地址。页面提供任务、审批、记忆和设置组成的本地工作台；主题按钮按“跟随系统 → 浅色 → 深色”循环，并仅把该选择保存到浏览器本地存储。任务详情只轮询同源的本地状态 JSON，动态状态与时间线用文本节点更新，不接收或插入服务器返回的 HTML。

使用 `--provider openai-compatible` 启动 WebUI 时，服务启动阶段只解锁一次本地 vault；之后同一进程中的“持续对话”面板可以连续发送消息。每条消息仍会经过同一个 AgentLoop、治理策略、审批和审计流程。主密码不会保存，服务重启后需要再次输入；WebUI 仍只监听 `127.0.0.1`。

本次工作树的审计环境没有 Chromium、Playwright 或其他可用浏览器二进制，因此未生成 `docs/screenshots/webui-light.png` 或 `docs/screenshots/webui-dark.png`，也没有以占位图替代。需要截图时，可在具备浏览器的本地环境启动上述 WebUI 后，分别在 1440px 宽度的浅色和深色主题下捕获真实任务工作台画面。

默认 Mock provider 不需要 API Key。`run` 的默认 Mock 脚本只用于安全的无模型路径；要让实际任务完成，应提供受支持的真实 provider，或运行固定的 `demo` 演示。

## 配置

工作区可选的 `guarded-agent.toml` 只支持 `[limits]` 与 `[validation]`。`[validation].commands` 是唯一可预授权的验证器列表。配置不能包含凭据，也不能降低路径围栏、敏感路径保护、命令分类或硬上限。

## 凭据管理

真实模型模式使用 `openai-compatible` provider。API Key 与主密码都在终端隐藏输入；主密码通过 Scrypt 派生密钥，API Key 以 AES-GCM 认证加密后写入本地 vault。默认 vault 路径是**执行命令时的当前目录**下的 `.guarded-agent/credentials.vault`，可用 `--vault` 指定其他受限权限的位置。

在目标机上，先进入仅自己可访问的目录，再录入凭据：

```bash
mkdir -p ~/.local/share/guarded-agent
chmod 700 ~/.local/share/guarded-agent
cd ~/.local/share/guarded-agent
python -m guarded_agent credential set --provider openai-compatible
python -m guarded_agent credential status
# 重复 set 即安全地更新 provider、endpoint 或 API Key（会重新加密写入）
python -m guarded_agent credential set --provider openai-compatible
python -m guarded_agent credential status --unlock
python -m guarded_agent credential clear
```

> **提示**：上述命令先 `cd` 离开了仓库根目录，所以其中的 `python` 不会再指向项目 venv。请先按上一节说明激活 venv 或使用 venv 绝对路径调用，否则会报 `No module named guarded_agent`。

`status` 只报告是否已配置；加 `--unlock` 后也只报告 provider 与 endpoint，**绝不回显 API Key 或主密码**。遗忘主密码无法恢复原凭据，只能 `clear` 后重新 `set`。真实 provider 运行时会在终端解锁 vault：

```bash
python -m guarded_agent run \
  --workspace /absolute/path/to/trusted-project \
  --goal '修复一个可验证的问题' \
  --provider openai-compatible
```

单文件二进制可将上述 `python -m guarded_agent` 前缀替换为 `./guarded-agent`。

## 测试

离线测试入口为：

```bash
make test
```

本工作树当前解释器为 Python 3.14 时，ASGI 传输相关 Web 测试会跳过，因为该组合会挂起；GitHub Actions 的 `unit-test` job 使用目标版本 Python 3.12 执行 `make test` 与 `make quality`。托管结果以仓库的 **Actions** 页面或 Pull Request 的 **Checks** 区域为准，本地验证不能替代 hosted CI pass 记录。仓库同时保留 `.gitlab-ci.yml` 兼容课程原始检查。

## 机制演示

`python -m guarded_agent demo` 在临时工作区和 Mock LLM 中确定性展示三条机制：敏感文件删除被治理拒绝；一次错误修改收到测试失败反馈后被修正；已审批动作的参数被篡改后不会执行。该命令不需要网络或 API Key。

## 分发

课程目标分发物是未签名的 **Linux x86_64** PyInstaller 单文件可执行程序 `guarded-agent`。目标机取得该文件后，首次执行前需要授予执行权限：

```bash
chmod +x guarded-agent
./guarded-agent version
./guarded-agent demo
./guarded-agent web --workspace /absolute/path/to/trusted-project
```

在 Linux x86_64 上从源码本地构建时，先安装包含 PyInstaller 的构建依赖，再运行检查进仓库的构建入口：

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[build]'
make binary
./dist/guarded-agent version
./dist/guarded-agent demo
```

成功后产物为 `dist/guarded-agent`。GitHub Actions 的 `build-binary` job 会用 Python 3.12 构建、运行 `version` 与 `demo`，再上传名为 `guarded-agent-linux-x86_64` 的 artifact。获取方式：打开 GitHub 仓库的 **Actions** 页面，进入一次成功的 **CI** workflow，在页面底部 **Artifacts** 下载；artifact 只在 workflow 成功且尚未超过 GitHub 保留期时可用。当前不提供 Docker、PyPI 包、macOS/Windows 构建或公网部署 URL；这是经用户确认的课程要求偏离，已记录在 `SPEC_PROCESS.md`。

## 目录结构

```text
src/guarded_agent/     Harness、CLI、WebUI、治理、工具、存储与 provider
tests/                 离线单元与集成测试
docs/superpowers/      设计、计划与过程证据
SPEC.md                产品与系统规约
PLAN.md                实施计划与交付状态
Makefile               测试、lint 与类型检查入口
```

## 安全边界

只对**可信仓库**运行本工具。它是应用层护栏，**不是 OS 级强沙箱**；项目中的测试、构建脚本或依赖安装仍可能执行恶意代码。

核心边界包括：路径必须是工作区内的相对 POSIX 路径，真实路径与符号链接不得逃逸；普通文件工具拒绝 `.git`、`.env*`、私钥和 vault 路径；命令以 argv 直接执行、不使用 shell；高风险操作须绑定到规范化动作摘要并只可审批/消费一次；硬禁令（如提权、危险 Git 操作）不可审批。命令输出与审计会脱敏，子进程不继承 API Key。

## 已知限制

- 不支持并发任务、多用户协作、公网 WebUI、远程部署或从中途子进程恢复。
- 不支持不可信/恶意项目，也不能替代容器、虚拟机或操作系统隔离。
- 目标二进制仅为 Linux x86_64，且未签名；其他平台及 CPU 架构不在支持范围内。
- 本地构建与 GitHub Actions artifact 仅产出未签名的 Linux x86_64 单文件二进制；不发布长期稳定的 Release 下载链接。
- Python 3.13 及以上不在声明的支持范围；本地 Python 3.14 仅用于辅助验证且会跳过 ASGI Web 测试，正式测试和构建请使用 Python 3.12。
- WebUI 中的任务固定使用 Mock provider（只读控制台），不会消耗真实 API Key；真实模型任务请通过 CLI `guarded-agent run --provider openai-compatible` 发起。

## 许可证

本项目采用 [MIT License](LICENSE)。直接运行、开发和构建依赖的许可证及核对范围见 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)；发布二进制前仍应对最终锁定的完整传递依赖做一次许可证清单复核。
