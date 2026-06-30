import time

class CircuitBreaker:
    def __init__(self, threshold, timeout):
        self.threshold = threshold
        self.timeout = timeout
        self.failures = 0
        self.last_failure_time = 0
        self.state = "closed"

    def can_request(self):
        if self.state == "closed":
            return True
        if self.state == "open":
            now = time.time()
            if now - self.last_failure_time > self.timeout:
                self.state = "half-open"
                return True
            return False
        return True

    def record_success(self):
        self.failures = 0
        self.state = "closed"

    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.threshold:
            self.state = "open"
