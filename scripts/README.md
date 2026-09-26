# `scripts/` —— 验证探针

## 这个目录放什么

这里放的是**追踪具体缺陷时写下的一次性验证探针**：每个脚本对应一次真实的排查，
它把「修复确实生效」这件事重新跑一遍，并只打印若干条 `结论: 值` 形式的结论行。

它们是**修复的证据**，不是交付代码，也**不是**这个项目的测试套件。

- 正式的测试在 [`tests/`](../tests)，由 `pytest` 驱动，是 CI 的守门人。
- 这里的脚本是**补充证据**：有些结论（比如「把这个修复改回去，探针就连 JSON 都产不出来」）
  用普通单元测试表达不了，只能用一个故意破坏基线的脚本来证明。

## 为什么它们不参与 lint

`pyproject.toml` 里有：

```toml
[tool.ruff]
extend-exclude = ["scripts"]
```

这些脚本里有临时字符串切分、`# type: ignore`、故意构造的畸形输入等等，
它们是一次性排查的产物，不打算满足产品代码的标准。`helpui/` 和 `tests/` 是严格 lint 的。

## 约定

**报告结论行，而不是日志。** 跑完只打印几行 `check: value`，把完整日志写进文件。
排查问题时，先写一个这样的探针，而不是把日志粘贴进对话里。

**只在需要时跑。** 其中几个脚本会启动真实的 uvicorn 子进程并生成完整项目，
单个耗时可达数十秒到几分钟。它们不是每次改动都该跑的东西。

## 脚本清单

### 发布验证

| 脚本 | 用途 |
|---|---|
| `verify_release_metadata.py` | 发布元数据审计：`OWNER` 占位符、版本号与 CHANGELOG 是否一致、文档里的本地链接是否指向真实文件、LICENSE 年份/著作权人、`pyproject` 入口点。 |
| `verify_release_acceptance.py` | **独立最终验收**：全新 `git clone` → 建 venv → `pip install -e ".[dev]"` → ruff → mypy → pytest → `generate` → `helpui test` → **真起服务**确认页面 200、提交表单、历史入库。整个流程从零开始，用于验证提交后的状态，而不是工作区。 |
| `verify_docs_claims.py` | 把 `README.md` / `CONTRIBUTING.md` 里写给用户的**每条命令和路径**真实执行一遍（四条命令、`--port/--host` 落盘、拒绝覆盖、`--json` 可管道、环境变量表与生成代码是否对得上）。 |
| `verify_ci_workflow.py` | 校验 `.github/workflows/ci.yml`：YAML 能否解析、matrix/触发分支/编码环境变量是否正确，以及**那段内联 bash 的 `run:` 块是否与本地实测过的副本逐字节一致**，并用 `bash -n` 检查其语法。 |
| `verify_ci_locally.sh` | 用 **bash 语义**把 CI 的每一步在本地真实重放一遍（含那段多行 `python -c` 断言）。需要 Git Bash / WSL bash。日志写进 `.ci_local.log`。 |
| `verify_scripts_intact.py` | 本目录自检：`slow_tool.py` 仍可被扫描，且没有任何脚本引用已不存在的文件。 |

### 缺陷复现证据

| 脚本 | 用途 |
|---|---|
| `verify_password_widget.py` | **`--password` 退化成明文输入框**的复现与对照。直接调 `infer_type` 展示那条出错的 `or` 短路链，再用三个真实 fixture 给出权威结论。 |
| `verify_option_types.py` | 类型推断回归对照。对三个 fixture 打印每个选项推断出的类型；可用于与 stash 出的基线逐行比对，证明改动没引入回归。 |
| `verify_baseline_proof.py` | 证明取消功能的修复是**承重的**：它把生成器源码里那一处「阻塞调用」改回去重新生成，探针随即产不出 JSON。这正是普通 `git checkout` 做不到的隔离方式。 |
| `verify_cancel_semantics.py` | 取消/超时语义（`timeout > cancelled > failed > succeeded` 的优先级）以及事件循环探针。 |
| `verify_templates.py` | 把打包的 8 个模板用真实数据逐个渲染一遍，报告失败项。 |
| `verify_task6.py` | 文件类型位置参数上传、上传大小上限等结论的进程内验证（`TestClient`，无需 uvicorn）。 |
| `verify_task6_fixtures.py` | 上一条的「浏览器真实路径」版本：对三个真实 fixture 的 `convert` 命令跑一遍。 |
| `verify_task4.py` | 取消相关结论的进程内验证。**注意**：`verify_baseline_proof.py` 会读取本文件的 `PROBE` 字符串并重新执行，改这个文件时留意那个耦合。 |
| `verify_generated_app.py` | 生成项目的端到端验证：用真实 uvicorn 子进程覆盖取消、超时、校验、下载等路径。**最慢的一个。** |
| `verify_cli.py` | `helpui generate` 的命令行行为：产物是否齐全、`--port` 是否写进 config/README、重复生成是否拒绝、`--force` 是否覆盖、`spec.json` 能否往返、生成文件是否全为 LF。 |

### 共用的 fixture

| 脚本 | 用途 |
|---|---|
| `slow_tool.py` | 验证用 CLI：`--help` 瞬间返回，但子命令**会真的睡**。取消、超时、并发这几个脚本都靠它制造可控的长任务。它同时是一个被 HelpUI 扫描的目标工具。 |

## 怎么跑

先按 [CONTRIBUTING.md](../CONTRIBUTING.md) 装好开发环境：

```console
python -m pip install -e ".[dev]"
```

然后直接跑：

```console
python scripts/verify_release_metadata.py     # 快，纯静态检查
python scripts/verify_docs_claims.py          # 中等，会真实执行文档里的命令
python scripts/verify_release_acceptance.py   # 慢，全新 clone + 真起服务
bash    scripts/verify_ci_locally.sh          # 慢，完整重放 CI
```

在 Windows 上先把控制台设成 UTF-8（`PYTHONIOENCODING=utf-8`）：
本项目的 fixture 和模板含非 ASCII 字符，GBK 控制台会直接抛 `UnicodeEncodeError`。
