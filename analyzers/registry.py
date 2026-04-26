"""チェックモジュール レジストリ — 動的importを一元管理"""
import logging

log = logging.getLogger("bpo")

# チェックモジュール定義: (module_path, function_name, extra_args_keys)
CHECK_MODULES = [
    {
        "name": "common",
        "module": "analyzers.checks.common",
        "function": "run_common_checks",
        "args": ["campaigns", "thresholds"],
    },
    {
        "name": "google",
        "module": "analyzers.checks.google",
        "function": "run_google_checks",
        "args": ["campaigns", "thresholds"],
    },
    {
        "name": "meta",
        "module": "analyzers.checks.meta",
        "function": "run_meta_checks",
        "args": ["campaigns", "thresholds", "pixel_status"],
    },
    {
        "name": "tiktok",
        "module": "analyzers.checks.tiktok",
        "function": "run_tiktok_checks",
        "args": ["campaigns", "thresholds", "pixel_status"],
    },
    {
        "name": "cross",
        "module": "analyzers.checks.cross",
        "function": "run_cross_checks",
        "args": ["campaigns", "thresholds"],
    },
]


def run_all_checks(campaigns, thresholds, data=None):
    """全チェックモジュールを実行して結果をmerge

    Args:
        campaigns: キャンペーンリスト
        thresholds: 閾値設定
        data: unified format データ全体 (pixel_status等を含む)
    Returns:
        list[dict]: 全チェック結果
    """
    import importlib

    all_results = []
    # pixel_statuses は {"meta": {...}, "tiktok": {...}} 形式（fetch_dataで構築）
    # pixel_status はレガシー形式（単一dict）
    pixel_statuses = (data or {}).get("pixel_statuses", {})
    legacy_pixel = (data or {}).get("pixel_status")

    for mod_def in CHECK_MODULES:
        try:
            module = importlib.import_module(mod_def["module"])
            func = getattr(module, mod_def["function"])

            # 引数を組み立て
            kwargs = {}
            for arg in mod_def["args"]:
                if arg == "campaigns":
                    kwargs["campaigns"] = campaigns
                elif arg == "thresholds":
                    kwargs["thresholds"] = thresholds
                elif arg == "pixel_status":
                    # 媒体別pixel_statusを優先、レガシーfallback
                    platform = mod_def.get("name", "")
                    kwargs["pixel_status"] = pixel_statuses.get(platform, legacy_pixel)

            results = func(**kwargs)
            all_results.extend(results)
            log.debug(f"[{mod_def['name']}] {len(results)}件のチェック結果")
        except Exception as e:
            log.warning(f"{mod_def['name']}チェックエラー: {e}")

    return all_results
