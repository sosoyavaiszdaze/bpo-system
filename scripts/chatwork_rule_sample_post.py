"""5 主要 rule_id の daily_indication.md.j2 レンダリングサンプル投稿
(ADR-005 / Day 3 G タスク後 — 目視確認用)

実行: venv/bin/python3 scripts/chatwork_rule_sample_post.py

仕様:
- 投稿先: rid 435851481 (パイロットン ad通知パイプライン)
- [テスト] プレフィクス必須、後で削除可能
- 各投稿に投稿時刻のサフィックスを付けて idempotency 衝突を回避
- pilotton 真値 (CV 156 / CPA ¥9,251 / 月次 ¥1,443,150) を流用
- 5 rule_id それぞれに応じた severity / target / metadata で生成
- 5 投稿 = 5 message_id を取得して報告
"""
from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from notifiers.chatwork_notifier import ChatWorkClient, ChatWorkError  # noqa: E402
from templates.chatwork import render  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("rule_sample")

# pilotton 真値 (Day 5.2 / 直近 30 日)
PILOTTON_REAL_CV = 156
PILOTTON_REAL_CPA = "¥9,251"
PILOTTON_REAL_SPEND = "¥1,443,150"
TEST_DISPLAY_NAME = "[テスト] 株式会社パイロットン"

# 各 rule_id 別のサンプルケース
SAMPLES = [
    {
        "rule_id": "DQ-CAPI-MISSING",
        "title": "CAPI（Conversion API）未実装",
        "severity_label": "重要度高",
        "fact": (
            f"直近30日の Pixel 計測 CV {PILOTTON_REAL_CV} 件 / CPA {PILOTTON_REAL_CPA}"
            f"（消化額 {PILOTTON_REAL_SPEND}）。iOS14.5+ の SKAN 影響で計測欠損 30-40% 推定。"
        ),
        "impact": (
            "CV 計測欠損により学習シグナルが減衰し、Meta 配信最適化が本来到達点に届いていません。"
            "CAPI を有効化することで Pixel + Server の二重計測 (dedup 済) で精度回復が見込めます。"
        ),
        "scenario_label": "現実シナリオ",
        "expected_effect": "月次 CPA -8% (¥9,251 → ¥8,510 程度、CV +12〜15件)",
        "payload": {},
    },
    {
        "rule_id": "PIXEL-DORMANT",
        "title": "Pixel 休眠検出",
        "severity_label": "重要度高",
        "fact": (
            "MYNAILPLEX 用に過去作成された Pixel が直近 312 日間イベント受信なし。"
            "現在稼働中のメイン Pixel と並走しており、レポート集計の混線リスクあり。"
        ),
        "impact": (
            "休眠 Pixel に紐付く広告セットが残っていた場合、メイン Pixel への学習が分散します。"
            "整理することで配信学習の集約と運用ミス防止に寄与します。"
        ),
        "scenario_label": "現実シナリオ",
        "expected_effect": "学習集約による CPA -3〜5%、運用ミス工数削減",
        "payload": {"dormant_days": 312},
    },
    {
        "rule_id": "DOMAIN-NOT-VERIFIED",
        "title": "ドメイン認証未完了",
        "severity_label": "重要度高",
        "fact": (
            "Meta Business Manager の「ドメイン」一覧に対象ドメイン (mynailplex.example) が未登録。"
            "AEM (集計イベント測定) の前提条件である認証が未完のため iOS14.5+ 配信が抑制されています。"
        ),
        "impact": (
            "iOS opt-out ユーザの CV シグナルが Meta 側で計測できず、入札最適化が劣化。"
            "認証完了は AEM 設定 (M61) を解放する前提条件となります。"
        ),
        "scenario_label": "現実シナリオ",
        "expected_effect": "iOS 配信のクリック→CV 経路が回復、CPA -5〜10%",
        "payload": {"recommended_method": "dns"},
    },
    {
        "rule_id": "AEM-NOT-CONFIGURED",
        "title": "AEM 設定 (新仕様への移行)",
        "severity_label": "重要度高",
        "fact": (
            "2025 年 6 月に Meta は AEM の 8 イベント枠制限と手動優先順位設定を撤廃。"
            "現在は対象ドメインの全イベントが自動集計される仕様に変更されています。"
            "対象 Pixel で value 最適化が無効、Purchase の value bucket 設定が未調整です。"
        ),
        "impact": (
            "iOS opt-out ユーザに対する CV value 推定が機能せず、ROAS 最適化が活用できません。"
            "Purchase イベントで value 最適化を有効化することで Meta の自動配信ロジックが改善します。"
        ),
        "scenario_label": "現実シナリオ",
        "expected_effect": "ROAS 系広告セットの効率改善、value 推定で CPA 改善 -5%",
        "payload": {},
    },
    {
        "rule_id": "FIRST-PARTY-DATA-MISSING",
        "title": "1st パーティデータ未連携 (Customer File 未アップロード)",
        "severity_label": "重要度高",
        "fact": (
            "Meta Audiences に自社顧客リストが未連携。"
            "Lookalike (類似オーディエンス) のシードとなる Customer Audience が存在せず、"
            "新規獲得の効率化施策が機能していません。"
        ),
        "impact": (
            "既存顧客の除外配信ができず、Lookalike 1〜3% シードによる新規拡張も未活用。"
            "Customer File アップロードで両方が解放されます。"
        ),
        "scenario_label": "現実シナリオ",
        "expected_effect": "新規獲得 CPA -10〜15%、ターゲティング精度向上",
        "payload": {},
    },
]


def build_indication_context(sample: dict, suffix: str) -> dict:
    """1 件指摘の indication context を生成。client_display_name に時刻 suffix を付与し
    idempotency 衝突を回避する。
    """
    return {
        "client_display_name": f"{TEST_DISPLAY_NAME} #{suffix}",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "greeting": (
            "本投稿は ADR-005 G タスクのレンダリング目視確認用サンプルです (実運用ではありません)。"
        ),
        "indications": [{
            "title": sample["title"],
            "rule_id": sample["rule_id"],
            "severity_label": sample["severity_label"],
            "fact": sample["fact"],
            "impact": sample["impact"],
            "scenario_label": sample["scenario_label"],
            "expected_effect": sample["expected_effect"],
            "payload": sample["payload"],
        }],
        "footer_note": (
            f"※ 本投稿は smoke test です（[テスト] プレフィクス、suffix={suffix}）。"
            "ChatWork 上で内容を目視確認後、削除いただいて構いません。"
        ),
    }


def main() -> int:
    if not os.environ.get("CHATWORK_API_TOKEN"):
        log.error("CHATWORK_API_TOKEN 未設定")
        return 1
    room_id = os.environ.get("CHATWORK_ROOM_ID_PILOTTON")
    if not room_id:
        log.error("CHATWORK_ROOM_ID_PILOTTON 未設定")
        return 1

    log.info("5 rule_id サンプル投稿開始 (rid 435851481)")
    log.info(f"投稿時刻ベースの suffix で idempotency 衝突を回避")

    client = ChatWorkClient(room_id=room_id)

    posted_results: list[dict] = []
    base_suffix = datetime.now().strftime("%Y%m%d-%H%M%S")

    for i, sample in enumerate(SAMPLES, 1):
        # 各投稿をユニーク化: ベース時刻 + 連番 + 短い uuid
        suffix = f"{base_suffix}-{i:02d}-{uuid.uuid4().hex[:6]}"
        log.info("=" * 60)
        log.info(f"[{i}/{len(SAMPLES)}] {sample['rule_id']} (suffix={suffix})")

        ctx = build_indication_context(sample, suffix)
        body = render("daily_indication.md.j2", ctx)
        log.info(f"  本文長: {len(body)} 文字")

        try:
            result = client.post_message(body)
            if result.get("skipped"):
                log.warning(f"  ⚠ SKIPPED (idempotency hit、suffix の unique 化が効いていない可能性)")
            else:
                msg_id = result.get("message_id", "")
                log.info(f"  ✅ POSTED message_id={msg_id}")
                posted_results.append({
                    "rule_id": sample["rule_id"],
                    "message_id": msg_id,
                    "suffix": suffix,
                    "body_length": len(body),
                })
        except ChatWorkError as e:
            log.error(f"  ❌ FAILED: {e}")
            posted_results.append({
                "rule_id": sample["rule_id"],
                "error": str(e),
                "suffix": suffix,
            })

        # ChatWork rate limit (5/sec) に余裕を持って 0.3s sleep
        time.sleep(0.3)

    log.info("=" * 60)
    log.info("投稿完了サマリー")
    log.info(f"  成功: {sum(1 for r in posted_results if 'message_id' in r)} / {len(SAMPLES)}")
    for r in posted_results:
        if "message_id" in r:
            log.info(f"  • {r['rule_id']:30s} → message_id={r['message_id']}")
        else:
            log.info(f"  • {r['rule_id']:30s} → ERROR: {r.get('error', 'unknown')}")

    return 0 if all("message_id" in r for r in posted_results) else 2


if __name__ == "__main__":
    sys.exit(main())
