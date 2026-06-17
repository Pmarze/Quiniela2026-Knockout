from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Google Sheets document URL", re.compile(r"https://docs\.google\.com/spreadsheets/d/[A-Za-z0-9_-]{20,}", re.I)),
    ("Google Sheets export path", re.compile(r"spreadsheets/d/[A-Za-z0-9_-]{20,}", re.I)),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_-]{20,}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}")),
    ("OpenAI key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}", re.I)),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private key block", re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")),
    ("local Windows user path", re.compile(r"C:\\Users\\[^\\\s\"']+", re.I)),
    (
        "sensitive assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|token|secret|password|passwd|pwd|sheet_id)\b"
            r"\s*[:=]\s*['\"]?(?!$|<|\.{3}|your_|your-|placeholder|example|none|null)"
            r"([A-Za-z0-9_\-]{12,})"
        ),
    ),
]

FORBIDDEN_TRACKED_SUFFIXES = (
    ".env",
    ".local.json",
)

# Archivos de documentacion interna que pueden contener rutas locales de ejemplo
SCAN_EXCLUDED_FILES = {
    "CLAUDE.md",
}


def _git_lines(args: list[str]) -> list[str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _candidate_files() -> list[Path]:
    files = set(_git_lines(["ls-files", "--cached", "--others", "--exclude-standard"]))
    return sorted(ROOT / f for f in files if (ROOT / f).is_file())


def _is_text(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:4096]
    except OSError:
        return False
    return b"\0" not in chunk


def _scan_file(path: Path) -> list[str]:
    rel = path.relative_to(ROOT).as_posix()
    if path.name in SCAN_EXCLUDED_FILES:
        return []
    issues: list[str] = []

    lower = rel.lower()
    if lower.endswith(FORBIDDEN_TRACKED_SUFFIXES) and lower != ".env.example":
        issues.append(f"{rel}: archivo local/secret versionable")

    if not _is_text(path):
        return issues

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return issues

    for label, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            issues.append(f"{rel}:{line}: {label}")
    return issues


def main() -> int:
    issues: list[str] = []
    for path in _candidate_files():
        issues.extend(_scan_file(path))

    if issues:
        print("SECURITY SCAN FAILED")
        for issue in issues:
            print(f"- {issue}")
        return 1

    print("security scan ok")
    print(f"files scanned: {len(_candidate_files())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
