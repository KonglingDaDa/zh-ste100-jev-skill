"""Unit and contract tests for zh-ste100-skill."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from contracts.schema import (  # noqa: E402
    CONTRACT_VERSION,
    JevSemanticVerdict,
    LexiconEntry,
    LintFinding,
    LintReport,
    LintSummary,
)


def _load_script_module(name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load script module {rel_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lint_mod = _load_script_module("zh_ste_lint", "scripts/zh-ste-lint.py")
jev_mod = _load_script_module("zh_ste_jev", "scripts/zh-ste-jev.py")


class TestContracts(unittest.TestCase):
    """Verify G1 contract boundaries and fail-loud validation."""

    def test_lexicon_entry_validation(self) -> None:
        entry = LexiconEntry(
            category="nominalization",
            pattern="对…进行分析",
            replacement="分析…",
            reason="去除弱动词",
            engine="hard",
        )
        self.assertEqual(entry.engine, "hard")
        with self.assertRaises(ValueError):
            LexiconEntry(category="", pattern="x", replacement="y", reason="z")
        with self.assertRaises(ValueError):
            LexiconEntry(category="a", pattern="x", replacement="y", reason="z", engine="invalid")

    def test_lint_finding_and_report_validation(self) -> None:
        finding = LintFinding(
            file="test.md",
            line=1,
            col=3,
            rule="nominalization",
            level="advisory-free",
            engine="hard",
            match="进行了排查",
            message="请使用真动词",
        )
        summary = LintSummary(
            mode="report",
            max_chars=35,
            baseline=0,
            total_chars=20,
            total_sentences=1,
            hard_violations=1,
            soft_advisories=0,
            total_findings=1,
            violations_per_100_chars=5.0,
            passed=False,
        )
        report = LintReport(schema_version=CONTRACT_VERSION, summary=summary, findings=[finding])
        dumped = report.to_dict()
        self.assertEqual(dumped["schema_version"], "zh-ste100/v1")
        self.assertEqual(len(dumped["findings"]), 1)

        with self.assertRaises(ValueError):
            LintFinding(
                file="test.md",
                line=0,
                col=1,
                rule="nominalization",
                level="advisory-free",
                engine="hard",
                match="x",
                message="y",
            )


class TestZhSteLinter(unittest.TestCase):
    """Verify static hard gates and Chinese literary soft rules."""

    def test_after_only_baseline_has_zero_hard_violations(self) -> None:
        after_path = REPO_ROOT / "examples" / "after-only.md"
        text = after_path.read_text(encoding="utf-8")
        rep = lint_mod.lint_text(text, filename="examples/after-only.md", mode="report")
        self.assertEqual(
            rep.summary.hard_violations,
            0,
            f"Expected 0 hard violations in after-only.md, got: {rep.findings}",
        )
        self.assertTrue(rep.summary.passed)

    def test_detects_hard_and_soft_diseases_in_bad_report(self) -> None:
        bad = (
            "需要注意的是，本次针对鉴权模块在缺少令牌的情况下可能会引发穿透的相关异常问题，"
            "由于接口的调用失败导致了数据库挂了，已经通过最小侵入式的方式完成了对配置项的收口；"
            "目前其状态基本上应该已经彻底解决且具有很高的可用性，后续会核查并检查若干文件。"
        )
        rep = lint_mod.lint_text(bad, filename="sample.md", mode="report")
        rules = {f.rule for f in rep.findings}
        for expected in (
            "taichi-hedge",
            "empty-overclaim",
            "bamboo-length",
            "de-stacking",
            "western-shell",
            "weak-verb",
            "buzzword-abstract",
            "vague-reference",
            "synonym-rotation",
        ):
            self.assertIn(expected, rules)
        self.assertFalse(rep.summary.passed)

    def test_three_modes_sentence_length_caps(self) -> None:
        # 31 个汉字：procedure(≤25) 应报错，report(≤35) 和 descriptive(≤40) 应通过
        sent_31 = "请先确认备份目录权限正确再去复制新的二进制可执行文件到生产目录。"
        self.assertEqual(lint_mod.count_effective_units(sent_31), 31)

        rep_proc = lint_mod.lint_text(sent_31, mode="procedure")
        self.assertIn("bamboo-length", {f.rule for f in rep_proc.findings})

        rep_report = lint_mod.lint_text(sent_31, mode="report")
        self.assertNotIn("bamboo-length", {f.rule for f in rep_report.findings})

        rep_desc = lint_mod.lint_text(sent_31, mode="descriptive")
        self.assertNotIn("bamboo-length", {f.rule for f in rep_desc.findings})

    def test_cli_json_and_selftest(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "zh-ste-lint.py"), "--selftest"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

        proc_json = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "zh-ste-lint.py"),
                "--mode",
                "report",
                "--json",
                str(REPO_ROOT / "examples" / "after-only.md"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc_json.returncode, 0, proc_json.stderr)
        data = json.loads(proc_json.stdout)
        self.assertEqual(data["schema_version"], CONTRACT_VERSION)
        self.assertTrue(data["summary"]["passed"])


class TestZhSteJevVerifier(unittest.TestCase):
    """Verify optional Jev semantic verifier payload construction and fallback."""

    def test_jev_selftest_and_graceful_skip(self) -> None:
        self.assertEqual(jev_mod.run_selftest(), 0)
        verdict: JevSemanticVerdict = jev_mod.call_jev_gateway(
            "网关鉴权中间件已修复，单测 6 条全部通过。",
            mode="report",
            api_key="",
        )
        self.assertEqual(verdict.status, "unavailable")
        self.assertEqual(verdict.verdict, "skipped")

    def test_rejects_unsafe_url_scheme(self) -> None:
        with self.assertRaises(ValueError):
            jev_mod.call_jev_gateway(
                "网关鉴权中间件已修复。",
                mode="report",
                base_url="file:///etc/passwd",
                api_key="dummy",
            )


if __name__ == "__main__":
    unittest.main()
