# Guarded Agent

Guarded Agent 是一个仅在本机运行的 Coding Agent Harness。LLM 只提出结构化动作；工作区边界、命令风险分类、人工审批、工具执行、测试反馈、审计和停机判断均由确定性代码完成。

它适合在**可信的本地代码仓库**中执行受控的修复任务。它不是操作系统级沙箱，不要对来源不明的项目运行。

## 1. 支持范围与前置条件

- 源码运行时：Python **3.12.x**（不支持 3.13+）。
- 二进制目标：未签名 Linux x86_64 单文件。
- WebUI：只监听 `127.0.0.1`，不提供公网部署。
- 默认 provider：Mock，无需网络和 API Key。
- 真实 provider：OpenAI-compatible 接口，例如 DeepSeek；API Key 只进入加密 vault。
- 需要 Git、Python 3.12 和一个可信工作区。若使用真实模型，还需要该 provider 的有效 API Key。

## 2. 从源码安装

在仓库根目录执行：

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python -m guarded_agent --help
```

每次打开新终端都要重新执行 `. .venv/bin/activate`。如果已经离开仓库目录，使用绝对路径调用模块，例如：

```bash
/absolute/path/to/ai4se_agent/.venv/bin/python -m guarded_agent --help
```

项目没有单独的 console-script；下面所有 `python -m guarded_agent` 都指仓库虚拟环境中的 Python。

## 3. 创建一个可运行的工作区

Agent 只能使用工作区内的相对路径。先创建一个你信任的项目目录，并在其中声明允许的验收命令：

```bash
mkdir -p ~/guarded-demo
cd ~/guarded-demo
git init
cat > guarded-agent.toml <<'TOML'
[validation]
commands = [["python", "-m", "pytest", "-q"]]
TOML
cat > test_smoke.py <<'PY'
def test_smoke():
    assert True
PY
```

`[validation].commands` 必须是 argv 数组；Agent 只能从这里选择验证器，不接受任意 shell 字符串。首次运行时 `.guarded-agent/` 和 SQLite 状态库会自动创建。

## 4. 离线演示与 CLI

回到仓库根目录并激活虚拟环境：

```bash
cd /absolute/path/to/ai4se_agent
. .venv/bin/activate

# 三个确定性机制：危险动作拦截、失败反馈修正、审批参数篡改拦截
python -m guarded_agent demo

# 查看完整命令帮助
python -m guarded_agent --help
python -m guarded_agent run --help
python -m guarded_agent memory --help
```

使用 Mock provider 创建任务（Mock 只用于离线演示，不会完成真实修复）：

```bash
python -m guarded_agent run \
  --workspace "$HOME/guarded-demo" \
  --goal '检查项目并修复一个可验证的问题' \
  --accept '["python", "-m", "pytest", "-q"]'
```

记忆是按工作区隔离的：

```bash
python -m guarded_agent memory add \
  --workspace "$HOME/guarded-demo" \
  --category convention \
  --content '测试使用 pytest'
python -m guarded_agent memory search \
  --workspace "$HOME/guarded-demo" --query pytest
```

## 5. 启动本地 WebUI

### 5.1 Mock 模式（无需 API Key）

在仓库根目录运行：

```bash
python -m guarded_agent web \
  --workspace "$HOME/guarded-demo" \
  --host 127.0.0.1 --port 8000
```

然后用浏览器打开 <http://127.0.0.1:8000/>。页面包含任务工作台、持续对话面板、执行时间线、审批中心、记忆库和设置。按 `Ctrl+C` 停止服务。

### 5.2 DeepSeek 等真实 provider 的持续对话

先把 API Key 写入带主密码的加密 vault。推荐把 vault 放在仓库之外，并限制目录权限：

```bash
mkdir -p ~/.local/share/guarded-agent
chmod 700 ~/.local/share/guarded-agent
export GUARD_VAULT="$HOME/.local/share/guarded-agent/credentials.vault"

python -m guarded_agent credential set \
  --provider openai-compatible \
  --endpoint https://api.deepseek.com/v1 \
  --vault "$GUARD_VAULT"
python -m guarded_agent credential status --vault "$GUARD_VAULT"
```

`set` 会隐藏输入 API Key 和主密码；重复执行会安全更新；`status` 不回显密钥，`clear` 会删除 vault：

```bash
python -m guarded_agent credential status --vault "$GUARD_VAULT" --unlock
python -m guarded_agent credential clear --vault "$GUARD_VAULT"
```

启动真实 provider WebUI：

```bash
python -m guarded_agent web \
  --workspace "$HOME/guarded-demo" \
  --provider openai-compatible \
  --model deepseek-chat \
  --vault "$GUARD_VAULT" \
  --host 127.0.0.1 --port 8000
```

服务启动时只输入一次 vault 主密码。之后可在页面持续发送多条消息；每条消息都会经过同一个 AgentLoop、治理、审批、工具执行和审计流程。服务停止后密钥不会留在进程中，下次启动需要再次解锁。不要把 API Key 放进命令行参数、配置文件、Git、日志或 shell 历史。

如果 provider 不是 DeepSeek，只需在 `credential set` 时填写其 OpenAI-compatible endpoint，并在 WebUI 使用对应模型名。

## 6. 凭据安全边界

vault 使用主密码派生密钥，并以 AES-GCM 加密 API Key；文件权限应保持为仅当前用户可读。项目不会提供明文查看功能，忘记主密码无法恢复，只能清除后重新录入。真实 API Key 会进入输出脱敏器，子进程环境不会继承它。凭据安全不能替代对恶意项目脚本的隔离，因此只对可信仓库运行。

## 7. 测试、质量检查与机制演示

```bash
make test       # pytest
make quality    # Ruff + mypy
make check      # test + quality
python -m guarded_agent demo
```

核心机制测试使用 Stub/Mock LLM，不依赖网络或真实 API。Python 3.12 是正式测试环境；本机 Python 3.14 下部分 ASGI 测试会跳过，GitHub Actions 使用 Python 3.12 执行完整测试。

## 8. 单文件二进制

GitHub Actions 的 `build-binary` 会构建并上传 `guarded-agent-linux-x86_64` artifact；在仓库 Actions 页面打开成功的 CI run 即可下载。artifact 受 GitHub 保留期限制，不是长期 Release。

在 Linux x86_64 本地构建：

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[build]'
make binary
chmod +x dist/guarded-agent
./dist/guarded-agent version
./dist/guarded-agent demo
./dist/guarded-agent web --workspace "$HOME/guarded-demo" --host 127.0.0.1
```

目标机首次执行下载的文件前运行 `chmod +x guarded-agent`。二进制未签名，Linux 可能显示来源或执行权限警告；确认文件来源后再允许运行。当前不提供 Docker、PyPI、macOS、Windows 或公网服务。

## 9. CI 与项目结构

每次 push 和 Pull Request 都触发 `.github/workflows/ci.yml`，其中包含 `unit-test` 和 `build-binary`；仓库也保留课程要求的 `.gitlab-ci.yml`，其中同样有名为 `unit-test` 的 job。

```text
src/guarded_agent/     Harness 内核、CLI、WebUI、治理、工具、存储、provider
tests/                 离线单元测试和 WebUI 测试
docs/superpowers/      设计与实施计划
SPEC.md                产品与系统规约
PLAN.md                任务和验证记录
SPEC_PROCESS.md        brainstorming、冷启动审计和取舍
AGENT_LOG.md           时间顺序的过程证据
REFLECTION.md          学生本人反思报告
```

## 10. 限制与许可证

- 只绑定本机 `127.0.0.1`，不支持公网部署、多用户或并发任务。
- 这是应用级治理，不是 OS/容器级沙箱；不要对不可信仓库运行。
- 二进制仅支持 Linux x86_64，且未签名。
- 使用第三方依赖时遵循其许可证，清单见 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。本项目代码采用 [MIT License](LICENSE)。
