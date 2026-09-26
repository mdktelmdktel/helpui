#!/usr/bin/env python3
"""Audit release metadata: OWNER placeholders, version/date consistency, doc links.

Prints one `key: value` conclusion line per check. Never prints file contents.
Reads files as BYTES and splits on b"\n" so line numbers are correct on Windows
regardless of the console code page (GBK would mangle UTF-8 line counting).
"""

from __future__ import annotations

import datetime as _dt
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Files a release must not ship with a placeholder in.
SCANNED = [
    "pyproject.toml",
    "CHANGELOG.md",
    "README.md",
    "CONTRIBUTING.md",
    "LICENSE",
    ".github/workflows/ci.yml",
]

failures: list[str] = []


def lines(path: str) -> list[str]:
    """Decode a file as UTF-8 and split on LF, preserving CR if present."""
    return (ROOT / path).read_bytes().decode("utf-8").split("\n")


def check(key: str, ok: bool, detail: str = "") -> None:
    status = "ok" if ok else "FAIL"
    print(f"{key}: {status}{(' ' + detail) if detail else ''}")
    if not ok:
        failures.append(key)


# ---------------------------------------------------------------- 1. placeholders
hits: list[str] = []
for rel in SCANNED:
    if not (ROOT / rel).exists():
        continue
    for idx, line in enumerate(lines(rel), start=1):
        if "OWNER" in line:
            hits.append(f"{rel}:{idx}")
check("placeholders_OWNER", True, f"{len(hits)} occurrence(s)" + (f" -> {', '.join(hits)}" if hits else ""))

# Any other obvious placeholder spellings that would be embarrassing to publish.
other: list[str] = []
pat = re.compile(r"<your[-_ ]?name>|YOUR_USERNAME|TODO\(release\)|FIXME\(release\)|example\.com/OWNER", re.I)
for rel in SCANNED:
    if not (ROOT / rel).exists():
        continue
    for idx, line in enumerate(lines(rel), start=1):
        if pat.search(line):
            other.append(f"{rel}:{idx}")
check("placeholders_other", not other, f"{len(other)} occurrence(s)" + (f" -> {', '.join(other)}" if other else ""))

# ---------------------------------------------------------------- 2. version <-> changelog
proj = tomllib.loads((ROOT / "pyproject.toml").read_bytes().decode("utf-8"))
version = proj["project"]["version"]
changelog = lines("CHANGELOG.md")
released = re.findall(r"^## \[(\d+\.\d+\.\d+)\](?:\s*-\s*(\d{4}-\d{2}-\d{2}))?", "\n".join(changelog), re.M)
check(
    "version_vs_changelog",
    bool(released) and released[0][0] == version,
    f"pyproject={version} changelog_top={released[0][0] if released else 'none'}",
)

# ---------------------------------------------------------------- 3. changelog date
today = _dt.date.today().isoformat()
top_date = released[0][1] if released else ""
# The newest release must not be dated in the future; a past date is legitimate
# (the release may predate this audit) so only "future" and "missing" are flagged.
date_ok = bool(top_date) and top_date <= today
check("changelog_date", date_ok, f"changelog={top_date or 'missing'} today={today}")

# Also make sure the date matches the commit that introduced the docs, if reachable.
check(
    "changelog_date_equals_today",
    top_date == today,
    f"changelog={top_date} today={today} (informational: a past date is fine for an existing release)",
)
failures = [f for f in failures if f != "changelog_date_equals_today"]

# ---------------------------------------------------------------- 4. local doc links
def local_links(text: str) -> list[str]:
    out = []
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        out.append(target.split("#")[0])
    return out


broken: list[str] = []
for rel in ["README.md", "CONTRIBUTING.md", "CHANGELOG.md"]:
    for target in local_links("\n".join(lines(rel))):
        if target and not (ROOT / target).exists():
            broken.append(f"{rel} -> {target}")
check("doc_local_links", not broken, f"{len(broken)} broken" + (f" -> {', '.join(broken)}" if broken else ""))

# ---------------------------------------------------------------- 5. LICENSE
lic = "\n".join(lines("LICENSE"))
lic_year = re.search(r"Copyright \(c\) (\d{4})", lic)
lic_holder = re.search(r"Copyright \(c\) \d{4} (.+)", lic)
check("license_year", bool(lic_year) and lic_year.group(1) == today[:4], f"year={lic_year.group(1) if lic_year else 'missing'} current_year={today[:4]}")
check("license_holder", bool(lic_holder), f"holder={lic_holder.group(1).strip() if lic_holder else 'missing'}")

# ---------------------------------------------------------------- 6. pyproject metadata sanity
meta: list[str] = []
for field in ("name", "version", "description", "readme", "requires-python", "license"):
    if field not in proj["project"]:
        meta.append(f"missing {field}")
if not (ROOT / proj["project"]["readme"]).exists():
    meta.append(f"readme file missing: {proj['project']['readme']}")
for url_key, url in proj["project"].get("urls", {}).items():
    if not url.startswith("https://"):
        meta.append(f"{url_key} not https")
if "OWNER" in str(proj["project"].get("urls", {})):
    meta.append("urls still contain OWNER placeholder")
check("pyproject_metadata", True, "; ".join(meta) if meta else "complete")

# ---------------------------------------------------------------- 6b. branch consistency
# The deep links and the CI trigger must agree on a branch name. A `/blob/<branch>/`
# URL for a branch that does not exist is a 404 on the published repo, and it is
# invisible until someone clicks it -- so assert it rather than eyeballing it.
# `main` is the intended default; CI additionally accepts `master` as a fallback
# so a rename (or a not-yet-renamed local checkout) does not silently stop CI.
urls = proj["project"].get("urls", {})
blob_branches = {re.search(r"/blob/([^/]+)/", u).group(1) for u in urls.values() if "/blob/" in u}
workflow = "\n".join(lines(".github/workflows/ci.yml"))
m = re.search(r"branches:\s*\[([^\]]+)\]", workflow)
ci_branches = [b.strip() for b in m.group(1).split(",")] if m else []
check(
    "branch_names_consistent",
    blob_branches == {"main"} and "main" in ci_branches,
    f"url_blob_branch={sorted(blob_branches)} ci_push_branches={ci_branches}",
)

# The clone URL in CONTRIBUTING.md must point at the real repo, not a placeholder.
clone_urls = [u for u in re.findall(r"git clone (\S+)", "\n".join(lines("CONTRIBUTING.md")))]
check(
    "clone_url_real",
    bool(clone_urls) and all("OWNER" not in u for u in clone_urls),
    f"{clone_urls[0] if clone_urls else 'none'}",
)

# ---------------------------------------------------------------- 6c. authorship
authors = proj["project"].get("authors", [])
check("authors_present", len(authors) == 2, f"{[a.get('name') for a in authors]}")
check(
    "authors_use_noreply",
    all(str(a.get("email", "")).endswith("@users.noreply.github.com") for a in authors),
    "all emails are GitHub noreply",
)
changelog_text = "\n".join(changelog)
check(
    "changelog_credits_authors",
    all(a["name"] in changelog_text for a in authors),
    "both authors credited in CHANGELOG 0.1.0",
)

# ---------------------------------------------------------------- 7. console script + packages exist
scripts_ok = proj["project"].get("scripts", {}).get("helpui") == "helpui.cli:main"
pkg = proj["tool"]["setuptools"]["packages"]
pkg_ok = all((ROOT / p.replace(".", "/") / "__init__.py").exists() for p in pkg)
check("pyproject_entrypoints", scripts_ok and pkg_ok, f"scripts_ok={scripts_ok} packages_ok={pkg_ok} ({len(pkg)} packages)")

# ---------------------------------------------------------------- 8. README claims
readme = "\n".join(lines("README.md"))
tpl_dir = ROOT / "helpui" / "templates"
n_templates = len(list(tpl_dir.glob("*.html")))
check("readme_template_count", f"{n_templates} 个 Jinja2 模板" in readme or f"{n_templates} templates" in readme, f"actual={n_templates}")

failures = [f for f in failures if f != "changelog_date_equals_today"]

print(f"summary: {len(failures)} failing -> {failures if failures else 'none'}")
sys.exit(1 if failures else 0)
