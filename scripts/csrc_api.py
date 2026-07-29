#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段三: CSRC 证监会信息披露平台兜底。
搜索 FA020010(基金合同) / FA010010(招募说明书) → 下载 PDF → pypdf 解析 → 分类
仅处理阶段一+二均未成功分类的基金，默认 8 worker 并行。
"""

import os, json, time, re, subprocess, tempfile, logging
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from classifier import classify

# Suppress pypdf noise
logging.disable(logging.CRITICAL)

CSRC_WORKERS = 10  # 并行数
CSRC_START_DATE = "2000-01-01"
CSRC_PAGE_SIZE = 50
CSRC_MAX_PAGES = 20
CSRC_CODE_ALIASES = {"151002": ("151001",)}

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


def _candidate_title_score(name: str, report_type: str) -> int:
    """完整合同/招募说明书优先，费率调整及修改公告降至末尾。"""
    compact = "".join(str(name or "").split())
    score = 0
    if report_type == "FA020010":
        if "基金合同" in compact:
            score += 200
        if compact.endswith("基金合同"):
            score += 100
        if compact.endswith(("基金合同（修订版）", "基金合同(修订版)")):
            score += 90
        if "修订" in compact or "更新" in compact:
            score += 10
    elif "招募说明书" in compact:
        score += 150

    if "公告" in compact:
        score -= 300
    if any(word in compact for word in ("降低费率", "调低费率", "费率调整", "修改公告", "摘要")):
        score -= 200
    return score


def _build_search_payload(
    fund_code: str,
    report_type: str,
    display_start: int,
) -> str:
    now = time.strftime("%Y-%m-%d")
    ao_items = [
        {"name": "sEcho", "value": "1"},
        {"name": "iColumns", "value": "6"},
        {"name": "sColumns", "value": ",,,,,,"},
        {"name": "iDisplayStart", "value": str(display_start)},
        {"name": "iDisplayLength", "value": str(CSRC_PAGE_SIZE)},
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
        {"name": "startUploadDate", "value": CSRC_START_DATE},
        {"name": "endUploadDate", "value": now},
    ]
    return json.dumps(ao_items, ensure_ascii=False, separators=(",", ":"))


def _fetch_search_page(ao_data: str) -> dict:
    search_url = f"{CSRC_SEARCH_URL}?aoData="
    validate_url(search_url)
    errors = []

    try:
        ps_script = f'''
Add-Type -AssemblyName System.Web
$aoData = '{ao_data}'
$encoded = [System.Web.HttpUtility]::UrlEncode($aoData)
$url = "{CSRC_SEARCH_URL}?aoData=" + $encoded
$resp = Invoke-WebRequest -Uri $url -Method GET -TimeoutSec 20 -UseBasicParsing
$resp.Content
'''
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            timeout=25,
        )
        stdout = proc.stdout
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if stdout and stdout.strip():
            return json.loads(stdout.strip())
        errors.append("PowerShell返回空响应")
    except Exception as exc:
        errors.append(f"PowerShell: {exc}")

    try:
        import urllib.request

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
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        errors.append(f"urllib: {exc}")

    raise RuntimeError("；".join(errors))


def search_csrc_candidates(
    fund_code: str,
    report_type: str = "FA020010",
) -> list:
    """查询并返回排序、去重后的全部 CSRC 文档候选。"""
    candidates = []
    seen_ids = set()

    for page in range(CSRC_MAX_PAGES):
        display_start = page * CSRC_PAGE_SIZE
        ao_data = _build_search_payload(fund_code, report_type, display_start)
        data = _fetch_search_page(ao_data)
        records = data.get("aaData") or []

        for item in records:
            upload_id = item.get("uploadInfoId")
            if not upload_id or upload_id in seen_ids:
                continue
            seen_ids.add(upload_id)
            candidates.append(
                {
                    "uploadId": upload_id,
                    "name": item.get("reportName", ""),
                    "reportSendDate": item.get("reportSendDate", ""),
                    "rt": report_type,
                }
            )

        total = int(data.get("iTotalRecords") or 0)
        if not records or display_start + len(records) >= total:
            break

    candidates.sort(
        key=lambda item: (
            _candidate_title_score(item.get("name", ""), report_type),
            str(item.get("reportSendDate", "")),
            int(item.get("uploadId") or 0),
        ),
        reverse=True,
    )
    return candidates


def search_csrc(fund_code: str, report_type: str = "FA020010") -> dict | None:
    """兼容旧调用：返回排序后的首个 CSRC 候选。"""
    candidates = search_csrc_candidates(fund_code, report_type)
    return candidates[0] if candidates else None


def _is_pdf_file(path: str) -> bool:
    try:
        if os.path.getsize(path) <= 1000:
            return False
        with open(path, "rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False


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
    if os.path.exists(dest) and _is_pdf_file(dest):
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
        if os.path.exists(dest) and _is_pdf_file(dest):
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
        if _is_pdf_file(dest):
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
    """依次尝试 CSRC 的完整合同与招募说明书候选，直到成功分类。"""
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
    candidate_attempts = []

    for report_type, label in [
        ("FA020010", "基金合同"),
        ("FA010010", "招募说明书"),
    ]:
        candidates = []
        query_errors = []
        seen_ids = set()
        search_codes = (code,) + CSRC_CODE_ALIASES.get(code, ())

        for search_code in search_codes:
            found = None
            query_error = None
            for attempt in range(2):
                try:
                    found = search_csrc_candidates(search_code, report_type)
                    query_error = None
                    break
                except Exception as exc:
                    query_error = exc
                    if attempt == 0:
                        time.sleep(0.5)

            if query_error is not None:
                query_errors.append(f"{search_code}: {query_error}")
                continue

            for candidate in found or []:
                if search_code != code and "系列" not in candidate.get("name", ""):
                    continue
                upload_id = candidate.get("uploadId")
                if upload_id in seen_ids:
                    continue
                seen_ids.add(upload_id)
                enriched = dict(candidate)
                enriched["searchCode"] = search_code
                candidates.append(enriched)

        candidates.sort(
            key=lambda item: (
                _candidate_title_score(item.get("name", ""), report_type),
                str(item.get("reportSendDate", "")),
            ),
            reverse=True,
        )

        if not candidates:
            status = "CSRC_QUERY_ERROR" if query_errors else "CSRC_NO_RECORDS"
            detail = (
                "；".join(query_errors)
                if query_errors
                else f"{CSRC_START_DATE}以来无记录"
            )
            candidate_attempts.append(
                {
                    "reportType": report_type,
                    "status": status,
                    "detail": detail,
                }
            )
            continue
        for candidate in candidates:
            upload_id = candidate["uploadId"]
            attempt_record = {
                "reportType": report_type,
                "uploadId": upload_id,
                "name": candidate.get("name", ""),
                "searchCode": candidate.get("searchCode", code),
            }

            pdf_path = None
            for attempt in range(2):
                pdf_path = download_csrc_pdf(upload_id, out_dir)
                if pdf_path:
                    break
                if attempt == 0:
                    time.sleep(0.5)
            if not pdf_path:
                attempt_record["status"] = "CSRC_DOWNLOAD_FAILED"
                candidate_attempts.append(attempt_record)
                continue

            try:
                text = parse_csrc_pdf(pdf_path)
            except Exception as exc:
                attempt_record["status"] = "CSRC_PARSE_EMPTY"
                attempt_record["detail"] = str(exc)
                candidate_attempts.append(attempt_record)
                continue
            if not text:
                attempt_record["status"] = "CSRC_PARSE_EMPTY"
                candidate_attempts.append(attempt_record)
                continue
            compact_text = re.sub(r"\s+", "", text)
            if not any(
                marker in compact_text
                for marker in ("基金合同", "基金契约", "基金备案")
            ):
                attempt_record["status"] = "CSRC_DOCUMENT_MISMATCH"
                candidate_attempts.append(attempt_record)
                continue

            clause_type, clause_text, detail = classify(text, stage=3)
            pdf_url = f"{CSRC_PDF_URL}?instanceid={upload_id}"
            result["clauseText"] = text_preview(clause_text)
            result["s3Url"] = pdf_url
            result["source"] = f"CSRC证监会({label})"

            if clause_type:
                result["clauseType"] = clause_type
                attempt_record["status"] = "CSRC_CLASSIFIED"
                attempt_record["clauseType"] = clause_type
                candidate_attempts.append(attempt_record)
                result["candidateAttempts"] = candidate_attempts
                if verbose:
                    print(f"  {code}: {clause_type} [CSRC {label}]")
                return result

            attempt_record["status"] = "CSRC_RULE_NO_MATCH"
            attempt_record["detail"] = {
                key: detail.get(key)
                for key in (
                    "anchor",
                    "has_20",
                    "has_50",
                    "has_60",
                    "has_10days",
                    "has_6month",
                    "has_report",
                    "has_no_meeting",
                    "has_auto_termination",
                )
            }
            candidate_attempts.append(attempt_record)

    result["candidateAttempts"] = candidate_attempts
    status_counts = {}
    for attempt in candidate_attempts:
        status = attempt.get("status", "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1
    summary = ", ".join(
        f"{status}={count}" for status, count in sorted(status_counts.items())
    )
    result["reason"] = (
        f"阶段3: CSRC_ALL_CANDIDATES_EXHAUSTED ({summary or '无候选'})"
    )
    if verbose:
        print(f"  {code}: NOT FOUND (CSRC: {summary or '无候选'})")
    return result


def text_preview(text: str, max_len: int = 800) -> str:
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def run_stage(
    funds: list, out_dir: str, verbose: bool = True, on_result=None
) -> tuple:
    """
    执行阶段三: CSRC 兜底(并行, 默认8 worker)。

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
            fund = futures[future]
            try:
                r = future.result()
            except Exception as exc:
                code, name, mgr, type1, type2 = fund
                r = {
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
                    "reason": f"阶段3: CSRC处理失败 ({exc})",
                }
            if on_result:
                try:
                    on_result(r)
                except Exception as exc:
                    if verbose:
                        print(f"   [警告] 保存检查点失败: {exc}", flush=True)
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
