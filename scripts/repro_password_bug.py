import sys

sys.path.insert(0, ".")

from helpui.parsers.base import infer_type
from helpui.parsers.click_parser import _METAVAR_TYPES

print("=== claim: _METAVAR_TYPES['TEXT'] is truthy, short-circuiting the or-chain ===")
value = _METAVAR_TYPES.get("TEXT", "")
print(f"  _METAVAR_TYPES.get('TEXT','') = {value!r}  truthy={bool(value)}")
text_meta = _METAVAR_TYPES.get("TEXT".upper(), "") or infer_type(
    "TEXT", "Password for the archive."
)
print(f"  current effective type for --password (click) = {text_meta!r}")

print()
print("=== would prose sniffing alone find it? ===")
print(f"  infer_type('TEXT', 'Password for the archive.') = {infer_type('TEXT', 'Password for the archive.')!r}")

print()
print("=== real fixture behaviour (authoritative) ===")
from helpui.parsers.click_parser import ClickParser
from helpui.parsers.typer_parser import TyperParser
from tests.conftest import help_text

for fixture, parser, sub in (
    ("click", ClickParser(), "convert"),
    ("typer", TyperParser(), "convert"),
    ("argparse", None, "convert"),
):
    if parser is None:
        from helpui.parsers.argparse_parser import ArgparseParser

        parser = ArgparseParser()
    cmd = parser.parse_subcommand(
        help_text(fixture, sub),
        tool_name=fixture,
        tool_path=f"{fixture}.py",
        command_name=sub,
    )
    pw = [o for o in cmd.options if o.dest == "password"]
    for opt in pw:
        print(f"  {fixture:9} --password -> type={opt.type!r}  (want 'password')")
    if not pw:
        print(f"  {fixture:9} --password not found in options")
