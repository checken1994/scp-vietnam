"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.




License: See LICENSE file
Contact: scp-vietnam@example.com
"""

"""
SCP V14 — Self-Healing Engine
"""
import logging
from datetime import datetime

from scp.core.db_manager import db_exec, db_query_all

logger = logging.getLogger("scp.v14")

class ErrorClassifier:
    @staticmethod
    def classify(verdict, reason, frame):
        reason_lower = (reason or "").lower()
        if frame == "unknown" or "không xác định" in reason_lower:
            return type('ErrCls', (), {'error_type': "classifier_error", 'domain': "classifier", 'detail': "Classifier không nhận diện", 'fixable': True})()
        if any(kw in reason_lower for kw in ["inapplicable", "không parse", "no checker"]):
            return type('ErrCls', (), {'error_type': "parser_error", 'domain': frame, 'detail': "Parser lỗi", 'fixable': True})()
        return type('ErrCls', (), {'error_type': "logic_error", 'domain': frame, 'detail': "AI sai", 'fixable': False})()

class HealthStateMachine:
    def __init__(self):
        self.cache_states = {}
        self.cache_counters = {}
        try:
            self.cache_states = {r['domain']: r['state'] for r in db_query_all("SELECT domain, state FROM health_states")}
            self.cache_counters = {r['domain']: r for r in db_query_all("SELECT * FROM health_counters")}
        except Exception as e:
            logger.debug(f"[V104.37] core/healing_engine.py: e={e}")

    def record(self, domain, verdict):
        if domain not in self.cache_counters:
            self.cache_counters[domain] = {"domain": domain, "pass": 0, "block": 0, "inapplicable": 0, "total": 0}
        c = self.cache_counters[domain]
        c["total"] += 1
        if verdict == "PASS": c["pass"] += 1
        elif verdict in ("BLOCK", "FAIL"): c["block"] += 1
        else: c["inapplicable"] += 1
        try:
            db_exec("INSERT OR REPLACE INTO health_counters (domain, pass, block, inapplicable, total) VALUES (?, ?, ?, ?, ?)",
                    (domain, c["pass"], c["block"], c["inapplicable"], c["total"]))
        except Exception as e:
            logger.debug(f"[V104.37] core/healing_engine.py: e={e}")
        # [V89 FIX] Also write to health_states (was read but never written)
        pass_rate = c["pass"] / max(1, c["total"])
        if pass_rate > 0.8:
            state = "HEALTHY"
        elif pass_rate > 0.5:
            state = "DEGRADED"
        elif pass_rate > 0.2:
            state = "CRITICAL"
        else:
            state = "FAILED"
        self.cache_states[domain] = state
        try:
            db_exec("INSERT OR REPLACE INTO health_states (domain, state, last_updated) VALUES (?, ?, ?)",
                    (domain, state, datetime.now().isoformat()))
        except Exception as e:
            logger.debug(f"[V104.37] core/healing_engine.py: e={e}")

class RecoveryQueue:
    def create_issue(self, domain, question, ai_answer, error_type, cause, fix_action="none"):
        import secrets
        from datetime import datetime
        issue_id = f"ISSUE-{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{secrets.randbelow(10000):04d}"
        try:
            db_exec("INSERT OR IGNORE INTO recovery_issues (id, timestamp, domain, question, ai_answer, error_type, cause, fix_action, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (issue_id, datetime.now().isoformat(), domain, question, ai_answer[:200] if ai_answer else "", error_type, cause[:200] if cause else "", fix_action, "OPEN"))
            return issue_id
        except Exception: return None

class KnowledgeMemory:
    def add(self, question, ai_answer, domain, error_type, cause, fix_action, fix_artifact, evidence, confidence):
        import secrets
        from datetime import datetime
        know_id = f"KNOW-{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{secrets.randbelow(9000)+1000}"
        try:
            db_exec("INSERT OR IGNORE INTO knowledge_memory (id, timestamp, question, ai_answer, domain, error_type, cause, fix_action, fix_artifact, evidence, confidence, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (know_id, datetime.now().isoformat(), question, ai_answer[:200], domain, error_type, cause[:200], fix_action, fix_artifact[:200], evidence[:200], confidence, "active"))
        except Exception as e:
            logger.debug(f"[V104.37] core/healing_engine.py: e={e}")

class SelfHealingEngine:
    def __init__(self):
        self.error_classifier = ErrorClassifier()
        self.health = HealthStateMachine()
        self.recovery = RecoveryQueue()
        self.knowledge = KnowledgeMemory()
        self.error_history = ErrorHistory()

class ErrorHistory:
    def record(self, question, ai_answer, frame, v13_verdict, final_verdict, verdict_detail, error_type, source, real_value, ai_value, reason):
        import hashlib
        from datetime import datetime
        ts = datetime.now().isoformat()
        sha256 = hashlib.sha256(f"{question}|{ai_answer}".encode()).hexdigest()[:16]
        try:
            db_exec("INSERT INTO error_history (timestamp, question, ai_answer, frame, v13_verdict, final_verdict, verdict_detail, error_type, source, real_value, ai_value, reason, sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ts, question, ai_answer[:500], frame, v13_verdict, final_verdict, verdict_detail, error_type, source, str(real_value) if real_value else None, str(ai_value) if ai_value else None, reason[:500], sha256))
        except Exception as e:
            logger.debug(f"[V104.37] core/healing_engine.py: e={e}")

    def get_similar_errors(self, question, limit=5):
        try:
            return db_query_all("SELECT * FROM error_history WHERE LOWER(question) LIKE ? ORDER BY timestamp DESC LIMIT ?", (f"%{question.lower()[:30]}%", limit))
        except Exception: return []
