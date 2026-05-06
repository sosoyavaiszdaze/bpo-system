"""クライアント MarTech スタック自動検証 (ADR-015 §2.3)

責務: クライアントの LP / サンクスページに HTTP リクエストを送り、
      tech_stack_signatures.yaml と照合してスタックを推定。
      clients.yaml の宣言値と突合して 4 状態判定 (一致 / 検出のみ / 宣言のみ / 不一致) を出す。

主要関数:
    - validate_client_tech_stack(client_id) -> dict
    - detect_stack_from_html(url) -> dict        # HTML/headers/cookies → カテゴリ別検出結果
    - score_signals(strong_hits, medium_hits, weak_hits) -> str  # confidence 判定

シグナル種別 (signatures.yaml の prefix と照合):
    - http_header:<key>
    - html_contains:<substring>
    - js_global:<varname>
    - cookie:<name>
    - domain_regex:<regex>
    - url_path_regex:<regex>

信頼度合算ロジック (ADR-015 §2.2 修正版):
    cookie シグナルは原則 weak に置き、validator 側で以下の合算を行う:
    - strong >= 1                              → high
    - medium >= 2                              → medium
    - medium >= 1 + weak >= 1 (合算 >= 2)       → medium (weak が medium に昇格)
    - medium >= 1 のみ                          → low
    - weak のみ (個数問わず)                   → unknown (cookie 残存問題で誤検知防止)
    - マッチなし                                → unknown
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import yaml

log = logging.getLogger("bpo")

ROOT = Path(__file__).resolve().parent.parent
SIGNATURES_PATH = ROOT / "config" / "tech_stack_signatures.yaml"
CLIENTS_PATH = ROOT / "config" / "clients.yaml"
OUTPUT_DIR = ROOT / "outputs"

USER_AGENT = "ZynectMartechValidator/1.0 (+https://zynect.example/martech-audit)"
HTTP_TIMEOUT_SEC = 15
HTTP_MAX_REDIRECTS = 3
HTTP_MAX_BODY_BYTES = 2_000_000  # 2 MB
ALLOWED_SCHEMES = {"https"}
ALLOWED_CONTENT_TYPE_PREFIXES = ("text/html", "application/xhtml+xml")

# SSRF 防止: private / loopback / link-local アドレス帯
# IPv4: 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16
# IPv6: ::1, fc00::/7, fe80::/10
_BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

# 5/7 自動検出主体カテゴリ (ヒアリング主体カテゴリは validator 結果より宣言値を優先)
PHASE_A_AUTO_CATEGORIES = {
    "ec_platform", "tag_manager", "analytics", "ad_platforms", "capi_status",
}


# ========== Public API ==========

def validate_client_tech_stack(client_id: str, sample_url: Optional[str] = None) -> dict:
    """クライアントの tech_stack を実シグナルから検証

    Returns:
        {
            "client_id": str,
            "validated_at": ISO,
            "url_probed": str,
            "categories": {
                "ec_platform":  {"detected": "ecforce", "confidence": "high",
                                  "matched_signals": [...], "declared": "ecforce",
                                  "diagnosis": "match" | "detected_only" | "declared_only" | "mismatch"},
                ...
            },
            "summary": {...},
            "internal_notifications": [...],   # 社内 ChatWork 投稿候補
        }
    """
    signatures = _load_signatures()
    client_cfg = _load_client_cfg(client_id)
    if not client_cfg:
        return {"error": f"client {client_id} not found in clients.yaml"}

    url = sample_url or _resolve_probe_url(client_cfg)
    probed = _http_probe(url) if url else {"error": "no_url"}

    detection_per_category: dict[str, dict] = {}
    if "error" not in probed:
        detection_per_category = _detect_all(signatures, probed)
    else:
        log.warning(f"[tech_stack] {client_id}: probe failed: {probed.get('error')}")

    declared = client_cfg.get("tech_stack") or {}

    categories: dict[str, dict] = {}
    notifications: list[str] = []
    for cat, _tools in (signatures.items() if isinstance(signatures, dict) else []):
        if cat in ("version", "last_updated", "schema_version"):
            continue
        det = detection_per_category.get(cat) or {"detected": None, "confidence": "unknown", "matched_signals": []}
        # M-1: list 宣言を全件保持 ([meta, google] のような複数宣言を全件診断)
        declared_items = _to_declared_list(declared.get(cat))
        diagnosis = _diagnose(det, declared_items, cat)
        categories[cat] = {
            "detected":   det["detected"],
            "confidence": det["confidence"],
            "matched_signals": det["matched_signals"],
            "declared":   declared_items if len(declared_items) > 1 else (declared_items[0] if declared_items else None),
            "declared_list": declared_items,
            "diagnosis":  diagnosis,
        }
        if diagnosis in ("detected_only", "declared_only", "mismatch"):
            notifications.append(_format_notification(client_id, cat, categories[cat]))

    summary = _build_summary(categories)
    result = {
        "client_id":     client_id,
        "validated_at":  datetime.now().isoformat(timespec="seconds"),
        "url_probed":    url,
        "probe_status":  probed.get("status"),
        "probe_error":   probed.get("error"),
        "categories":    categories,
        "summary":       summary,
        "internal_notifications": notifications,
    }
    _persist_history(client_id, result)
    return result


def detect_stack_from_html(probed: dict, signatures: Optional[dict] = None) -> dict:
    """テスト用: 既に取得済の {html, headers, cookies, url} 辞書から検出のみ実行"""
    sigs = signatures or _load_signatures()
    return _detect_all(sigs, probed)


def score_signals(strong_hits: int, medium_hits: int, weak_hits: int) -> str:
    """ADR-015 §2.2 修正版の合算ロジック

    weak は単独では未検出扱い、medium/strong との同時ヒットで初めて加算される。
    """
    if strong_hits >= 1:
        return "high"
    # weak は medium/strong が 1 件以上あって初めて medium に昇格
    if medium_hits >= 1 or strong_hits >= 1:
        effective_medium = medium_hits + weak_hits
    else:
        effective_medium = 0  # weak 単独は無視
    if effective_medium >= 2:
        return "medium"
    if medium_hits >= 1:
        return "low"
    return "unknown"


# ========== HTTP Probe ==========

def _http_probe(url: str) -> dict:
    """LP に GET、HTML / headers / cookies / final_url を取得 (SSRF 対策込み)

    セキュリティ制約:
      - scheme: https のみ許容 (http はエラー)
      - 解決後 IP が private / loopback / link-local なら拒否
      - redirect は手動追跡で最大 HTTP_MAX_REDIRECTS 回 (3)
      - レスポンス body は HTTP_MAX_BODY_BYTES (2 MB) で truncate
      - Content-Type が text/html / application/xhtml+xml 以外はスキップ
    """
    import urllib.request
    import urllib.error

    current_url = url
    accumulated_cookies: list[str] = []
    for hop in range(HTTP_MAX_REDIRECTS + 1):
        guard = _validate_url_for_probe(current_url)
        if guard.get("error"):
            return {"url": url, "final_url": current_url, "error": guard["error"]}

        req = urllib.request.Request(
            current_url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
        )
        # 手動 redirect 追跡: HTTPRedirectHandler を無効化
        opener = urllib.request.build_opener(_NoRedirectHandler())
        try:
            resp = opener.open(req, timeout=HTTP_TIMEOUT_SEC)
        except _RedirectIntercepted as e:
            # Location を取り出して次のホップへ
            loc = e.location
            if not loc:
                return {"url": url, "final_url": current_url, "error": "redirect_without_location"}
            # Location が相対なら絶対化
            if loc.startswith("/"):
                p = urlparse(current_url)
                loc = f"{p.scheme}://{p.netloc}{loc}"
            current_url = loc
            # Set-Cookie を蓄積
            for k, v in e.headers:
                if k.lower() == "set-cookie":
                    accumulated_cookies.append(v.split("=", 1)[0].strip())
            continue
        except urllib.error.HTTPError as e:
            return {"url": url, "final_url": current_url, "error": f"HTTPError {e.code}", "status": e.code}
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return {"url": url, "final_url": current_url, "error": f"URLError: {e}"}
        except Exception as e:
            return {"url": url, "final_url": current_url, "error": f"unexpected: {e}"}

        try:
            return _build_probe_result(url, current_url, resp, accumulated_cookies)
        finally:
            try:
                resp.close()
            except Exception:
                pass

    return {"url": url, "final_url": current_url, "error": f"too_many_redirects (>{HTTP_MAX_REDIRECTS})"}


class _RedirectIntercepted(Exception):
    """301/302/303/307/308 を捕捉して手動追跡に回すための内部例外"""
    def __init__(self, code: int, location: str, headers):
        self.code = code
        self.location = location
        self.headers = headers


class _NoRedirectHandler(__import__("urllib.request", fromlist=["HTTPRedirectHandler"]).HTTPRedirectHandler):
    """3xx を例外化して上位ハンドラに redirect 制御を委ねる"""

    def http_error_301(self, req, fp, code, msg, headers):
        raise _RedirectIntercepted(code, headers.get("Location"), list(headers.items()))

    def http_error_302(self, req, fp, code, msg, headers):
        raise _RedirectIntercepted(code, headers.get("Location"), list(headers.items()))

    def http_error_303(self, req, fp, code, msg, headers):
        raise _RedirectIntercepted(code, headers.get("Location"), list(headers.items()))

    def http_error_307(self, req, fp, code, msg, headers):
        raise _RedirectIntercepted(code, headers.get("Location"), list(headers.items()))

    def http_error_308(self, req, fp, code, msg, headers):
        raise _RedirectIntercepted(code, headers.get("Location"), list(headers.items()))


def _validate_url_for_probe(url: str) -> dict:
    """SSRF 防御: scheme チェック + ホスト名解決 + private IP 拒否

    Returns:
        {"error": <str>} を返したらブロック、空 dict なら通過
    """
    try:
        parsed = urlparse(url)
    except Exception as e:
        return {"error": f"invalid_url: {e}"}

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return {"error": f"scheme_blocked: only {sorted(ALLOWED_SCHEMES)} allowed, got {parsed.scheme!r}"}
    if not parsed.hostname:
        return {"error": "no_hostname"}

    host = parsed.hostname
    # ホスト名 → 全 IP 解決して各 IP を private チェック
    try:
        addr_info = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        return {"error": f"dns_resolution_failed: {e}"}
    for family, _, _, _, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        for net in _BLOCKED_IP_NETWORKS:
            if ip in net:
                return {"error": f"private_ip_blocked: {host} -> {ip_str} ({net})"}
    return {}


def _build_probe_result(orig_url: str, final_url: str, resp, accumulated_cookies: list) -> dict:
    """response から probe 結果を構築。Content-Type / size 制約も適用"""
    content_type = (resp.headers.get("Content-Type") or "").lower()
    if not any(content_type.startswith(p) for p in ALLOWED_CONTENT_TYPE_PREFIXES):
        return {
            "url": orig_url, "final_url": final_url,
            "status": resp.status, "error": f"content_type_skipped: {content_type!r}",
        }

    raw = resp.read(HTTP_MAX_BODY_BYTES + 1)
    truncated = len(raw) > HTTP_MAX_BODY_BYTES
    if truncated:
        raw = raw[:HTTP_MAX_BODY_BYTES]

    charset = "utf-8"
    m = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    if m:
        charset = m.group(1).lower()
    try:
        html = raw.decode(charset, errors="replace")
    except LookupError:
        html = raw.decode("utf-8", errors="replace")

    headers = {k.lower(): v for k, v in resp.getheaders()}
    cookies = list(accumulated_cookies)
    for k, v in resp.getheaders():
        if k.lower() == "set-cookie":
            cookies.append(v.split("=", 1)[0].strip())

    return {
        "url":       orig_url,
        "final_url": final_url,
        "status":    resp.status,
        "html":      html,
        "headers":   headers,
        "cookies":   cookies,
        "truncated": truncated,
    }


# ========== Detection ==========

def _detect_all(signatures: dict, probed: dict) -> dict:
    """全カテゴリ × 全ツールに対してシグナル合算 → カテゴリ別の最有力ツールを返す"""
    out: dict[str, dict] = {}
    for cat, tools in signatures.items():
        if cat in ("version", "last_updated", "schema_version"):
            continue
        if not isinstance(tools, dict):
            continue
        out[cat] = _detect_category(cat, tools, probed)
    return out


def _detect_category(category: str, tools: dict, probed: dict) -> dict:
    """1 カテゴリ内で全ツールを評価し最高 confidence のツールを採用"""
    candidates = []
    for tool, sigs in tools.items():
        if not isinstance(sigs, dict):
            continue
        strong = sigs.get("strong") or []
        medium = sigs.get("medium") or []
        weak = sigs.get("weak") or []
        indirect = sigs.get("indirect") or []

        s_hit = [s for s in strong if _signal_matches(s, probed)]
        m_hit = [s for s in medium if _signal_matches(s, probed)]
        w_hit = [s for s in weak if _signal_matches(s, probed)]
        i_hit = [s for s in indirect if _signal_matches(s, probed)]

        # CAPI などの indirect カテゴリは strong/medium と同列に扱う
        if i_hit and not (s_hit or m_hit or w_hit):
            confidence = "low" if len(i_hit) == 1 else "medium"
        else:
            confidence = score_signals(len(s_hit), len(m_hit), len(w_hit))

        if confidence == "unknown":
            continue
        candidates.append({
            "tool":             tool,
            "confidence":       confidence,
            "matched_signals":  s_hit + m_hit + w_hit + i_hit,
            "_strong_count":    len(s_hit),
            "_medium_count":    len(m_hit),
            "_weak_count":      len(w_hit),
            "_indirect_count":  len(i_hit),
        })

    # 採用基準: confidence rank → strong → medium → weak の順で最強候補
    confidence_rank = {"high": 3, "medium": 2, "low": 1, "unknown": 0}
    if not candidates:
        return {"detected": None, "confidence": "unknown", "matched_signals": []}
    candidates.sort(
        key=lambda c: (
            -confidence_rank.get(c["confidence"], 0),
            -c["_strong_count"], -c["_medium_count"], -c["_weak_count"],
        ),
    )
    best = candidates[0]
    return {
        "detected":        best["tool"],
        "confidence":      best["confidence"],
        "matched_signals": best["matched_signals"],
    }


def _signal_matches(signal: str, probed: dict) -> bool:
    """単一シグナル文字列を probed dict と照合"""
    if ":" not in signal:
        return False
    kind, value = signal.split(":", 1)
    kind = kind.strip()
    value = value.strip()
    html = probed.get("html") or ""
    headers = probed.get("headers") or {}
    cookies = probed.get("cookies") or []
    url = probed.get("final_url") or probed.get("url") or ""

    if kind == "http_header":
        return value.lower() in headers
    if kind == "html_contains":
        return value in html
    if kind == "js_global":
        # 単純な部分一致 (window.<varname> / var <varname> = / <varname> = の何れかをラフに検出)
        patterns = [
            f"window.{value}", f"var {value}", f"{value}=", f"{value} =",
            f"\"{value}\"", f"'{value}'",
        ]
        return any(p in html for p in patterns)
    if kind == "cookie":
        return any(c == value or c.startswith(value + "=") for c in cookies)
    if kind == "domain_regex":
        host = urlparse(url).netloc
        try:
            return bool(re.search(value, host))
        except re.error:
            return False
    if kind == "url_path_regex":
        path = urlparse(url).path + (("?" + urlparse(url).query) if urlparse(url).query else "")
        try:
            return bool(re.search(value, path))
        except re.error:
            return False
    return False


# ========== Diagnosis ==========

def _diagnose(detection: dict, declared, category: str) -> str:
    """4 状態判定: match / detected_only / declared_only / mismatch / pending

    declared は str / list / None を受ける (M-1: list 宣言の全件診断)。
    list の場合: detected が要素のいずれかと一致すれば match、不在なら declared_only、
                detected が一致しなければ mismatch、未検出かつ全要素 unknown なら pending。
    """
    detected = detection.get("detected")
    confidence = detection.get("confidence", "unknown")
    declared_list = _to_declared_list(declared)

    # 全要素が unknown / pending / 空 → 宣言なし扱い
    has_real_declared = any(d not in (None, "", "unknown", "pending") for d in declared_list)
    if not has_real_declared:
        if detected:
            return "detected_only"
        return "pending"
    if not detected or confidence == "unknown":
        return "declared_only"
    if detected in declared_list:
        return "match"
    return "mismatch"


def _to_declared_list(decl) -> list:
    """宣言値を list 形式に正規化 (M-1: 複数宣言を全件保持)

    入力例:
      - "ecforce"               → ["ecforce"]
      - ["meta", "google"]       → ["meta", "google"]
      - {"value": "gtm"}         → ["gtm"]
      - {"value": "unknown"}     → ["unknown"]
      - None                     → []
    """
    if decl is None:
        return []
    if isinstance(decl, str):
        return [decl]
    if isinstance(decl, list):
        # list 要素が dict ({value:...}) の場合も拾う
        out = []
        for item in decl:
            if isinstance(item, dict):
                v = item.get("value")
                if v is not None:
                    out.append(v)
            elif item is not None:
                out.append(item)
        return out
    if isinstance(decl, dict):
        v = decl.get("value")
        return [v] if v is not None else []
    return []


# レガシー API 互換 (テスト等が直接呼ぶケースに備える)
def _normalize_declared(decl) -> Optional[str]:
    """単一値の取得が必要な箇所向け。list 宣言時は最初の値を返す。"""
    items = _to_declared_list(decl)
    return items[0] if items else None


# ========== Helpers ==========

def _load_signatures() -> dict:
    return yaml.safe_load(SIGNATURES_PATH.read_text(encoding="utf-8")) or {}


def _load_client_cfg(client_id: str) -> dict:
    cfg = yaml.safe_load(CLIENTS_PATH.read_text(encoding="utf-8")) or {}
    return cfg.get("clients", {}).get(client_id, {}) or {}


def _resolve_probe_url(client_cfg: dict) -> Optional[str]:
    """clients.yaml から検査用 URL を解決"""
    # 優先順: tech_stack.probe_url → seo.site_url → company.url
    ts = client_cfg.get("tech_stack") or {}
    if isinstance(ts, dict) and ts.get("probe_url"):
        return ts["probe_url"]
    seo = client_cfg.get("seo") or {}
    if seo.get("site_url"):
        return seo["site_url"]
    company = client_cfg.get("company") or {}
    return company.get("url")


def _build_summary(categories: dict) -> dict:
    counts = {"match": 0, "detected_only": 0, "declared_only": 0, "mismatch": 0, "pending": 0}
    for c in categories.values():
        diag = c.get("diagnosis") or "pending"
        counts[diag] = counts.get(diag, 0) + 1
    return {
        "total_categories":  len(categories),
        "by_diagnosis":      counts,
        "auto_categories":   list(PHASE_A_AUTO_CATEGORIES),
    }


def _format_notification(client_id: str, category: str, info: dict) -> str:
    diag = info["diagnosis"]
    declared = info["declared"]
    detected = info["detected"]
    confidence = info["confidence"]
    if diag == "detected_only":
        return f"[{client_id}] {category}: 未宣言だが検出 (detected={detected}, confidence={confidence})"
    if diag == "declared_only":
        return f"[{client_id}] {category}: 宣言値あり (declared={declared}) だが LP から検出できず"
    if diag == "mismatch":
        return f"[{client_id}] {category}: 不一致 (declared={declared}, detected={detected}, confidence={confidence})"
    return f"[{client_id}] {category}: {diag}"


def _persist_history(client_id: str, result: dict) -> None:
    """outputs/{client_id}/tech_stack_verification.yaml に append"""
    path = OUTPUT_DIR / client_id / "tech_stack_verification.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {"client_id": client_id, "history": []}
        except Exception:
            data = {"client_id": client_id, "history": []}
    else:
        data = {"client_id": client_id, "history": []}
    data.setdefault("history", []).append(result)
    data["last_validated_at"] = result["validated_at"]
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(path)
