#!/usr/bin/env python3
"""zh-ste-jev.py — 可选的 Jev（TypeSafe System One）受控中文语义复核器.

静态正则检查器（`scripts/zh-ste-lint.py`）负责机械可核对的硬门禁与西化表面特征；
本脚本作为可选的第二道门禁，通过本机 Jev 网关（默认 `http://127.0.0.1:18650`）
在百毫秒内对以下 4 个纯语义维度做结构化概率判断：
1. `conclusion_definite`: 首句是否直接给出明确结论或状态，不绕弯铺垫
2. `no_taichi_hedging`: 全文是否消除了推诿打太极的含糊语气（且明确区分已验证与未验证边界）
3. `bamboo_flow_natural`: 句式是否符合现代汉语竹节流水句（无多层长定语挂起、无生硬欧化翻译腔）
4. `meaning_preserved`: （提供原文对比时）改写后是否 100% 保留了原文的事实、数字、条件与置信度

遵循 `system-one-in-loop` 与 `jev-testing` 规范：
- 只读取环境变量 `TYPESAFE_BASE_URL`（默认 `http://127.0.0.1:18650`）与 `TYPESAFE_API_KEY`；
- 未配置环境或网关不可达时，显式返回 `status="unavailable", verdict="skipped"`，绝不伪造通过。
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from contracts.schema import (  # noqa: E402
    CONTRACT_VERSION,
    VALID_MODES,
    JevSemanticVerdict,
)

PASS_THRESHOLD = 0.70
FAIL_THRESHOLD = 0.35


def build_jev_payload(
    candidate_text: str,
    *,
    mode: str = "report",
    original_text: str | None = None,
) -> dict[str, Any]:
    """构造单次请求多问题（Single-State Multi-Question）的 Jev 分类载荷."""
    if mode not in VALID_MODES:
        raise ValueError(f"Unsupported mode {mode!r}; expected one of {VALID_MODES}")

    state: dict[str, Any] = {
        "mode": mode,
        "candidate_text": candidate_text.strip()[:4000],
    }
    if original_text is not None and original_text.strip():
        state["original_text"] = original_text.strip()[:4000]

    questions: dict[str, Any] = {
        "conclusion_definite": {
            "type": "noul",
            "instructions": (
                "Judge whether `candidate_text` states a clear, direct conclusion or action right at the beginning "
                "without throat-clearing or bureaucratic preamble."
            ),
            "criteria": {
                "yes": (
                    "Opens immediately with a concrete verdict, current status, root cause, or direct instruction "
                    "so the reader knows the outcome in the first sentence."
                ),
                "no": (
                    "Starts with vague background, process monologue, empty filler phrases, or hides the conclusion "
                    "at the end of a long paragraph."
                ),
            },
        },
        "no_taichi_hedging": {
            "type": "noul",
            "instructions": (
                "Judge whether `candidate_text` avoids evasive hedging ('打太极') and states verified vs. unverified "
                "facts with concrete precision."
            ),
            "criteria": {
                "yes": (
                    "States verified outcomes and unverified boundaries explicitly with concrete names/numbers, "
                    "without evasive words like '基本上应该', '理论上差不多', or '在一定程度上'."
                ),
                "no": (
                    "Uses evasive qualifiers, vague quantifiers ('若干/相关/大体上'), or blurs whether verification "
                    "actually succeeded."
                ),
            },
        },
        "bamboo_flow_natural": {
            "type": "noul",
            "instructions": (
                "Judge whether `candidate_text` reads like natural Chinese bamboo-joint clauses (竹节流水句) "
                "with clear subject + concrete verb order, free of westernized long pre-nominal modifiers."
            ),
            "criteria": {
                "yes": (
                    "Uses short, sequential Chinese clauses ordered by condition/cause -> actor + concrete verb -> result, "
                    "easy to understand in one pass."
                ),
                "no": (
                    "Contains awkward translationese shells ('基于…下/针对…问题'), stacked '的' modifiers before nouns, "
                    "or weak nominalized verbs ('对…进行/完成对…的')."
                ),
            },
        },
    }

    if "original_text" in state:
        questions["meaning_preserved"] = {
            "type": "noul",
            "instructions": (
                "Compare `candidate_text` against `original_text` and judge whether all factual claims, numbers, "
                "conditions, warnings, and epistemic modality are faithfully preserved."
            ),
            "criteria": {
                "yes": (
                    "Preserves every fact, number, precondition, warning, and uncertainty boundary from `original_text` "
                    "without inventing facts or upgrading 'possible' to 'certain'."
                ),
                "no": (
                    "Drops safety conditions/numbers, invents unsupported facts, or changes the factual conclusion "
                    "of `original_text`."
                ),
            },
        }

    return {
        "model": "jev-latest",
        "state": state,
        "questions": questions,
    }


def evaluate_probabilities(
    probs: dict[str, float | None],
    *,
    mode: str = "report",
) -> JevSemanticVerdict:
    """将 Jev 返回的各维度概率根据确定性阈值转化为 JevSemanticVerdict."""
    required_keys = ["no_taichi_hedging", "bamboo_flow_natural"]
    if mode == "report":
        required_keys.append("conclusion_definite")
    if probs.get("meaning_preserved") is not None:
        required_keys.append("meaning_preserved")

    values = [probs.get(k) for k in required_keys]
    if any(v is None for v in values):
        return JevSemanticVerdict(
            schema_version=CONTRACT_VERSION,
            status="undecided",
            conclusion_definite=probs.get("conclusion_definite"),
            no_taichi_hedging=probs.get("no_taichi_hedging"),
            bamboo_flow_natural=probs.get("bamboo_flow_natural"),
            meaning_preserved=probs.get("meaning_preserved"),
            verdict="undecided",
            notes="部分必需维度未返回有效概率，按系统门禁规则归为 undecided。",
        )

    concrete_vals: list[float] = [v for v in values if v is not None]
    if any(v <= FAIL_THRESHOLD for v in concrete_vals):
        verdict = "fail"
        status = "ok"
        notes = f"存在低于失败阈值 ({FAIL_THRESHOLD}) 的语义维度。"
    elif all(v >= PASS_THRESHOLD for v in concrete_vals):
        verdict = "pass"
        status = "ok"
        notes = f"全部必需语义维度均高于通过阈值 ({PASS_THRESHOLD})。"
    else:
        verdict = "undecided"
        status = "undecided"
        notes = f"部分维度处于灰区 ({FAIL_THRESHOLD} < p < {PASS_THRESHOLD})，需人工或大模型复核。"

    return JevSemanticVerdict(
        schema_version=CONTRACT_VERSION,
        status=status,
        conclusion_definite=probs.get("conclusion_definite"),
        no_taichi_hedging=probs.get("no_taichi_hedging"),
        bamboo_flow_natural=probs.get("bamboo_flow_natural"),
        meaning_preserved=probs.get("meaning_preserved"),
        verdict=verdict,
        notes=notes,
    )


def call_jev_gateway(
    candidate_text: str,
    *,
    mode: str = "report",
    original_text: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout_sec: float = 10.0,
) -> JevSemanticVerdict:
    """调用本机或环境变量指定的 TypeSafe Jev HTTP 接口执行语义复核."""
    resolved_url = (base_url or os.environ.get("TYPESAFE_BASE_URL") or "http://127.0.0.1:18650").rstrip("/")
    resolved_key = api_key if api_key is not None else os.environ.get("TYPESAFE_API_KEY", "")

    if not resolved_key:
        return JevSemanticVerdict(
            schema_version=CONTRACT_VERSION,
            status="unavailable",
            conclusion_definite=None,
            no_taichi_hedging=None,
            bamboo_flow_natural=None,
            meaning_preserved=None,
            verdict="skipped",
            notes="未检测到环境变量 TYPESAFE_API_KEY，跳过可选 Jev 语义复核（仅依赖 zh-ste-lint.py 静态门禁）。",
        )

    payload = build_jev_payload(candidate_text, mode=mode, original_text=original_text)
    req_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    parsed = urllib.parse.urlsplit(f"{resolved_url}/v1/systemone")
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Unsupported Jev gateway URL scheme or host: {resolved_url!r}")

    conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parsed.netloc, timeout=timeout_sec)
    path = parsed.path or "/v1/systemone"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    try:
        conn.request(
            "POST",
            path,
            body=req_bytes,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {resolved_key}",
            },
        )
        resp = conn.getresponse()
        raw_body = resp.read().decode("utf-8", "replace")
        if resp.status >= 400:
            raise OSError(f"HTTP {resp.status}: {raw_body[:200]}")
        body = json.loads(raw_body)
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
        return JevSemanticVerdict(
            schema_version=CONTRACT_VERSION,
            status="unavailable",
            conclusion_definite=None,
            no_taichi_hedging=None,
            bamboo_flow_natural=None,
            meaning_preserved=None,
            verdict="skipped",
            notes=f"Jev 网关不可达或响应异常 ({exc})，已标记为 skipped。",
        )
    finally:
        conn.close()

    if body.get("stopReason") not in (None, "stop"):
        return JevSemanticVerdict(
            schema_version=CONTRACT_VERSION,
            status="error",
            conclusion_definite=None,
            no_taichi_hedging=None,
            bamboo_flow_natural=None,
            meaning_preserved=None,
            verdict="undecided",
            notes=f"Jev stopReason={body.get('stopReason')!r}, error={body.get('errorMessage')!r}",
        )

    answers = body.get("answers", {})
    probs: dict[str, float | None] = {
        "conclusion_definite": _extract_prob(answers.get("conclusion_definite")),
        "no_taichi_hedging": _extract_prob(answers.get("no_taichi_hedging")),
        "bamboo_flow_natural": _extract_prob(answers.get("bamboo_flow_natural")),
        "meaning_preserved": _extract_prob(answers.get("meaning_preserved"))
        if "meaning_preserved" in answers
        else None,
    }
    return evaluate_probabilities(probs, mode=mode)


def _extract_prob(ans: Any) -> float | None:
    if not isinstance(ans, dict):
        return None
    val = ans.get("probability")
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return val * 1.0
    return None


def run_selftest() -> int:
    """运行离线确定性自检（校验载荷构造、阈值分流与缺失密钥时的优雅降级）."""
    p = build_jev_payload(
        "网关鉴权已修复，单测 6 条全过。",
        mode="report",
        original_text="针对网关鉴权进行了修复，单测通过。",
    )
    if p["model"] != "jev-latest":
        raise RuntimeError("Unexpected model name in payload")
    if set(p["questions"].keys()) != {
        "conclusion_definite",
        "no_taichi_hedging",
        "bamboo_flow_natural",
        "meaning_preserved",
    }:
        raise RuntimeError(f"Unexpected question keys: {p['questions'].keys()}")

    v_pass = evaluate_probabilities(
        {
            "conclusion_definite": 0.92,
            "no_taichi_hedging": 0.88,
            "bamboo_flow_natural": 0.95,
            "meaning_preserved": 0.91,
        },
        mode="report",
    )
    if v_pass.verdict != "pass" or v_pass.status != "ok":
        raise RuntimeError(f"Expected pass verdict, got {v_pass}")

    v_fail = evaluate_probabilities(
        {
            "conclusion_definite": 0.20,
            "no_taichi_hedging": 0.85,
            "bamboo_flow_natural": 0.80,
            "meaning_preserved": None,
        },
        mode="report",
    )
    if v_fail.verdict != "fail":
        raise RuntimeError(f"Expected fail verdict, got {v_fail}")

    v_gray = evaluate_probabilities(
        {
            "conclusion_definite": 0.52,
            "no_taichi_hedging": 0.85,
            "bamboo_flow_natural": 0.80,
            "meaning_preserved": None,
        },
        mode="report",
    )
    if v_gray.verdict != "undecided":
        raise RuntimeError(f"Expected undecided verdict, got {v_gray}")

    v_skip = call_jev_gateway("测试文本", api_key="")
    if v_skip.status != "unavailable" or v_skip.verdict != "skipped":
        raise RuntimeError(f"Expected skipped verdict when key missing, got {v_skip}")

    print("✓ zh-ste-jev --selftest: 全部离线自检断言通过 (5/5)。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="zh-ste-jev",
        description="可选 Jev（System One）受控中文语义复核器",
    )
    parser.add_argument("--file", type=str, help="待复核的受控中文文件路径（不传则读 stdin）")
    parser.add_argument("--original", type=str, default=None, help="可选：改写前的原文文件路径（用于核验原意保真）")
    parser.add_argument("--mode", choices=VALID_MODES, default="report", help="运行档位 (默认 report)")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式结果")
    parser.add_argument("--selftest", action="store_true", help="运行离线确定性自检")

    args = parser.parse_args(argv)
    if args.selftest:
        return run_selftest()

    if args.file:
        candidate = Path(args.file).read_text(encoding="utf-8")
    else:
        candidate = sys.stdin.read()

    original = Path(args.original).read_text(encoding="utf-8") if args.original else None
    verdict = call_jev_gateway(candidate, mode=args.mode, original_text=original)

    if args.json:
        print(json.dumps(verdict.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(
            f"[zh-ste-jev] status={verdict.status} | verdict={verdict.verdict} | "
            f"definite={verdict.conclusion_definite} | no_taichi={verdict.no_taichi_hedging} | "
            f"bamboo_flow={verdict.bamboo_flow_natural} | preserved={verdict.meaning_preserved}\n"
            f"说明: {verdict.notes}"
        )

    return 0 if verdict.verdict in ("pass", "skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
