# HelpUI

**给任意非交互式命令行工具自动生成一个本地网页界面，只需要它的 `--help` 输出。**

HelpUI 读取工具的 `--help`，解析出它的参数结构，然后生成一个独立的 Web 应用：
有表单、有运行按钮、有实时日志、有结果渲染、有历史记录、有结果下载。
不用写包装代码，也不需要前端构建。

```console
$ helpui scan ./mytool.py
mytool  [argparse]  (D:\work\mytool.py)
  Convert and inspect files.
  detection: high - argparse section headers detected

  (root): Convert and inspect files.
    opt --verbose          bool
        Enable verbose logging

  convert: Convert an input file to another format.
    opt --output           str      [default: out.txt]
        Output path
    opt --format           choice   [default: json] choices=['json', 'csv', 'tsv']
        Output format
    arg input_file         file     (required)
        File to convert
```

---

## 当前状态

**五个阶段已全部实现**，命令行、解析层、生成器、运行时都已就位。

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1 | 数据模型、解析器（argparse/click/typer）、框架探测、`scan` 命令 | 已完成 |
| 2 | 生成器、golden 测试 | 已完成 |
| 3 | 运行时：FastAPI 应用、表单、实时日志、结果渲染 | 已完成 |
| 4 | 历史记录、下载、取消、超时、安全层 | 已完成 |
| 5 | `helpui test`、端到端测试、文档 | 已完成 |

四条命令都可用：`scan`、`generate`、`serve`、`test`。`--help` 里不再有任何占位未实现项。

---

## 安装

需要 **Python 3.11 或更高版本**。

```console
# 克隆后以可编辑模式安装
pip install -e .

# 连开发/测试工具一起装（pytest、httpx、ruff、mypy、click、typer）
pip install -e ".[dev]"
```

`click` 和 `typer` 只属于**测试依赖**：它们是 HelpUI 要*解析*的框架，
不是运行 HelpUI 本身需要的库。把它们放在 `dev` 里，所以 `helpui scan` 在最小安装下就能跑。

---

## 四条命令

### `helpui scan <tool> [--json] [--subcommand NAME]`

运行 `<tool> --help`，解析后打印得到的 `CLISpec`。

```console
helpui scan mytool                      # 人类可读的摘要
helpui scan mytool --json               # 机器可读的信封
helpui scan mytool --json | jq .spec    # 只要 spec 部分
helpui scan mytool --subcommand convert # 只看某个子命令的帮助页
helpui scan ./script.py --json          # 传 .py 文件会用当前解释器运行
helpui scan "python -m mypackage"       # 也接受完整命令行
```

`--json` 在标准输出上只打印**一个** JSON 文档，可以安全地接管道。
警告和错误一律走标准错误。

```json
{
  "ok": "true",
  "schema_version": 1,
  "spec": {
    "tool_name": "sample_click",
    "tool_path": "/abs/path/sample_click.py",
    "description": "Convert an input file to another format.",
    "commands": [
      {
        "name": "convert",
        "help": "Convert an input file to another format.",
        "options": [
          {
            "name": "--output",
            "dest": "output",
            "type": "str",
            "required": false,
            "default": "out.txt",
            "choices": null,
            "help": "Output path. [default: out.txt]",
            "is_flag": false,
            "multiple": false,
            "value_name": "TEXT",
            "aliases": ["-o", "--output"]
          }
        ],
        "positionals": [],
        "subcommands": []
      }
    ],
    "parser_kind": "click"
  },
  "detection": {
    "parser_kind": "click",
    "confidence": "high",
    "reason": "click section headers detected"
  },
  "warnings": []
}
```

读取这个结果时有三点要注意：

* `ok` 是**字符串** `"true"` / `"false"`，不是 JSON 布尔值（见 [SPEC.md](SPEC.md) §6.2）。
  判断方式：`payload["ok"] == "false"`。
* 根命令的 `name` 是**空字符串** `""`，不是 `"(root)"`——后者只是人类可读输出里的显示标签。
* `option.help` **保留** click 附加的 `[default: ...]` 注解原文。

扫描失败时，同样的信封会带上一个稳定的错误码并以状态码 1 退出：

```json
{
  "ok": "false",
  "schema_version": 1,
  "code": "unknown_framework",
  "error": "could not identify the CLI framework from the help output",
  "hint": "Supported frameworks: argparse, click, typer."
}
```

稳定的错误码共六个：`tool_not_found`、`no_help_output`、`unknown_framework`、
`subcommand_not_found`、`parse_failed`、`internal_error`。

### `helpui generate <tool> --out <dir> [--port 8000]`

扫描工具并写出一个自包含的 Web 项目目录。目标目录非空时会拒绝覆盖，
除非显式加 `--force`。

```console
helpui generate mytool --out webui
helpui generate mytool --out webui --port 9000 --host 0.0.0.0
helpui generate mytool --out webui --force
```

`--port` 和 `--host` 会写进生成项目的 `config.py` 与 `README.md`，
之后也可以直接用环境变量覆盖（见下文）。

### `helpui serve <dir> [--port 8000] [--host 127.0.0.1]`

启动生成好的项目。等价于在项目目录里执行 `python app.py`。

```console
helpui serve webui
helpui serve webui --port 9000 --host 0.0.0.0
```

### `helpui test <dir>`

对生成好的项目跑冒烟测试：加载 spec、导入应用、访问各页面、
**提交一次假任务**、确认结果与历史写入。输出 JSON：

```console
$ helpui test webui
{
  "ok": "true",
  "project_dir": "/abs/path/webui",
  "checks": [
    {
      "name": "project_exists",
      "ok": true,
      "detail": "/abs/path/webui contains app.py and spec.json"
    }
  ]
}
```

和 `scan` 一样，`ok` 是字符串 `"true"` / `"false"`，并且它**没有 `--json` 参数**——
与 `scan` 不同，JSON 是它唯一的输出格式。全部通过时退出码 0，否则 1；
目录不存在、不是目录、缺 `spec.json`、项目代码有语法错误、探测超时等情况
都会返回 `ok=false` 并附带可读的 `detail`，**不会抛异常**。

单个 check 失败不影响其余 check。共 13 项：

`project_exists`、`spec_loads`、`app_imports`、`app_boots_factory`、`app_boots`、
`index_responds`、`history_responds`、`api_spec_responds`、`static_assets`、
`form_renders`、`submit_job`、`history_recorded`、`api_run_roundtrip`。

---

## 生成的项目

生成结果是**纯源码**——生成器不会预先创建数据库文件，
`data/history.db` 在首次运行时才出现。

```console
python app.py                  # 默认 http://127.0.0.1:8000
python app.py --port 9000      # 换端口
python app.py --host 0.0.0.0   # 监听所有网卡
```

运行时只需四个依赖：`fastapi`、`jinja2`、`uvicorn`、`python-multipart`。
生成的 `README.md` 里也写了这些。

### 页面功能

* **首页**：工具名、描述、子命令列表。
* **表单页**：按 `CLIOption` 渲染控件，见下方的类型对照表。
* **运行**：提交后执行工具，通过 HTMX 轮询推送 stdout / stderr。
* **结果页**：自动识别 JSON、CSV 和纯文本，分别用表格或 `<pre>` 渲染。
  所有工具输出都会做 HTML 转义，**绝不当作标记渲染**。
* **历史页**：从 SQLite 读取，列出时间、参数、退出码、耗时，
  可查看完整记录、可下载。
* **取消**：直接杀掉正在运行的子进程。
* **超时**：默认 300 秒，可配置。

### 可配置项

用环境变量覆盖，不需要改生成出来的文件：

| 变量 | 默认值 | 含义 |
|---|---|---|
| `HELPUI_HOST` | `127.0.0.1` | 监听地址 |
| `HELPUI_PORT` | `8000` | 监听端口 |
| `HELPUI_TIMEOUT` | `300` | 超时秒数，到点杀进程 |
| `HELPUI_MAX_OUTPUT_BYTES` | `5242880` | 捕获输出上限 |
| `HELPUI_MAX_UPLOAD_BYTES` | `67108864` | 单个上传文件上限 |
| `HELPUI_MAX_UPLOAD_FILES` | `16` | 单次运行上传文件数上限 |
| `HELPUI_HISTORY_LIMIT` | `200` | 历史页显示条数 |

---

## 支持的框架

框架探测依据帮助输出的**结构特征**，支持三种：

| 框架 | 识别依据 | 说明 |
|---|---|---|
| **typer** | rich 表格面板（box-drawing 或 ASCII 两种渲染都认） | 最先检测，排在 argparse/click 之前 |
| **argparse** | 小写 `usage:`，加上小写的 `positional arguments:` / `optional arguments:` / `options:`，且**不含** click 的大写表头 | 处理折行的 usage、epilog、嵌套子命令、`{a,b}` 形式的 choice |
| **click** | 大写 `Usage:`，加上 `Options:` / `Commands:` / `Arguments:` 中至少一个 | 处理 `[required]`、`[default: x]`、`[env var: X]`、`[a\|b\|c]` 形式的 choice、`multiple=True` |

检测按上表顺序进行。typer 排第一是因为 rich 面板若按别的方式解析会被误读；
argparse 那条明确要求**不出现** click 的大写表头，所以同时带两种特征的页面不会被误判。
完全没有表头、只有一行裸 usage 的，会以 `confidence: low` 归到 argparse 或 click，
而不是直接失败。其余的——man page 风格的帮助、docopt、fire、手写解析器——
一律报 `unknown_framework` 并以退出码 1 结束。
HelpUI 有意不做通用帮助解析（见 [SPEC.md](SPEC.md) §4.5）。

参数类型到控件的映射（`CLIOption.type` 在构造时就对照一个封闭词表校验，
识别不了的会降级为 `str`）：

| 类型 | 控件 |
|---|---|
| `str` | 文本框（同时也是未知类型的兜底） |
| `int`、`float` | 数字输入框 |
| `bool` | 复选框 |
| `choice` | 下拉选择 |
| `file` | 文件上传 |
| `dir` | 带路径提示的文本框 |
| `password` | 密码框 |

`multiple` 是 `CLIOption` 上**独立的布尔字段**，不属于那八种类型之一——
一个参数可以同时是 `type: "str"` 和 `multiple: true`，渲染成可重复填写的多值输入。
`value_name`（即 metavar）用作输入框的占位提示，`aliases` 列出全部写法
（如 `["-o", "--output"]`）。

---

## 已知限制

以下都是有意为之并已记录的取舍，完整理由见 [SPEC.md](SPEC.md)。

1. **argparse 的数字类型在帮助里看不出来。** `type=int` 渲染出来是
   `--retries RETRIES`，没有任何文本线索，所以 HelpUI 报 `str`。
   想让它识别成 `int`，可以显式写 `metavar="INT"`，或用
   `ArgumentDefaultsHelpFormatter`。（SPEC §4.6.1）
2. **`--token` 不会被当成密码框。** 只有 *password* / *passphrase* 字样
   （或 `PASSWORD`/`SECRET` 这类 metavar）才会选中密码控件；
   猜错会把用户需要核对的内容藏起来。所以叫 `--token` 的凭据渲染成普通文本框，
   而三个 fixture 里的 `--password` **确实**都是密码框。（SPEC §4.6.2）
3. **没装 rich 的 typer 会被标成 `click`。** 它的输出与 click 逐字节相同，
   解析结果依然正确，只是 `parser_kind` 这个标签不同。（SPEC §4.2）
4. **`PATH` 这类 metavar 本身有歧义。** HelpUI 读帮助文本的措辞来决定是文件还是目录，
   默认按文件处理。（SPEC §4.6.3）
5. **`--subcommand "a b"` 会按空白拆成两个 argv 项。** 全程不经过 shell，
   所以**不会执行**任何东西，但一个看起来像 shell 片段的取值会变成若干个字面参数。（SPEC §5.2）
6. **彩色帮助输出**会被强制关掉（`NO_COLOR=1`、`TERM=dumb`、`COLUMNS=100`）；
   如果工具无视这些设置，输出里可能带 ANSI 转义序列。（SPEC §4.6.5）
7. **参数的帮助文本原样展示**，包括 click 附加的 `[default: x]` 注解。（SPEC §4.6.4）
8. **argparse 的必填参数会被报成非必填。** argparse 是用 *usage 行*表达必填的
   （`--token TOKEN` 不带方括号），而 `argparse_parser` 只在参数自己的帮助单元格里
   找字面单词 "required"——那个位置永远不会有，所以 argparse 参数的 `required` 恒为 `False`。
   click 会在帮助单元格里追加 `[required]`，解析正确；typer 的 rich 选项行没有这个标注，
   所以 typer 的选项同样是 `False`（只有 typer 用 `*` 标记的*位置参数*能识别出必填）。
   这是**一个可修的解析器缺陷，不是框架行为差异**，这里如实记录，没有绕过去。（SPEC §4.6.7）

---

## 开发

```console
pip install -e ".[dev]"

pytest -q              # 全部测试
ruff check .           # 代码检查
mypy helpui            # 类型检查（strict 模式）
```

这三项就是 CI 检查的内容。测试有几百个，这里不写死数字——
要当前数量请跑 `pytest --collect-only -q`。

### 测试是怎么组织的

分三层，每一层都驱动**真实产物**，而不是对着手写的预期值断言：

* **单元/解析测试**（`test_model`、`test_scanner`、`test_detector`、
  `test_parsers_*`、`test_scan_cli`）把 `tests/fixtures/` 里**真实的** fixture 脚本
  通过扫描器跑一遍。手写的帮助字符串会与 argparse/click/typer 的实际输出脱节；
  驱动真实 fixture 在第一阶段就抓出了五个真实解析 bug，
  包括折行的 usage、argparse 的 epilog、以及 typer 的面板布局。
* **`test_generator.py`** 把生成结果与 golden 文件逐字节对比，
  并断言生成的项目能被导入和运行。
* **`test_e2e.py`** 通过 `helpui.selftest`（与 `helpui test` 同一条代码路径）
  为每个 fixture 启动生成的项目，提交一个真实任务，再把相关页面和 API 走一遍。
  模板层面的缺陷就是靠这层抓到的：某个页面可能只在「已经存在一条带参数的运行记录」时
  才出问题（`templates/record.html` 就曾如此），而单纯的 `GET /` 冒烟测试照样返回 `200`。

fixture 是**能真正运行的工具**，不只是帮助文本，所以同一批脚本同时服务于
解析、执行器和端到端测试：

```console
python tests/fixtures/sample_argparse.py convert --help
python tests/fixtures/sample_click.py convert --help
python tests/fixtures/sample_typer.py convert --help
```

fixture 子进程固定使用 `COLUMNS=100`、`LINES=50`、`TERM=dumb`、`NO_COLOR=1`
和 `PYTHONIOENCODING=utf-8`，这样帮助文本不会因为终端宽度不同而折行不一致
（80 列的 CI 机器和 120 列的开发机本来会有差异）。

### 目录结构

```
helpui/
  pyproject.toml
  SPEC.md                  # 每一个自行决策及其理由
  README.md  CONTRIBUTING.md  CHANGELOG.md  LICENSE
  helpui/
    cli.py                 # helpui 命令入口
    model.py               # CLIOption / CLICommand / CLISpec
    scanner.py             # 运行 <tool> --help（shell=False，参数以列表传入）
    scanner_service.py     # scan_tool()：探测 + 解析 + 展开子命令
    generator.py           # 写出生成项目（helpui_app/ 与模板、静态资源）
    selftest.py            # run_project_tests()：支撑 `helpui test`
    parsers/
      base.py              # 解析器接口与文本工具
      argparse_parser.py
      click_parser.py
      typer_parser.py      # rich 面板归一化 + 复用 click 解析
      detector.py          # 框架探测
    runtime/
      server.py            # serve_project()：`helpui serve` 的入口
    templates/             # 8 个 Jinja2 模板，会被复制进生成项目
      _error.html          # HTMX 错误替换用的自包含片段
    static/                # htmx.min.js 与 style.css，内置进生成的应用
  scripts/                 # 一次性的排查探针，作为修复证据保留（已排除出 lint）
                           #   清单与用法见 scripts/README.md
  tests/
    conftest.py            # 真实 fixture 测试脚手架
    fixtures/sample_{argparse,click,typer}.py
    test_model.py          test_scanner.py       test_detector.py
    test_parsers_argparse.py  test_parsers_click.py  test_parsers_typer.py
    test_scan_cli.py       test_generator.py     test_e2e.py
```

**生成出来的项目**（`helpui generate` 会写 24 个文件）长这样：

```
<out>/
  app.py                   # 入口：python app.py
  spec.json                # 扫描得到的 CLISpec
  README.md                # 这个项目怎么跑
  helpui_app/              # 10 个模块：app、config、executor、history、
                           #   paths、rendering、security、server、spec、__init__
  templates/               # 8 个模板（含 _error.html）
  static/                  # htmx.min.js、style.css
  data/                    # .gitkeep；history.db 在运行时出现在这里
```

---

## 设计原则

* **依赖尽量少。** `scan` 只用标准库；FastAPI、Jinja2、uvicorn 只有生成的应用才需要。
* **永远不经过 shell。** 子进程参数一律以列表传入并使用 `shell=False`，
  测试会断言注入形状的输入保持字面量。
* **干净地失败。** 每条失败路径都给出稳定的错误码而不是 traceback——
  `unknown_framework` 是一等公民结果，不是事后补的兜底。
* **能用一半的表单也比崩掉强。** 解析器遇到畸形帮助不会抛异常，
  能提取多少就提取多少，剩下的忽略。
* **生成的项目是自足的。** 模板和静态资源原样复制，htmx 本地内置，
  生成物不 import HelpUI。

## 参与贡献

安装、检查命令、测试约定，以及「HelpUI 本体」与「它生成的代码」这两半之间的关系，
见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可

MIT，见 [LICENSE](LICENSE)。
