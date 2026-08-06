import json
import threading
from pathlib import Path

# Create a lock for thread safety
lock = threading.Lock()

# Define the default defensive playbook
DEFAULT_DEFENSIVE_PLAYBOOK = {
    "block_ip": True,
    "tighten_rate_limit": True,
    "enable_honeypot": True,
    "forensic_logging": True,
    "alert_cert": True
}

class EscalationManager:
    def __init__(self, data_dir: str = "data"):
        self.escalation_log_path = Path(data_dir) / "escalation_log.jsonl"
        self.escalation_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._active: dict = {}
        self._history: list = []

    def on_threat_detected(self, threat: dict):
        # Classify the severity of the threat
        severity = self.classify_threat(threat)
        with lock:
            self.log_action(f"Threat detected: {threat}, severity: {severity}", "threat_detection")

    def classify_threat(self, threat: dict) -> str:
        # Implement threat classification logic here
        # For demonstration purposes, assume all threats are high severity
        return "high"

    def start_countdown(self, threat: dict, timeout_min: int = 30):
        # Start a countdown timer
        timer = threading.Timer(timeout_min * 60, self.on_timeout, args=(threat,))
        timer.start()
        with lock:
            self.log_action(f"Countdown started for threat: {threat}, timeout: {timeout_min} minutes", "countdown_started")

    def on_timeout(self, threat: dict):
        # Execute the default defensive playbook
        with lock:
            self.log_action(f"Timeout occurred for threat: {threat}, executing default defensive playbook", "timeout_occurred")
        self.execute_defensive_playbook(threat)

    def execute_defensive_playbook(self, threat: dict):
        # Implement the default defensive playbook
        for action, enabled in DEFAULT_DEFENSIVE_PLAYBOOK.items():
            if enabled:
                # Implement the logic for each action here
                # For demonstration purposes, just log the action
                with lock:
                    self.log_action(f"Executing {action} for threat: {threat}", action)

    def log_action(self, message: str, why: str):
        import time
        log_entry = {
            "timestamp": time.time(),
            "message": message,
            "why": why
        }
        with self.escalation_log_path.open("a") as log_file:
            json.dump(log_entry, log_file)
            log_file.write("\n")

    def get_active_escalations(self) -> list:
        return list(self._active.values())

    def get_escalation_history(self) -> list:
        return self._history

    def on_human_approval(self, threat_id: str, action: str = "default"):
        self._history.append({"threat_id": threat_id, "action": "approved", "human_action": action})
        self.log_action(f"Human approved {threat_id}: {action}", "human_approval")

    def on_human_rejection(self, threat_id: str):
        self._history.append({"threat_id": threat_id, "action": "rejected"})
        self.log_action(f"Human rejected {threat_id}", "human_rejection")
