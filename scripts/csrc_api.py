#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段三: CSRC 证监会信息披露平台兜底。
搜索 FA020010(基金合同) / FA010010(招募说明书) → 下载 PDF → pypdf 解析 → 分类
仅处理阶段一+二均未成功分类的基金，默认 5 worker 并行。
"""

import os, json, time, re, subprocess, tempfile, logging
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from classifier import classify

# Suppress pypdf noise
logging.disable(logging.CRITICAL)

CSRC_WORKERS = 5  # 并行数

CSRC_SEARCH_URL = (
    "http://eid.csrc.gov.cn/fund/disclose/advanced_search_report.do"
)
CSRC_PDF_URL = (
    "http://eid.csrc.gov.cn/fund/disclose/instance_show_pdf_id.do"
)
ALLOWED_HOSTS = {"eid.csrc.gov.cn"}


def validate_url(url: str) -> None:
    host = urllib.parse.urlparse(url).hostname
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"Untrusted host: {host}")


def search_csrc(fund_code: str, report_type: str = "FA020010") -> dict | None:
    """
    在 CSRC 搜索基金合同/招募说明书。

    Args:
        fund_code: 6位基金代码
        report_type: FA020010(基金合同) 或 FA010010(招募说明书)

    Returns:
        {"uploadId": ..., "name": ...} 或 None
    """
    # 构建 DataTables aoData
    now = time.strftime("%Y-%m-%d")
    ao_items = [
        {"name": "sEcho", "value": "1"},
        {"name": "iColumns", "value": "6"},
        {"name": "sColumns", "value": ",,,,,,"},
        {"name": "iDisplayStart", "value": "0"},
        {"name": "iDisplayLength", "value": "5"},
        {"name": "mDataProp_0", "value": "fundCode"},
        {"name": "mDataProp_1", "value": "fundId"},
        {"name": "mDataProp_2", "value": "reportName"},
        {"name": "mDataProp_3", "value": "organName"},
        {"name": "mDataProp_4", "value": "reportDesp"},
        {"name": "mDataProp_5", "value": "reportSendDate"},
        {"name": "fundType", "value": ""},
        {"name": "reportType", "value": report_type},
        {"name": "reportYear", "value": ""},
        {"name": "fundCompanyShortName", "value": ""},
        {"name": "fundCode", "value": fund_code},
        {"name": "fundShortName", "value": ""},
        {"name": "startUploadDate", "value": "2019-01-01"},
        {"name": "endUploadDate", "value": now},
    ]

    ao_data = json.dumps(ao_items, ensure_ascii=False, separators=(",", ":"))

    # 用 PowerShell 搜索(Win), 或 python requests 兜底
    try:
        # PowerShell path (Windows)
        search_url = f"{CSRC_SEARCH_URL}?aoData="
        validate_url(search_url)
        ps_script = f'''
Add-Type -AssemblyName System.Web
$aoData = '{ao_data}'
$encoded = [System.Web.HttpUtility]::UrlEncode($aoData)
$url = "{CSRC_SEARCH_URL}?aoData=" + $encoded
$resp = Invoke-WebRequest -Uri $url -Method GET -TimeoutSec 15 -UseBasicParsing
$resp.Content
'''
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            timeout=20,
        )
        # Handle both bytes and str (depends on text= param)
        stdout = proc.stdout
        if isinstance(stdout, bytes):
            try:
                stdout = stdout.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    stdout = stdout.decode("gbk")
                except UnicodeDecodeError:
                    stdout = stdout.decode("utf-8", errors="replace")
        if stdout and stdout.strip():
            data = json.loads(stdout.strip())
        else:
            raise ValueError("empty stdout")
        if data.get("iTotalRecords", 0) > 0 and data.get("aaData"):
            item = data["aaData"][0]
            return {
                "uploadId": item.get("uploadInfoId"),
                "name": item.get("reportName", ""),
                "rt": report_type,
            }
    except Exception:
        pass

    # Fallback: python requests
    try:
        import urllib.request, urllib.parse

        encoded = urllib.parse.quote(ao_data, safe="")
        url = f"{CSRC_SEARCH_URL}?aoData={encoded}"
        validate_url(url)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            if data.get("iTotalRecords", 0) > 0 and data.get("aaData"):
                item = data["aaData"][0]
                return {
                    "uploadId": item.get("uploadInfoId"),
                    "name": item.get("reportName", ""),
                    "rt": report_type,
                }
    except Exception:
        pass

    return None


def download_csrc_pdf(upload_id: int, out_dir: str) -> str | None:
    """
    从 CSRC 下载 PDF。

    Args:
        upload_id: CSRC instanceid
        out_dir: 输出目录

    Returns:
        本地 PDF 路径 或 None
    """
    dest = os.path.join(out_dir, f"csrc_{upload_id}.pdf")
    if os.path.exists(dest) and os.path.getsize(dest) > 1000:
        return dest

    pdf_url = f"{CSRC_PDF_URL}?instanceid={upload_id}"
    validate_url(pdf_url)

    # PowerShell download (Windows, handles CSRC cookies better)
    try:
        ps_script = f'''
$url = "{pdf_url}"
$dest = "{dest}"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -Uri $url -OutFile $dest -TimeoutSec 30 -UseBasicParsing -Headers @{{"Accept"="application/pdf"}}
'''
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            timeout=35,
        )
        if os.path.exists(dest) and os.path.getsize(dest) > 1000:
            return dest
    except Exception:
        pass

    # Python fallback
    try:
        import urllib.request

        req = urllib.request.Request(
            pdf_url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/pdf",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            with open(dest, "wb") as f:
                f.write(resp.read())
        if os.path.getsize(dest) > 1000:
            return dest
    except Exception:
        pass

    return None


def parse_csrc_pdf(pdf_path: str) -> str | None:
    """pypdf 解析 CSRC PDF (PyMuPDF 对CSRC PDF必然乱码, 直接用pypdf)"""
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        t = page.extract_text()
        if t:
            text += t
    return text if text.strip() else None


def process_fund_csrc(
    fund: tuple, out_dir: str, verbose: bool = True
) -> dict:
    """
    处理单只基金: CSRC 搜索 → 下载 → pypdf 解析 → 分类

    Args:
        fund: (code, name, mgr, type1, type2)
        out_dir: 输出目录

    Returns:
        结果 dict (与 datayes_api 同结构)
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
        "stage": 3,
        "reason": "",
    }

    # Step 1: 搜索 CSRC (先基金合同, 再招募说明书)，每条路径最多重试 2 次
    for rt, label in [("FA020010", "基金合同"), ("FA010010", "招募说明书")]:
        for attempt in range(2):  # 最多重试2次
            search_result = search_csrc(code, rt)
            if search_result:
                break
            if attempt == 0:
                time.sleep(0.5)  # 重试前等待

        if not search_result:
            continue

        upload_id = search_result["uploadId"]

        # Step 2: 下载 (最多重试2次)
        pdf_path = None
        for attempt in range(2):
            pdf_path = download_csrc_pdf(upload_id, out_dir)
            if pdf_path:
                break
            if attempt == 0:
                time.sleep(0.5)

        if not pdf_path:
            continue

        # Step 3: pypdf 解析
        text = parse_csrc_pdf(pdf_path)
        if not text:
            continue

        # 验证确实是基金合同(含关键词)
        if "基金合同" not in text and "基金备案" not in text:
            continue

        # Step 4: 分类
        clause_type, clause_text, detail = classify(text, stage=3)
        result["clauseText"] = text_preview(clause_text)
        result["s3Url"] = f"{CSRC_PDF_URL}?instanceid={upload_id}"
        result["source"] = f"CSRC证监会({label})"

        if clause_type:
            result["clauseType"] = clause_type
            if verbose:
                print(f"  {code}: {clause_type} [CSRC {label}]")
            return result
        else:
            if verbose:
                print(
                    f"  {code}: 未匹配 [CSRC {label}] "
                    f"(h20={detail.get('has_20')} h60={detail.get('has_60')} "
                    f"no_meet={detail.get('has_no_meeting')} 6m={detail.get('has_6month')})"
                )

    # 两条路径都未找到
    result["reason"] = "阶段3: CSRC两路(基金合同+招募说明书)均未找到可用文档"
    if verbose:
        print(f"  {code}: NOT FOUND (CSRC)")
    return result


def text_preview(text: str, max_len: int = 800) -> str:
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def run_stage(
    funds: list, out_dir: str, verbose: bool = True
) -> tuple:
    """
    执行阶段三: CSRC 兜底(并行, 默认5 worker)。

    Args:
        funds: [(code, name, mgr, type1, type2), ...]  待处理基金列表
        out_dir: PDF 下载目录

    Returns:
        (classified_list, not_found_list)
    """
    classified = []
    not_found = []
    stats = {"done": 0, "ok": 0, "nf": 0}
    lock = Lock()

    total = len(funds)
    if verbose:
        print(f"  待处理: {total} 只 (并行 {CSRC_WORKERS} worker)", flush=True)

    def process_one(fund):
        r = process_fund_csrc(fund, out_dir, verbose=False)
        with lock:
            stats["done"] += 1
            if r["clauseType"]:
                stats["ok"] += 1
            else:
                stats["nf"] += 1
            if verbose and stats["done"] % 10 == 0:
                print(
                    f"   [{stats['done']}/{total}]  "
                    f"已分类:{stats['ok']}  未分类:{stats['nf']}",
                    flush=True,
                )
        return r

    with ThreadPoolExecutor(max_workers=CSRC_WORKERS) as executor:
        futures = {executor.submit(process_one, f): f for f in funds}
        for future in as_completed(futures):
            r = future.result()
            if r["clauseType"]:
                classified.append(r)
            else:
                not_found.append(
                    (r["code"], r["name"], r["mgr"], r["type1"], r["type2"])
                )

    if verbose:
        print(
            f"   完成: 已分类 {len(classified)}, 未分类 {len(not_found)} "
            + (f"({len(classified)/total*100:.1f}%)" if total > 0 else "")
        )

    return classified, not_found
