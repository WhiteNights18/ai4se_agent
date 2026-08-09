# Guarded Agent

## 项目简介

Guarded Agent 是一个治理优先、仅在本地运行的 Coding Agent Harness。LLM 只提出结构化动作；确定性代码负责工作区围栏、命令风险分类、一次性审批、工具执行、验证反馈、审计和停机判断。CLI 与本地 WebUI 共用同一个应用服务；默认 Mock provider 可离线演示，无需 API Key 或网络。

目标运行时为 **Python 3.12**。项目不使用现成的 Agent 编排框架。

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

# 记忆管理
python -m guarded_agent memory add \
  --workspace /absolute/path/to/trusted-project \
  --category convention --content '测试使用 pytest'
python -m guarded_agent memory search \
  --workspace /absolute/path/to/trusted-project --query pytest

# 查看已安装版本
python -m guarded_agent version
```

WebUI 只允许绑定 `127.0.0.1`，访问地址为 `http://127.0.0.1:8000`。它固定在启动时给定的一个工作区；不接受主密码、任意 shell 输入或公网地址。

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

`status` 只报告是否已配置；加 `--unlock` 后也只报告 provider 与 endpoint，**绝不回显 API Key 或主密码**。遗忘主密码无法恢复原凭据，只能 `clear` 后重新 `set`。真实 provider 运行时会在终端解锁 vault：

```bash
python -m guarded_agent run \
  --workspace /absolute/path/to/trusted-project \
  --goal '修复一个可验证的问题' \
  --provider openai-compatible
```

完成 Task 10 后，单文件二进制可将上述 `python -m guarded_agent` 前缀替换为 `./guarded-agent`。

## 测试

离线测试入口为：

```bash
make test
```

本工作树当前解释器为 Python 3.14 时，ASGI 传输相关 Web 测试会跳过，因为该组合会挂起；目标 CI 应使用 Python 3.12 执行这些测试。GitLab CI 的 `unit-test` job 尚未实现，因此不能声称 CI 已通过。

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

尚未生成可下载 artifact：Task 10 的 `guarded-agent.spec` 与构建脚本仍未完成，所以这里不虚构下载链接或已可运行的构建命令。完成该 spec 后，构建命令应以检查进仓库的 spec 为准。当前不提供 Docker、PyPI 包、macOS/Windows 构建或公网部署 URL；这是经用户确认的课程要求偏离，已记录在 `SPEC_PROCESS.md`。

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
- 当前尚无 PyInstaller artifact、构建 spec 或 GitLab CI `unit-test` 实现。
- Python 3.14 环境会跳过 ASGI Web 测试；请在 Python 3.12 CI 上执行完整该组测试。

## 许可证

本项目采用 [MIT License](LICENSE)。第三方依赖及其许可证信息以各依赖的发布元数据为准；尚未提供独立的第三方许可证汇总文件。
