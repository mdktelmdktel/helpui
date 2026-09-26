# HelpUI —— 规格说明与设计决策

本文档记录 **HelpUI 自行做出的每一处取舍**，用于回应需求中的含糊之处，这是项目任务书的要求。
判断「它为什么是这个行为」时，以本文档为准。

状态图例：**[D] 已决策**（已实现并有测试）、**[L] 限制**（接受、已记录、不修）、
**[O] 待定**（推迟到后续阶段）。

---

## 1. 范围

### 1.1 HelpUI 是什么

给定一个非交互式的命令行工具，HelpUI 运行 `<tool> --help`，把输出解析成结构化的
`CLISpec`，再生成一个独立的 FastAPI + HTMX Web 应用：把这个 spec 渲染成表单、
执行工具、展示结果。

### 1.2 HelpUI 不是什么（v1）

* 不做交互式 TUI/REPL 包装。
* 不解析任意格式的 help（见 §4.5）。
* 不做认证、多租户或 RBAC。
* 不做 Playwright／浏览器自动化测试。
* 不做远程或分布式执行。
* 不引入前端框架，也没有前端构建步骤。

---

## 2. 技术决策

### [D] 2.1 用标准库，不用 ORM

任务书允许「SQLite（`sqlite3` 或 SQLModel），偏好更少的依赖」。
我们选择 **标准库的 `sqlite3`**。理由：历史记录表只是几个字段，且只由单个进程写入；
在这个规模上，ORM 只会多一个依赖和一套迁移方案，没有收益。`sqlite3` 还能让生成出来的
项目以最小依赖集运行。

### [D] 2.2 spec 模型用 `dataclasses`，不用 Pydantic

`CLISpec`／`CLICommand`／`CLIOption` 是普通的 `@dataclass`，配手写的
`to_dict`／`from_dict`。Pydantic 能提供更多校验，但唯一需要校验的地方是 FastAPI，
而它并不需要校验 *spec* 模型——spec 由我们自己的解析器产出，不是用户输入的。
用 dataclasses 可以让 `scan` 这条代码路径完全不引入硬依赖，
所以只装了标准库也能跑 `helpui scan`。

### [D] 2.3 `type` 是 `str`，不是 `Enum`

`CLIOption.type` 是普通字符串，取值限定在
`str|int|float|bool|choice|file|dir|password` 这个词表内。理由：

1. `json.dumps` 直接可用，不需要自定义 encoder。
2. 将来某个解析器给出无法识别的类型时，降级成 `str`（文本框）
   而不是在反序列化时抛异常——能部分可用的表单胜过崩溃。
3. `spec.json` 保持人类可读且向前兼容。

`CLIOption.__post_init__` 通过把未知取值改写为 `str` 来强制这个词表。

### [D] 2.4 `default` 是 `str | None`，不是带类型的值

help 文本是唯一的真相来源，而它把默认值打印成文本（`[default: out.txt]`）。
把 `"3"` 解析成整数 `3`，等于凭空发明了命令行工具从未在 help 里声明过的类型信息。
运行时（阶段 3）在提交时才转换成声明的类型，此时转换失败会成为用户可见的校验错误，
而不是一次静默的误解析。

---

## 3. 数据模型

### [D] 3.1 字段名冻结

要求的模型按原样实现。阶段 1 额外加了两个 **增量** 字段，因为表单渲染需要它们，
而以后再补会改变 `spec.json`：

| 字段 | 类型 | 原因 |
|---|---|---|
| `CLIOption.value_name` | `str` | help 里写的 metavar（`FILE`），用作输入框的 placeholder，让用户看到工具期望什么。 |
| `CLIOption.aliases` | `list[str]` | 所有拼写形式（`["-o", "--output"]`）。构建 argv 列表（阶段 3）和界面上同时显示两种形式都需要它。没有它，`primary_flag` 就无法优先选择长选项。 |

两者都有默认值且可选，所以旧版本写出的 `spec.json` 仍能加载。

### [D] 3.2 `CLICommand.subcommands` 是扁平的名称列表

嵌套子命令（typer 的 `admin reset`）表示为：

* 根命令在 `subcommands` 里列出 `["admin"]`，以及
* 一个 **独立的顶层 `CLICommand`**，名为 `"admin reset"`。

理由：递归的 `CLICommand` 树会让 `spec.json` 和生成的路由都变成递归，
两边都会变复杂。带点的扁平列表保持「一个命令 = 一个表单 = 一个路由」，
把层级关系保留在名字里，并且渲染成一个简单的链接列表。

### [D] 3.3 位置参数的 `name` 就是 metavar

位置参数没有 `--flag`。`name` 保存 argparse／click 打印出来的 metavar 原样
（`input_file`、`INPUT_FILE`），`dest` 保存 Python 安全的写法。
运行时在构建 argv 时统一大小写。

### [D] 3.4 位置参数的 `required`

argparse 和 click 的位置参数默认必填，除非另行声明，所以当它们在 usage 行里
不被 `[...]` 括起时，HelpUI 标记为 `required=True`。

---

## 4. 解析

### [D] 4.1 探测基于特征且保守，只看 help 文本

`detect_parser` 按以下顺序检查捕获到的 help 文本：

1. **typer** —— 存在 rich 表格面板。
2. **argparse** —— 小写 `usage:` *并且* 至少含
   `positional arguments:` / `optional arguments:` / `options:` 之一，
   且不含 click 的大写表头。
3. **click** —— 大写 `Usage:` 加上 `Options:` / `Commands:` / `Arguments:`
   至少之一。
4. **unknown** —— 其余情况。

猜错会生成一个坏表单，而 `unknown` 会给出干净、可操作的错误，
所以探测器宁可返回 `unknown`，也不做没把握的猜测。
`confidence` 报告为 `high`（结构匹配）或 `low`（启发式），
并由 `helpui scan` 展示出来以便排查。

### [D] 4.2 typer 探测需要两种渲染形式

typer 用 rich 绘制 help，rich 会用：

* **制表符边框**面板（`┌─ Options ────┐`），在有颜色时；
* **ASCII** 面板（`+- Options ----+`），否则。

已在 Windows（GBK 控制台）上用 typer 0.27 + rich 验证。两种形式都会被匹配。
**没装 rich 的 typer 与 click 在文本上完全相同**，所以：

* 探测器把它标记为 `click`，并且
* `_source_imports_typer` 会嗅探目标 `.py` 里的 `import typer`，但**仅作为平局判定**——
  只在文本判断已经得出「形如 click」之后才应用，且只对已存在的 `.py` 文件生效。

**[L]** 因此，没装 rich 的编译型／二进制 typer 工具会被报告为 `click`。
这没有危害，因为在这一层上 typer *就是* click：解析是正确的，只有 `parser_kind`
这个标签不同。

### [D] 4.3 typer 复用 click 解析器

`TyperParser` 继承 `ClickParser`，只增加一步归一化，把面板转换成 click 形状的文本
（`typer_parser.normalise_rich_help`）。这正是任务书要求的
（「typer 复用 click 的解析逻辑，只在探测上有差异」），
而且这意味着修好 click 就自动修好了 typer。

归一化必须在提取描述之前运行，因为对原始面板文本调用 `first_paragraph`
会返回面板的行。因此 `TyperParser.parse` 会从归一化后的文本重新推导 `spec.description`。

### [D] 4.4 显式处理的框架特有怪癖

| 观察（已用真实 fixture 输出验证） | 处理方式 |
|---|---|
| argparse 把 `usage:` 行折行到缩进的续行上 | `first_paragraph` 和 `_usage_description` 跳过缩进续行，否则描述会变成 `"COMMAND ..."` |
| argparse 在最后一节之后打印不缩进的 **epilog** | `split_sections` 在「空行之后的不缩进行」处结束一节，这样 epilog 不会被当成幽灵选项解析 |
| argparse 把子命令嵌套渲染在 `COMMAND` metavar 之下 | `_subcommands_from_positionals` 从缩进更深的行里把它们找回来 |
| argparse 的 `{json,csv}` 选项 metavar 出现在 `positional arguments:` 里 | 当作可选项的位置参数，**而不是**子命令名 |
| argparse 默认的 formatter 对 `type=int, default=3` **不**打印 `(default: 3)` | `default` 为 `None`。HelpUI 不发明默认值。**[L]** 见 §4.6 |
| click 用 **两个及以上空格** 分隔选项与 help | 行按第一个连续空格处分隔，所以 `-v, --verbose    Enable verbose logging` 不会被读成 `metavar="Enable verbose logging"` |
| click 在 help 单元格末尾追加 `[default: x]`、`[required]`、`[env var: X]`、`[a\|b\|c]` | 解析为注解；choices 来自方括号形式 |
| typer 把 *选项单元格本身* 拆到多列（`--output  -o  <str>`） | `_split_row` 扫描开头的单元格，只要它们看起来像选项或类型，于是两个别名和类型都能保留 |
| typer 用前导 `*` 标记必填参数 | 剥离并记为 `required=True` |
| typer 在 usage 行里渲染 `[OPTIONS]`、`[ARGS]...`、`COMMAND` | 识别为结构占位符，绝不当作可填参数 |
| 子命令的 usage 行会回显命令路径（`admin reset`） | 对已知命令名做剥离，避免出现幽灵位置参数 |

### [D] 4.5 只支持三种框架

`unknown` 是 **一等结果**，不是事后补上的错误分支。按任务书「不解析任意格式的 help」，
没有任何代码去尝试通用的／man page 式解析。`NAME`／`SYNOPSIS` 风格的页面会得到
`unknown_framework`。

### [L] 4.6 已知的解析限制

1. **argparse 的数字类型不可见。** `add_argument("--retries", type=int)`
   在 help 里只产生 `--retries RETRIES`——没有任何文本信号表明它是整数。
   HelpUI 报告为 `str`。*缓解措施：* 运行时仅当目标工具接受该值时才接受它，
   产生的错误会显示在日志面板里。使用 `argparse.ArgumentDefaultsHelpFormatter`
   或显式 `metavar="INT"` 的工具 *会* 被正确识别类型。
2. **`--token` 不是密码字段。** 类型嗅探是保守的：只有 *password* / *passphrase*
   这两个词（出现在散文中）或 metavar `PASSWORD`／`SECRET`／`PASSPHRASE`
   才会选中密码控件。名为 `--token` 的凭据渲染成普通文本框，
   因为猜错会 *遮住* 用户需要核对的数据。

   **阶段 2 已修复。** 上面的规则是对的，但 click／typer 的代码 *并非* 如此：
   metavar 表（`_METAVAR_TYPES`，把 `TEXT` 映射为 `str`）和 `_type_from_angle()`
   （对 typer 的 `<str>` 列返回 `"str"`）都在散文嗅探 *之前* 被查询，
   而两者都返回真值，于是 `or` 链 **在两处** 短路，`infer_type` 从未被执行。
   click 和 typer 把 `str` 选项的 metavar 写成 `TEXT`／`<str>`，
   所以它们的 `--password` 被报告为 `str` 并渲染成 **明文**——密码直接显示出来。
   修复让散文嗅探在密码词表上优先。修复后已验证：三个 fixture
   （`sample_argparse.py`、`sample_click.py`、`sample_typer.py`）的 `--password`
   都是 `password`，而 `--token` 仍为 `str`。**[D]**
3. **`--config` 与 `--config-file`。** metavar 为 `PATH` 时有歧义；
   由散文决定（`directory|folder` → `dir`，否则 `file`）。
4. **选项描述不做重排。** `help` 保留原始散文，包括 click 的 `[default: x]` 注解，
   这样用户能确切看到工具说了什么。
5. **多字节／带颜色的 help。** 每次 help 调用都强制设置 `NO_COLOR=1`、`TERM=dumb`
   和 `COLUMNS=100`，以保证输出稳定且无 ANSI 转义。
   忽略这些设置的工具可能在界面上产生颜色转义码。
6. **工具 *字符串* 里的 Windows 路径含空格。** `--subcommand` 这类字符串的
   `shlex` 切分遵循平台规则；路径含空格时请传真实的文件路径
   （文件路径是直接处理的，不经过切分）。
7. **argparse 的必填选项被报告为可选。** 这是 **可修的解析器缺陷，不是框架差异。**
   argparse 用 *usage 行* 以结构化方式表达「必填」：必填选项不带方括号
   （`--token TOKEN`），可选选项带方括号（`[--tag TAG]`）。
   它从不把 "required" 这个词写进该选项自己的 help 单元格。而 `argparse_parser`
   是拿 `required=bool(re.search(r"\brequired\b(?!:)", help_text))`
   去匹配 **那个 help 单元格**，所以永远匹配不上，每个 argparse 选项都返回
   `required=False`。click 和 typer 则会追加一个字面量 `[required]` 注解，
   `click_parser.infer_required` 能正确解析。
   在三个 fixture 的 `--token`（三者都声明为必填）上实测：

   | 框架 | help 里 "required" 出现次数 | `--token required` |
   |---|---|---|
   | argparse | **0** | `False` ← 缺陷 |
   | click | 1 | `True` |
   | typer | 1 | `False`（选项行没有 `[required]`） |

   修法是（对 argparse）改为读取 usage 行的方括号，而不是在 help 单元格里
   grep 这个词。记录于此，是为了让这个行为是「有意保留」而非「意外如此」。**[L]**

### [D] 4.7 解析器遇到畸形输入绝不抛异常

解析器抛异常会把一个部分可用的工具变成硬失败。每个解析器返回它能理解的部分，
忽略其余部分。`scanner_service` 仍然做了防御性包裹（`except Exception`），
所以解析器自身的 bug 会表现为 `parse_failed`，而不是一个 traceback。

---

## 5. 扫描器

### [D] 5.1 处处 `shell=False`，argv 用列表

任务书强制要求，并由测试保障：`run_help` 构建列表并传 `shell=False`。
测试断言 `; rm -rf /`、`$(whoami)` 和 `` `id` `` **原样、未展开** 地到达子进程。

### [D] 5.2 `subcommand` 按空白切分成多个 argv 项

`--subcommand "admin reset"` 是一个 *路径*，所以它变成两个 argv 项。
后果已被记录并测试：`--subcommand "; rm -rf /"` 变成五个字面量参数，而不是一个。
这里不涉及 shell，所以这是语义说明，不是漏洞。

### [D] 5.3 help 调用不会挂起

每次 help 调用：

* 都有超时（默认 20 秒）；
* 强制 `NO_COLOR`／`TERM=dumb`／`COLUMNS` 以获得稳定输出；
* 设置 `stdin=DEVNULL`，这样忽略 `--help` 转而读 stdin 的工具会立即退出，
  而不是把扫描卡住。

### [D] 5.4 子命令展开有深度上限且容错

`MAX_SUBCOMMAND_DEPTH = 2`（足够覆盖 `admin reset`）。取不到 help 的子命令会作为
**警告** 记录在 `ScanReport` 上并打印到标准错误；spec 的其余部分仍然可用。

### [D] 5.5 `--help` 非零退出即失败

如果工具不认识某个子命令，click 会打印 *命令组* 的 usage 并以非零退出。
把那一页当作子命令的 spec，会静默地为一个错误的命令生成表单，
所以 `_scan_one_subcommand` 用 `subcommand_not_found` 拒绝它。

---

## 6. CLI 契约

### [D] 6.1 JSON 走标准输出，诊断走标准错误

`scan` 和 `test` 在标准输出上只输出一个 JSON 文档，所以
`helpui scan tool --json | jq` 永远安全。警告和人类可读的错误走标准错误。
有测试断言标准输出上没有多余内容。

### [D] 6.2 带版本的信封，以及 `ok` 是字符串

`scan --json` 打印的是一个 *信封*，不是裸的 `CLISpec`：

```json
{
  "ok": "true",
  "schema_version": 1,
  "spec": { ... CLISpec ... },
  "detection": {"parser_kind": "...", "confidence": "...", "reason": "..."},
  "warnings": []
}
```

任务书要求「稳定的、机器可读的字段」。spec 本身在 `spec` 下保持要求的结构原样，
信封则承载探测置信度和警告——这些是 `generate` 需要的信息，
也是排查扫描问题的用户想看到的信息。`schema_version` 让将来的变更可以被检测到，
而不是被静默误读。

**`ok` 是字符串 `"true"`／`"false"`，不是 JSON 布尔值**——这是为了对齐任务书里
给 `test` 的字面示例 `{"ok": true/false, "checks": [...]}`。`scan` 采用同一约定，
使两条命令保持一致。调用方可以安全地判断 `payload["ok"] == "false"`。
**[O]** 如果这对使用者造成了不便，阶段 5 可以在两条命令里都改成真正的布尔值；
把决策记录在这里，是为了让这个改动是有意为之。

### [D] 6.3 错误码是封闭、稳定的集合

| 错误码 | 含义 |
|---|---|
| `tool_not_found` | 工具不存在／不在 PATH 上 |
| `no_help_output` | 运行工具失败，或它没有打印任何可用内容 |
| `unknown_framework` | 抓到了 help，但不属于 argparse/click/typer |
| `subcommand_not_found` | 指定的子命令不存在 |
| `parse_failed` | 某个解析器抛异常（防御性；不应发生） |
| `internal_error` | 任何意外情况，保证管道里永远看不到 traceback |

### [D] 6.4 退出码

`0` 成功，`1` 操作失败，`2` 命令行用法错误（argparse 的默认行为）。

### [D] 6.5 后续子命令的占位实现（历史记录，阶段 1）

`generate`、`serve` 和 `test` 从阶段 1 起就声明在 `--help` 里，以便提前固定 CLI 契约。
阶段 1 交付时（commit `7870d9e`），它们的模块带有正确的类型签名但会抛
`NotImplementedError`；CLI 捕获随之而来的 `ImportError` 路径并输出
`error: ... is not implemented yet (planned for phase N)`，退出码 1。

**四条命令现在都已实现**——`scan`、`generate`、`serve` 和 `test`。
任何子命令都不再走占位路径，`--help` 里也没有任何标注为 "not implemented yet" 的项。
保留这段说明只是为了记录历史：正是这个机制让阶段 1 能在不交付假行为的前提下
公布一个稳定的 CLI 契约。

### [D] 6.6 `helpui test` 的输出契约

`helpui test <dir>` **没有 `--json` 开关**——与 `scan` 不同，JSON 是它唯一的输出格式，
所以传 `--json` 属于用法错误（退出码 `2`）。它在标准输出上打印一个信封，
且只有当所有检查都通过时才退出 `0`：

```json
{
  "ok": "true",
  "project_dir": "/abs/path/to/project",
  "checks": [{"name": "...", "ok": true, "detail": "..."}]
}
```

`ok` 遵循与 `scan` 相同的字符串约定（§6.2）。共 **13** 项检查，
按代价从低到高排序，以便早期失败能短路掉昂贵的检查：

| 检查项 | 断言内容 |
|---|---|
| `project_exists` | 目录里有 `app.py` 和 `spec.json` |
| `spec_loads` | `spec.json` 能往返成 `CLISpec` |
| `app_imports` | 在子进程里能导入 `helpui_app.app` |
| `app_boots_factory` | `create_app()` 返回一个应用对象 |
| `app_boots` | 应用能启动（`TestClient`） |
| `index_responds` | `GET /` → 200 |
| `history_responds` | `GET /history` → 200 |
| `api_spec_responds` | `GET /api/spec` → 200 |
| `static_assets` | `GET /static/style.css` → 200 |
| `form_renders` | 每个命令的表单页 → 200 |
| `submit_job` | 真实提交一次运行并记录一行 |
| `history_recorded` | 该次运行在历史里可见 |
| `api_run_roundtrip` | 该次运行可通过 API 读回 |

---

## 7. 测试决策

### [D] 7.1 测试驱动真实 fixture，而不是手写的 help 字符串

每个解析器测试都把真实的 `tests/fixtures/sample_*.py` 通过扫描器跑一遍
（`tests/conftest.help_text`）。手写的 help 字符串会与库真实输出的内容产生偏移——
这个做法在阶段 1 抓出了五个真实 bug，包括：

* argparse 折行的 `usage:` 行被解析成描述；
* argparse 的 epilog 被解析成一个幽灵选项；
* 子命令 usage 行里的 `{json,csv,tsv}` 被展开成假的子命令
  （`convert json`、`convert csv`，……）；
* typer 的制表符面板丢失了分节标题；
* typer 的 `--output -o` 丢失了短选项别名。

### [D] 7.2 fixture 子进程强制固定宽度

每个 fixture 子进程都设置 `COLUMNS=100`、`LINES=50`、`TERM=dumb`、`NO_COLOR=1`。
否则 argparse／click／typer 会把 usage 文本按终端宽度折行，
解析结果（以及阶段 2 的 golden 文件）在 80 列的 CI 机器和 120 列的开发者终端上
就会不同。

### [D] 7.3 fixture 是可运行的工具，不只是 help 文本

fixture 接受真实参数并做真实（无害）的工作，
所以同一批脚本同时服务于解析器测试（§7.1）、生成器 golden 测试，
以及 `tests/test_e2e.py` 里的端到端测试。

### [D] 7.4 端到端测试跑的是完整项目

`test_e2e.py` 为每个 fixture 生成一个真实项目，通过与 `helpui test` 相同的
`helpui.selftest` 代码路径启动它（§6.6），提交一次任务，
然后走一遍产生的页面和 API。生成器或模板的缺陷只有在这个层级才看得见：
`templates/record.html` 曾经把 dict 迭代写成
`{% for key, value in record.params %}`（Jinja2 迭代 dict 时只产出键，
所以解包会抛 `ValueError: too many values to unpack`），
这使运行的 **详情** 页返回 `500`，而 `GET /` 和 `GET /history` 仍然正常。
端到端测试现在会在一份带参数的运行存在 *之后* 抓取详情页，
所以这一类缺陷会大声失败。

---

## 8. 阶段 1 建立的跨阶段契约

现在记录这些，是因为后续阶段依赖它们：

1. `CLISpec.to_dict()/from_dict()` 就是 `spec.json` 的格式。往返相等性有测试覆盖，
   未知键会被忽略以保持向前兼容。
2. 每个解析器都实现 `parse()` 和 `parse_subcommand()`。子命令页与根页用同一套代码解析；
   唯一的区别是去掉子命令的痕迹（回显的命令名、`COMMAND` metavar）。
3. `ScanFailure.code` 的取值（§6.3）是 `scan` 的错误词表。
4. `GenerationResult`、`SelfTestResult`／`Check` 和 `serve_project` 都以最终签名声明，
   所以阶段 2／3／5 是去实现，而不是重新设计。

---

## 9. 生成器决策（阶段 2）

### [D] 9.1 生成的项目是一个包，不是散落的模块

生成器写出 `helpui_app/`（10 个模块）加上一个薄薄的根级 `app.py`，
而不是一堆平级的 `.py` 文件。两个原因：

1. 两个入口点都不需要 `sys.path` 小把戏：`python app.py` 和 `pytest`
   都能按名字导入 `helpui_app.*`，因为这个包就在入口点旁边。
2. 用单一的通用包名，可以让生成的模块不与 *被包装工具* 自己的模块撞名。
   被包装的工具是任意的；像 `app.py` 或 `executor.py` 这样放在顶层的通用名，
   恰恰是用户自己的项目最可能使用的名字，撞名会静默遮蔽掉其中一方。

### [D] 9.2 `history.db` 在运行时创建，生成器绝不创建它

`generate_project()` 只写源码文件（`app.py`、`spec.json`、`helpui_app/`、
`templates/`、`static/`、`README.md`）加上一个带 `.gitkeep` 占位符的 `data/`。
它 **不** 创建 SQLite 文件。让生成的目录树等于「一个人会提交的文件集合」，意味着：

* 输出是确定且可 diff 的——否则 golden 比对就得处理一个字节内容取决于创建时间的
  二进制产物；
* `--force` 重新生成时没有任何陈旧数据库需要考虑；
* 应用在首次使用时才惰性创建该文件，所以生成后从未运行过的项目不会留下数据库。

### [D] 9.3 生成的文本一律以 LF 结尾

所有生成的文本都经过 `_write_text_lf()`。它固定用 `\n` 写字节，
而不是用 `Path.write_text()`。在 Windows 上，`write_text` 会把 `\n`
转换成 `os.linesep`（`\r\n`），于是 *同样的* 生成器输入在 Windows 和 Linux 上
会产生不同的字节。这会同时破坏两件事：golden 文件比对（它只存一种规范形式）
以及任何内容哈希。强制 LF 让输出与平台无关，
而生成的项目在 Windows 上仍是合法的 Python，因为 Python 接受源码里的 LF。

### [D] 9.4 argv 必须包含子命令路径

`Command.argv_path()` 返回子命令名——例如 `["convert"]` 或 `["admin", "reset"]`——
而 `build_argv(tool, command_path, values)` 把它们拼在选项 *之前*：

```
argv = [*tool, *command_path, ...options and positionals...]
```

缺少子命令名时，click 和 argparse 的子解析器会用 "no such option" 拒绝 **每一个**
选项，因为这些选项声明在子命令上，而不是根解析器上。
因此生成的项目把命令路径视为调用的必需组成部分，而不是元数据。

### [D] 9.5 `build_argv` 是三段式签名，且从不拼接字符串

```python
def build_argv(
    tool: list[str],          # argv prefix: [python, /path/to/tool.py] or ["mytool"]
    command_path: list[str],  # subcommand name(s); [] when the tool has none
    values: list[tuple[str | None, tuple[str, ...]]],  # (flag, values); flag=None => positional
) -> list[str]: ...
```

把 `tool` 与 `command_path` 分开呼应了 §9.4：
它们在概念上是不同的东西（要运行什么 vs. 要运行哪个子命令），
而且子命令是由命令定义提供的，不是由表单提供的。
`values` 把一个 flag 与它的值配对，这样一个选项就能贡献多个 argv 项
（一个 `multiple=True` 的选项，或每个值一个 flag），
而 `flag=None` 标记位置参数。

每一项都是单独追加的——这个函数里没有任何字符串拼接。
正是这一点让 §5.1 的无 shell 保证一路贯穿到生成的应用里：
交给 `subprocess` 的 argv 是一串字面量参数，
所以任何用户提供的值都无法被重新解释成 shell 语法。

### [D] 9.6 `type='file'` 字段只走 multipart

`CLIOption.type` 为 `file` 的字段——无论是选项还是位置参数——
**只** 从 multipart 文件上传中取值。该字段收到纯文本值时不会被识别，
**等同于未提供**；若该字段必填，提交会以 `<dest> is required` 失败。

因此，应当接受手输路径的表单字段必须声明为 `str`（文本框），而不是 `file`。
这是「`file` 的含义是用户在浏览器里选中一个文件并上传其内容／路径」，
而不是「用户输入一个路径」所导致的刻意结果。

### [D] 9.7 阻塞执行必须走线程池

`run_command` 通过 `run_in_threadpool` 调用阻塞的 `execute()`，并且必须保持如此。
FastAPI 把 `async def` 路由派发到事件循环线程上，**不会** 把它们移到线程池；
在 `async def` 路由里直接调用阻塞的 `subprocess` 等待，
会在子进程运行的整个期间卡住整个服务器——不会有任何其他请求被服务，
包括本应用来观察这次运行的实时日志和取消端点。

### [L] 9.8 `HELPUI_TIMEOUT=0` 或负值行为未定义

每次运行的超时从 `HELPUI_TIMEOUT` 读取。值为 `0` 或负数没有定义的含义——
它既不是「不超时」，也不是「立即超时」。
未设置（或无法解析）时回落到文档所述的默认值。
记录于此，是为了说明「缺少校验」是有意为之；
将来的版本应当要么拒绝这些值，要么定义它们。

---

## 10. 方法论

### [D] 10.1 探针的 *时机* 比探针的 *数量* 更重要

一个阶段 2 的具体教训，保留下来是因为它耗费了真实时间。

一次取消／超时的排查用 **四个并发探针** 测量取消路径，读到一个可疑的约 4 毫秒延迟。
这个数字看起来排除了「事件循环被阻塞」的可能，于是该假设被丢弃了。但它其实是对的。
这些探针是在阻塞调用 *开始之前* 启动的，所以它们测量的是一个空闲的循环，
而不是一个被卡住的循环：正确的解读是，在阻塞等待 *期间* 发出的探针，
其耗时应该与等待时间相当。

**通用规则：** 对于并发或阻塞类缺陷，探针相对疑似阻塞窗口的 *发出时机*，
决定了它究竟能否看见这个缺陷。一个很快结束的探针，并不能证明缺陷不存在——
它可能只是错过了那个窗口。并发探针必须锚定在疑似卡顿的时刻
（从窗口内部发出，或对一个已知被阻塞的时段计时），
而未锚定的探针给出「很快」的结果，应当视为「不能说明问题」，而不是「足以排除」。

同样的纪律贯穿了整个项目：本文档里的每一条断言，都是对着真实命令输出来核实的，
而不是对着意图核实的；若干自信的断言（包括阶段 1 bug 的数量、
`scan` JSON 示例的形状，以及一个「argparse 会打印 `[required]`」的解释）
在实测之前都是错的。**先探测，再下笔。**
