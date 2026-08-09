# GitHub Actions 托管 CI 变更设计

## 目标

以 GitHub 作为实际托管平台，让 push 与 Pull Request 页面直接显示测试和二进制构建结果，并提供可下载的 Linux x86_64 单文件 artifact。

## 方案

新增 `.github/workflows/ci.yml`，包含 `unit-test` 与 `build-binary` 两个 job，均使用 Ubuntu 和 Python 3.12。前者安装 `.[dev]` 后运行 `make test` 与 `make quality`；后者安装 `.[build]`，运行 `make binary`、二进制 `version` 和 `demo`，再上传 `dist/guarded-agent`。

保留 `.gitlab-ci.yml`，用于兼容课程原始文档及其可能存在的自动检查。GitHub Actions 是当前仓库的真实 hosted CI；两套配置调用相同 Makefile 入口，避免行为分叉。

## 触发与权限

workflow 在对 `main` 的 Pull Request 和向 `main`、`feature/**` 的 push 上触发。只需要读取仓库内容，不使用 secrets，不授予写权限。artifact 使用固定名称 `guarded-agent-linux-x86_64`。

## 文档与验证

README、SPEC、PLAN、SPEC_PROCESS 和 AGENT_LOG 改为说明 GitHub 是当前托管平台，同时保留 GitLab 兼容配置。测试通过解析 workflow，检查 job、Python 版本、Makefile 命令与 artifact 路径；本地继续执行完整测试、质量检查和二进制 smoke test。

## 边界

不删除 GitLab CI，不新增公网部署，不发布 GitHub Release，不改变 Harness 核心，也不宣称 workflow 在提交前已经通过。提交并推送后，以 PR 的 Checks 页面和 Actions 页面为最终 hosted CI 证据。
