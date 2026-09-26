# 变更日志

本项目所有值得记录的变更都写在这里。

格式遵循 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/spec/v2.0.0.html)。

## [未发布]

## [0.1.0] - 2026-09-26

本版本由 mdktelmdktel 与 ds老师 协作完成。

首个可用版本。计划的五个阶段全部完成：解析、生成、运行时、历史记录/安全，
以及自检命令。

### 新增

**解析（`helpui scan`）**
- `CLIOption` / `CLICommand` / `CLISpec` 数据模型，JSON 往返稳定。
- **argparse**、**click** 和 **typer** 的解析器。typer 复用 click 解析器，
  只对 rich 表格面板做归一化（制表符边框和 ASCII 两种渲染形式都支持）。
- 基于特征的框架探测，并报告置信度；
  不支持的 help 输出返回 `unknown`，而不是给出错误的猜测。
- 扫描器以 `shell=False`、argv 列表、超时、关闭 stdin，
  以及固定的 `NO_COLOR`／`TERM`／`COLUMNS` 环境运行 `<tool> --help`。
- 子命令展开（支持 `admin reset` 这类嵌套路径），有深度上限且容错。
- `helpui scan <tool> [--json] [--subcommand NAME]`，
  标准输出上是带版本的 JSON 信封，诊断走标准错误。
- 稳定的错误码：`tool_not_found`、`no_help_output`、
  `unknown_framework`、`subcommand_not_found`、`parse_failed`、
  `internal_error`。

**生成（`helpui generate`）**
- 写出一个独立的 Web 项目（`app.py`、`spec.json`、`helpui_app/`、
  `templates/`、`static/`、`data/`、`README.md`），用
  `python app.py` 即可运行。
- 模板和静态资源原样复制，所以生成的项目永远不需要安装 HelpUI。
- htmx 2.0.4 本地内置——不走 CDN，不需要构建，不需要 npm。

**运行时（在生成的项目里）**
- 表单渲染，每种选项类型对应一个控件：`str`、`int`、`float`、
  `bool`、`choice`、`file`、`dir`、`password`，外加多值输入。
- 以 `shell=False` 和 argv 列表执行子进程，流式输出 stdout 和
  stderr，可配置超时、输出上限，以及一个会杀掉进程的取消按钮。
- 结果渲染，能识别 JSON（数组或对象）、CSV 和纯文本，
  并且始终对工具输出做 HTML 转义。
- SQLite 历史记录，含每次运行的参数、退出码、耗时和下载。
- 安全层：以 `spec.json` 里的工具路径作为白名单，值通过类型转换
  和已解析的 choice 列表做校验，上传的文件名被缩减为安全的 basename，
  并在可用的平台上施加 POSIX 地址空间限制。

**自检（`helpui test`）**
- 每个生成的项目 13 项冒烟检查，报告
  `{"ok": "true"|"false", "checks": [...]}`。从不抛异常：项目缺失或损坏
  会以 `ok=false` 报告，并附一条说明性的检查项。

### 修复

通过让生成的项目真正端到端跑起来，发现并修掉了若干缺陷。
前九个是在开发初期引入的；第十个只在全新克隆时出现。

1. 生成的 `app.py` 引用了一个并不存在的 `config.spec`。
2. `quote_argv_entry` 被使用但从未导入，导致每个 `POST /run/*`
   都返回 500。
3. `_error.html` 片段被引用但并不存在，导致校验失败和取消请求返回 500。
4. `Path.write_text` 在 Windows 上把换行转换成 CRLF，使生成的字节
   依赖平台，并破坏了逐字节的 golden 比对。
5. **argv 遗漏了子命令名**，导致任何带子命令的工具都会拒绝每一个选项
   （`no such option`）。`build_argv` 现在接收命令路径。
6. 超时的运行被报告为 `failed`，因为被杀死子进程的非零退出码覆盖了状态。
   现在的状态优先级是 timeout > cancelled > failed > succeeded。
7. **click/typer 的 `--password` 退化成明文输入框**——类型推断的 `or` 链上
   有两处独立的短路——在渲染出的表单里泄露了密码。
8. 文件类型的位置参数会静默丢弃上传，于是用户即使完全按渲染出的表单填写，
   也永远无法提交。
9. `GET /history/{id}` 始终返回 500（`record.html` 迭代 dict 时没有用
   `.items()`）。
10. `POST /cancel/*` 会把整个服务器冻结约 25 秒：这条 `async def` 路由
    在事件循环上直接调用了阻塞的执行器。执行器现在通过
    `run_in_threadpool` 运行。
11. 全新克隆会产生 CRLF 文件，使 golden 测试在任何干净的检出上都失败。
    通过提交带 `eol=lf` 的 `.gitattributes` 修复。
12. `helpui test generated`（相对路径）找不到项目，
    因为探针子进程以 `cwd=<target>` 运行；现在路径会被解析成绝对路径。

### 已知限制

- argparse 的数字类型（`type=int`）在 help 输出里不可见，因此被报告为 `str`。
  使用显式的 `metavar="INT"` 或 `ArgumentDefaultsHelpFormatter`
  才能得到 `int` 探测。
- **argparse 的 `required` 永远不会被设置。** 必填选项是用 usage 行里
  不带方括号的 token 表达的，但解析器是在该选项的 help 单元格里找字面量
  `required`，那里永远没有。click 的 `[required]` 标记能正确解析。
- **typer 的必填 *选项* 不会被探测到**，原因属于同一类：
  typer 只在位置参数行上打印 `*` 和 `[required]`。
- 没装 `rich` 的 typer 被标记为 `click`。它的输出与 click 相同，
  所以解析仍然正确——只是标签不同。
- `PATH` metavar 有歧义；由 help 散文在文件和目录控件之间决定，默认取文件。
- `--subcommand "a b"` 的值会按空白切分成两个 argv 项。
  这里不涉及 shell，所以什么都不会被执行，但该值会变成多个字面量参数。
- `HELPUI_TIMEOUT=0` 或负值行为未定义。
- 忽略 `NO_COLOR` / `TERM=dumb` 的工具可能输出 ANSI 转义码。

[Unreleased]: https://github.com/mdktelmdktel/helpui/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mdktelmdktel/helpui/releases/tag/v0.1.0
