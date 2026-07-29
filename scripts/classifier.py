#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一清盘条款分类器。

三种类型定义:
  类型1: 20日披露 + 60日向证监会报告+提方案+召开持有人大会(无时限)
  类型2: 20日披露 + 60日内10个工作日报告 + 6个月内召开持有人大会
  类型3: 20日披露 + 50日或60日直接终止 + 不需召开持有人大会

关键区分:
  T1 vs T2: 是否同时存在报告和大会时限
  T3: 50日或60日 + 终止/清算 + 不需召开大会
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
    artifact_gap = "" if stage == 1 else r".{0,80}?"
    return rf"{number_pattern}(?![0０]){artifact_gap}{suffix}"


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
    deadline_number_stage = max(stage, 2)
    day_30 = _working_day_pattern(
        _number_pattern(r"[3３][0０]", "三十", deadline_number_stage), stage
    )
    day_50 = _working_day_pattern(
        _number_pattern(r"[5５][0０]", "五十", deadline_number_stage), stage
    )
    day_60 = _working_day_pattern(
        _number_pattern(r"[6６][0０]", "六十", deadline_number_stage), stage
    )
    artifact_gap = "" if stage == 1 else r".{0,80}?"
    people_200 = (
        _number_pattern(r"[2２][0０][0０]", "二百", stage)
        + r"(?![0０])"
        + artifact_gap
        + r"人"
    )
    people_100 = (
        _number_pattern(r"[1１][0０][0０]", "一百", stage)
        + r"(?![0０])"
        + artifact_gap
        + r"人"
    )
    people_threshold = (
        rf"(?:{people_200}|{people_100})" if stage == 3 else people_200
    )
    amount_5000 = (
        _number_pattern(r"[5５](?:[,，]?[0０]{3})", "五千", stage)
        + r"万元?"
    )
    day_10 = _working_day_pattern(
        _number_pattern(r"[1１][0０]", "十", deadline_number_stage), stage
    ) + (r"内" if stage == 1 else r"内?")
    month_6 = (
        _number_pattern(r"[6６]", "六", deadline_number_stage)
        + artifact_gap
        + r"个月内"
    )

    regulator = r"(?:中国证监会|中国证券监督管理委员会|证监会|CSRC)"
    report = rf"(?:报告(?:至|给)?{regulator}|(?:向|报送至)?{regulator}(?:报告|报送|备案|说明原因)|报{regulator}(?:备案)?)"
    solution = r"(?:(?:提出|制定|报送)(?:相应的?)?)?(?:解决|处置|应对)方案"
    holder = r"(?:本?基金)?(?:份额)?持有" + artifact_gap + r"人大会"
    meeting = rf"(?:召集|召开){artifact_gap}{holder}"
    no_meeting = rf"(?:不需|无需|不需要|无须|不必){artifact_gap}(?:召集|召开)?{artifact_gap}{holder}"
    termination = (
        r"(?:终止(?:本)?(?:《?基金合同》?|合同)|"
        r"(?:本?《?基金合同》?).{0,80}?终止|"
        r"(?:本基金|基金管理人).{0,50}?(?:宣布基金)?终止|"
        r"(?:进入|进行|履行).{0,80}?(?:基金财产)?清算(?:程序)?|"
        r"(?:基金财产)?清算.{0,80}?终止)"
    )

    features = {
        "anchor": True,
        "has_20": bool(re.search(day_20, context)),
        "has_30": bool(re.search(day_30, context)),
        "has_50": bool(re.search(day_50, context)),
        "has_60": bool(re.search(day_60, context)),
        "has_200": bool(re.search(people_threshold, context)),
        "has_5000": bool(re.search(amount_5000, context)),
        "has_6month": bool(re.search(month_6, context)),
        "has_10days": bool(re.search(day_10, context)),
        "has_report": bool(re.search(report, context)),
        "has_solution": bool(re.search(solution, context)),
        "has_meeting": bool(re.search(meeting, context)),
        "has_no_meeting": bool(re.search(no_meeting, context)),
        "has_auto_termination": bool(re.search(termination, context)),
        "stage": stage,
    }

    type_1_chain = False
    type_2_chain = False
    type_3_chain = False

    for trigger_pattern in (day_30, day_50, day_60):
        for trigger in re.finditer(trigger_pattern, context):
            sentence = _sentence_around(context, trigger.start(), trigger.end())
            legacy_direct = (
                stage == 3
                and re.search(people_100, context)
                and re.search(regulator, sentence)
            )
            if re.search(termination, sentence) and (
                re.search(no_meeting, sentence) or legacy_direct
            ):
                type_3_chain = True
                continue

            boundary = HARD_BOUNDARY_RE.search(context, trigger.end())
            if boundary and re.search(termination, sentence):
                next_sentence = _bounded_window(
                    context,
                    boundary.end(),
                    350,
                )
                continuation = r"^(?:由)?(?:上述|前述|该|此)情形"
                if (
                    re.search(continuation, next_sentence)
                    and re.search(no_meeting, next_sentence)
                ):
                    type_3_chain = True

    for trigger_pattern in (day_30, day_50, day_60):
        for trigger in re.finditer(trigger_pattern, context):
            window = _bounded_window(context, trigger.start(), 700)
            linked_report_deadline = bool(
                re.search(day_10, window) and re.search(report, window)
            )
            linked_meeting_deadline = bool(
                re.search(month_6, window) and re.search(meeting, window)
            )

            if linked_report_deadline and linked_meeting_deadline:
                type_2_chain = True
            elif re.search(report, window) and (
                re.search(solution, window)
                or re.search(meeting, window)
                or "备案" in window
            ):
                type_1_chain = True
    if not features["has_50"] and not features["has_60"]:
        if re.search(report, context) and re.search(solution, context):
            type_1_chain = True

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
    artifact_gap = "" if stage == 1 else r".{0,80}?"
    people_200 = (
        _number_pattern(r"[2２][0０][0０]", "二百", stage)
        + r"(?![0０])"
        + artifact_gap
        + r"人"
    )
    people_100 = (
        _number_pattern(r"[1１][0０][0０]", "一百", stage)
        + r"(?![0０])"
        + artifact_gap
        + r"人"
    )
    people_threshold = (
        rf"(?:{people_200}|{people_100})" if stage == 3 else people_200
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
        has_holder_label = (
            "份额持有人" in base_clause
            or (
                stage == 3
                and (
                    "基金持有人" in base_clause
                    or "基金的持有人" in base_clause
                )
            )
        )
        if not (
            has_holder_label
            and re.search(people_threshold, base_clause)
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
