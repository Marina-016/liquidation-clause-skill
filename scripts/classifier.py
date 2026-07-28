#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一清盘条款分类器。

三种类型定义:
  类型1: 20日披露 + 60日向证监会报告+提方案+召开持有人大会(无时限)
  类型2: 20日披露 + 60日内10个工作日报告 + 6个月内召开持有人大会
  类型3: 20日披露 + 50日直接终止 + 不需召开持有人大会

关键区分:
  T1 vs T2: 是否同时存在报告和大会时限
  T3: 50日 + 自动终止 + 不需召开大会(非60日)
"""

import re


VALID_STAGES = {1, 2, 3}
OCR_SEPARATORS_RE = re.compile(r"[\u200b\u200c\u200d\ufeff·•∙⋅_＿\-‐‑‒–—]+")
HARD_BOUNDARY_RE = re.compile(r"[。！？!?]")


def _normalize_text(text: str, stage: int) -> str:
    """Normalize PDF extraction noise while preserving clause punctuation."""
    cleaned = re.sub(r"\s+", "", text or "")
    if stage == 3:
        cleaned = OCR_SEPARATORS_RE.sub("", cleaned)
    return cleaned


def _number_pattern(arabic: str, chinese: str, stage: int) -> str:
    parts = [arabic]
    if stage >= 2:
        parts.append(chinese)
    return rf"(?:{'|'.join(parts)})"


def _working_day_pattern(number_pattern: str, stage: int) -> str:
    suffix = r"个工作日" if stage < 3 else r"个?工作日"
    return rf"{number_pattern}{suffix}"


def _is_linked(
    text: str, first_pattern: str, second_pattern: str, max_gap: int = 80
) -> bool:
    """Return whether the second phrase follows the first within one clause."""
    for first in re.finditer(first_pattern, text):
        tail = text[first.end() : first.end() + max_gap]
        boundary = HARD_BOUNDARY_RE.search(tail)
        if boundary:
            tail = tail[: boundary.start()]
        if re.search(second_pattern, tail):
            return True
    return False


def _bounded_window(text: str, start: int, max_len: int) -> str:
    window = text[start : start + max_len]
    boundary = HARD_BOUNDARY_RE.search(window)
    return window[: boundary.start()] if boundary else window


def _sentence_around(text: str, start: int, end: int) -> str:
    previous = list(HARD_BOUNDARY_RE.finditer(text, 0, start))
    sentence_start = previous[-1].end() if previous else 0
    following = HARD_BOUNDARY_RE.search(text, end)
    sentence_end = following.start() if following else len(text)
    return text[sentence_start:sentence_end]


def _features_for_context(context: str, stage: int) -> dict:
    day_20 = _working_day_pattern(
        _number_pattern(r"[2２][0０]", "二十", stage), stage
    )
    day_50 = _working_day_pattern(
        _number_pattern(r"[5５][0０]", "五十", stage), stage
    )
    day_60 = _working_day_pattern(
        _number_pattern(r"[6６][0０]", "六十", stage), stage
    )
    people_200 = (
        _number_pattern(r"[2２][0０][0０]", "二百", stage) + r"人"
    )
    amount_5000 = (
        _number_pattern(r"[5５](?:[,，]?[0０]{3})", "五千", stage) + r"万元?"
    )
    day_10 = _working_day_pattern(
        _number_pattern(r"[1１][0０]", "十", stage), stage
    ) + r"内"
    month_6 = _number_pattern(r"[6６]", "六", stage) + r"个月内"

    regulator = r"(?:中国证监会|中国证券监督管理委员会|证监会|CSRC)"
    report = rf"(?:报告(?:至|给)?{regulator}|(?:向|报送至)?{regulator}(?:报告|报送))"
    solution = r"(?:提出|制定)(?:相应的?)?(?:解决|处置|应对)方案"
    meeting = r"(?:召集|召开)(?:本?基金)?份额持有人大会"
    no_meeting = r"(?:不需|无需|不需要|无须)召开(?:本?基金)?份额持有人大会"
    auto_termination = (
        r"(?:(?:本?基金合同).{0,12}(?:(?:自动|直接|应当)?终止|进入清算程序)|"
        r"(?:自动|直接|应当).{0,12}(?:本?基金合同)?终止)"
    )

    features = {
        "anchor": True,
        "has_20": bool(re.search(day_20, context)),
        "has_50": bool(re.search(day_50, context)),
        "has_60": bool(re.search(day_60, context)),
        "has_200": bool(re.search(people_200, context)),
        "has_5000": bool(re.search(amount_5000, context)),
        "has_6month": bool(re.search(month_6, context)),
        "has_10days": bool(re.search(day_10, context)),
        "has_report": bool(re.search(report, context)),
        "has_solution": bool(re.search(solution, context)),
        "has_meeting": bool(re.search(meeting, context)),
        "has_no_meeting": bool(re.search(no_meeting, context)),
        "has_auto_termination": bool(re.search(auto_termination, context)),
        "stage": stage,
    }

    type_1_chain = False
    type_2_chain = False
    type_3_chain = False

    for trigger in re.finditer(day_60, context):
        window = _bounded_window(context, trigger.start(), 350)
        linked_report_deadline = _is_linked(window, day_10, report)
        linked_meeting_deadline = _is_linked(window, month_6, meeting)

        if linked_report_deadline and linked_meeting_deadline:
            type_2_chain = True

        if (
            _is_linked(window, report, solution, max_gap=120)
            and _is_linked(window, solution, meeting, max_gap=180)
            and not linked_report_deadline
            and not linked_meeting_deadline
        ):
            type_1_chain = True

    for trigger in re.finditer(day_50, context):
        window = _bounded_window(context, trigger.start(), 250)
        if (
            re.search(auto_termination, window)
            and re.search(no_meeting, window)
            and (
                _is_linked(window, auto_termination, no_meeting, max_gap=120)
                or _is_linked(window, no_meeting, auto_termination, max_gap=120)
            )
        ):
            type_3_chain = True

    features["type_1_chain"] = type_1_chain
    features["type_2_chain"] = type_2_chain
    features["type_3_chain"] = type_3_chain
    return features


def classify(text: str, stage: int = 1) -> tuple:
    """
    从 PDF 提取的全文定位清盘条款并分类。

    Args:
        text: PDF 全文 (可以含空白，函数内会清洗)
        stage: 阶段号(1/2/3)，决定匹配宽松度

    Returns:
        (类型标签, 条款原文片段, 判定细节dict)
        类型标签: "类型1: 备案" | "类型2: 备案+6个月大会" | "类型3: 自动触发终止" | None
    """
    if stage not in VALID_STAGES:
        raise ValueError("stage must be 1, 2, or 3")

    text_clean = _normalize_text(text, stage)
    amount_5000 = (
        _number_pattern(r"[5５](?:[,，]?[0０]{3})", "五千", stage) + r"万元?"
    )
    people_200 = (
        _number_pattern(r"[2２][0０][0０]", "二百", stage) + r"人"
    )

    day_20 = _working_day_pattern(
        _number_pattern(r"[2２][0０]", "二十", stage), stage
    )

    # Evaluate every valid amount anchor instead of trusting the first occurrence.
    contexts = []
    for match in re.finditer(amount_5000, text_clean):
        start = max(0, match.start() - 500)
        end = min(len(text_clean), match.end() + 900)
        base_clause = _sentence_around(text_clean, match.start(), match.end())
        if not (
            "份额持有人" in base_clause
            and re.search(people_200, base_clause)
            and re.search(day_20, base_clause)
        ):
            continue
        contexts.append(text_clean[start:end])

    if not contexts:
        return (None, "", {"anchor": False, "stage": stage})

    first_detail = None
    for context in contexts:
        detail = _features_for_context(context, stage)
        if first_detail is None:
            first_detail = detail

        has_base_threshold = (
            detail["has_20"] and detail["has_200"] and detail["has_5000"]
        )
        if not has_base_threshold:
            continue

        if detail["type_3_chain"]:
            return ("类型3: 自动触发终止", text_preview(context), detail)
        if detail["type_2_chain"]:
            return ("类型2: 备案+6个月大会", text_preview(context), detail)
        if detail["type_1_chain"]:
            return ("类型1: 备案", text_preview(context), detail)

    return (None, text_preview(contexts[0]), first_detail)


def text_preview(text: str, max_len: int = 800) -> str:
    """截取条款可读片段"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"
