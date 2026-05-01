#!/usr/bin/env python3
"""config/clients.yaml を v3 形式へマイグレーションする。

設計: docs/report_design/v3_client_config_spec.md

v2 → v3 変換内容:
    - 各クライアントエントリに company / contact / report ブロックを追加
    - 既存 `name` フィールドは v3.0 では後方互換のため残す（v3.2 で廃止予定）
    - 業界・企業正式名称・担当者氏名は "[要入力: ...]" でプレースホルダ化
    - 後で人間が grep "要入力" で見つけて手動入力する

使い方:
    python scripts/migrate_clients_v3.py             # ドライラン（差分のみ表示）
    python scripts/migrate_clients_v3.py --apply     # 実際に書き込み（バックアップ自動取得）

副作用:
    config/clients.yaml.v2.bak.YYYYMMDD-HHMMSS にバックアップを取得してから上書きする。
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "clients.yaml"


def make_yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=2, offset=0)
    return y


def build_company_block(legacy_name: str) -> CommentedMap:
    cm = CommentedMap()
    # legacy_name は社内識別用の便宜的な名前のため、企業正式名称として転用しない
    cm["name"] = "[要入力: 企業正式名称]"
    cm["honorific"] = "御中"
    cm["industry"] = "[要入力: ec_retail / saas_b2b / finance / education / local_service から選択]"
    cm["industry_label"] = "[要入力: 業界の表示ラベル（例: SaaS）]"
    return cm


def build_contact_block() -> CommentedMap:
    cm = CommentedMap()
    cm["name"] = "[要入力: 担当者氏名]"
    cm["honorific"] = "様"
    cm["title"] = "[要入力: 役職（任意）]"
    return cm


def build_report_block() -> CommentedMap:
    cm = CommentedMap()
    cm["display_name"] = "広告アカウント健康診断レポート"
    cm["include_zynect_insights"] = True
    cm["include_appendix"] = True
    cm["premium_model_for_insights"] = False
    cm["report_period_days"] = 30
    return cm


def migrate_client_entry(client_id: str, entry: CommentedMap) -> tuple[bool, list[str]]:
    """1クライアント分のエントリを v3 形式に変換する。

    Returns:
        (changed, fields_added)
    """
    changed = False
    added: list[str] = []

    # company ブロック
    if "company" not in entry:
        legacy_name = entry.get("name", client_id)
        entry.insert(0, "company", build_company_block(legacy_name))
        added.append("company")
        changed = True

    # contact ブロック
    if "contact" not in entry:
        # company の直後に挿入
        keys = list(entry.keys())
        idx = keys.index("company") + 1 if "company" in keys else 0
        entry.insert(idx, "contact", build_contact_block())
        added.append("contact")
        changed = True

    # report ブロック
    if "report" not in entry:
        keys = list(entry.keys())
        idx = keys.index("contact") + 1 if "contact" in keys else len(keys)
        entry.insert(idx, "report", build_report_block())
        added.append("report")
        changed = True

    # legacy `name` は後方互換のため保持（v3.2 で廃止予定）
    return changed, added


def migrate_defaults(defaults: CommentedMap) -> bool:
    """defaults に anthropic_model_premium を追加（v3 設計通り）。"""
    if "anthropic_model_premium" in defaults:
        return False
    defaults["anthropic_model_premium"] = "claude-opus-4-7"
    return True


def collect_required_inputs(client_id: str, entry: CommentedMap) -> list[dict]:
    """`[要入力: ...]` プレースホルダを収集して、人間が後で埋める一覧を返す。"""
    out: list[dict] = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, str) and node.startswith("[要入力:"):
            out.append({"client": client_id, "field": path, "value": node})

    walk(entry, "")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="clients.yaml を v3 形式へマイグレーション")
    parser.add_argument("--apply", action="store_true", help="実際にファイルを上書きする（既定はドライラン）")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="対象 yaml パス")
    args = parser.parse_args()

    if not args.config.exists():
        print(f"ERROR: {args.config} が見つかりません", file=sys.stderr)
        return 1

    yaml = make_yaml()
    with args.config.open("r", encoding="utf-8") as f:
        data = yaml.load(f)

    if data is None or "clients" not in data:
        print("ERROR: clients ブロックが見つかりません", file=sys.stderr)
        return 1

    summary: list[dict] = []
    required_inputs: list[dict] = []

    # defaults の更新
    if "defaults" in data:
        if migrate_defaults(data["defaults"]):
            summary.append({"client": "(defaults)", "added": ["anthropic_model_premium"]})

    # 各クライアントの変換
    for client_id, entry in data["clients"].items():
        if not isinstance(entry, dict):
            continue
        changed, added = migrate_client_entry(client_id, entry)
        if changed:
            summary.append({"client": client_id, "added": added})
        required_inputs.extend(collect_required_inputs(client_id, entry))

    # 結果出力
    print("=" * 60)
    print(f"対象ファイル: {args.config}")
    print(f"クライアント数: {len(data['clients'])}")
    print("=" * 60)
    if summary:
        for s in summary:
            print(f"  [{s['client']}] 追加ブロック: {s['added']}")
    else:
        print("  変更なし（既に v3 形式）")
    print()

    if required_inputs:
        print("=" * 60)
        print(f"⚠️ 人間が手動入力すべきフィールド: {len(required_inputs)} 件")
        print("=" * 60)
        for r in required_inputs:
            print(f"  [{r['client']}] {r['field']}")
            print(f"    現在: {r['value']}")
        print()
        print("→ ファイルを開いて grep '要入力' で全箇所を確認できます")
        print()

    # 書き込み
    if args.apply:
        ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = args.config.with_suffix(f".yaml.v2.bak.{ts}")
        shutil.copy2(args.config, backup)
        print(f"✅ バックアップ作成: {backup}")

        with args.config.open("w", encoding="utf-8") as f:
            yaml.dump(data, f)
        print(f"✅ マイグレーション適用済み: {args.config}")
    else:
        print("ℹ️ ドライランです。書き込むには --apply を付けてください")

    return 0


if __name__ == "__main__":
    sys.exit(main())
