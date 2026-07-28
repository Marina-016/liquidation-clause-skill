#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一清盘条款分类器。

三种类型定义:
  类型1: 20日披露 + 60日向证监会报告+提方案+召开持有人大会(无时限)
  类型2: 20日披露 + 60日内10个工作日报告 + 6个月内召开持有人大会
  类型3: 20日披露 + 50日直接终止 + 不需召开持有人大会

关键区分:
  T1 vs T2: 是否有 "6个月内" 时限
  T3: 50日 + 不需召开大会(非60日)
"""

import re


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
    text_clean = text.replace('\n', '').replace(' ', '')

    # ===== Step 1: 用"五千万元"锚定清盘条款章节 =====
    anchor_matches = list(re.finditer(
        r'(?:五千|5000|5[,，]000)\s*万',
        text_clean
    ))

    best_match = None
    for m in anchor_matches:
        start = max(0, m.start() - 500)
        end = min(len(text_clean), m.end() + 800)
        ctx = text_clean[start:end]

        # 验证上下文中含清盘条款关键词(非募集成立条件)
        if re.search(r'(?:份额持有人|二百人|200\s*人)', ctx):
            best_match = ctx
            break

    if not best_match:
        # 兜底: 搜"连续.*工作日"
        fallback = re.search(
            r'连续\s*(?:[二三四五六七八九十]|二十|三十|四十|五十|六十|[23456]0)\s*个?\s*工?作?日',
            text_clean
        )
        if fallback:
            start = max(0, fallback.start() - 300)
            end = min(len(text_clean), fallback.end() + 600)
            best_match = text_clean[start:end]
        else:
            return (None, '', {'anchor': False})

    # ===== Step 2: 提取天数 =====
    has_60 = bool(re.search(r'(?:六十|6[0０])\s*个?\s*工?作?日', best_match))
    has_50 = bool(re.search(r'(?:五十|5[0０])\s*个?\s*工?作?日', best_match))
    has_20 = bool(re.search(r'(?:二十|2[0０])\s*个?\s*工?作?日', best_match))

    # ===== Step 3: 提取类型特征 =====

    # 类型3特征: "不需召开"/"无需召开"，且紧邻清盘上下文
    has_no_meeting = False
    for m in re.finditer(r'(?:不需召开|无需召开|不需要召开|无须召开)', best_match):
        nearby = best_match[max(0, m.start()-100):m.end()+100]
        if re.search(r'(?:二百|200\s*人|五千|5000|工作日|清算|终止)', nearby):
            has_no_meeting = True
            break

    # 类型2特征: "6个月内" 或 "六个月内"
    has_6month = bool(re.search(r'(?:六个月内|6个月内)', best_match))

    # 类型2特征: "10个工作日内" 或 "十个工作日内"
    has_10days = bool(re.search(r'(?:十个工作日内|10个工作日内)', best_match))

    # 报告证监会
    has_report = bool(re.search(r'(?:报告).{0,50}(?:证监|CSRC)', best_match))

    # ===== Step 4: 判定 =====
    detail = {
        'has_20': has_20, 'has_50': has_50, 'has_60': has_60,
        'has_6month': has_6month, 'has_10days': has_10days,
        'has_report': has_report, 'has_no_meeting': has_no_meeting,
        'stage': stage,
    }

    # 类型3: (50日 或 60日) + 不需召开持有人大会 — 关键特征是无需大会
    if (has_50 or has_60) and has_no_meeting:
        return ('类型3: 自动触发终止', text_preview(best_match), detail)

    # 类型2: 60日 + 报告证监会 + 6个月内大会
    if has_60 and has_6month:
        return ('类型2: 备案+6个月大会', text_preview(best_match), detail)

    # 类型2(10日报告也算): 60日 + 10个工作日内
    if has_60 and has_10days:
        return ('类型2: 备案+6个月大会', text_preview(best_match), detail)

    # 类型1: 60日 + 报告证监会 + 无6月内时限
    if has_60 and has_report and not has_6month:
        return ('类型1: 备案', text_preview(best_match), detail)

    # 兜底: 有60日但无法区分T1/T2 → 按T2处理(更保守)
    if has_60 and not has_50 and not has_no_meeting:
        if has_report:
            return ('类型1: 备案', text_preview(best_match), detail)

    return (None, text_preview(best_match), detail)


def text_preview(text: str, max_len: int = 800) -> str:
    """截取条款可读片段"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + '…'
