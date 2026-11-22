"""Grep gate: the repo never names a real carrier, or a system or term from a former
employer's stack.

Content is fictional (plan ids like TRN-1001-001, ZIPs 000xx). This scans every file git tracks
or would add, so a real name can't slip in through a doc, a fixture or a comment.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()
TOOL_STATE = (".claude/", ".enola/", ".vscode/", ".idea/")
"""Local editor and agent folders: not part of the project, and never committed."""

BANNED = re.compile(
    # A former employer's systems and in-house terms.
    r"\b(QuoteManage|Luma|Ignite|Sunfire|MARx|AOR|TPV)\b|\b[Aa]dvocate\b"
    # Real Medicare carriers.
    r"|\b(Humana|Aetna|UnitedHealthcare|United Healthcare|Cigna|Anthem|Elevance|WellCare|"
    r"Wellcare|Centene|Kaiser|Molina|Devoted Health|Clover Health|Alignment Health|SCAN Health|"
    r"Highmark|Florida Blue|Blue Cross|BlueCross|Mutual of Omaha|CareSource|Priority Health)\b"
)


def project_files() -> list[Path]:
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(
        [git, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    paths = [p for p in out.split("\0") if p and not p.startswith(TOOL_STATE)]
    return [ROOT / p for p in paths if (ROOT / p).resolve() != SELF and (ROOT / p).is_file()]


def test_no_real_carriers_or_former_employer_terms() -> None:
    hits = []
    for path in project_files():
        data = path.read_bytes()
        if b"\0" in data:
            continue  # binary
        for n, line in enumerate(data.decode(errors="replace").splitlines(), 1):
            if m := BANNED.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{n}: {m.group(0)}")
    assert not hits, "banned names in the repo:\n" + "\n".join(hits)


@pytest.mark.parametrize(
    ("line", "banned"),
    [
        ("Call the Humana line", True),
        ("Our AOR form", True),
        ("an advocate for the member", True),
        ("Major favor, tpv-free", False),
        ("Ignition timing", False),
        ("Larkspur Benefits Group", False),
    ],
)
def test_the_gate_pattern(line: str, banned: bool) -> None:
    assert bool(BANNED.search(line)) is banned
