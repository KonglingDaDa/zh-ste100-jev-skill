"""Seam contracts for zh-ste100-skill (G1 Contract-First).

Defines immutable data structures and validation boundaries shared by:
1. Markdown segmenter & static soft/hard linter (`scripts/zh-ste-lint.py`)
2. Optional Jev System-One semantic verifier (`scripts/zh-ste-jev.py`)
3. Unit & contract test suites (`tests/test_zh_ste.py`)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

CONTRACT_VERSION = "zh-ste100/v1"
VALID_MODES = ("report", "procedure", "descriptive")
VALID_LEVELS = ("advisory-free", "advisory")
VALID_ENGINES = ("hard", "soft")


@dataclass(frozen=True)
class LexiconEntry:
    """One controlled Chinese vocabulary or replacement rule entry."""

    category: str
    pattern: str
    replacement: str
    reason: str
    engine: str = "hard"

    def __post_init__(self) -> None:
        if not self.category or not self.pattern:
            raise ValueError("LexiconEntry requires non-empty category and pattern")
        if self.engine not in VALID_ENGINES:
            raise ValueError(f"Invalid engine {self.engine!r}; expected one of {VALID_ENGINES}")


@dataclass(frozen=True)
class LintFinding:
    """A single violation or soft-style advisory emitted by zh-ste-lint."""

    file: str
    line: int
    col: int
    rule: str
    level: str
    engine: str
    match: str
    message: str
    suggestion: str = ""

    def __post_init__(self) -> None:
        if not self.file:
            raise ValueError("LintFinding.file must not be empty")
        if self.line < 1 or self.col < 1:
            raise ValueError(f"LintFinding line/col must be >= 1, got ({self.line}, {self.col})")
        if self.level not in VALID_LEVELS:
            raise ValueError(f"Invalid level {self.level!r}; expected one of {VALID_LEVELS}")
        if self.engine not in VALID_ENGINES:
            raise ValueError(f"Invalid engine {self.engine!r}; expected one of {VALID_ENGINES}")
        if not self.rule or not self.message:
            raise ValueError("LintFinding requires non-empty rule and message")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LintSummary:
    """Aggregate metrics for a lint run."""

    mode: str
    max_chars: int
    baseline: int
    total_chars: int
    total_sentences: int
    hard_violations: int
    soft_advisories: int
    total_findings: int
    violations_per_100_chars: float
    passed: bool

    def __post_init__(self) -> None:
        if self.mode not in VALID_MODES:
            raise ValueError(f"Invalid mode {self.mode!r}; expected one of {VALID_MODES}")
        if self.max_chars < 5:
            raise ValueError("max_chars must be >= 5")
        if self.baseline < 0:
            raise ValueError("baseline must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LintReport:
    """Top-level output contract for zh-ste-lint."""

    schema_version: str
    summary: LintSummary
    findings: list[LintFinding] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.schema_version != CONTRACT_VERSION:
            raise ValueError(f"Unsupported schema_version {self.schema_version!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "summary": self.summary.to_dict(),
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass(frozen=True)
class JevSemanticVerdict:
    """Output contract for optional Jev (System One) semantic check."""

    schema_version: str
    status: str  # "ok" | "undecided" | "unavailable" | "error"
    conclusion_definite: float | None
    no_taichi_hedging: float | None
    bamboo_flow_natural: float | None
    meaning_preserved: float | None
    verdict: str  # "pass" | "fail" | "undecided" | "skipped"
    notes: str = ""

    def __post_init__(self) -> None:
        if self.status not in ("ok", "undecided", "unavailable", "error"):
            raise ValueError(f"Invalid Jev status: {self.status!r}")
        if self.verdict not in ("pass", "fail", "undecided", "skipped"):
            raise ValueError(f"Invalid Jev verdict: {self.verdict!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
