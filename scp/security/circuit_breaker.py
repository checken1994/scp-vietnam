import threading
import time
import random
from collections import deque

class CircuitBreaker:
    def __init__(self, threshold_rps=100, cooldown_sec=60, recovery_half_open_ratio=0.5):
        self.threshold_rps = threshold_rps
        self.cooldown_sec = cooldown_sec
        self.recovery_half_open_ratio = recovery_half_open_ratio
        self.lock = threading.Lock()
        self.state = 'closed'  # 'closed', 'open', 'half_open'
        self.total_requests = 0
        self.shed_requests = 0
        self.trips = 0
        self.current_rps = 0
        self.timestamps = deque()
        self.consecutive_exceed = 0
        self.trip_time = None
        self.half_open_start_time = None
        self.half_open_failed = False

    def record_request(self):
        with self.lock:
            self.total_requests += 1
            now = time.time()
            self.timestamps.append(now)
            while self.timestamps and now - self.timestamps[0] > 1.0:
                self.timestamps.popleft()
            self.current_rps = len(self.timestamps)

            # Auto-trip if RPS exceeds threshold for 3 consecutive seconds
            if self.current_rps > self.threshold_rps:
                self.consecutive_exceed += 1
                if self.consecutive_exceed >= 3:
                    self.trip()
            else:
                self.consecutive_exceed = 0

            # Auto-recover from OPEN to HALF_OPEN after cooldown
            if self.state == 'open' and self.trip_time and now - self.trip_time >= self.cooldown_sec:
                self.state = 'half_open'
                self.half_open_start_time = now
                self.half_open_failed = False

            # Auto-recover from HALF_OPEN to CLOSED after 10 sec without failure
            if self.state == 'half_open' and self.half_open_start_time and now - self.half_open_start_time >= 10.0 and not self.half_open_failed:
                self.state = 'closed'
                self.trip_time = None
                self.half_open_start_time = None
                self.consecutive_exceed = 0

    def should_allow(self):
        with self.lock:
            now = time.time()
            # Auto-recover from OPEN to HALF_OPEN after cooldown
            if self.state == 'open' and self.trip_time and now - self.trip_time >= self.cooldown_sec:
                self.state = 'half_open'
                self.half_open_start_time = now
                self.half_open_failed = False

            # Auto-recover from HALF_OPEN to CLOSED after 10 sec without failure
            if self.state == 'half_open' and self.half_open_start_time and now - self.half_open_start_time >= 10.0 and not self.half_open_failed:
                self.state = 'closed'
                self.trip_time = None
                self.half_open_start_time = None
                self.consecutive_exceed = 0

            if self.state == 'closed':
                return True
            elif self.state == 'open':
                self.shed_requests += 1
                return False
            elif self.state == 'half_open':
                # Allow a fraction of requests to test the system
                if random.random() < self.recovery_half_open_ratio:
                    return True
                else:
                    self.shed_requests += 1
                    return False

    def trip(self):
        with self.lock:
            self.state = 'open'
            self.trip_time = time.time()
            self.trips += 1
            self.consecutive_exceed = 0
            self.half_open_start_time = None
            self.half_open_failed = False

    def reset(self):
        with self.lock:
            self.state = 'closed'
            self.trip_time = None
            self.half_open_start_time = None
            self.half_open_failed = False
            self.consecutive_exceed = 0

    def get_state(self):
        with self.lock:
            return self.state

    def stats(self):
        with self.lock:
            return {
                'total_requests': self.total_requests,
                'shed_requests': self.shed_requests,
                'trips': self.trips,
                'current_rps': self.current_rps
            }

    def record_failure(self):
        with self.lock:
            if self.state == 'half_open':
                self.state = 'open'
                self.trip_time = time.time()
                self.trips += 1
                self.half_open_start_time = None
                self.half_open_failed = False