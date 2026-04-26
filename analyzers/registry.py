"""チェックモジュール レジストリ — 起動時キャッシュ付き動的import"""
import importlib
import logging

log = logging.getLogger("bpo")

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

# モジュールキャッシュ（初回呼び出しで構築）
_module_cache = {}


def _get_check_func(mod_def):
    """モジュール+関数を1回だけimportしてキャッシュ"""
    key = mod_def["module"] + "." + mod_def["function"]
    if key not in _module_cache:
        module = importlib.import_module(mod_def["module"])
        _module_cache[key] = getattr(module, mod_def["function"])
    return _module_cache[key]


def _build_kwargs(mod_def, campaigns, thresholds, pixel_statuses, legacy_pixel):
    """モジュール定義から関数引数を組み立て"""
    kwargs = {}
    for arg in mod_def["args"]:
        if arg == "campaigns":
            kwargs["campaigns"] = campaigns
        elif arg == "thresholds":
            kwargs["thresholds"] = thresholds
        elif arg == "pixel_status":
            platform = mod_def.get("name", "")
            kwargs["pixel_status"] = pixel_statuses.get(platform, legacy_pixel)
    return kwargs


def run_all_checks(campaigns, thresholds, data=None):
    """全チェックモジュールを実行して結果をmerge

    Args:
        campaigns: キャンペーンリスト
        thresholds: 閾値設定
        data: unified format データ全体 (pixel_status等を含む)
    Returns:
        list[dict]: 全チェック結果
    """
    all_results = []
    pixel_statuses = (data or {}).get("pixel_statuses", {})
    legacy_pixel = (data or {}).get("pixel_status")

    for mod_def in CHECK_MODULES:
        try:
            func = _get_check_func(mod_def)
            kwargs = _build_kwargs(mod_def, campaigns, thresholds, pixel_statuses, legacy_pixel)
            results = func(**kwargs)
            all_results.extend(results)
            log.debug(f"[{mod_def['name']}] {len(results)}件")
        except Exception as e:
            log.warning(f"{mod_def['name']}チェックエラー: {e}", exc_info=True)

    return all_results
