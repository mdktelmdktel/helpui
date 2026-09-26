# 参与贡献

感谢你关注 HelpUI。本文档说明如何搭建环境、代码如何组织，以及项目遵循的约定。

## 环境搭建

需要 **Python 3.11 或更高版本**。

```console
git clone https://github.com/OWNER/helpui.git
cd helpui
python -m pip install -e ".[dev]"
```

`click` 和 `typer` 被刻意放在 `dev` extra 里：它们是 HelpUI 要*解析*的框架，
不是运行 HelpUI 需要的库。因此 `helpui scan` 本身在最小安装下就能跑，只需要标准库。

## 检查项

CI 会跑的东西，本地都能跑：

```console
ruff check .        # lint（helpui/ 和 tests/；scripts/ 被排除，见下）
mypy helpui         # 严格类型检查
pytest -q           # 全量测试
```

在 Windows 上，请确保你的终端是 UTF-8（`PYTHONIOENCODING=utf-8`）；
测试 fixture 和生成的模板含有 GBK 控制台打印不出来的字符。

## 代码如何组织

```
helpui/
  cli.py             `helpui` 入口（很薄：解析参数，然后委派）
  model.py           CLIOption / CLICommand / CLISpec —— 冻结的契约
  scanner.py         运行 `<tool> --help`；唯一执行外部工具的地方
  scanner_service.py 探测 + 解析 + 展开子命令；持有错误码
  parsers/           每个框架一个模块，外加共享的文本工具
  generator.py       从 CLISpec 写出一个独立项目
  selftest.py        `helpui test`：端到端驱动一个生成的项目
  templates/         Jinja2 模板，原样复制进生成的项目
  static/            style.css 和内置的 htmx（不走 CDN）
```

### 项目的两半

把它们在心里分开看会很有帮助：

1. **HelpUI 自身**（`helpui/`）—— 解析 help 输出并写出项目。
2. **生成出来的项目**（`helpui_app/`）—— FastAPI 应用、执行器、历史记录、安全层。
   这份代码是以 *字符串形式* 存在于 `generator.py` 里的
   （`_fastapi_app_source()`、`_executor_source()` 等），因为它必须被写进用户的目录。

**修改生成出来的行为，就是修改 `generator.py` 里对应的 `_*_source()` 函数**——
而不是改 `helpui_app/` 下的某个文件，那个目录只在生成出来的输出目录里存在。
同理，修改 `templates/` 会同时改变 HelpUI 和它生成的每一个项目。

## 测试约定

**驱动真实的 fixture 胜过手写 help 字符串。** `tests/fixtures/` 里的 fixture 是
可运行的 argparse／click／typer 工具。解析器测试会执行它们并解析真实输出，
因为 help 输出会与库实际打印的内容产生偏移。手写字符串会掩盖这种偏移；
真实 fixture 已经抓出过多个真实 bug。

**保持 fixture 的确定性。** help 文本会按终端宽度折行，
所以每个 fixture 子进程都设置 `COLUMNS=100`、`TERM=dumb` 和 `NO_COLOR=1`
（见 `tests/conftest.py`）。没有这些设置，80 列的 CI 机器和 120 列的开发者终端
会解析出不同结果。

**golden 文件按字节逐一比对。** 生成器原样复制模板；
测试断言这些副本与打包的源文件逐字节相同。
这也是 `.gitattributes` 固定 `eol=lf` 的原因——检出时的 CRLF 会让生成结果不可复现。

**绝不把生成的项目导入测试进程。** 涉及生成应用的测试会让它在子进程里运行
（见 `tests/test_e2e.py`），这样它的 `helpui_app` 包就不会与 `sys.modules`
里的任何东西冲突。

**探针：报告结论行，而不是日志。** `scripts/` 存放追查特定 bug 时写的一次性验证探针。
它们打印几行 `check: value` 然后退出——它们是修复的证据，不是交付代码，
所以该目录被排除在 lint 之外。排查 bug 时，优先写这样一个探针，
而不是把日志粘贴进对话里。

## 约定

- **提交信息** 遵循 [Conventional Commits](https://www.conventionalcommits.org/)：
  `feat(scope): ...`、`fix(scope): ...`、`docs: ...`、`chore: ...`。
- **文档字符串** 解释*为什么*，而不是*做了什么*。当某个决策不那么显然时，
  把理由记下来——未来的读者不该被迫重新推导一遍。
- **不引入新的重量级依赖。** HelpUI 刻意让 `scan` 停留在标准库上。
  如果确实需要某个依赖，把它加到 `pyproject.toml` 的 `dev` extra 或运行时列表里，
  在 PR 描述里写一条说明，并在 `SPEC.md` 里给出理由。
- **类型注解** 覆盖 `helpui/` 下的所有代码；`mypy` 以严格模式运行。
- **绝不拼接 shell 字符串。** 子进程参数一律以列表传递并配 `shell=False`。
  测试断言注入形状的输入保持字面量。

## 决策记录在哪里

`SPEC.md` 是项目自行做出的每一处取舍的记录：支持什么、刻意不支持什么，
以及每个生成器决策背后的理由。如果你改变了某个行为，
请在同一个 PR 里更新 `SPEC.md`。`CHANGELOG.md` 记录用户可见的变更。

## 反馈 bug

请包含：

- 你拿给 HelpUI 的那个工具（或它的 `--help` 的最小复现），
- `helpui scan <tool> --json` 的输出，它记录了探测到的框架和所有警告，
- 你期望的行为是什么。

HelpUI 解析错误的 help 输出，是最有价值的一类报告——
这正是 fixture 驱动的解析器测试存在的意义。
