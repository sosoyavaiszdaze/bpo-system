"""ルール preflight チェック (R2: 5/7 23:xx)

責務: 起動時に config/foundation/, verticals/, ec_platforms/, precision_categories/
      配下の全ルールについて trigger.condition を空 namespace で eval 試行し、
      構文エラー / NameError / 未定義参照を検出する。

検出対象:
    - SyntaxError / IndentationError (eval 時)
    - NameError (未定義変数参照)
    - その他 Python 例外 (eval が落ちるケース全般)

検出されないが懸念事項として残るもの:
    - "意味的に間違った" condition (構文は OK だが論理が逆等)
    - data_source で約束されたフィールドが trigger に出てこない不整合
    - prerequisite で参照される rule_id の存在
    → これらは別 lint で対応 (Phase B)

呼出:
    venv/bin/python3 scripts/preflight_rule_check.py [--fail-fast]

Phase A 既定: warn-only (exit 0、ログに警告のみ)
将来: --fail-fast 化、CI でも実行、launchd 起動前に呼出
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
LAYER_DIRS = {
    "foundation":          ROOT / "config" / "foundation",
    "vertical":            ROOT / "config" / "verticals",
    "ec_platform":         ROOT / "config" / "ec_platforms",
    "precision_category":  ROOT / "config" / "precision_categories",
}

log = logging.getLogger("preflight")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ========== Public API ==========

def run_preflight(layer_filter: Optional[list[str]] = None) -> dict:
    """全層のルールを load → trigger.condition を eval で検証

    Returns:
        {
            "total_rules": int,
            "ok_count": int,
            "error_count": int,
            "errors": [{"layer", "file", "rule_id", "exception"}],
            "warnings": [...]
        }
    """
    rules = _load_all_rules(layer_filter)
    log.info(f"preflight: loaded {len(rules)} rules across {len(LAYER_DIRS)} layers")

    errors = []
    warnings = []
    ok_count = 0

    for rule in rules:
        rid = rule.get("id", "?")
        src = rule.get("_source_file", "?")
        layer = rule.get("layer", "?")
        condition = (rule.get("trigger") or {}).get("condition")

        if not condition:
            warnings.append({
                "layer": layer, "file": src, "rule_id": rid,
                "issue": "trigger.condition 未定義",
            })
            continue

        # auto_proposal_engine._evaluate_trigger と同じ namespace で試行
        result = _try_eval(condition)
        if result["ok"]:
            ok_count += 1
            continue
        record = {
            "layer": layer, "file": src, "rule_id": rid,
            "condition": condition,
            "exception": result["exception"],
        }
        if result["severity"] == "error":
            errors.append(record)
        else:
            # severity == "warn": empty namespace 由来の TypeError 等 (実 client_state で動く想定)
            warnings.append(record)

    summary = {
        "total_rules": len(rules),
        "ok_count": ok_count,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }
    return summary


def print_summary(summary: dict) -> None:
    """結果を整形ログ出力"""
    total = summary["total_rules"]
    ok = summary["ok_count"]
    err = summary["error_count"]
    warn = summary["warning_count"]

    log.info(f"preflight 結果: total={total} ok={ok} error={err} warn={warn}")

    if summary["warnings"]:
        log.warning(f"--- warnings ({warn}) ---")
        for w in summary["warnings"][:10]:
            issue = w.get("issue") or w.get("exception", "(no issue)")
            log.warning(f"  [{w['layer']}/{w['rule_id']}] {issue}  ({w['file']})")
        if warn > 10:
            log.warning(f"  ... and {warn - 10} more (TypeError 等は empty namespace 由来、実 client_state で動く想定)")

    if summary["errors"]:
        log.error(f"--- errors ({err}) — 即修正必須 ---")
        for e in summary["errors"][:20]:
            log.error(f"  [{e['layer']}/{e['rule_id']}] {e['exception']}")
            log.error(f"    condition: {e['condition']}")
            log.error(f"    file: {e['file']}")
        if err > 20:
            log.error(f"  ... and {err - 20} more")


# ========== Private ==========

def _load_all_rules(layer_filter: Optional[list[str]] = None) -> list[dict]:
    """auto_proposal_engine._load_all_layers の preflight 用最小再実装"""
    out = []
    for layer_name, layer_dir in LAYER_DIRS.items():
        if layer_filter and layer_name not in layer_filter:
            continue
        if not layer_dir.exists():
            continue
        for yaml_file in sorted(layer_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as e:
                log.error(f"YAML パース失敗: {yaml_file} — {e}")
                continue
            for rule in data.get("rules", []) or []:
                rule.setdefault("layer", layer_name)
                rule.setdefault("_source_file", str(yaml_file.relative_to(ROOT)))
                out.append(rule)
    return out


def _try_eval(condition: str) -> dict:
    """auto_proposal_engine._evaluate_trigger と同じ safe namespace で eval 試行

    namespace は実際の trigger 評価時と同じ構造 (空 dict ベースの _DotDict 互換)。
    NameError / SyntaxError / TypeError 等を捕捉して返す。
    """
    safe_globals = {"__builtins__": {}}
    safe_locals = {
        "client_state":     _PreflightDict(),
        "ad_platform_data": _PreflightDict(),
        "rule_evaluation":  _PreflightDict(),
        "True":  True,
        "False": False,
        "None":  None,
    }
    try:
        eval(condition, safe_globals, safe_locals)
        return {"ok": True, "exception": None, "severity": "ok"}
    except SyntaxError as e:
        # 構文エラー: 即修正必須
        return {"ok": False, "exception": f"SyntaxError: {e}", "severity": "error"}
    except NameError as e:
        # 未定義変数 (例: typo): 即修正必須
        return {"ok": False, "exception": f"NameError: {e}", "severity": "error"}
    except TypeError as e:
        # empty namespace で None vs number 比較等。実 client_state があれば動く想定。
        # → warn 扱いに分類 (Phase B の --client モードでより精緻に検証)
        return {"ok": False, "exception": f"TypeError: {e}", "severity": "warn"}
    except ZeroDivisionError as e:
        return {"ok": False, "exception": f"ZeroDivisionError: {e}", "severity": "error"}
    except Exception as e:
        return {"ok": False, "exception": f"{type(e).__name__}: {e}", "severity": "warn"}


class _PreflightDict(dict):
    """auto_proposal_engine._DotDict と同じ構造を持つテスト用 dict

    存在しない属性アクセスは None を返す (実 trigger eval と同じ挙動)。
    NameError ではなく AttributeError の代わりに None を返すことで、
    "rule_evaluation.foo == True" のようなアクセスが空 namespace でも
    通る (= condition 構文として正しい) ことを検証できる。
    """

    def __getattr__(self, name):
        return None

    def __getitem__(self, key):
        return None


# ========== CLI ==========

def main() -> int:
    parser = argparse.ArgumentParser(description="ルール preflight チェック (R2)")
    parser.add_argument(
        "--fail-fast", action="store_true",
        help="error が 1 件でもあれば exit 1 で停止 (デフォルトは warn-only で exit 0)",
    )
    parser.add_argument(
        "--layer", action="append",
        help="特定の layer のみ検査 (foundation / vertical / ec_platform / precision_category)",
    )
    args = parser.parse_args()

    summary = run_preflight(layer_filter=args.layer)
    print_summary(summary)

    if args.fail_fast and summary["error_count"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
