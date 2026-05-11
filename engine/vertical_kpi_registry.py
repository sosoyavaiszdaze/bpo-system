"""Industry-specific KPI/event registry.

The rule registry answers "what can Zynect check?". This module answers
"for this client's industry, which outcomes and data sources matter?".
It keeps vertical-specific concepts such as registration CV, paid CV, LTV,
MMP, SDK, and SKAN out of ad-platform-specific code.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "config" / "vertical_kpi_registry.yaml"


@dataclass(frozen=True)
class VerticalKPIProfile:
    vertical_id: str
    display_name: str
    primary_goal: str
    funnel_events: dict[str, dict[str, Any]]
    economic_metrics: dict[str, dict[str, Any]]
    quality_dimensions: list[dict[str, Any]]
    data_sources: dict[str, dict[str, Any]]
    notification_focus: list[str]
    rule_focus: dict[str, list[str]]
    aliases: tuple[str, ...] = ()
    raw: dict[str, Any] | None = None

    @property
    def required_event_ids(self) -> list[str]:
        return [
            event_id
            for event_id, spec in self.funnel_events.items()
            if spec.get("required") is True
        ]

    @property
    def required_data_source_ids(self) -> list[str]:
        return [
            source_id
            for source_id, spec in self.data_sources.items()
            if spec.get("required") is True
        ]


@lru_cache(maxsize=4)
def load_vertical_kpi_registry(path: str | Path | None = None) -> dict[str, VerticalKPIProfile]:
    """Load KPI profiles keyed by vertical id and aliases."""
    registry_path = Path(path) if path else DEFAULT_PATH
    raw = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    profiles = raw.get("profiles") or {}
    out: dict[str, VerticalKPIProfile] = {}
    for vertical_id, payload in profiles.items():
        if not isinstance(payload, dict):
            continue
        profile = VerticalKPIProfile(
            vertical_id=str(vertical_id),
            display_name=str(payload.get("display_name") or vertical_id),
            primary_goal=str(payload.get("primary_goal") or ""),
            funnel_events=payload.get("funnel_events") if isinstance(payload.get("funnel_events"), dict) else {},
            economic_metrics=payload.get("economic_metrics") if isinstance(payload.get("economic_metrics"), dict) else {},
            quality_dimensions=payload.get("quality_dimensions") if isinstance(payload.get("quality_dimensions"), list) else [],
            data_sources=payload.get("data_sources") if isinstance(payload.get("data_sources"), dict) else {},
            notification_focus=[
                str(x) for x in (payload.get("notification_focus") or [])
            ],
            rule_focus={
                str(k): [str(x) for x in (v or [])]
                for k, v in (payload.get("rule_focus") or {}).items()
                if isinstance(v, list)
            },
            aliases=tuple(str(x) for x in (payload.get("aliases") or [])),
            raw=payload,
        )
        out[profile.vertical_id] = profile
        for alias in profile.aliases:
            out[alias] = profile
    return out


def resolve_client_vertical(client_cfg: dict[str, Any] | None) -> str:
    """Return the best vertical key from current client config shapes."""
    client_cfg = client_cfg or {}
    company = client_cfg.get("company") if isinstance(client_cfg.get("company"), dict) else {}
    for key in (
        client_cfg.get("vertical"),
        company.get("industry"),
        client_cfg.get("industry"),
        company.get("vertical"),
    ):
        if key:
            return str(key)
    return "default"


def get_vertical_kpi_profile(client_cfg_or_vertical: dict[str, Any] | str | None) -> VerticalKPIProfile:
    registry = load_vertical_kpi_registry()
    if isinstance(client_cfg_or_vertical, str):
        vertical = client_cfg_or_vertical
    else:
        vertical = resolve_client_vertical(client_cfg_or_vertical)
    return registry.get(vertical) or registry.get("default")


def build_client_kpi_readiness(client_id: str, client_cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Summarize which vertical-specific KPI sources are configured.

    This is deliberately conservative: it reports configuration readiness, not
    whether the third-party API actually works. Runtime health remains in
    Decision Trace / job metrics.
    """
    client_cfg = client_cfg or {}
    profile = get_vertical_kpi_profile(client_cfg)
    source_status = {}
    for source_id, spec in profile.data_sources.items():
        configured = _source_configured(source_id, client_cfg)
        source_status[source_id] = {
            "configured": configured,
            "required": spec.get("required") is True,
            "strongly_recommended": spec.get("strongly_recommended") is True,
            "accepted": spec.get("accepted") or [],
        }

    required_missing = [
        sid for sid, row in source_status.items()
        if row["required"] and not row["configured"]
    ]
    recommended_missing = [
        sid for sid, row in source_status.items()
        if row["strongly_recommended"] and not row["configured"]
    ]
    return {
        "client_id": client_id,
        "vertical_id": profile.vertical_id,
        "display_name": profile.display_name,
        "primary_goal": profile.primary_goal,
        "required_events": [
            {
                "event_id": event_id,
                "label": spec.get("label") or event_id,
                "canonical": spec.get("canonical") or event_id,
                "source_preference": spec.get("source_preference") or [],
            }
            for event_id, spec in profile.funnel_events.items()
            if spec.get("required") is True
        ],
        "economic_metrics": profile.economic_metrics,
        "quality_dimensions": profile.quality_dimensions,
        "source_status": source_status,
        "required_missing": required_missing,
        "recommended_missing": recommended_missing,
        "ready_for_high_confidence_recommendations": not required_missing,
        "notification_focus": profile.notification_focus,
        "rule_focus": profile.rule_focus,
    }


def normalize_vertical_event(event_name: str, profile: VerticalKPIProfile | str) -> str | None:
    """Map app/product event names to a vertical canonical event id."""
    if isinstance(profile, str):
        profile = get_vertical_kpi_profile(profile)
    target = str(event_name or "").strip().lower()
    if not target:
        return None
    for event_id, spec in profile.funnel_events.items():
        names = {event_id, str(spec.get("canonical") or "")}
        names.update(str(x) for x in (spec.get("synonyms") or []))
        if target in {x.lower() for x in names if x}:
            return event_id
    return None


def _source_configured(source_id: str, client_cfg: dict[str, Any]) -> bool:
    ads = client_cfg.get("ads") if isinstance(client_cfg.get("ads"), dict) else {}
    app = client_cfg.get("app") if isinstance(client_cfg.get("app"), dict) else {}
    analytics = client_cfg.get("analytics") if isinstance(client_cfg.get("analytics"), dict) else {}
    integrations = client_cfg.get("integrations") if isinstance(client_cfg.get("integrations"), dict) else {}

    if source_id in {"meta_api", "ad_platform_api"}:
        meta = ads.get("meta") if isinstance(ads.get("meta"), dict) else {}
        return bool(meta.get("enabled") is not False and meta.get("account_id"))
    if source_id == "mmp":
        mmp = app.get("mmp") or analytics.get("mmp") or integrations.get("mmp")
        return bool(_enabled_or_path(mmp))
    if source_id in {"sdk_or_backend_events", "conversion_source"}:
        return any(
            bool(app.get(k))
            for k in ("sdk_event_log_path", "backend_event_log_path", "event_csv_path")
        ) or bool(analytics.get("events_path"))
    if source_id == "skan":
        skan = app.get("skan") or integrations.get("skan")
        return bool(_enabled_or_path(skan) or app.get("skan_report_path"))
    if source_id == "app_store_revenue":
        revenue = app.get("revenue") or integrations.get("revenue")
        return bool(_enabled_or_path(revenue) or app.get("app_store_revenue_path"))
    if source_id == "ec_platform":
        return bool(client_cfg.get("ec_platform") or (client_cfg.get("company") or {}).get("ec_platform"))
    if source_id == "crm":
        return bool(client_cfg.get("crm") or integrations.get("crm"))
    return bool(client_cfg.get(source_id))


def _enabled_or_path(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value.get("enabled") or value.get("path") or value.get("provider"))
    return bool(value)
