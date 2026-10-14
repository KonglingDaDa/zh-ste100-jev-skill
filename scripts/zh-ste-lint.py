#!/usr/bin/env python3
"""zh-ste-lint.py — 零依赖受控中文写作与汇报静态检查器（「篇法 · 句法 · 字法」三层九律）.

按中国传统文章学自上而下分三层治理，不搞碎片化规则大杂烩：
- 第一层【篇法】（王小波说理章法）：
  1. `taichi-hedge`    — 禁太极推诿词（应该已经/基本上/大概/理论上/似乎/在一定程度上）
  2. `overclaim`       — 禁空话铺垫、自夸与无证据打包票（需要注意的是/优雅的重构/已彻底解决/完全没有问题）
- 第二层【句法】（余光中《论中文的常态与变态》+ 现代汉语竹节流水句 + 民机句长）：
  3. `bamboo-length`   — 竹节断句，一事一顿（procedure≤25字 / report≤35字 / descriptive≤40字，禁分号与长连词串动作）
  4. `de-stacking`     — 禁左分支 ≥3 个“的”字长定语与“名词+的+动作”（接口的调用失败）西化主宾语
  5. `western-shell`   — 禁欧化介词与冗余被动套壳（基于…下/针对…问题/对于…而言/由于…所导致的）
- 第三层【字法】（中国民机《简化语言规范》+ ASD-STE100 受控词表）：
  6. `weak-verb`       — 真动词立骨，禁弱动词义肢（对…进行/作出/加以/予以/完成对…的）
  7. `buzzword-slang`  — 留真术语；上禁互联网黑话与“具备…性”伪术语，下禁“抽风/炸了/挂了/太蠢”粗俗俚语
  8. `vague-reference` — 具体点名与数字，禁悬空代词（该/其/此/上述）与含糊量词（相关/若干/一些/适当）
  9. `synonym-rotation`— 同篇一义一词，禁止同组近义词轮换
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

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

# 「篇法 · 句法 · 字法」三层九律主目录
RULE_CATALOG: dict[str, dict[str, str]] = {
    # 第一层：篇法（谋篇与诚实）
    "taichi-hedge": {
        "layer": "篇法",
        "engine": "hard",
        "level": "advisory-free",
        "desc": "第1律[篇法] 禁太极推诿词（应该已经/基本上/大概/理论上/似乎/在一定程度上），须给死结论或明写未测边界",
    },
    "overclaim": {
        "layer": "篇法",
        "engine": "hard",
        "level": "advisory-free",
        "desc": "第2律[篇法] 禁空话起手式、自夸与无证据打包票（需要注意的是/优雅的重构/已彻底解决/完全没有问题）",
    },
    # 第二层：句法（竹节与语序）
    "bamboo-length": {
        "layer": "句法",
        "engine": "hard",
        "level": "advisory-free",
        "desc": "第3律[句法] 竹节断句，一事一顿（procedure≤25/report≤35/descriptive≤40字，禁分号与长连词串动作）",
    },
    "de-stacking": {
        "layer": "句法",
        "engine": "soft",
        "level": "advisory",
        "desc": "第4律[句法] 禁左分支≥3个“的”长定语与西化“名词+的+动词”主宾语（如“接口的调用失败”→“调用接口失败”）",
    },
    "western-shell": {
        "layer": "句法",
        "engine": "soft",
        "level": "advisory-free",
        "desc": "第5律[句法] 剥除欧化介词与被动套壳（基于…下/针对…问题/对于…而言/由于…所导致的）",
    },
    # 第三层：字法（选词与定名）
    "weak-verb": {
        "layer": "字法",
        "engine": "hard",
        "level": "advisory-free",
        "desc": "第6律[字法] 真动词立骨，砍掉弱动词义肢（对…进行/作出/加以/予以/完成对…的）",
    },
    "buzzword-slang": {
        "layer": "字法",
        "engine": "hard",
        "level": "advisory-free",
        "desc": "第7律[字法] 留真术语；上禁黑话与“具备…性”伪术语，下禁“抽风/炸了/数据库挂了/太蠢”粗俗俚语",
    },
    "vague-reference": {
        "layer": "字法",
        "engine": "hard",
        "level": "advisory",
        "desc": "第8律[字法] 具体点名与准确数字，禁悬空代词（该/其/此/上述）与含糊量词（相关/若干/一些/适当）",
    },
    "synonym-rotation": {
        "layer": "字法",
        "engine": "hard",
        "level": "advisory-free",
        "desc": "第9律[字法] 同篇一义一词，禁止同一文件内轮换同组近义词",
    },
}

# 兼容旧版细粒度规则名的别名映射（方便 --disable 向后兼容）
LEGACY_RULE_ALIASES: dict[str, str] = {
    "nominalization": "weak-verb",
    "empty-phrase": "overclaim",
    "overclaim-absolutism": "overclaim",
    "semicolon-join": "bamboo-length",
    "multi-action-chain": "bamboo-length",
    "long-sentence": "bamboo-length",
    "noun-phrase-subject": "de-stacking",
    "translationese-shell": "western-shell",
    "passive-voice": "western-shell",
    "jargon-buzzword": "buzzword-slang",
    "pseudo-abstract-noun": "buzzword-slang",
    "colloquial-slang": "buzzword-slang",
    "dangling-pronoun": "vague-reference",
    "vague-quantifier": "vague-reference",
}

# ==================== 第一层：篇法正则 ====================
TAICHI_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?:应该已经|应该基本|基本上应该|基本上已经|大体上已经|差不多已经|理论上应该|理论上已经)"),
        "第1律[篇法] 删掉太极词，明确写“已验证通过”或“代码已改，尚未运行验证”",
    ),
    (
        re.compile(r"(?:在一定程度上|在某种程度上|从某种意义上|某种意义上来说|从某种角度来看)"),
        "第1律[篇法] 删掉零信息度词，写出具体影响范围或数字",
    ),
    (
        re.compile(r"(?:基本上|大体上|似乎|好像|看起来|不出意外的话|大概率是|大概是)"),
        "第1律[篇法] 勿打太极：已查实的给死结论；未查实的写“日志显示 A，尚待确认 B”",
    ),
]

OVERCLAIM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?:需要注意的是|值得一提的是|需要指出的是|显而易见|不难看出|众所周知|总而言之|综上所述)"),
        "第2律[篇法] 零信息套话起手式；请直接删掉，首句开门见山写结论或条件",
    ),
    (
        re.compile(
            r"(?:(?:已|已经)?彻底解决|完全没有问题|绝对不会出问题|百分百没问题|毫无疑问|"
            r"优雅地解决|优雅的重构|显著提升了(?:模块|系统|代码)?的鲁棒性)"
        ),
        "第2律[篇法] 禁用自夸或无保留打包票；请客观写明具体改动、已验证范围与待观察限制",
    ),
]

# ==================== 第二层：句法正则 ====================
MULTI_ACTION_PATTERN = re.compile(r"(?:并且|同时)[^。！？\n]{1,25}?(?:从而|进而|并最终)")

NOUN_PHRASE_SUBJECT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"([\u4e00-\u9fff]{2,8})的(?:再度|再次|频繁|异常|自动|手动)?(调用|加载|初始化|执行|连接|解析|同步|"
            r"更新|创建|删除|部署|坍方|选购|继续|中断|失败|超时)(?:失败|成功|中断|异常|完成)?"
            r"(?=(?:，)?(?:是|导致|引起|造成|只好|需要|使|会))"
        ),
        r"第4律[句法] 西化名词化主语：按余光中“中文以小句做主语”改为“\1\2…”或“\2\1…”",
    ),
    (
        re.compile(
            r"(?:由于|因为|随着|反对|杯葛|委托|完成)([\u4e00-\u9fff]{2,8})的"
            r"(调用|加载|初始化|执行|连接|解析|同步|更新|创建|删除|部署|坍方|选购|继续)(?:失败|异常|超时|中断)?"
        ),
        r"第4律[句法] 西化名词词组：去掉“的”，改用动宾/主谓小句（如“调用\1”或“\1\2”）",
    ),
]

WESTERN_SHELL_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"(?:基于|在)[^，。；！？\n]{2,25}?的(?:情况|前提|背景|基础)之?下"),
        "advisory-free",
        "第5律[句法] 剥掉“基于/在…的情况下”欧化外壳，直接写前提短句",
    ),
    (
        re.compile(r"针对[^，。；！？\n]{2,30}?的(?:相关)?(?:问题|情况|风险|现象|异常)"),
        "advisory-free",
        "第5律[句法] 剥掉“针对…的问题”欧化外壳，拆为“X 出现 Y。我做了 Z。”",
    ),
    (
        re.compile(r"对于[^，。；！？\n]{1,15}?而言"),
        "advisory-free",
        "第5律[句法] 删掉“对于…而言”，直接用对象做主语",
    ),
    (
        re.compile(r"通过[^，。；！？\n]{2,20}?的方式(?:来|去)?"),
        "advisory-free",
        "第5律[句法] 删掉“通过…的方式”，直接写具体动作",
    ),
    (
        re.compile(
            r"(?:(?:由于|因为)[^，。；！？\n]{2,25}?所(?:导致|引起|造成)的?|"
            r"由[^，。；！？\n]{1,12}?所[\u4e00-\u9fff]{2,4}|"
            r"被(?:意外|成功|自动|重新|完整)[\u4e00-\u9fff]{2,4})"
        ),
        "advisory",
        "第5律[句法] 西化被动或冗余因果套壳；建议改为“谁 + 真动词 + 宾语”主动句",
    ),
]

# ==================== 第三层：字法正则 ====================
WEAK_VERB_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?:对|针对|将|把)[^，。；！？\n]{1,18}?(?:进行|加以|予以)了?([\u4e00-\u9fff]{2,4})"),
        r"第6律[字法] 删掉弱动词“对…进行/加以/予以”，直接写“\1…”",
    ),
    (
        re.compile(r"(?:进行|加以|予以|开展)了?(?:初步|全面|深入|详细|全方位|自动|手动|进一步)?([\u4e00-\u9fff]{2,4})"),
        r"第6律[字法] 删掉弱动词，直接用真动词“\1”",
    ),
    (
        re.compile(r"作出(?:了)?(?:重大|热烈|明确|相应|进一步)?([\u4e00-\u9fff]{2,4})"),
        r"第6律[字法] 删掉“作出”，直接写“\1”",
    ),
    (
        re.compile(r"(?:完成|实现)了对[^，。；！？\n]{1,18}?的([\u4e00-\u9fff]{2,6})"),
        r"第6律[字法] 删掉“完成/实现了对…的”，直接写“已\1…”",
    ),
    (
        re.compile(r"(?:做|搞|弄)(?:了)?(?:一下|一些|一个)(?:适当|简单|初步)?的?([\u4e00-\u9fff]{2,4})"),
        r"第6律[字法] 删掉“做/搞一下”，直接写具体动作“\1”",
    ),
    (
        re.compile(r"在(?:处理|执行|解析|调用)[^，。；！？\n]{1,12}?的过程中"),
        "第6律[字法] 删掉“在…的过程中”，改为“处理/解析…时”",
    ),
]

BUZZWORD_SLANG_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(?:赋能|抓手|组合拳|闭环|对齐颗粒度|颗粒度|拉齐水位|水位拉齐|拉齐|边界收口|收口|"
            r"最小侵入式|零侵入|心智负担|感知度|无缝|极致|丝滑|一站式|全方位|颠覆性|生态化反)"
        ),
        "第7律[字法] 禁用空洞黑话；请替换为具体文件名、行数、耗时或真动词",
    ),
    (
        re.compile(
            r"(?:具有|具备|拥有|呈现出|体现出|展现出)(?:了)?(?:较高|很高|良好|较好|极高|一定|更强|颇高)?的?"
            r"([\u4e00-\u9fff]{1,4}(?:性|度))"
        ),
        r"第7律[字法] 余光中批“伪术语”：勿写“具有/具备…\1”，直接写出具体可测表现",
    ),
    (
        re.compile(r"(?:实现|完成)了对?[\u4e00-\u9fff]{2,10}?的([\u4e00-\u9fff]{2,4}化)"),
        r"第7律[字法] 余光中批“抽象名词化”：勿写“实现了…的\1”，直接写具体改动步骤",
    ),
    (
        re.compile(
            r"(?:抽风|一路往上炸|迟早会炸|原地爆炸|炸了|抗造|太蠢|蠢透了|"
            r"(?:数据库|服务|节点|接口|系统|机器)挂了)"
        ),
        "第7律[字法] 王小波工程规范：禁用粗俗含糊俚语，请说明具体系统行为（如“请求超时/进程退出/内存增加”）",
    ),
]

VAGUE_REFERENCE_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(
            r"(?:若干个?|一些|一部分|某几个|相关(?:的)?(?:配置|文件|模块|接口|记录|异常|问题|设置)|"
            r"相应(?:的)?(?:配置|修改|处理)|适当(?:的)?(?:调整|优化|修改)|酌情|必要的时候)"
        ),
        "hard",
        "第8律[字法] 含糊数量或范围词；请替换为准确数字、具体文件名或明确触发条件",
    ),
    (
        re.compile(
            r"(?:该(?:文件|配置|服务|模块|函数|接口|问题|错误|异常|记录|任务|项目|脚本|参数|设置)|"
            r"对其|将其|使其|当其|若其|其(?:状态|原因|结果|配置)|上述|前述)"
        ),
        "soft",
        "第8律[字法] 悬空代词增加回扫负担；请直接写出具体对象名称",
    ),
]

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
    return len(CJK_CHAR_RE.findall(cleaned)) + len(ASCII_WORD_RE.findall(cleaned))


def normalize_disabled_rules(raw_disabled: set[str] | None) -> set[str]:
    """将用户传入的规则 ID（含旧版细粒度别名）统一归一化为三层九律 ID."""
    if not raw_disabled:
        return set()
    return {LEGACY_RULE_ALIASES.get(r, r) for r in raw_disabled}


def _active_level(rule_id: str, mode: str, sub_kind: str = "hard") -> str:
    if rule_id == "taichi-hedge":
        return "advisory-free" if mode in ("report", "procedure") else "advisory"
    if rule_id == "vague-reference":
        if sub_kind == "soft":
            return "advisory"
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
    """按「篇法 · 句法 · 字法」三层九律扫描输入文本，返回符合契约的 LintReport."""
    if mode not in VALID_MODES:
        raise ValueError(f"Unsupported mode {mode!r}; choose from {VALID_MODES}")

    effective_max_chars = max_chars if max_chars is not None else MODE_MAX_CHARS[mode]
    disabled = normalize_disabled_rules(disabled_rules)
    findings: list[LintFinding] = []

    in_fence = False
    total_chars = 0
    total_sentences = 0
    seen_synonyms: dict[str, dict[str, tuple[int, int]]] = {g: {} for g in SYNONYM_GROUPS}

    for line_idx, raw_line in enumerate(text.splitlines(), start=1):
        if CODE_FENCE_RE.match(raw_line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        stripped = raw_line.strip()
        if not stripped or TABLE_SEP_RE.match(raw_line) or HEADING_RE.match(raw_line):
            continue

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

            # 第 3 律 [句法] bamboo-length：禁止分号串联
            if "bamboo-length" not in disabled:
                for semi_match in re.finditer(r"[；;]", cleaned_seg):
                    findings.append(
                        LintFinding(
                            file=filename,
                            line=line_idx,
                            col=seg_col + semi_match.start(),
                            rule="bamboo-length",
                            level="advisory-free",
                            engine="hard",
                            match=semi_match.group(0),
                            message="第3律[句法] 禁止用分号串联动作或判断；请改为句号拆成竹节短句",
                            suggestion="。",
                        )
                    )

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

                # ==================== 第一层：篇法 ====================
                # 第 1 律：taichi-hedge
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
                                    message=hint,
                                    suggestion=hint,
                                )
                            )

                # 第 2 律：overclaim
                if "overclaim" not in disabled:
                    for pat, hint in OVERCLAIM_PATTERNS:
                        for m in pat.finditer(sent_clean):
                            findings.append(
                                LintFinding(
                                    file=filename,
                                    line=line_idx,
                                    col=sent_col + m.start(),
                                    rule="overclaim",
                                    level="advisory-free",
                                    engine="hard",
                                    match=m.group(0),
                                    message=hint,
                                    suggestion="交代具体改动、已验证范围与未知边界",
                                )
                            )

                # ==================== 第二层：句法 ====================
                # 第 3 律：bamboo-length（句长 + 多动作连词串联）
                if "bamboo-length" not in disabled:
                    if units > effective_max_chars:
                        findings.append(
                            LintFinding(
                                file=filename,
                                line=line_idx,
                                col=sent_col,
                                rule="bamboo-length",
                                level="advisory-free",
                                engine="hard",
                                match=sent_raw.strip()[:28] + ("…" if len(sent_raw.strip()) > 28 else ""),
                                message=(
                                    f"第3律[句法] 单句有效字词数 {units} 超过 {mode} 档上限 {effective_max_chars}；"
                                    "请按竹节流水句拆为 2～3 句"
                                ),
                                suggestion="一事一顿拆成短句",
                            )
                        )
                    for m in MULTI_ACTION_PATTERN.finditer(sent_clean):
                        findings.append(
                            LintFinding(
                                file=filename,
                                line=line_idx,
                                col=sent_col + m.start(),
                                rule="bamboo-length",
                                level="advisory-free",
                                engine="hard",
                                match=m.group(0),
                                message="第3律[句法] 单句用“并且/同时…从而”串联多个动作；请按竹节句拆开",
                                suggestion="拆分为独立短句",
                            )
                        )

                # 第 4 律：de-stacking（≥3个“的”长定语 + 西化“名词的动作”主宾语）
                if "de-stacking" not in disabled:
                    de_positions = [m.start() for m in re.finditer(r"的", sent_clean)]
                    if len(de_positions) >= 3:
                        findings.append(
                            LintFinding(
                                file=filename,
                                line=line_idx,
                                col=sent_col + de_positions[2],
                                rule="de-stacking",
                                level="advisory",
                                engine="soft",
                                match=sent_raw.strip()[:30] + ("…" if len(sent_raw.strip()) > 30 else ""),
                                message=(
                                    f"第4律[句法] 单句连用 {len(de_positions)} 个“的”（左分支长定语挂起工作记忆）；"
                                    "请改用竹节流水句（单句“的” ≤ 2 个）"
                                ),
                                suggestion="把“的”字前的条件和动作提出来独立成句",
                            )
                        )
                    for pat, hint in NOUN_PHRASE_SUBJECT_PATTERNS:
                        for m in pat.finditer(sent_clean):
                            findings.append(
                                LintFinding(
                                    file=filename,
                                    line=line_idx,
                                    col=sent_col + m.start(),
                                    rule="de-stacking",
                                    level="advisory",
                                    engine="soft",
                                    match=m.group(0),
                                    message=hint,
                                    suggestion="改为动宾或主谓小句",
                                )
                            )

                # 第 5 律：western-shell（欧化介词壳 + 冗余被动壳）
                if "western-shell" not in disabled:
                    for pat, sev, hint in WESTERN_SHELL_PATTERNS:
                        for m in pat.finditer(sent_clean):
                            findings.append(
                                LintFinding(
                                    file=filename,
                                    line=line_idx,
                                    col=sent_col + m.start(),
                                    rule="western-shell",
                                    level=sev,
                                    engine="soft",
                                    match=m.group(0),
                                    message=hint,
                                    suggestion="剥除欧化套壳，直接写主谓宾短句",
                                )
                            )

                # ==================== 第三层：字法 ====================
                # 第 6 律：weak-verb（真动词立骨，砍掉弱动词义肢）
                if "weak-verb" not in disabled:
                    for pat, hint in WEAK_VERB_PATTERNS:
                        for m in pat.finditer(sent_clean):
                            findings.append(
                                LintFinding(
                                    file=filename,
                                    line=line_idx,
                                    col=sent_col + m.start(),
                                    rule="weak-verb",
                                    level="advisory-free",
                                    engine="hard",
                                    match=m.group(0),
                                    message=hint,
                                    suggestion=hint,
                                )
                            )

                # 第 7 律：buzzword-slang（禁黑话、伪度词与粗俗俚语）
                if "buzzword-slang" not in disabled:
                    for pat, hint in BUZZWORD_SLANG_PATTERNS:
                        for m in pat.finditer(sent_clean):
                            findings.append(
                                LintFinding(
                                    file=filename,
                                    line=line_idx,
                                    col=sent_col + m.start(),
                                    rule="buzzword-slang",
                                    level="advisory-free",
                                    engine="hard",
                                    match=m.group(0),
                                    message=hint,
                                    suggestion="改用准确工程术语、具体动作或实测数字",
                                )
                            )

                # 第 8 律：vague-reference（禁悬空代词与含糊量词）
                if "vague-reference" not in disabled:
                    for pat, sub_kind, hint in VAGUE_REFERENCE_PATTERNS:
                        for m in pat.finditer(sent_clean):
                            findings.append(
                                LintFinding(
                                    file=filename,
                                    line=line_idx,
                                    col=sent_col + m.start(),
                                    rule="vague-reference",
                                    level=_active_level("vague-reference", mode, sub_kind),
                                    engine="hard" if sub_kind == "hard" else "soft",
                                    match=m.group(0),
                                    message=hint,
                                    suggestion="写出具体对象名称或准确数量",
                                )
                            )

                # 第 9 律：synonym-rotation（同篇一义一词）
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
                                            level="advisory-free",
                                            engine="hard",
                                            match=w,
                                            message=(
                                                f"第9律[字法] 同义轮换（{group_name}）：第 {first_line} 行已用“{first_word}”，"
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
    """将 LintReport 渲染为终端易读的三层九律检查报告."""
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
        lines.append("✓ 零违规：篇法、句法、字法三层九律全部通过。")
    else:
        lines.append("-" * 72)
        for f in report.findings:
            badge = "[硬违规]" if f.level == "advisory-free" else "[软提示]"
            layer = RULE_CATALOG.get(f.rule, {}).get("layer", "章法")
            lines.append(f"{f.file}:{f.line}:{f.col} {badge} [{layer}:{f.rule}] 命中: {f.match!r}")
            lines.append(f"  原因: {f.message}")

    if report.jev is not None:
        jv = report.jev
        lines.append(
            f"[Jev 篇法语义终审] status={jv.status} | verdict={jv.verdict} | "
            f"definite={jv.conclusion_definite} | no_taichi={jv.no_taichi_hedging} | "
            f"bamboo_flow={jv.bamboo_flow_natural} | preserved={jv.meaning_preserved}"
        )
        if jv.notes:
            lines.append(f"  说明: {jv.notes}")
    return "\n".join(lines)


def run_selftest() -> int:
    """内置自检断言（验证「篇法 · 句法 · 字法」三层九律全部触发、情态豁免、代码块忽略与表格解析）."""
    bad_sample = (
        "需要注意的是，本次针对网关在缺少凭证的情况下可能会引发异常的相关问题，"
        "由于接口的调用失败导致了数据库挂了，已经通过最小侵入式的方式完成了对鉴权逻辑的边界收口；"
        "目前其状态基本上应该已经彻底解决且具备良好的可用性，后续会再核查并检查若干配置。"
    )
    rep = lint_text(bad_sample, filename="bad.md", mode="report")
    triggered_rules = {f.rule for f in rep.findings}
    expected_rules = set(RULE_CATALOG.keys())
    missing = expected_rules - triggered_rules
    if missing:
        raise RuntimeError(f"Selftest failed: expected 9 rules not triggered: {missing}")
    if rep.summary.passed:
        raise RuntimeError("Selftest failed: bad_sample must fail in report mode")

    good_modality = "同步请求认证失败。可能原因：访问令牌已过期，或令牌格式错误。请更换访问令牌后重试。"
    rep_mod = lint_text(good_modality, filename="modality.md", mode="report")
    if rep_mod.summary.hard_violations != 0:
        raise RuntimeError(f"Unexpected hard violations: {rep_mod.findings}")

    fenced_sample = (
        "```python\n"
        "# 需要注意的是，这里对相关配置进行了最小侵入式收口；基本上应该没问题\n"
        "```\n"
        "我修改了 `auth_service_nominalization_check.py` 的登录验证逻辑。单测 6 条全部通过。\n"
    )
    rep_fence = lint_text(fenced_sample, filename="fence.md", mode="report")
    if rep_fence.summary.total_findings != 0:
        raise RuntimeError(f"Code fence/inline code leaked: {rep_fence.findings}")

    table_sample = (
        "| 模块 | 状态 |\n"
        "| --- | --- |\n"
        "| 鉴权模块 | 已修复空指针，单测 4 条全过。 |\n"
        "| 缓存模块 | 对相关配置进行了收口；基本上应该好了。 |\n"
    )
    rep_table = lint_text(table_sample, filename="table.md", mode="report")
    table_rules = {f.rule for f in rep_table.findings}
    if "weak-verb" not in table_rules or "bamboo-length" not in table_rules:
        raise RuntimeError(f"Table cell lint failed: {table_rules}")

    print("✓ zh-ste-lint --selftest: 「篇法 · 句法 · 字法」三层九律自检全部通过 (9/9 rules, 4/4 groups)。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="zh-ste-lint",
        description="受控中文技术与汇报静态检查器（「篇法 · 句法 · 字法」三层九律）",
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
    parser.add_argument("--jev", action="store_true", help="同时运行 Jev（TypeSafe System One）篇法语义终审")
    parser.add_argument("--original", type=str, default=None, help="配合 --jev 使用：改写前的原文文件路径")
    parser.add_argument("--json", action="store_true", help="输出符合 zh-ste100/v1 契约的 JSON")
    parser.add_argument("--list-rules", action="store_true", help="按「篇法 · 句法 · 字法」列出三层九律")
    parser.add_argument("--selftest", action="store_true", help="运行内置确定性自检套件")

    args = parser.parse_args(argv)

    if args.selftest:
        return run_selftest()

    if args.list_rules:
        for rid, meta in RULE_CATALOG.items():
            print(f"[{meta['layer']}] {rid:18s} ({meta['level']:13s}) {meta['desc']}")
        return 0

    raw_disabled = {r.strip() for r in args.disable.split(",") if r.strip()}
    valid_names = set(RULE_CATALOG.keys()) | set(LEGACY_RULE_ALIASES.keys())
    unknown = raw_disabled - valid_names
    if unknown:
        print(f"Error: 未知规则 ID: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    disabled = normalize_disabled_rules(raw_disabled)

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
