#!/usr/bin/env python3
"""zh-ste-lint.py — 零依赖受控中文写作与汇报静态检查器（软硬双引擎）.

融合：
1. 硬信息层（Hard Gates）：对标《民用飞机维修技术数据简化语言规范》与 ASD-STE100，
   拦截万能弱动词（nominalization）、太极含糊词（taichi-hedge）、互联网黑话（jargon-buzzword）、
   零信息套话（empty-phrase）、分号串联（semicolon-join）、多动作串联（multi-action-chain）、
   超长句（long-sentence）、同义轮换（synonym-rotation）与欧化介词套壳（translationese-shell）。
2. 汉语言文学软章法（Soft Chinese Flow）：对标余光中《论中文的常态与变态》《论的的不休》与现代汉语竹节流水句，
   检查左分支多层“的”字长定语（de-stacking）、悬空指代词（dangling-pronoun）、含糊量词（vague-quantifier）
   与可改为主动句的被动结构（passive-voice）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

# 优先加载仓库契约包；单文件独立拷贝运行时回退内置等价契约
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from contracts.schema import (  # noqa: E402
    CONTRACT_VERSION,
    VALID_MODES,
    JevSemanticVerdict,
    LintFinding,
    LintReport,
    LintSummary,
)

MODE_MAX_CHARS: dict[str, int] = {
    "procedure": 25,
    "report": 35,
    "descriptive": 40,
}

RULE_CATALOG: dict[str, dict[str, str]] = {
    "nominalization": {
        "engine": "hard",
        "level": "advisory-free",
        "desc": "万能弱动词+名词化（进行/作出/加以/予以/完成对…的），应还原为单/双音节真动词",
    },
    "taichi-hedge": {
        "engine": "hard",
        "level": "advisory-free",
        "desc": "太极推诿含糊词（应该/大概/基本上/理论上/似乎/在一定程度上），汇报须给死结论或明写未测边界",
    },
    "jargon-buzzword": {
        "engine": "hard",
        "level": "advisory-free",
        "desc": "互联网黑话与伪术语（赋能/闭环/抓手/收口/对齐/颗粒度/最小侵入式/丝滑等），应改写为具体动作或测量值",
    },
    "empty-phrase": {
        "engine": "hard",
        "level": "advisory-free",
        "desc": "零信息套话起手式（需要注意的是/值得一提的是/显而易见/不难看出等），应直接删去",
    },
    "translationese-shell": {
        "engine": "soft",
        "level": "advisory-free",
        "desc": "欧化介词套壳（基于…下/针对…问题/对于…而言/通过…的方式），应剥壳拆为竹节短句",
    },
    "semicolon-join": {
        "engine": "hard",
        "level": "advisory-free",
        "desc": "使用分号连接动作或分句，应改为句号拆成独立短句",
    },
    "multi-action-chain": {
        "engine": "hard",
        "level": "advisory-free",
        "desc": "单句内用“并且/同时/从而/进而”串联过多动作，应按竹节流水句拆开",
    },
    "long-sentence": {
        "engine": "hard",
        "level": "advisory-free",
        "desc": "单句有效字词数超过当前档位上限（procedure:25 / report:35 / descriptive:40）",
    },
    "synonym-rotation": {
        "engine": "hard",
        "level": "advisory-free",
        "desc": "同一文件内同义词轮换（同一动作或概念混用多个近义词），应全篇锁定一词",
    },
    "de-stacking": {
        "engine": "soft",
        "level": "advisory",
        "desc": "左分支“的”字长定语堆叠（单句 ≥3 个“的”），违背汉语竹节句传统，挂起阅读工作记忆",
    },
    "dangling-pronoun": {
        "engine": "soft",
        "level": "advisory",
        "desc": "悬空代词（该/其/此/上述/前述），应直接点名具体对象",
    },
    "vague-quantifier": {
        "engine": "hard",
        "level": "advisory",
        "desc": "含糊数量或范围词（若干/一些/一部分/相关/适当/酌情），应改写为准确数字或具体名称",
    },
    "passive-voice": {
        "engine": "soft",
        "level": "advisory",
        "desc": "可改为真动词主动句的西化被动结构（被…所/由…所/由于…所导致的）",
    },
}

# 1. 万能弱动词（文字的义肢）
NOMINALIZATION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?:对|针对|将|把)[^，。；！？\n]{1,18}?(?:进行|加以|予以)了?([\u4e00-\u9fff]{2,4})"),
        r"删掉“对…进行/加以/予以”，直接写“\1…”",
    ),
    (
        re.compile(r"(?:进行|加以|予以|开展)了?(?:初步|全面|深入|详细|全方位|自动|手动|进一步)?([\u4e00-\u9fff]{2,4})"),
        r"删掉弱动词，直接用真动词“\1”",
    ),
    (
        re.compile(r"作出(?:了)?(?:重大|热烈|明确|相应|进一步)?([\u4e00-\u9fff]{2,4})"),
        r"删掉“作出”，直接写“\1”",
    ),
    (
        re.compile(r"(?:完成|实现)了对[^，。；！？\n]{1,18}?的([\u4e00-\u9fff]{2,6})"),
        r"删掉“完成/实现了对…的”，直接写“已\1…”",
    ),
    (
        re.compile(r"(?:做|搞|弄)(?:了)?(?:一下|一些|一个)(?:适当|简单|初步)?的?([\u4e00-\u9fff]{2,4})"),
        r"删掉“做/搞一下”，直接写具体动作“\1”",
    ),
    (
        re.compile(r"在(?:处理|执行|解析|调用)[^，。；！？\n]{1,12}?的过程中"),
        "删掉“在…的过程中”，改为“处理/解析…时”",
    ),
]

# 2. 太极含糊词（report / procedure 档硬拦截，descriptive 档提示）
TAICHI_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?:应该已经|应该基本|基本上应该|基本上已经|大体上已经|差不多已经|理论上应该|理论上已经)"),
        "删掉太极词，明确写“已验证通过”或“代码已改，尚未验证”",
    ),
    (
        re.compile(r"(?:在一定程度上|在某种程度上|从某种意义上|某种意义上来说|从某种角度来看)"),
        "删掉零信息度词，写出具体影响范围或数字",
    ),
    (
        re.compile(r"(?:基本上|大体上|似乎|好像|看起来|不出意外的话|大概率是|大概是)"),
        "勿打太极：已查实的直接给死结论；未查实的写“已确认 A，待验证 B”",
    ),
]

# 3. 互联网黑话与伪术语
BUZZWORD_PATTERN = re.compile(
    r"(?:赋能|抓手|组合拳|闭环|对齐颗粒度|颗粒度|拉齐水位|水位拉齐|拉齐|边界收口|收口|"
    r"最小侵入式|零侵入|心智负担|感知度|无缝|极致|丝滑|一站式|全方位|颠覆性|生态化反)"
)

# 4. 零信息套话起手式
EMPTY_PHRASE_PATTERN = re.compile(
    r"(?:需要注意的是|值得一提的是|需要指出的是|显而易见|不难看出|众所周知|总而言之|综上所述)"
)

# 5. 欧化介词套壳
TRANSLATIONESE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?:基于|在)[^，。；！？\n]{2,25}?的(?:情况|前提|背景|基础)之?下"),
        "剥掉“基于/在…的情况下”外壳，直接写前提短句",
    ),
    (
        re.compile(r"针对[^，。；！？\n]{2,30}?的(?:相关)?(?:问题|情况|风险|现象|异常)"),
        "剥掉“针对…的问题”外壳，拆为“X 出现 Y。我做了 Z。”",
    ),
    (
        re.compile(r"对于[^，。；！？\n]{1,15}?而言"),
        "删掉“对于…而言”，直接用对象做主语",
    ),
    (
        re.compile(r"通过[^，。；！？\n]{2,20}?的方式(?:来|去)?"),
        "删掉“通过…的方式”，直接写具体动作",
    ),
]

# 6. 多动作串联
MULTI_ACTION_PATTERN = re.compile(r"(?:并且|同时)[^。！？\n]{1,25}?(?:从而|进而|并最终)")

# 7. 悬空代词
DANGLING_PRONOUN_PATTERN = re.compile(
    r"(?:该(?:文件|配置|服务|模块|函数|接口|问题|错误|异常|记录|任务|项目|脚本|参数|设置)|"
    r"对其|将其|使其|当其|若其|其(?:状态|原因|结果|配置)|上述|前述)"
)

# 8. 含糊数量与范围词
VAGUE_QUANTIFIER_PATTERN = re.compile(
    r"(?:若干个?|一些|一部分|某几个|相关(?:的)?(?:配置|文件|模块|接口|记录|异常|问题|设置)|"
    r"相应(?:的)?(?:配置|修改|处理)|适当(?:的)?(?:调整|优化|修改)|酌情|必要的时候)"
)

# 9. 西化被动结构（排除“被锁/被占/被截断”等施事无关短词）
PASSIVE_VOICE_PATTERN = re.compile(
    r"(?:(?:由于|因为)[^，。；！？\n]{2,25}?所(?:导致|引起|造成)的?|"
    r"由[^，。；！？\n]{1,12}?所[\u4e00-\u9fff]{2,4}|"
    r"被(?:意外|成功|自动|重新|完整)[\u4e00-\u9fff]{2,4})"
)

# 10. 同义轮换组（每组内同时出现 >=2 个不同词即报警）
SYNONYM_GROUPS: dict[str, tuple[str, ...]] = {
    "检查组": ("检查", "核查", "检视", "清查"),
    "验证组": ("验证", "校验", "核验", "印证"),
    "删除组": ("删除", "移除", "删掉", "抹除"),
    "用户组": ("用户", "客户", "使用者"),
    "目录组": ("目录", "文件夹"),
}

CODE_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
INLINE_CODE_RE = re.compile(r"`[^`]+`")
HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
TABLE_SEP_RE = re.compile(r"^\s*\|?(?:\s*:?-{2,}:?\s*\|)+\s*:?-{2,}:?\s*\|?\s*$")
LIST_PREFIX_RE = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)")
BLOCKQUOTE_PREFIX_RE = re.compile(r"^(\s*>\s*)")
SENTENCE_SPLIT_RE = re.compile(r"([^。！？!?]+(?:[。！？!?]+|$))")
CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")
ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def strip_inline_code(text: str) -> str:
    """用等长空格替换行内反引号代码，保留原始列偏移并避免误报代码标识符."""
    return INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), text)


def count_effective_units(sentence: str) -> int:
    """计算中文单句有效长度：每个汉字算 1，每个英文/数字单词算 1（忽略标点与行内代码）."""
    cleaned = strip_inline_code(sentence)
    cjk_count = len(CJK_CHAR_RE.findall(cleaned))
    word_count = len(ASCII_WORD_RE.findall(cleaned))
    return cjk_count + word_count


def _active_level(rule_id: str, mode: str) -> str:
    """根据运行档位动态决定规则的严格级别."""
    if rule_id == "taichi-hedge":
        return "advisory-free" if mode in ("report", "procedure") else "advisory"
    if rule_id == "vague-quantifier":
        return "advisory-free" if mode == "report" else "advisory"
    return RULE_CATALOG[rule_id]["level"]


def lint_text(
    text: str,
    *,
    filename: str = "<stdin>",
    mode: str = "report",
    max_chars: int | None = None,
    baseline: int = 0,
    disabled_rules: set[str] | None = None,
) -> LintReport:
    """对输入 Markdown/文本执行软硬双引擎静态检查，返回符合契约的 LintReport."""
    if mode not in VALID_MODES:
        raise ValueError(f"Unsupported mode {mode!r}; choose from {VALID_MODES}")

    effective_max_chars = max_chars if max_chars is not None else MODE_MAX_CHARS[mode]
    disabled = disabled_rules or set()
    findings: list[LintFinding] = []

    in_fence = False
    total_chars = 0
    total_sentences = 0

    # 记录同义词首次与后续出现位置: group_name -> {word: (line, col)}
    seen_synonyms: dict[str, dict[str, tuple[int, int]]] = {g: {} for g in SYNONYM_GROUPS}

    lines = text.splitlines()
    for line_idx, raw_line in enumerate(lines, start=1):
        if CODE_FENCE_RE.match(raw_line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        stripped = raw_line.strip()
        if not stripped or TABLE_SEP_RE.match(raw_line) or HEADING_RE.match(raw_line):
            continue

        # 剥离 blockquote / 列表标记，保留列号偏移
        working = raw_line
        base_col_offset = 0
        bq_match = BLOCKQUOTE_PREFIX_RE.match(working)
        if bq_match:
            base_col_offset += len(bq_match.group(1))
            working = working[len(bq_match.group(1)) :]
        lst_match = LIST_PREFIX_RE.match(working)
        if lst_match:
            base_col_offset += len(lst_match.group(1))
            working = working[len(lst_match.group(1)) :]

        # 如果是 Markdown 表格行，按单元格切分；否则整行作为单段
        segments: list[tuple[int, str]] = []
        if "|" in working and working.strip().startswith("|"):
            pos = 0
            for part in working.split("|"):
                cell = part.strip()
                if cell:
                    col_start = base_col_offset + pos + part.find(cell) + 1
                    segments.append((col_start, cell))
                pos += len(part) + 1
        else:
            segments.append((base_col_offset + 1, working))

        for seg_col, seg_text in segments:
            cleaned_seg = strip_inline_code(seg_text)
            if not cleaned_seg.strip():
                continue

            # 1. 分号串联检查（semicolon-join）
            if "semicolon-join" not in disabled:
                for semi_match in re.finditer(r"[；;]", cleaned_seg):
                    findings.append(
                        LintFinding(
                            file=filename,
                            line=line_idx,
                            col=seg_col + semi_match.start(),
                            rule="semicolon-join",
                            level=_active_level("semicolon-join", mode),
                            engine="hard",
                            match=semi_match.group(0),
                            message="禁止用分号串联动作或判断；请改为句号拆成竹节短句",
                            suggestion="。",
                        )
                    )

            # 按句末标点切分句子检查句级规则
            for sent_match in SENTENCE_SPLIT_RE.finditer(seg_text):
                sent_raw = sent_match.group(0)
                if not sent_raw.strip():
                    continue
                sent_col = seg_col + sent_match.start()
                sent_clean = strip_inline_code(sent_raw)
                units = count_effective_units(sent_raw)
                if units == 0:
                    continue

                total_chars += units
                total_sentences += 1

                # 2. 句长检查（long-sentence）
                if "long-sentence" not in disabled and units > effective_max_chars:
                    findings.append(
                        LintFinding(
                            file=filename,
                            line=line_idx,
                            col=sent_col,
                            rule="long-sentence",
                            level=_active_level("long-sentence", mode),
                            engine="hard",
                            match=sent_raw.strip()[:28] + ("…" if len(sent_raw.strip()) > 28 else ""),
                            message=(
                                f"单句有效字词数 {units} 超过 {mode} 档上限 {effective_max_chars}；"
                                "请按时间/因果拆成竹节短句"
                            ),
                            suggestion="按“前提 → 动作 → 结果”拆为 2～3 句",
                        )
                    )

                # 3. 左分支长定语“的的不休”检查（de-stacking）
                if "de-stacking" not in disabled:
                    de_positions = [m.start() for m in re.finditer(r"的", sent_clean)]
                    if len(de_positions) >= 3:
                        findings.append(
                            LintFinding(
                                file=filename,
                                line=line_idx,
                                col=sent_col + de_positions[2],
                                rule="de-stacking",
                                level=_active_level("de-stacking", mode),
                                engine="soft",
                                match=sent_raw.strip()[:30] + ("…" if len(sent_raw.strip()) > 30 else ""),
                                message=(
                                    f"单句连用 {len(de_positions)} 个“的”（左分支长定语挂起工作记忆）；"
                                    "请改用汉语竹节流水句（单句“的” ≤ 2 个）"
                                ),
                                suggestion="把“的”字前的条件和动作放出来独立成短句",
                            )
                        )

                # 4. 万能弱动词 + 名词化（nominalization）
                if "nominalization" not in disabled:
                    for pat, hint in NOMINALIZATION_PATTERNS:
                        for m in pat.finditer(sent_clean):
                            findings.append(
                                LintFinding(
                                    file=filename,
                                    line=line_idx,
                                    col=sent_col + m.start(),
                                    rule="nominalization",
                                    level=_active_level("nominalization", mode),
                                    engine="hard",
                                    match=m.group(0),
                                    message=f"发现弱动词名词化（文字的义肢）：{hint}",
                                    suggestion=hint,
                                )
                            )

                # 5. 太极含糊词（taichi-hedge）
                if "taichi-hedge" not in disabled:
                    for pat, hint in TAICHI_PATTERNS:
                        for m in pat.finditer(sent_clean):
                            findings.append(
                                LintFinding(
                                    file=filename,
                                    line=line_idx,
                                    col=sent_col + m.start(),
                                    rule="taichi-hedge",
                                    level=_active_level("taichi-hedge", mode),
                                    engine="hard",
                                    match=m.group(0),
                                    message=f"发现太极含糊表达：{hint}",
                                    suggestion=hint,
                                )
                            )

                # 6. 互联网黑话与伪术语（jargon-buzzword）
                if "jargon-buzzword" not in disabled:
                    for m in BUZZWORD_PATTERN.finditer(sent_clean):
                        findings.append(
                            LintFinding(
                                file=filename,
                                line=line_idx,
                                col=sent_col + m.start(),
                                rule="jargon-buzzword",
                                level=_active_level("jargon-buzzword", mode),
                                engine="hard",
                                match=m.group(0),
                                message=f"禁用空洞黑话/伪术语“{m.group(0)}”；请改写为具体动作或可测量指标",
                                suggestion="替换为具体文件名、行数、耗时或动作动词",
                            )
                        )

                # 7. 零信息套话起手式（empty-phrase）
                if "empty-phrase" not in disabled:
                    for m in EMPTY_PHRASE_PATTERN.finditer(sent_clean):
                        findings.append(
                            LintFinding(
                                file=filename,
                                line=line_idx,
                                col=sent_col + m.start(),
                                rule="empty-phrase",
                                level=_active_level("empty-phrase", mode),
                                engine="hard",
                                match=m.group(0),
                                message=f"零信息套话“{m.group(0)}”；请直接删掉，开门见山写结论或条件",
                                suggestion="删除",
                            )
                        )

                # 8. 欧化介词套壳（translationese-shell）
                if "translationese-shell" not in disabled:
                    for pat, hint in TRANSLATIONESE_PATTERNS:
                        for m in pat.finditer(sent_clean):
                            findings.append(
                                LintFinding(
                                    file=filename,
                                    line=line_idx,
                                    col=sent_col + m.start(),
                                    rule="translationese-shell",
                                    level=_active_level("translationese-shell", mode),
                                    engine="soft",
                                    match=m.group(0),
                                    message=f"欧化介词套壳：{hint}",
                                    suggestion=hint,
                                )
                            )

                # 9. 多动作连词串联（multi-action-chain）
                if "multi-action-chain" not in disabled:
                    for m in MULTI_ACTION_PATTERN.finditer(sent_clean):
                        findings.append(
                            LintFinding(
                                file=filename,
                                line=line_idx,
                                col=sent_col + m.start(),
                                rule="multi-action-chain",
                                level=_active_level("multi-action-chain", mode),
                                engine="hard",
                                match=m.group(0),
                                message="单句串联多个动作与结果；请按竹节流水句拆成多句",
                                suggestion="拆分为独立短句",
                            )
                        )

                # 10. 悬空代词（dangling-pronoun）
                if "dangling-pronoun" not in disabled:
                    for m in DANGLING_PRONOUN_PATTERN.finditer(sent_clean):
                        findings.append(
                            LintFinding(
                                file=filename,
                                line=line_idx,
                                col=sent_col + m.start(),
                                rule="dangling-pronoun",
                                level=_active_level("dangling-pronoun", mode),
                                engine="soft",
                                match=m.group(0),
                                message=f"悬空指代“{m.group(0)}”增加回扫负担；请直接写出具体对象名称",
                                suggestion="替换为具体对象名",
                            )
                        )

                # 11. 含糊数量与范围词（vague-quantifier）
                if "vague-quantifier" not in disabled:
                    for m in VAGUE_QUANTIFIER_PATTERN.finditer(sent_clean):
                        findings.append(
                            LintFinding(
                                file=filename,
                                line=line_idx,
                                col=sent_col + m.start(),
                                rule="vague-quantifier",
                                level=_active_level("vague-quantifier", mode),
                                engine="hard",
                                match=m.group(0),
                                message=f"含糊词“{m.group(0)}”；请替换为准确数字、具体文件名或明确触发条件",
                                suggestion="写出准确数量或具体名称",
                            )
                        )

                # 12. 西化被动结构（passive-voice）
                if "passive-voice" not in disabled:
                    for m in PASSIVE_VOICE_PATTERN.finditer(sent_clean):
                        findings.append(
                            LintFinding(
                                file=filename,
                                line=line_idx,
                                col=sent_col + m.start(),
                                rule="passive-voice",
                                level=_active_level("passive-voice", mode),
                                engine="soft",
                                match=m.group(0),
                                message="西化被动或冗余因果套壳；建议改为“谁 + 真动词 + 宾语”主动句",
                                suggestion="改为主动句",
                            )
                        )

                # 13. 同义轮换扫描（synonym-rotation）
                if "synonym-rotation" not in disabled:
                    for group_name, words in SYNONYM_GROUPS.items():
                        for w in words:
                            idx = sent_clean.find(w)
                            if idx == -1:
                                continue
                            group_seen = seen_synonyms[group_name]
                            if w not in group_seen:
                                group_seen[w] = (line_idx, sent_col + idx)
                                if len(group_seen) >= 2:
                                    first_word = next(iter(group_seen.keys()))
                                    first_line, _ = group_seen[first_word]
                                    findings.append(
                                        LintFinding(
                                            file=filename,
                                            line=line_idx,
                                            col=sent_col + idx,
                                            rule="synonym-rotation",
                                            level=_active_level("synonym-rotation", mode),
                                            engine="hard",
                                            match=w,
                                            message=(
                                                f"同义轮换（{group_name}）：前文第 {first_line} 行已用“{first_word}”，"
                                                f"此处又换用“{w}”；请全篇统一使用同一个词"
                                            ),
                                            suggestion=first_word,
                                        )
                                    )

    hard_count = sum(1 for f in findings if f.level == "advisory-free")
    soft_count = sum(1 for f in findings if f.level == "advisory")
    rate = round((len(findings) / total_chars) * 100.0, 2) if total_chars > 0 else 0.0
    passed = hard_count <= baseline

    summary = LintSummary(
        mode=mode,
        max_chars=effective_max_chars,
        baseline=baseline,
        total_chars=total_chars,
        total_sentences=total_sentences,
        hard_violations=hard_count,
        soft_advisories=soft_count,
        total_findings=len(findings),
        violations_per_100_chars=rate,
        passed=passed,
    )
    return LintReport(schema_version=CONTRACT_VERSION, summary=summary, findings=findings)


def format_human_report(report: LintReport) -> str:
    """将 LintReport 渲染为终端易读的中文检查报告."""
    s = report.summary
    status_tag = "PASS (通过)" if s.passed else "FAIL (未通过)"
    lines = [
        f"[zh-ste-lint] 档位={s.mode} (句长上限≤{s.max_chars}字) | 结果={status_tag}",
        (
            f"统计: 共 {s.total_sentences} 句 / {s.total_chars} 有效字词 | "
            f"硬违规(advisory-free)={s.hard_violations} (基线≤{s.baseline}) | "
            f"软章法提示(advisory)={s.soft_advisories}"
        ),
    ]
    if not report.findings:
        lines.append("✓ 零违规：未检出硬门禁违规或西化病句。")
    else:
        lines.append("-" * 72)
        for f in report.findings:
            badge = "[硬违规]" if f.level == "advisory-free" else "[软提示]"
            lines.append(f"{f.file}:{f.line}:{f.col} {badge} [{f.rule}] 命中: {f.match!r}")
            lines.append(f"  原因: {f.message}")

    if report.jev is not None:
        jv = report.jev
        lines.append(
            f"[Jev 语义复核] status={jv.status} | verdict={jv.verdict} | "
            f"definite={jv.conclusion_definite} | no_taichi={jv.no_taichi_hedging} | "
            f"bamboo_flow={jv.bamboo_flow_natural} | preserved={jv.meaning_preserved}"
        )
        if jv.notes:
            lines.append(f"  说明: {jv.notes}")
    return "\n".join(lines)


def run_selftest() -> int:
    """内置自检断言（验证硬违规、软章法、客观情态豁免、代码块忽略、表格解析与三档句长）."""
    # 1. 典型 AI 病句应同时触发硬门禁与汉语言文学软章法
    bad_sample = (
        "需要注意的是，本次针对网关在缺少凭证的情况下可能会引发异常的相关问题，"
        "已经通过最小侵入式的方式完成了对鉴权逻辑的边界收口；"
        "目前其状态基本上应该已经恢复正常了，后续会再核查并检查若干配置。"
    )
    rep = lint_text(bad_sample, filename="bad.md", mode="report")
    triggered_rules = {f.rule for f in rep.findings}
    expected_rules = {
        "empty-phrase",
        "translationese-shell",
        "de-stacking",
        "jargon-buzzword",
        "nominalization",
        "semicolon-join",
        "long-sentence",
        "taichi-hedge",
        "dangling-pronoun",
        "vague-quantifier",
        "synonym-rotation",
    }
    missing = expected_rules - triggered_rules
    if missing:
        raise RuntimeError(f"Selftest failed: expected rules not triggered: {missing}")
    if rep.summary.passed:
        raise RuntimeError("Selftest failed: bad_sample must fail in report mode")

    # 2. 客观情态（“可能原因”）在受控改写中不应被误判为太极推诿词
    good_modality = "同步请求认证失败。可能原因：访问令牌已过期，或令牌格式错误。请更换访问令牌后重试。"
    rep_mod = lint_text(good_modality, filename="modality.md", mode="report")
    if rep_mod.summary.hard_violations != 0:
        raise RuntimeError(f"Unexpected hard violations: {rep_mod.findings}")

    # 3. Fenced 代码块与行内反引号内的内容必须完全忽略
    fenced_sample = (
        "```python\n"
        "# 需要注意的是，这里对相关配置进行了最小侵入式收口；基本上应该没问题\n"
        "```\n"
        "我修改了 `auth_service_nominalization_check.py` 的登录验证逻辑。单测 6 条全部通过。\n"
    )
    rep_fence = lint_text(fenced_sample, filename="fence.md", mode="report")
    if rep_fence.summary.total_findings != 0:
        raise RuntimeError(f"Code fence/inline code leaked: {rep_fence.findings}")

    # 4. 表格单元格应独立切分检查
    table_sample = (
        "| 模块 | 状态 |\n"
        "| --- | --- |\n"
        "| 鉴权模块 | 已修复空指针，单测 4 条全过。 |\n"
        "| 缓存模块 | 对相关配置进行了收口；基本上应该好了。 |\n"
    )
    rep_table = lint_text(table_sample, filename="table.md", mode="report")
    table_rules = {f.rule for f in rep_table.findings}
    if "nominalization" not in table_rules or "semicolon-join" not in table_rules:
        raise RuntimeError(f"Table cell lint failed: {table_rules}")

    print("✓ zh-ste-lint --selftest: 全部内置自检断言通过 (4/4 groups)。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="zh-ste-lint",
        description="受控中文技术与汇报静态检查器（民机简化中文硬门禁 + 汉语言文学竹节句软处理）",
    )
    parser.add_argument("files", nargs="*", help="待检查的 Markdown 或文本文件（不传则从 stdin 读取）")
    parser.add_argument(
        "--mode",
        choices=VALID_MODES,
        default="report",
        help="运行档位: report(任务汇报≤35字,默认) | procedure(操作指令≤25字) | descriptive(技术说明≤40字)",
    )
    parser.add_argument("--max-chars", type=int, default=None, help="覆盖当前档位的单句有效字词数上限")
    parser.add_argument("--baseline", type=int, default=0, help="允许容忍的硬违规数量上限（默认 0）")
    parser.add_argument("--disable", type=str, default="", help="逗号分隔的禁用规则 ID 列表")
    parser.add_argument("--jev", action="store_true", help="同时运行 Jev（TypeSafe System One）语义复核")
    parser.add_argument("--original", type=str, default=None, help="配合 --jev 使用：改写前的原文文件路径")
    parser.add_argument("--json", action="store_true", help="输出符合 zh-ste100/v1 契约的 JSON")
    parser.add_argument("--list-rules", action="store_true", help="列出全部规则 ID、引擎分类与说明")
    parser.add_argument("--selftest", action="store_true", help="运行内置确定性自检套件")

    args = parser.parse_args(argv)

    if args.selftest:
        return run_selftest()

    if args.list_rules:
        for rid, meta in RULE_CATALOG.items():
            print(f"{rid:22s} [{meta['engine']:4s} | {meta['level']:13s}] {meta['desc']}")
        return 0

    disabled = {r.strip() for r in args.disable.split(",") if r.strip()}
    unknown = disabled - set(RULE_CATALOG.keys())
    if unknown:
        print(f"Error: 未知规则 ID: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2

    all_findings: list[LintFinding] = []
    collected_texts: list[str] = []
    total_chars = 0
    total_sentences = 0
    effective_max = args.max_chars if args.max_chars is not None else MODE_MAX_CHARS[args.mode]

    if not args.files:
        content = sys.stdin.read()
        collected_texts.append(content)
        rep = lint_text(
            content,
            filename="<stdin>",
            mode=args.mode,
            max_chars=effective_max,
            baseline=args.baseline,
            disabled_rules=disabled,
        )
        all_findings.extend(rep.findings)
        total_chars += rep.summary.total_chars
        total_sentences += rep.summary.total_sentences
    else:
        for fpath in args.files:
            p = Path(fpath)
            if not p.is_file():
                print(f"Error: 文件不存在或不可读: {fpath}", file=sys.stderr)
                return 2
            content = p.read_text(encoding="utf-8")
            collected_texts.append(content)
            rep = lint_text(
                content,
                filename=str(fpath),
                mode=args.mode,
                max_chars=effective_max,
                baseline=args.baseline,
                disabled_rules=disabled,
            )
            all_findings.extend(rep.findings)
            total_chars += rep.summary.total_chars
            total_sentences += rep.summary.total_sentences

    jev_verdict: JevSemanticVerdict | None = None
    if args.jev:
        jev_script = Path(__file__).resolve().parent / "zh-ste-jev.py"
        spec = importlib.util.spec_from_file_location("zh_ste_jev", jev_script)
        if spec is not None and spec.loader is not None:
            jev_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(jev_mod)
            orig_text = Path(args.original).read_text(encoding="utf-8") if args.original else None
            jev_verdict = jev_mod.call_jev_gateway(
                "\n\n".join(collected_texts),
                mode=args.mode,
                original_text=orig_text,
            )

    hard_count = sum(1 for f in all_findings if f.level == "advisory-free")
    soft_count = sum(1 for f in all_findings if f.level == "advisory")
    rate = round((len(all_findings) / total_chars) * 100.0, 2) if total_chars > 0 else 0.0
    jev_ok = jev_verdict is None or jev_verdict.verdict in ("pass", "skipped")
    passed = (hard_count <= args.baseline) and jev_ok

    combined_report = LintReport(
        schema_version=CONTRACT_VERSION,
        summary=LintSummary(
            mode=args.mode,
            max_chars=effective_max,
            baseline=args.baseline,
            total_chars=total_chars,
            total_sentences=total_sentences,
            hard_violations=hard_count,
            soft_advisories=soft_count,
            total_findings=len(all_findings),
            violations_per_100_chars=rate,
            passed=passed,
        ),
        findings=all_findings,
        jev=jev_verdict,
    )

    if args.json:
        print(json.dumps(combined_report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_human_report(combined_report))

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
