"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.

This software is protected by Vietnamese Intellectual Property Law and
international treaties. Unauthorized commercial use is prohibited.

Patent Pending:
  - WHY Engine (Verifikation Plan Generator)
  - Recursive Why (Trust Tracing to Axiomatic Sources)
  - MetaFalsifier (Verification Plan Falsification)
  - ProofGraph (Dynamic Proof Dependency Graph)

License: See LICENSE file
Contact: scp-vietnam@example.com
"""

#!/usr/bin/env python3
"""
SCP V14 - 24/7 Background Runner
=================================
"""
import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
LOG_DIR = BASE_DIR / "logs"
PID_FILE = BASE_DIR / "scp_v14.pid"
LOG_FILE = LOG_DIR / "scp_v14.log"
ERROR_LOG = LOG_DIR / "scp_v14_error.log"
HEALTH_FILE = LOG_DIR / "health.json"
LOG_DIR.mkdir(exist_ok=True)

CRASH_BACKOFF_BASE = 5
CRASH_BACKOFF_MAX = 300
# [V88 BOOST] 400 Q/cycle + sleep=0.5s + 24 workers
# Expected: ~300+ Q/cycle fetched, ~10s process → ~1800 Q/min = ~100K+/day
QUESTIONS_PER_CYCLE = 400  # [V90 OPT]
SLEEP_BETWEEN_CYCLES = 0.0  # [V93 BOOST] no sleep — APIs handle rate limiting — minimal sleep, sources have own rate limiting
HEALTH_STALE_MINUTES = 10


def setup_logging(foreground=False):
    fmt = '%(asctime)s [%(levelname)s] %(message)s'
    dfmt = '%Y-%m-%d %H:%M:%S'
    fh = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8')
    fh.setFormatter(logging.Formatter(fmt, dfmt))
    eh = RotatingFileHandler(ERROR_LOG, maxBytes=2*1024*1024, backupCount=3, encoding='utf-8')
    eh.setFormatter(logging.Formatter(fmt, dfmt))
    eh.setLevel(logging.ERROR)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    root.addHandler(eh)
    if foreground:
        c = logging.StreamHandler()
        c.setFormatter(logging.Formatter(fmt, dfmt))
        root.addHandler(c)
    logging.getLogger('urllib3').setLevel(logging.WARNING)


def write_pid():
    PID_FILE.write_text(str(os.getpid()))


def remove_pid():
    try: PID_FILE.unlink()
    except FileNotFoundError: pass


def is_running():
    if not PID_FILE.exists(): return False, None
    try:
        pid = int(PID_FILE.read_text().strip())
        # [FIX] Windows: os.kill(pid, 0) doesn't work reliably — use tasklist
        if sys.platform == 'win32':
            try:
                result = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                                      capture_output=True, text=True, timeout=5)
                if str(pid) in result.stdout:
                    return True, pid
                return False, pid
            except Exception:
                return False, pid
        else:
            # Linux/Mac: os.kill(pid, 0) works
            try:
                os.kill(pid, 0)
                return True, pid
            except (OSError, ProcessLookupError):
                return False, pid
    except (OSError, ValueError):
        return False, None


def write_health(status, extra=None):
    data = {"status": status, "timestamp": datetime.now().isoformat(), "pid": os.getpid()}
    if extra: data.update(extra)
    try: HEALTH_FILE.write_text(json.dumps(data, indent=2))
    except Exception: pass


def read_health():
    if not HEALTH_FILE.exists(): return None
    try: return json.loads(HEALTH_FILE.read_text())
    except Exception: return None


def run_forever(interval=2000):  # [V90 OPT] 2000 Q/cycle for max throughput
    setup_logging(foreground=False)
    logger = logging.getLogger(__name__)

    # [OK] FIX Windows: redirect stdout/stderr to log file with utf-8 encoding
    # to prevent UnicodeEncodeError when print() contains non-cp1252 chars
    # On ALL platforms (not just Windows) -- safe and consistent
    try:
        sys.stdout = open(LOG_FILE, 'a', encoding='utf-8', buffering=1)
        sys.stderr = open(ERROR_LOG, 'a', encoding='utf-8', buffering=1)
    except Exception as e:
        logger.warning(f"Cannot redirect stdout: {e}")

    write_pid()
    logger.info("=" * 60)
    logger.info("SCP V14 24/7 STARTING")
    logger.info(f"PID: {os.getpid()}")
    logger.info(f"Platform: {sys.platform}")
    logger.info(f"Python: {sys.version.split()[0]}")
    logger.info("=" * 60)
    write_health("starting")

    shutdown_requested = False
    def handle_signal(signum, frame):
        nonlocal shutdown_requested
        logger.info(f"Signal {signum} received - shutting down gracefully...")
        shutdown_requested = True
    try:
        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)
    except (AttributeError, ValueError):
        pass

    try:
        sys.path.insert(0, str(BASE_DIR))
        sys.path.insert(0, str(BASE_DIR / "core"))
        from scp.runtime import SCPV14
        try:
            from scp.core.github_backup import backup_to_hf
        except Exception:
            backup_to_hf = None
        try:
            from scp.meta.meta import MetaCognitionEngine
        except Exception:
            MetaCognitionEngine = None
        # meta_learning stub - no-op for now
        def run_meta_learning_cycle(): return {"changes_made": [], "no_changes_reason": "stub"}
    except Exception as e:
        logger.error(f"FATAL: import fail: {e}")
        traceback.print_exc()
        write_health("fatal", {"error": str(e)})
        remove_pid()
        return 1

    try:
        logger.info("Initializing SCPV14 engine...")
        engine = SCPV14()

        # [V99] Start V98 background security jobs (ThreatSimulator + IntelCrawler)
        try:
            from scp.runtime.judge import RealityJudge
            v98_judge = RealityJudge()
            # Start background scheduler in separate thread (don't block main loop)
            import threading
            def _v98_bg_runner():
                import asyncio as _a
                _a.run(v98_judge.schedule_background_jobs())
            v98_bg_thread = threading.Thread(target=_v98_bg_runner, daemon=True, name="v98-background")
            v98_bg_thread.start()
            logger.info("[V99] V98 background scheduler started (ThreatSimulator 6h + IntelCrawler 12h)")

            # [V104.36 #56-wire] Create PredictiveEngine WITH production judge
            # TẠI SAO: V104.35 #56 fixed SelfLearner to accept judge, but run_247
            # never passed judge → self-learning trained throwaway instance.
            # Now: PredictiveEngine gets v98_judge → real self-correction in 24/7.
            try:
                from scp.prediction.predictive import PredictiveEngine
                predictive = PredictiveEngine(judge=v98_judge)
                logger.info("[V104.36] PredictiveEngine wired to production judge "
                            "(SelfLearner will retrain judge.v13.classifier)")
                # Store on engine for later access
                engine._predictive_engine = predictive
            except Exception as pe:
                logger.warning(f"[V104.36] PredictiveEngine init failed: {pe}")
        except Exception as e:
            logger.warning(f"[V99] V98 background scheduler failed: {e}")

        # [V96] Initialize Memory Manager
        try:
            from scp.core.memory_manager import MemoryManager
            mm = MemoryManager.get_instance()
            mm.set_limits(
                ram_warning=400,      # Warn at 400MB
                ram_critical=600,     # Force GC at 600MB
                gc_trigger=300,       # Start GC at 300MB
                cache_limit=3000,      # Max 3000 cache entries
                verdict_limit=100,    # Max 100 verdicts
            )
            mm.start()
            logger.info("[V96] Memory Manager started")
        except Exception as e:
            logger.warning(f"[V96] Memory Manager init failed: {e}")

        # Use getattr to be robust to different engine versions
        if hasattr(engine, 'meta') and engine.meta is not None:
            meta_cog = engine.meta
        elif MetaCognitionEngine is not None:
            try:
                meta_cog = MetaCognitionEngine(engine)
            except Exception:
                try:
                    meta_cog = MetaCognitionEngine()
                except Exception:
                    meta_cog = None
        else:
            meta_cog = None
        logger.info("Engine ready.")
    except Exception as e:
        logger.error(f"FATAL: engine init fail: {e}")
        traceback.print_exc()
        write_health("fatal", {"error": str(e)})
        remove_pid()
        return 1

    write_health("running", {"cycles_completed": 0})
    cycle = 0
    total_questions = 0
    crash_count = 0

    # [V29] Start auto-backup thread (chạy nền, mỗi 6h)
    backup_thread = None
    try:
        import threading

        from scp.core.auto_backup import DEFAULT_INTERVAL_HOURS, DEFAULT_MAX_BACKUPS, backup_db
        def backup_loop():
            while not shutdown_requested:
                try:
                    backup_db(DEFAULT_MAX_BACKUPS)
                    logger.info("[V29] Auto-backup completed")
                except Exception as e:
                    logger.warning(f"[V29] Auto-backup error: {e}")
                # Sleep 6h but check shutdown every 60s
                for _ in range(DEFAULT_INTERVAL_HOURS * 60):
                    if shutdown_requested:
                        break
                    time.sleep(60)
        backup_thread = threading.Thread(target=backup_loop, daemon=True, name="auto-backup")
        backup_thread.start()
        logger.info("[V29] Auto-backup thread started (every 6h)")
    except Exception as e:
        logger.warning(f"[V29] Auto-backup thread failed to start: {e}")

    # [V93.4 DOUBLE-BUFFER+] Improved prefetch with surplus initial fetch
    _prefetch_questions = []
    _prefetch_error = []
    _prefetch_thread = None

    # [V93.4] Initial fetch — nạp 2 round để có dư Q cho cycle 1+2
    try:
        from scp.core.real_question_fetcher import RealQuestionFetcher
        init_fetcher = RealQuestionFetcher()
        init_fetcher.fetch_and_store(interval)
        _prefetch_questions.extend(init_fetcher.get_unused(interval))
        logger.info(f"[V93.4] Initial prefetch: {len(_prefetch_questions)} questions ready (2 rounds)")
    except Exception as e:
        logger.warning(f"[V93.4] Initial prefetch error: {e}")

    while not shutdown_requested:
        cycle += 1
        cycle_start = time.time()
        cycle_questions = 0
        cycle_errors = 0
        try:
            logger.info(f"=== CYCLE {cycle} ===")

            write_health("running", {
                "cycles_completed": cycle - 1,
                "current_cycle": cycle,
                "phase": "fetching",
                "interval": interval,
                "total_questions": total_questions,
            })

            # [V93.4] Use pre-fetched questions from previous cycle's background fetch
            real_questions = []
            fetch_elapsed = 0

            if not shutdown_requested:
                if _prefetch_thread and _prefetch_thread.is_alive():
                    fetch_wait_start = time.time()
                    _prefetch_thread.join(timeout=45)  # [V93.4] increased wait
                    fetch_elapsed = time.time() - fetch_wait_start
                    if _prefetch_thread.is_alive():
                        logger.warning("[V93.4] Prefetch still running after 45s — using what we have")

                real_questions = _prefetch_questions.copy()
                _prefetch_questions.clear()
                logger.info(f"[V93.4] Prefetch ready: {len(real_questions)} questions "
                            f"(waited {fetch_elapsed:.1f}s)")

                write_health("running", {
                    "cycles_completed": cycle - 1,
                    "current_cycle": cycle,
                    "phase": "processing",
                    "interval": interval,
                    "fetched": len(real_questions),
                    "fetch_time_sec": round(fetch_elapsed, 1),
                    "total_questions": total_questions,
                })

            # [V93.4] Start fetching NEXT cycle's questions in background
            if not shutdown_requested:
                _prefetch_questions.clear()
                _prefetch_error.clear()
                def _do_prefetch():
                    try:
                        from scp.core.real_question_fetcher import RealQuestionFetcher
                        pf = RealQuestionFetcher()
                        pf.fetch_and_store(interval)
                        qs = pf.get_unused(interval)
                        _prefetch_questions.extend(qs)
                        logger.info(f"[V93.4] Background prefetch done: {len(qs)} questions for next cycle")
                    except Exception as e:
                        _prefetch_error.append(str(e))
                        logger.warning(f"[V93.4] Background prefetch error: {e}")

                _prefetch_thread = threading.Thread(target=_do_prefetch, daemon=True)
                _prefetch_thread.start()

            # [V67] If no real questions available (network down / all sources failed),
            # fall back to existing unused questions in DB. If still nothing, sleep and retry.
            if not real_questions:
                logger.warning("[V67] No real questions available this cycle — "
                               "will retry next cycle after sleep")
            else:
                real_q_count = 0
                process_start = time.time()

                # [V72] Batch processing — process all real questions IN PARALLEL
                # Was (V71): sequential loop, 50 Q × 0.1s = 5s
                # Now (V72): parallel with 8 workers, 50 Q × 0.1s / 8 = ~0.7s (7x faster)
                if hasattr(engine, 'process_batch') and len(real_questions) > 1:
                    try:
                        # Build list of (question, ai_answer, source) tuples
                        batch = []
                        for rq in real_questions:
                            q_text = rq.get("question", "")
                            q_ai = rq.get("ai_answer", "")
                            if q_text:
                                batch.append((q_text, q_ai or "", "real_fetcher"))

                        if batch:
                            # Update heartbeat before batch
                            write_health("running", {
                                "cycles_completed": cycle - 1,
                                "current_cycle": cycle,
                                "phase": "batch_processing",
                                "interval": interval,
                                "fetched": len(real_questions),
                                "batch_size": len(batch),
                                "total_questions": total_questions,
                            })

                            verdicts = engine.process_batch(batch, max_workers=128)  # [V93.5] reduced to prevent deadlock
                            real_q_count = len(verdicts)
                            cycle_questions += real_q_count
                            total_questions += real_q_count

                            # Count errors from verdicts
                            for v in verdicts:
                                if v.verdict == "UNKNOWN" and "Batch" in (v.reasoning or ""):
                                    cycle_errors += 1

                            logger.info(f"[V72] Batch processed {real_q_count} questions in "
                                        f"{time.time() - process_start:.1f}s "
                                        f"({real_q_count / max(time.time() - process_start, 0.1):.1f} Q/s)")
                    except Exception as e:
                        logger.warning(f"[V72] Batch processing failed, falling back to sequential: {e}")
                        # Fallback to sequential
                        for rq_idx, rq in enumerate(real_questions, 1):
                            if shutdown_requested: break
                            try:
                                q_text = rq.get("question", "")
                                q_ai = rq.get("ai_answer", "")
                                if q_text:
                                    engine.process(q_text, q_ai or "", source="real_fetcher")
                                    cycle_questions += 1
                                    total_questions += 1
                                    real_q_count += 1
                            except Exception as e2:
                                cycle_errors += 1
                                logger.warning(f"[V72] Sequential fallback error: {e2}")
                                if cycle_errors > 10:
                                    break
                else:
                    # Sequential processing (single question or no process_batch method)
                    for rq_idx, rq in enumerate(real_questions, 1):
                        if shutdown_requested: break
                        try:
                            q_text = rq.get("question", "")
                            q_ai = rq.get("ai_answer", "")
                            if q_text:
                                engine.process(q_text, q_ai or "", source="real_fetcher")
                                cycle_questions += 1
                                total_questions += 1
                                real_q_count += 1

                                if rq_idx % 5 == 0:
                                    write_health("running", {
                                        "cycles_completed": cycle - 1,
                                        "current_cycle": cycle,
                                        "phase": "processing",
                                        "interval": interval,
                                        "fetched": len(real_questions),
                                        "processed": rq_idx,
                                        "total_questions": total_questions,
                                        "process_time_sec": round(time.time() - process_start, 1),
                                    })
                        except Exception as e:
                            cycle_errors += 1
                            logger.warning(f"[V67] RealQ error: {e}")
                            if cycle_errors > 10:
                                logger.error("Too many errors - aborting cycle")
                                break

                logger.info(f"[V72] Cycle {cycle}: real_questions_processed={real_q_count}/{len(real_questions)} "
                            f"in {time.time() - process_start:.1f}s")

            logger.info(f"Cycle {cycle}: total_processed={cycle_questions}, errors={cycle_errors}")

            # [V64→V65] CuriosityAsker — ask curiosity questions every cycle
            # [V65 FIX] Was: logger.debug on error → silent fail. Now: logger.warning + visibility.
            if not shutdown_requested and cycle % 3 == 0:
                try:
                    from scp.meta.curiosity_asker import CuriosityAsker
                    # [V93.7] Was: every 5 cycles, 10 questions -> backlog barely moves.
                    # Now: every 3 cycles, 30 questions -> clears backlog faster.
                    asker = CuriosityAsker(max_per_run=30, sleep_between=0.0)
                    curiosity_result = asker.ask_batch(30)
                    asked = curiosity_result.get("asked", 0)
                    if asked > 0:
                        logger.info(f"CuriosityAsker: asked={asked} "
                                    f"PASS={curiosity_result.get('pass',0)} "
                                    f"FAIL={curiosity_result.get('fail',0)} "
                                    f"knowledge_gained={curiosity_result.get('knowledge_gained',0)}")
                    else:
                        # [V65] Log WHY asked=0 — previously invisible
                        logger.warning(
                            f"CuriosityAsker: asked=0 (silent fail) | "
                            f"pass={curiosity_result.get('pass',0)} "
                            f"fail={curiosity_result.get('fail',0)} "
                            f"unknown={curiosity_result.get('unknown',0)} "
                            f"knowledge_gained={curiosity_result.get('knowledge_gained',0)} "
                            f"— check meta_curiosity table & RealityJudge init"
                        )
                except Exception as e:
                    logger.warning(f"CuriosityAsker exception: {e}", exc_info=True)

            if not shutdown_requested and cycle % 3 == 0 and meta_cog is not None:
                try:
                    if hasattr(meta_cog, 'run_meta_cycle'):
                        meta_cog.run_meta_cycle()
                    elif hasattr(meta_cog, 'run_cycle'):
                        meta_cog.run_cycle()
                except Exception as e:
                    logger.error(f"Meta-Cognition error: {e}")
            if not shutdown_requested and cycle % 5 == 0:
                try:
                    ml_result = run_meta_learning_cycle()
                    if ml_result.get("changes_made"):
                        logger.info(f"Meta-Learning: {len(ml_result['changes_made'])} changes")
                except Exception as e:
                    logger.warning(f"Meta-Learning error: {e}")

            # [V65] ExperienceEngine — reflect + learn every 10 cycles
            # Was: NOT called → experiences = 0 even after 68K questions
            # Now: every 10 cycles, reflect on knowledge + error_history + memory,
            #       extract lessons, store to `experiences` table (NOT auto-apply policy).
            if not shutdown_requested and cycle % 10 == 0:
                try:
                    from scp.experience.experience import ExperienceEngine
                    exp_engine = ExperienceEngine()
                    lessons = exp_engine.reflect()
                    learned = 0
                    if lessons:
                        learned = exp_engine.learn(lessons)
                    stats = exp_engine.get_stats()
                    logger.info(
                        f"ExperienceEngine[cycle {cycle}]: "
                        f"reflected={len(lessons)} lessons, "
                        f"learned={learned} new experiences, "
                        f"stats={stats}"
                    )

                    # [V93.6] Actually APPLY unapplied lessons — was: computed but never used/marked
                    # 1. Pull unapplied policies (domain_tolerances, confidence_adjustments, etc.)
                    # 2. Persist to JSON so judge.py can load + apply at inference time
                    # 3. mark_applied() so they don't pile up unbounded (was: 3343 lessons, 0 applied)
                    policies = exp_engine.get_active_policies()
                    lesson_ids = policies.pop("_lesson_ids", [])
                    if lesson_ids:
                        import json as _json
                        policy_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "active_policies.json")
                        try:
                            with open(policy_path, "w") as pf:
                                _json.dump(policies, pf, ensure_ascii=False, indent=2)
                        except Exception as e:
                            logger.warning(f"[V93.6] Failed writing active_policies.json: {e}")
                        exp_engine.mark_applied(lesson_ids)
                        logger.info(
                            f"[V93.6] Applied {len(lesson_ids)} lessons -> "
                            f"domain_tolerances={len(policies.get('domain_tolerances', {}))}, "
                            f"confidence_adjustments={len(policies.get('confidence_adjustments', {}))}, "
                            f"recurring_errors={len(policies.get('recurring_errors', []))}"
                        )
                except Exception as e:
                    logger.warning(f"ExperienceEngine error: {e}", exc_info=True)

            # [OK] Periodic background tasks — ASYNC (don't block main pipeline)
            # PredictiveOrchestrator + Consolidator every 5 cycles, run in background thread
            if not shutdown_requested and cycle % 5 == 0:
                import threading
                def _run_background():
                    if hasattr(engine, 'consolidator') and engine.consolidator:
                        try:
                            cons_result = engine.consolidator.consolidate_all()
                            if cons_result and cons_result.get('summaries_created', 0) > 0:
                                logger.info(f"Consolidator: {cons_result['summaries_created']} new summaries")
                        except Exception as e:
                            logger.warning(f"Consolidator error: {e}")
                    if hasattr(engine, 'predictive') and engine.predictive:
                        try:
                            pred_result = engine.predictive.run_cycle()
                            if pred_result:
                                logger.info(f"Predictive cycle: {pred_result}")
                        except Exception as e:
                            logger.warning(f"Predictive error: {e}")
                # Run in background thread (non-blocking)
                bg_thread = threading.Thread(target=_run_background, daemon=True)
                bg_thread.start()

            # [PHASE 1] Auto-cleanup every 500 cycles (~24h at 200 Q/cycle)
            if not shutdown_requested and cycle % 500 == 0:
                def _run_cleanup():
                    try:
                        pass  # auto_cleanup deprecated
                        # stats_before = get_cleanup_stats()
                        # logger.info(f"[CLEANUP] Before: {stats_before}")
                        # result = auto_cleanup(threshold=10.0, max_age_days=30)
                        # logger.info(f"[CLEANUP] Result: {result}")
                        # stats_after = get_cleanup_stats()
                        # logger.info(f"[CLEANUP] After: {stats_after}")
                    except Exception as e:
                        logger.warning(f"[CLEANUP] Auto-cleanup failed: {e}")
                cleanup_thread = threading.Thread(target=_run_cleanup, daemon=True)
                cleanup_thread.start()

            # [V93.6] Process reverify_queue — was: cycle%5==0, limit=10 → drains only
            # 2 items/cycle avg, causing 3699-item backlog. Now: every cycle, limit=300
            # to actually clear backlog (3699/300 ≈ 12 cycles to drain, then steady-state).
            if not shutdown_requested and cycle % 2 == 0:
                def _run_reverify():
                    try:
                        if hasattr(engine, 'judge') and engine.judge and hasattr(engine.judge, 'reverify_scheduler'):
                            sched = engine.judge.reverify_scheduler
                            if sched:
                                result = sched.process_pending(limit=150)
                                if result.get("processed", 0) > 0:
                                    logger.info(f"[V93.6] ReVerify: processed={result['processed']} "
                                                f"stable={result['stable']} changed={result['changed']} "
                                                f"errors={result['errors']}")
                    except Exception as e:
                        logger.warning(f"[V93.6] ReVerify error: {e}")
                reverify_thread = threading.Thread(target=_run_reverify, daemon=True)
                reverify_thread.start()

            # [V81] Auto-VACUUM every 1000 cycles OR when DB > 100MB
            if not shutdown_requested and (cycle % 1000 == 0):
                def _run_vacuum():
                    try:
                        from scp.core.db_manager import get_db_size_mb, vacuum_db
                        size_before = get_db_size_mb()
                        logger.info(f"[V81] DB size before VACUUM: {size_before:.1f} MB")
                        if size_before > 50:  # Only VACUUM if > 50MB
                            vacuum_db()
                            size_after = get_db_size_mb()
                            logger.info(f"[V81] DB size after VACUUM: {size_after:.1f} MB "
                                        f"(saved {size_before - size_after:.1f} MB)")
                        else:
                            logger.info("[V81] DB < 50MB, skip VACUUM")
                    except Exception as e:
                        logger.warning(f"[V81] VACUUM failed: {e}")
                vacuum_thread = threading.Thread(target=_run_vacuum, daemon=True)
                vacuum_thread.start()

            ct = time.time() - cycle_start
            # [FIX 2026-07-09] Expose backup health status on /health — previously
            # backup staleness (confirmed live: ~8.7h gap with zero uploads) was
            # completely invisible outside of manually checking HF dataset commit
            # history. Now surfaced so it can be monitored the same way as cycles.
            _backup_status = {}
            try:
                from scp.core.github_backup import get_backup_status
                _backup_status = get_backup_status()
            except Exception:
                pass
            write_health("running", {
                "cycles_completed": cycle,
                "total_questions": total_questions,
                "last_cycle_time_sec": round(ct, 2),
                "last_cycle_questions": cycle_questions,
                "last_cycle_errors": cycle_errors,
                "crash_count": crash_count,
                "backup": _backup_status,
            })
            crash_count = 0

            # [V88] Auto-backup SQLite to HF dataset every 5 cycles
            if backup_to_hf and not shutdown_requested:
                try:
                    backup_to_hf(cycle)
                except Exception as be:
                    logger.warning(f"[V88] Backup error: {be}")

            if not shutdown_requested and cycle % 5 == 0:
                time.sleep(SLEEP_BETWEEN_CYCLES)
        except KeyboardInterrupt:
            shutdown_requested = True
            break
        except Exception as e:
            crash_count += 1
            logger.error(f"CRASH on cycle {cycle} (#{crash_count}): {e}")
            traceback.print_exc()
            write_health("crashed", {"crash_count": crash_count, "error": str(e)[:200], "cycles_completed": cycle - 1})
            if shutdown_requested: break
            backoff = min(CRASH_BACKOFF_BASE * (2 ** crash_count), CRASH_BACKOFF_MAX)
            logger.info(f"Restarting in {backoff}s (crash #{crash_count})...")
            time.sleep(backoff)
            try:
                engine = SCPV14()
                if hasattr(engine, 'meta') and engine.meta is not None:
                    meta_cog = engine.meta
                elif MetaCognitionEngine is not None:
                    try:
                        meta_cog = MetaCognitionEngine(engine)
                    except Exception:
                        try:
                            meta_cog = MetaCognitionEngine()
                        except Exception:
                            meta_cog = None
                else:
                    meta_cog = None
            except Exception as e2:
                logger.error(f"Re-init failed: {e2}")

    # [V96] Stop Memory Manager
    try:
        mm = MemoryManager.get_instance()
        mm.stop()
        logger.info("[V96] Memory Manager stopped")
    except Exception:
        pass

    logger.info("=" * 60)
    logger.info("SCP V14 24/7 SHUTDOWN COMPLETE")
    logger.info(f"Total cycles: {cycle}, Total questions: {total_questions}")
    logger.info("=" * 60)
    write_health("stopped", {"cycles_completed": cycle, "total_questions": total_questions})
    remove_pid()
    return 0


def start_background(interval=2000):  # [V90 OPT] 2000 Q/cycle
    running, pid = is_running()
    if running:
        print(f"[!] SCP V14 da dang chay (PID {pid}).")
        print("    Dung: python run_247.py stop")
        return 1
    print("[*] Khoi dong SCP V14 24/7...")
    cmd = [sys.executable, str(Path(__file__).resolve()), "foreground", "--interval", str(interval)]
    log_fd = open(LOG_FILE, 'a', encoding='utf-8')
    err_fd = open(ERROR_LOG, 'a', encoding='utf-8')
    if sys.platform == 'win32':
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log_fd, stderr=err_fd,
                                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, cwd=str(BASE_DIR))
    else:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log_fd, stderr=err_fd,
                                start_new_session=True, cwd=str(BASE_DIR))
    time.sleep(2)
    if proc.poll() is not None:
        print(f"[!] Process exited immediately - check {ERROR_LOG}")
        return 1
    print(f"[+] SCP V14 started (PID {proc.pid})")
    print(f"    Log:     {LOG_FILE}")
    print(f"    Health:  {HEALTH_FILE}")
    return 0


def stop():
    running, pid = is_running()
    if not running:
        print("[+] Khong co process dang chay.")
        if PID_FILE.exists(): remove_pid()
        return 0
    print(f"[*] Dung SCP V14 (PID {pid})...")
    try:
        if sys.platform == 'win32':
            # [FIX] Windows: use taskkill /F /T to kill process tree
            subprocess.run(['taskkill', '/PID', str(pid), '/F', '/T'], capture_output=True)
            # Wait and verify
            for _ in range(5):
                time.sleep(1)
                result = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                                      capture_output=True, text=True, timeout=5)
                if str(pid) not in result.stdout:
                    break
            else:
                # Last resort: kill all python
                subprocess.run(['taskkill', '/F', '/IM', 'python.exe'], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
            for _ in range(10):
                time.sleep(1)
                try: os.kill(pid, 0)
                except OSError: break
            else:
                os.kill(pid, signal.SIGKILL)
        print("[+] Stopped.")
    except Exception as e:
        print(f"[!] Loi: {e}")
    remove_pid()
    return 0


def status():
    running, pid = is_running()
    health = read_health()
    print("=" * 60)
    print("  SCP V14 24/7 STATUS")
    print("=" * 60)
    print(f"  Process: {'RUNNING' if running else 'NOT RUNNING'}")
    if pid: print(f"  PID:     {pid}")
    print()
    if health:
        print(f"  Last heartbeat: {health.get('timestamp', '?')}")
        print(f"  Status:         {health.get('status', '?')}")

        # [V69] Show current cycle phase if mid-cycle
        phase = health.get('phase', '')
        current_cycle = health.get('current_cycle')
        if phase and current_cycle:
            print(f"  Current cycle:  {current_cycle} (phase: {phase})")
            if 'fetched' in health:
                print(f"  Fetched:        {health['fetched']} questions")
            if 'fetch_time_sec' in health:
                print(f"  Fetch time:     {health['fetch_time_sec']}s")
            if 'processed' in health and 'fetched' in health:
                pct = (health['processed'] / max(health['fetched'], 1)) * 100
                print(f"  Processed:      {health['processed']}/{health['fetched']} ({pct:.0f}%)")
            if 'process_time_sec' in health:
                print(f"  Process time:   {health['process_time_sec']}s")
            if 'interval' in health:
                print(f"  Interval:       {health['interval']} Q/cycle")

        if 'cycles_completed' in health: print(f"  Cycles done:    {health['cycles_completed']}")
        if 'total_questions' in health: print(f"  Total Q:        {health['total_questions']}")
        if 'last_cycle_time_sec' in health: print(f"  Last cycle:     {health['last_cycle_time_sec']}s")
        if 'last_cycle_questions' in health: print(f"  Last cycle Q:   {health['last_cycle_questions']}")
        if 'crash_count' in health and health['crash_count'] > 0: print(f"  Crashes:        {health['crash_count']}")
        try:
            ts = datetime.fromisoformat(health['timestamp'])
            age_sec = (datetime.now() - ts).total_seconds()
            age_min = age_sec / 60
            # [V69] Reduced stale threshold during processing — heartbeat should update every 5 Q
            # If processing phase and stale > 2 min → really stuck
            stale_threshold = 2 if phase == 'processing' else HEALTH_STALE_MINUTES
            if running and age_min > stale_threshold:
                print()
                print(f"  [!] WARNING: Process running but health is stale ({age_min:.1f} min)")
                if phase:
                    print(f"      Stuck in '{phase}' phase. Possible network timeout or hang.")
                print("      Try: python run_247.py restart")
            elif running and phase:
                print(f"  Heartbeat age:  {age_sec:.0f}s (OK — actively working)")
        except (ValueError, KeyError): pass
    else:
        print("  Health: (chua co)")
    print()
    print(f"  Logs:    {LOG_FILE}")
    print(f"           {ERROR_LOG}")
    print(f"  PID file: {PID_FILE}")
    print("=" * 60)
    return 0 if running else 1


def show_logs(follow=False, lines=50):
    if not LOG_FILE.exists():
        print(f"[!] Chua co log file: {LOG_FILE}")
        return 1
    if follow:
        print(f"[*] Following {LOG_FILE} (Ctrl+C to exit)...")
        try:
            with open(LOG_FILE, encoding='utf-8') as f:
                all_lines = f.readlines()
                for line in all_lines[-lines:]: print(line, end='')
                f.seek(0, 2)
                while True:
                    line = f.readline()
                    if not line:
                        time.sleep(0.5)
                        continue
                    print(line, end='')
        except KeyboardInterrupt:
            print("\n[*] Stopped.")
    else:
        print(f"[*] Last {lines} lines of {LOG_FILE}:")
        print("-" * 60)
        try:
            with open(LOG_FILE, encoding='utf-8') as f:
                all_lines = f.readlines()
                for line in all_lines[-lines:]: print(line, end='')
        except Exception as e: print(f"[!] Loi: {e}")
        print("-" * 60)
    return 0


def restart(interval=200):  # [OPT] 200 Q/cycle
    print("[*] Restarting...")
    stop()
    time.sleep(2)
    return start_background(interval)


def main():
    parser = argparse.ArgumentParser(description="SCP V14 - 24/7 Background Runner")
    sub = parser.add_subparsers(dest="command")
    p_start = sub.add_parser("start", help="Bat dau chay nen")
    p_start.add_argument("--interval", type=int, default=QUESTIONS_PER_CYCLE)
    sub.add_parser("stop", help="Dung")
    sub.add_parser("status", help="Kiem tra trang thai")
    sub.add_parser("restart", help="Restart")
    p_log = sub.add_parser("logs", help="Xem log")
    p_log.add_argument("--tail", action="store_true")
    p_log.add_argument("--lines", type=int, default=50)
    p_fg = sub.add_parser("foreground", help="Internal")
    p_fg.add_argument("--interval", type=int, default=QUESTIONS_PER_CYCLE)
    args = parser.parse_args()
    if args.command == "start": return start_background(args.interval)
    elif args.command == "stop": return stop()
    elif args.command == "status": return status()
    elif args.command == "restart": return restart()
    elif args.command == "logs": return show_logs(follow=args.tail, lines=args.lines)
    elif args.command == "foreground": return run_forever(args.interval)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
