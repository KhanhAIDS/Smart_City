import unittest
import time
from smart_city_common.circuit_breaker import CircuitBreaker

class TestCircuitBreaker(unittest.TestCase):
    def setUp(self):
        self.cb = CircuitBreaker(threshold=2, timeout=0.1)

    def test_breaker_trips(self):
        self.assertTrue(self.cb.can_request())
        
        self.cb.record_failure()
        self.assertTrue(self.cb.can_request())
        
        self.cb.record_failure()
        self.assertFalse(self.cb.can_request())
        
        time.sleep(0.15)
        self.assertTrue(self.cb.can_request())
        self.assertEqual(self.cb.state, "half-open")
        
        self.cb.record_success()
        self.assertTrue(self.cb.can_request())
        self.assertEqual(self.cb.state, "closed")

if __name__ == "__main__":
    unittest.main()
