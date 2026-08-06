""" JudgeVerdict
SCP V90 — Reality Judge Module
Cross-check SLMs, verify with reality, produce final verdicts.
Extracted from engine.py for modularity.
"""


import asyncio
import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger("scp.judge")


# [V5.3-WIRE] multi_llm_check integration — opt-in via SCP_MULTI_LLM_CHECK=1 env var.
# TẠI SAO: DNA SCP #20 "ảo giác đồng thuận" — 100 AI cùng kết luận chưa chắc 100
# nguồn độc lập. Cross-check giữa 2-3 LLM providers (OpenRouter + Groq) để phát
# hiện disagreement. Default OFF để không tăng latency khi không cần.
# Lazy singleton — chỉ instantiate khi env var ON.
_MULTI_LLM_CHECKER_SINGLETON = None
_MULTI_LLM_CHECKER_LOCK = __import__("threading").Lock()


def _get_multi_llm_checker():
    """Lazy singleton for MultiLLMChecker. Returns None if env var OFF."""
    global _MULTI_LLM_CHECKER_SINGLETON
    if os.environ.get("SCP_MULTI_LLM_CHECK", "0") != "1":
        return None
    if _MULTI_LLM_CHECKER_SINGLETON is None:
        with _MULTI_LLM_CHECKER_LOCK:
            if _MULTI_LLM_CHECKER_SINGLETON is None:
                try:
                    from scp.meta.multi_llm_check import MultiLLMChecker
                    _MULTI_LLM_CHECKER_SINGLETON = MultiLLMChecker()
                    logger.info("[V5.3-WIRE] MultiLLMChecker initialized — adversary answers will be cross-checked")
                except Exception as e:
                    logger.warning(f"[V5.3-WIRE] MultiLLMChecker init failed: {e} — multi-LLM check disabled")
                    _MULTI_LLM_CHECKER_SINGLETON = False
    return _MULTI_LLM_CHECKER_SINGLETON if _MULTI_LLM_CHECKER_SINGLETON is not False else None


# [V104.42 #AH] Internal signal — source passed watchlist check, proceed to INSERT

# ============================================================
# JUDGE VERDICT
# ============================================================
@dataclass

# ============================================================
# REALITY JUDGE — Cross-check SLMs + Verify với Reality
# ============================================================


class JudgeBgMixin:
    """Mixin for RealityJudge — provides schedule_background_jobs."""

    async def schedule_background_jobs(self):
        """[V98] Start background scheduler for ThreatSimulator + ThreatIntelCrawler.

        Runs indefinitely (until cancelled). Should be started by run_247.py.

        Schedule:
          - ThreatSimulator: every 6 hours
          - ThreatIntelCrawler: every 12 hours
        """
        logger.info("[V98] Background scheduler started")
        sim_interval = int(os.environ.get("SCP_THREAT_SIMULATOR_INTERVAL", "10"))  # V103: 10s default
        sim_count = int(os.environ.get("SCP_THREAT_SIMULATOR_COUNT", "200"))  # V103: 200 attacks/cycle
        intel_interval = 12 * 3600  # 12 hours
        last_sim = 0
        last_intel = 0

        if sim_interval <= 0:
            logger.info("[V98] ThreatSimulator DISABLED (SCP_THREAT_SIMULATOR_INTERVAL=0)")

        while True:
            now = time.time()

            if sim_interval > 0 and now - last_sim >= sim_interval:
                try:
                    report = await self.run_threat_simulation(count=sim_count)
                    if report:
                        logger.info(f"[V98] ThreatSimulator: {report.get('bypass_found', 0)} bypasses found ({sim_count} attacks)")
                    last_sim = now
                except Exception as e:
                    logger.warning(f"[V98] ThreatSimulator error: {e}")

            if now - last_intel >= intel_interval:
                try:
                    updates = await self.run_threat_intel_crawl()
                    logger.info(f"[V98] ThreatIntelCrawler: {len(updates)} sources crawled")
                    last_intel = now
                except Exception as e:
                    logger.warning(f"[V98] ThreatIntelCrawler error: {e}")

            # [V104.42 #AV] TẠI SAO: ReVerify process_pending was never in background
            # scheduler → queue fills with pending, never processed on /ask 24/7 runtime.
            # Fix: process pending reverify entries every 5 minutes.
            if self.reverify_scheduler and now - getattr(self, '_last_reverify', 0) >= 300:
                try:
                    processed = self.reverify_scheduler.process_pending(limit=5)
                    # [V5.9-FIX] TẠI SAO: process_pending() returns DICT
                    # {"processed": N, "stable": N, "changed": N, "errors": N}
                    # but old code did `if processed > 0` → TypeError: dict > int.
                    # Fix: extract .get("processed", 0) before comparison.
                    processed_count = processed.get("processed", 0) if isinstance(processed, dict) else processed
                    if processed_count > 0:
                        logger.info(f"[V104.42 #AV] ReVerify: processed {processed_count} pending entries "
                                    f"(stable={processed.get('stable', 0) if isinstance(processed, dict) else 0}, "
                                    f"changed={processed.get('changed', 0) if isinstance(processed, dict) else 0})")
                    self._last_reverify = now
                except Exception as e:
                    logger.warning(f"[V104.42 #AV] ReVerify process_pending error: {e}")

            # [V104.44 #CS] TẠI SAO: CanaryTokenMonitor.check_trigger was never called
            # → canary tokens injected but never scanned for exfiltration.
            # Fix: scan recent error_history answers for canary triggers every 10 min.
            if self.canary_monitor and now - getattr(self, '_last_canary_scan', 0) >= 600:
                try:
                    from scp.core.db_manager import db_query_all
                    _recent = db_query_all(
                        "SELECT ai_answer, question FROM error_history "
                        "ORDER BY id DESC LIMIT 50"
                    )
                    _triggers = 0
                    for _row in _recent or []:
                        _text = str(_row.get("ai_answer", "")) + " " + str(_row.get("question", ""))
                        _hit = self.canary_monitor.check_trigger(_text, source="error_history_scan")
                        if _hit:
                            _triggers += 1
                            logger.warning(f"[V104.44 #CS] Canary trigger detected: token={_hit.token[:20]}...")
                            if self.notifications:
                                try:
                                    self.notifications.notify(
                                        event_type="canary_triggered",
                                        title="Canary Token Triggered",
                                        message=f"Canary {_hit.token[:20]} found in recent answers — possible exfiltration",
                                        severity="critical",
                                    )
                                except Exception as e:
                                    # [ROOT-FIX 5] Was `except Exception as e: pass` — swallowed
                                    # canary-trigger notification errors silently → operators
                                    # miss critical exfiltration alerts.
                                    logger.warning(f"[judge] canary_triggered notify failed: {e}")
                    if _triggers > 0:
                        logger.info(f"[V104.44 #CS] Canary scan: {_triggers} triggers in 50 recent answers")
                    self._last_canary_scan = now
                except Exception as e:
                    logger.warning(f"[V104.44 #CS] Canary scan error: {e}")

            # [V104.44 #CW] TẠI SAO: StorageManager.check_and_maintain was never scheduled
            # → disk/DB growth unmonitored. Fix: run every 30 min.
            if hasattr(self, 'storage_manager') and self.storage_manager and now - getattr(self, '_last_storage_check', 0) >= 1800:
                try:
                    self.storage_manager.check_and_maintain()
                    self._last_storage_check = now
                except Exception as e:
                    logger.debug(f"[V104.44 #CW] StorageManager error: {e}")

            # [V104.45 #CR] TẠI SAO: AutoPayloadGenerator was never scheduled →
            # payload pool never refreshed. Fix: generate new payloads every 12h.
            if now - getattr(self, '_last_payload_gen', 0) >= 43200:  # 12h
                try:
                    from scp.security.auto_payload_generator import AutoPayloadGenerator
                    _gen = AutoPayloadGenerator()
                    _payloads = _gen.generate(count=50)
                    if _payloads:
                        logger.info(f"[V104.45 #CR] AutoPayloadGenerator: {len(_payloads)} payloads generated")
                    self._last_payload_gen = now
                except Exception as e:
                    logger.debug(f"[V104.45 #CR] AutoPayloadGenerator error: {e}")

            await asyncio.sleep(60)  # check every 1 minute

