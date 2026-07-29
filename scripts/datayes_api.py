#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段一 & 阶段二: Datayes 数据源。
阶段一: 基金合同(classifyName=基金合同) → PyMuPDF 解析 → 分类
阶段二A: 同一份完整基金合同 → PyMuPDF解析 + pypdf兜底 → 宽松分类
阶段二B: 替代源(招募说明书/发售公告/成立公告/资料概要) → PyMuPDF解析 + pypdf兜底 → 分类
"""

import os, sys, json, time, hashlib, urllib.request, urllib.parse
import requests
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Lock, local
from collections import defaultdict

from classifier import classify, text_preview

# ============== 配置 ==============
ALLOWED_HOSTS = {
    "gw.datayes.com",
    "r.datayes.com",
    "bigdata-s3.wmcloud.com",
}
API_WORKERS = 16
DL_WORKERS = 16
PARSE_WORKERS = 8

# ============== S3 下载 ==============
_s3_session_local = local()


def get_s3_session():
    session = getattr(_s3_session_local, "session", None)
    if session is None:
        session = requests.Session()
        session.trust_env = False
        _s3_session_local.session = session
    return session


def validate_url(url: str) -> None:
    host = urllib.parse.urlparse(url).hostname
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"Untrusted host: {host}")


def get_token() -> str:
    token = os.environ.get("DATAYES_TOKEN", "").strip()
    if not token:
        raise ValueError("Missing DATAYES_TOKEN environment variable")
    return token


def s3_headers():
    return {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://r.datayes.com/",
        "Authorization": f"Bearer {get_token()}",
        "x-amz-date": time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
    }


def api_get(url, params):
    """调用 Datayes API"""
    validate_url(url)
    qs = urllib.parse.urlencode(params)
    url_full = f"{url}?{qs}"
    req = urllib.request.Request(
        url_full, headers={"Authorization": f"Bearer {get_token()}"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def download_pdf(s3_url, out_dir):
    """下载 PDF，返回本地路径，失败返回 None。URL MD5 去重缓存。"""
    validate_url(s3_url)
    url_hash = hashlib.md5(s3_url.encode()).hexdigest()[:12]
    dest = os.path.join(out_dir, f"{url_hash}.pdf")
    if os.path.exists(dest) and os.path.getsize(dest) > 1000:
        return dest  # 缓存命中

    for attempt in range(2):
        try:
            s = get_s3_session()
            r = s.get(s3_url, headers=s3_headers(), timeout=300, stream=True)
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            return dest
        except Exception:
            if attempt == 1:
                return None
            time.sleep(2)
    return None


# ============== PDF 解析 ==============


def parse_pymupdf(pdf_path):
    """PyMuPDF 解析 PDF 全文"""
    import fitz

    doc = fitz.open(pdf_path)

    # 快速验证: 非合同(PDF<10页 且不含关键词)
    if doc.page_count < 10:
        first_page = doc[0].get_text()
        if "基金合同" not in first_page and "基金管理人" not in first_page:
            doc.close()
            return None, "非基金合同PDF"

    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()

    # 检测乱码(PyMuPDF对CSRC PDF会输出乱码)
    garbage_count = sum(1 for c in text if ord(c) in range(0xFFFD, 0xFFFF))
    if garbage_count > 3:
        return None, "PyMuPDF输出乱码"

    return text, None


def parse_pypdf(pdf_path):
    """pypdf 解析 PDF 全文 (对非标CMap兼容性更好)"""
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        t = page.extract_text()
        if t:
            text += t
    return text if text.strip() else None


def parse_pdf(pdf_path, stage: int = 1):
    """
    解析 PDF 全文。
    阶段一/二: PyMuPDF 为主, 乱码则切换 pypdf
    返回: (text, error)  或  (None, error_msg)
    """
    text, err = parse_pymupdf(pdf_path)
    if text and err is None:
        return text, None

    # PyMuPDF 失败/乱码 → 尝试 pypdf
    if stage >= 1:
        try:
            text = parse_pypdf(pdf_path)
            if text:
                return text, None
        except Exception:
            pass

    return None, err or "PDF解析失败"


# ============== 合同查找 ==============


def _contract_title_score(title: str):
    """完整基金合同标题评分；公告、费率调整和修改说明不作为合同正文。"""
    compact = "".join(str(title or "").split())
    if "基金合同" not in compact or "摘要" in compact:
        return None
    if "公告" in compact:
        return None
    if compact.startswith("关于") and any(
        marker in compact for marker in ("降低", "调低", "调整", "费率", "变更", "修改")
    ):
        return None

    score = 100
    if compact.endswith("基金合同"):
        score += 100
    if compact.endswith(("基金合同（修订版）", "基金合同(修订版)")):
        score += 90
    if "修订" in compact or "更新" in compact:
        score += 10
    return score


def find_contract(fund_code, stage: int = 1):
    """
    查找基金合同/替代文档。

    阶段一: 只取 classifyName="基金合同"，按标题评分优选完整合同正文
    阶段二: 放宽到 招募说明书 > 发售公告 > 成立公告 > 资料概要 > 发行运作

    返回: {"s3Url": ..., "title": ..., "source": ...} 或 None
    """
    list_url = (
        "https://gw.datayes.com/aladdin_proxy/rrp_fund/mobile/whitelist/fund/announcement/list"
    )

    for params in [
        {"fundCode": fund_code, "annoType": "ISSUE_OPERATE", "pageSize": "200"},
        {"fundCode": fund_code, "pageSize": "200"},
    ]:
        try:
            resp = api_get(list_url, params)
            if resp.get("code") != 1:
                continue

            contract_candidates = []
            best_recruit = None
            best_sale = None
            best_summary = None
            best_operate = None

            for item in resp["data"]["list"]:
                title = item.get("title", "")
                cn = item.get("classifyName", "")
                cid = str(item.get("classifyId", ""))
                url = item["s3Url"]

                if stage == 1 and cn == "基金合同":
                    score = _contract_title_score(title)
                    if score is not None:
                        contract_candidates.append(
                            (
                                score,
                                {"s3Url": url, "title": title, "source": "基金合同"},
                            )
                        )

                if stage == 2:
                    if "招募" in title and best_recruit is None:
                        best_recruit = {
                            "s3Url": url,
                            "title": title,
                            "source": "招募说明书",
                        }
                    elif cid in ("1210203", "1210202") and best_sale is None:
                        best_sale = {
                            "s3Url": url,
                            "title": title,
                            "source": "发售/成立公告",
                        }
                    elif cn == "资料概要" and best_summary is None:
                        best_summary = {
                            "s3Url": url,
                            "title": title,
                            "source": "资料概要/招募说明书",
                        }
                    elif cid == "1210205" and best_operate is None:
                        best_operate = {
                            "s3Url": url,
                            "title": title,
                            "source": "发行运作",
                        }

            if contract_candidates:
                return max(contract_candidates, key=lambda candidate: candidate[0])[1]
            if stage == 2:
                if best_recruit:
                    return best_recruit
                if best_sale:
                    return best_sale
                if best_summary:
                    return best_summary
                if best_operate:
                    return best_operate
        except Exception:
            pass

    return None

# ============== 单只基金处理 ==============


def process_fund_datayes(fund, out_dir, stage: int, url_cache: dict, cache_lock: Lock):
    """
    处理单只基金: 找合同 → 下载 → 解析 → 分类

    Args:
        fund: (code, name, mgr, type1, type2)
        out_dir: PDF 缓存目录
        stage: 1 或 2
        url_cache: URL 去重缓存 (跨基金共享同一PDF)
        cache_lock: URL 缓存锁
    """
    code, name, mgr, type1, type2 = fund

    result = {
        "code": code,
        "name": name,
        "mgr": mgr,
        "type1": type1,
        "type2": type2,
        "clauseType": None,
        "clauseText": "",
        "s3Url": "",
        "source": "",
        "stage": stage,
        "reason": "",
    }

    # Step 1: 找合同
    contract = find_contract(code, stage)
    if not contract:
        result["reason"] = f"阶段{stage}: API未找到可用文档"
        return result

    s3_url = contract["s3Url"]
    result["s3Url"] = s3_url
    result["source"] = contract["source"]

    # Step 2: URL 去重
    with cache_lock:
        if s3_url in url_cache:
            cached = url_cache[s3_url]
            result["clauseType"] = cached["type"]
            result["clauseText"] = cached["text"]
            return result

    # Step 3: 下载
    pdf_path = download_pdf(s3_url, out_dir)
    if not pdf_path:
        result["reason"] = f"阶段{stage}: PDF下载失败"
        return result

    # Step 4: 解析
    text, parse_err = parse_pdf(pdf_path, stage)
    if not text:
        result["reason"] = f"阶段{stage}: {parse_err or 'PDF解析失败'}"
        return result

    # Step 5: 分类
    clause_type, clause_text, detail = classify(text, stage)
    result["clauseText"] = text_preview(clause_text)

    if clause_type:
        result["clauseType"] = clause_type
    else:
        result["reason"] = f"阶段{stage}: 分类规则未命中 (has_20={detail.get('has_20')}, has_60={detail.get('has_60')}, report={detail.get('has_report')}, meeting={detail.get('has_meeting')})"

    # Step 6: 缓存
    with cache_lock:
        url_cache[s3_url] = {
            "type": clause_type,
            "text": result.get("clauseText", ""),
        }

    return result


# ============== 批量执行 ==============


def _base_result(fund: tuple, stage: int) -> dict:
    code, name, mgr, type1, type2 = fund
    return {
        "code": code,
        "name": name,
        "mgr": mgr,
        "type1": type1,
        "type2": type2,
        "clauseType": None,
        "clauseText": "",
        "s3Url": "",
        "source": "",
        "stage": stage,
        "reason": "",
    }


def _parse_and_classify(pdf_path: str, stage: int) -> dict:
    text, parse_err = parse_pdf(pdf_path, stage)
    if not text:
        return {
            "clauseType": None,
            "clauseText": "",
            "reason": f"阶段{stage}: {parse_err or 'PDF解析失败'}",
        }

    clause_type, clause_text, detail = classify(text, stage)
    reason = ""
    if not clause_type:
        reason = (
            f"阶段{stage}: 分类规则未命中 "
            f"(has_20={detail.get('has_20')}, "
            f"has_60={detail.get('has_60')}, "
            f"report={detail.get('has_report')}, "
            f"meeting={detail.get('has_meeting')})"
        )
    return {
        "clauseType": clause_type,
        "clauseText": text_preview(clause_text),
        "reason": reason,
    }


def _apply_payload(result: dict, payload: dict) -> dict:
    result.update(payload)
    return result


def run_stage(
    funds: list,
    out_dir: str,
    stage: int,
    verbose: bool = True,
    on_result=None,
    document_stage: int = None,
    source_override: str = None,
) -> tuple:
    """
    执行阶段 1 或 2，使用独立的 API、下载和解析线程池。

    Args:
        funds: [(code, name, mgr, type1, type2), ...] 待处理基金列表
        out_dir: PDF 下载缓存目录
        stage: 分类规则阶段，1 或 2
        on_result: 可选回调，每只基金完成后接收结果 dict
        document_stage: 文档查找阶段；默认与 stage 相同
        source_override: 可选的数据来源显示名称

    Returns:
        (classified_list, not_found_list)
    """
    lookup_stage = stage if document_stage is None else document_stage
    if lookup_stage not in (1, 2):
        raise ValueError(f"unsupported document stage: {lookup_stage}")

    total = len(funds)
    if total == 0:
        if verbose:
            print("   完成: 已分类 0, 未分类 0 (0.0%)", flush=True)
        return [], []

    results = [None] * total
    done_count = 0
    classified_count = 0

    def finalize(index: int, result: dict) -> None:
        nonlocal done_count, classified_count
        if results[index] is not None:
            return
        results[index] = result
        done_count += 1
        if result.get("clauseType"):
            classified_count += 1
        if on_result:
            try:
                on_result(result)
            except Exception as exc:
                if verbose:
                    print(f"   [警告] 保存检查点失败: {exc}", flush=True)
        if verbose and (done_count % 20 == 0 or done_count == total):
            print(
                f"   [{done_count}/{total}]  "
                f"已分类:{classified_count}  未分类:{done_count - classified_count}",
                flush=True,
            )

    # 这些映射只由协调线程修改，因此 URL 去重不需要额外加锁。
    url_waiters = {}
    payload_cache = {}

    with (
        ThreadPoolExecutor(max_workers=API_WORKERS) as api_executor,
        ThreadPoolExecutor(max_workers=DL_WORKERS) as download_executor,
        ThreadPoolExecutor(max_workers=PARSE_WORKERS) as parse_executor,
    ):
        api_futures = {
            api_executor.submit(find_contract, fund[0], lookup_stage): (index, fund)
            for index, fund in enumerate(funds)
        }
        download_futures = {}
        parse_futures = {}

        while api_futures or download_futures or parse_futures:
            pending = (
                set(api_futures)
                | set(download_futures)
                | set(parse_futures)
            )
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)

            for future in completed:
                if future in api_futures:
                    index, fund = api_futures.pop(future)
                    result = _base_result(fund, stage)
                    try:
                        contract = future.result()
                    except Exception as exc:
                        result["reason"] = f"阶段{stage}: API调用失败 ({exc})"
                        finalize(index, result)
                        continue

                    if not contract:
                        result["reason"] = f"阶段{stage}: API未找到可用文档"
                        finalize(index, result)
                        continue

                    s3_url = contract["s3Url"]
                    result["s3Url"] = s3_url
                    result["source"] = source_override or contract["source"]

                    if s3_url in payload_cache:
                        finalize(
                            index,
                            _apply_payload(result, payload_cache[s3_url]),
                        )
                    elif s3_url in url_waiters:
                        url_waiters[s3_url].append((index, result))
                    else:
                        url_waiters[s3_url] = [(index, result)]
                        download_future = download_executor.submit(
                            download_pdf, s3_url, out_dir
                        )
                        download_futures[download_future] = s3_url

                elif future in download_futures:
                    s3_url = download_futures.pop(future)
                    try:
                        pdf_path = future.result()
                    except Exception:
                        pdf_path = None

                    if not pdf_path:
                        payload = {
                            "clauseType": None,
                            "clauseText": "",
                            "reason": f"阶段{stage}: PDF下载失败",
                        }
                        payload_cache[s3_url] = payload
                        for index, result in url_waiters.pop(s3_url):
                            finalize(index, _apply_payload(result, payload))
                        continue

                    parse_future = parse_executor.submit(
                        _parse_and_classify, pdf_path, stage
                    )
                    parse_futures[parse_future] = s3_url

                else:
                    s3_url = parse_futures.pop(future)
                    try:
                        payload = future.result()
                    except Exception as exc:
                        payload = {
                            "clauseType": None,
                            "clauseText": "",
                            "reason": f"阶段{stage}: PDF解析失败 ({exc})",
                        }
                    payload_cache[s3_url] = payload
                    for index, result in url_waiters.pop(s3_url):
                        finalize(index, _apply_payload(result, payload))

    classified = [r for r in results if r and r.get("clauseType")]
    not_found = [
        (r["code"], r["name"], r["mgr"], r["type1"], r["type2"])
        for r in results
        if r and not r.get("clauseType")
    ]

    if verbose:
        print(
            f"   完成: 已分类 {len(classified)}, 未分类 {len(not_found)} "
            f"({len(classified)/total*100:.1f}%)",
            flush=True,
        )

    return classified, not_found
