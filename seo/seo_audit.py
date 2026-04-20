"""SEO監査 - スタブ（後で実装）"""
import logging
log = logging.getLogger("bpo")

def run_seo_audit(client_id, seo_cfg):
    log.info(f"[{client_id}] SEO監査: スタブ（未実装）")
    return {"status": "stub", "site_url": seo_cfg.get("site_url", "")}
