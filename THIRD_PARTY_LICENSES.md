# 第三方依赖许可证

本文件列出 `pyproject.toml` 声明的**直接**运行、开发与构建依赖。许可证依据 2026-08-10 本地安装版本的 Python package metadata（`License-Expression`、`License` 或 PyPI classifier）整理；链接指向各项目的官方源码或主页。它不是传递依赖的完整 SBOM，也不替代依赖随附的许可证正文。发布冻结二进制前，应针对实际锁定环境重新生成并人工核对完整清单。

## 运行依赖

| 包（核对版本） | 用途 | 元数据中的许可证 | 官方来源 |
|---|---|---|---|
| cryptography 45.0.7 | Scrypt、AES-GCM | Apache-2.0 OR BSD-3-Clause | <https://github.com/pyca/cryptography> |
| FastAPI 0.116.2 | 本地 WebUI | MIT | <https://github.com/fastapi/fastapi> |
| HTTPX 0.28.1 | OpenAI-compatible HTTP 客户端 | BSD-3-Clause | <https://github.com/encode/httpx> |
| Jinja2 3.1.6 | 服务端 HTML 模板 | BSD-3-Clause | <https://github.com/pallets/jinja> |
| Pydantic 2.13.4 | 严格领域模型 | MIT | <https://github.com/pydantic/pydantic> |
| python-multipart 0.0.32 | Web 表单解析 | Apache-2.0 | <https://github.com/Kludex/python-multipart> |
| Typer 0.27.1 | CLI | MIT | <https://github.com/fastapi/typer> |
| Uvicorn 0.52.1 | localhost ASGI server | BSD-3-Clause | <https://github.com/Kludex/uvicorn> |

## 开发与构建依赖

| 包（核对版本） | 用途 | 元数据中的许可证 | 官方来源 |
|---|---|---|---|
| mypy 2.3.0 | 静态类型检查 | MIT | <https://github.com/python/mypy> |
| PyYAML 6.0.3 | CI 配置测试解析 | MIT | <https://github.com/yaml/pyyaml> |
| pytest 9.1.1 | 测试 | MIT | <https://github.com/pytest-dev/pytest> |
| Ruff 0.16.2 | lint | MIT | <https://github.com/astral-sh/ruff> |
| PyInstaller 6.22.0 | 单文件二进制构建 | GPL-2.0-or-later，带允许构建和分发非自由程序的特殊例外 | <https://github.com/pyinstaller/pyinstaller> |

`setuptools>=68` 是 PEP 517 构建后端要求，不会按项目直接依赖安装到目标运行环境；其许可证与实际解析版本也应在发布时纳入完整环境审计。
