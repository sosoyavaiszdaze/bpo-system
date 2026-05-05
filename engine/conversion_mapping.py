"""ADR-004: conversion_mapping.yaml 読み込みと action_type 正規化ヘルパ。

利用箇所:
    adapters/meta_adapter.py（フル実装）
    adapters/google_adapter.py / tiktok_adapter.py（Phase 2 で利用、現状は enabled=false）

主要関数:
    load_conversion_mapping() — config/conversion_mapping.yaml を Pydantic 検証付きで読み込み
    aggregate_actions(platform, kind, actions) — 正規化済み {canonical: value} を返す
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import yaml

log = logging.getLogger("bpo")

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "conversion_mapping.yaml"


@lru_cache(maxsize=1)
def load_conversion_mapping(path: Path | None = None):
    """conversion_mapping.yaml を読み込んで Pydantic 検証する。

    キャッシュ済 (lru_cache) のため複数 adapter からの呼び出しでも 1 回のみ I/O。

    Returns:
        ConversionMapping (Pydantic) または None（pydantic 未導入時 / 検証失敗時）
    """
    target = path or CONFIG_PATH
    if not target.exists():
        log.warning(f"conversion_mapping.yaml が見つかりません: {target}")
        return None
    with target.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    try:
        from engine.models import ConversionMapping, PYDANTIC_AVAILABLE
        if not PYDANTIC_AVAILABLE:
            log.warning("pydantic 未インストール、未検証の dict として返す")
            return raw
        return ConversionMapping(**raw)
    except Exception as e:
        log.error(f"conversion_mapping.yaml の Pydantic 検証失敗: {e}")
        raise


def _resolve_synonym_map(platform: str, kind: str, mapping=None) -> dict[str, tuple[str, str]]:
    """指定媒体・kind (conversion / revenue) の synonym → (canonical, dedup_strategy) を構築。

    Returns:
        { synonym_action_type: (canonical, dedup_strategy) }
    """
    if mapping is None:
        mapping = load_conversion_mapping()
    if mapping is None:
        return {}

    pf = mapping.get_platform(platform) if hasattr(mapping, "get_platform") else mapping.get("platforms", {}).get(platform)
    if pf is None:
        return {}
    enabled = pf.enabled if hasattr(pf, "enabled") else pf.get("enabled", False)
    if not enabled:
        return {}

    if kind == "conversion":
        types_map = pf.conversion_types if hasattr(pf, "conversion_types") else pf.get("conversion_types", {})
    elif kind == "revenue":
        types_map = pf.revenue_types if hasattr(pf, "revenue_types") else pf.get("revenue_types", {})
    else:
        raise ValueError(f"kind は 'conversion' または 'revenue'、実値: {kind}")

    out: dict[str, tuple[str, str]] = {}
    for _key, ct in types_map.items():
        canonical = ct.canonical if hasattr(ct, "canonical") else ct["canonical"]
        synonyms = ct.synonyms if hasattr(ct, "synonyms") else ct["synonyms"]
        dedup = ct.dedup_strategy if hasattr(ct, "dedup_strategy") else ct.get("dedup_strategy", "max")
        for syn in synonyms:
            out[syn] = (canonical, dedup)
    return out


def aggregate_actions(
    platform: str,
    kind: str,
    actions: Iterable[dict],
    mapping=None,
) -> dict[str, float]:
    """Meta/Google/TikTok の actions リストを canonical 単位で集約する。

    Args:
        platform: 'meta' / 'google' / 'tiktok'
        kind: 'conversion'（CV カウント） or 'revenue'（CV 値）
        actions: [{action_type, value}, ...] リスト
        mapping: ConversionMapping or None（None なら自動ロード）

    Returns:
        {canonical_label: aggregated_value} 例: {"purchase": 161, "lead": 0}

    重複報告（synonym 多重カウント）を回避し、dedup_strategy に従って集約する。
    enabled=false の媒体は空 dict を返す（呼び出し側でフォールバック判断）。
    """
    syn_map = _resolve_synonym_map(platform, kind, mapping=mapping)
    if not syn_map:
        return {}

    # canonical 単位でバケット化
    grouped: dict[str, list[float]] = {}
    canonical_to_strategy: dict[str, str] = {}
    for action in actions or []:
        a_type = action.get("action_type", "")
        if a_type not in syn_map:
            continue
        canonical, dedup = syn_map[a_type]
        try:
            v = float(action.get("value", 0))
        except (TypeError, ValueError):
            v = 0.0
        grouped.setdefault(canonical, []).append(v)
        canonical_to_strategy[canonical] = dedup

    # dedup_strategy 適用
    result: dict[str, float] = {}
    for canonical, values in grouped.items():
        strategy = canonical_to_strategy[canonical]
        if strategy == "max":
            result[canonical] = max(values) if values else 0.0
        elif strategy == "sum":
            result[canonical] = sum(values)
        elif strategy == "first":
            result[canonical] = values[0] if values else 0.0
        else:
            log.warning(f"未知の dedup_strategy={strategy}, max にフォールバック")
            result[canonical] = max(values) if values else 0.0
    return result
