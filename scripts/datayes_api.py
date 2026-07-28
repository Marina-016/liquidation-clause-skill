#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段一 & 阶段二: Datayes 数据源。
阶段一: 基金合同(classifyName=基金合同) → PyMuPDF 解析 → 分类
阶段二: 替代源(招募说明书/发售公告/成立公告/资料概要) → PyMuPDF解析 + pypdf兜底 → 分类
"""

import os, sys, json, time, hashlib, urllib.request, urllib.parse
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from collections import defaultdict

from classifier import classify, text_preview

# ============== 配置 ==============
ALLOWED_HOSTS = {
    "gw.datayes.com",
    "r.datayes.com",
    "bigdata-s3.wmcloud.com",
}
API_WORKERS = 10
DL_WORKERS = 10
PARSE_WORKERS = 6

# ============== S3 下载 ==============
_s3_session = None
_s3_session_lock = Lock()


def get_s3_session():
    global _s3_session
    if _s3_session is None:
        with _s3_session_lock:
            if _s3_session is None:
                _s3_session = requests.Session()
                _s3_session.trust_env = False
    return _s3_session


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


def find_contract(fund_code, stage: int = 1):
    """
    查找基金合同/替代文档。

    阶段一: 只取 classifyName="基金合同"，且优选标题不含"修订/修改/公告/摘要"的
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

            best_contract = None
            best_recruit = None  # 招募说明书
            best_sale = None  # 发售/成立公告
            best_summary = None  # 资料概要
            best_operate = None  # 发行运作

            for item in resp["data"]["list"]:
                title = item.get("title", "")
                cn = item.get("classifyName", "")
                cid = str(item.get("classifyId", ""))
                url = item["s3Url"]

                # 阶段一: 严格限定基金合同
                if stage == 1 and cn == "基金合同":
                    info = {"s3Url": url, "title": title, "source": "基金合同"}
                    if best_contract is None:
                        best_contract = info
                    # 优先选标题干净的完整合同
                    if (
                        "基金合同" in title
                        and "修订" not in title
                        and "调低" not in title
                        and "修改" not in title
                        and "公告" not in title
                        and "摘要" not in title
                    ):
                        return info

                # 阶段二: 放宽到所有可用文档
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

            # 按优先级返回
            if best_contract:
                return best_contract
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


def run_stage(
    funds: list, out_dir: str, stage: int, verbose: bool = True
) -> tuple:
    """
    执行阶段 1 或 2。

    Args:
        funds: [(code, name, mgr, type1, type2), ...]  待处理基金列表
        out_dir: PDF 下载缓存目录
        stage: 1 或 2

    Returns:
        (classified_list, not_found_list)
        - classified_list: 已成功分类的基金结果
        - not_found_list: 未分类的基金 (code, name, mgr, type1, type2)
    """
    classified = []
    not_found = []
    url_cache = {}
    cache_lock = Lock()

    stats = {"done": 0, "api": 0}
    stats_lock = Lock()

    def process_one(f):
        r = process_fund_datayes(f, out_dir, stage, url_cache, cache_lock)
        with stats_lock:
            stats["done"] += 1
        return r

    total = len(funds)
    if total == 0:
        if verbose:
            print("   完成: 已分类 0, 未分类 0 (0.0%)", flush=True)
        return classified, not_found

    with ThreadPoolExecutor(max_workers=max(API_WORKERS, DL_WORKERS)) as executor:
        futures = {executor.submit(process_one, f): f for f in funds}
        for future in as_completed(futures):
            r = future.result()
            if r["clauseType"]:
                classified.append(r)
            else:
                not_found.append(
                    (r["code"], r["name"], r["mgr"], r["type1"], r["type2"])
                )

            if verbose and stats["done"] % 20 == 0:
                print(
                    f"   [{stats['done']}/{total}]  "
                    f"已分类:{len(classified)}  未分类:{len(not_found)}",
                    flush=True,
                )

    if verbose:
        print(
            f"   完成: 已分类 {len(classified)}, 未分类 {len(not_found)} "
            f"({len(classified)/total*100:.1f}%)",
            flush=True,
        )

    return classified, not_found
