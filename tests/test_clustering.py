import unittest
from smart_city_common.clustering import compute_crowd_clusters

class TestClustering(unittest.TestCase):
    def test_single_person(self):
        dets = [{"bbox": [0, 0, 10, 10], "confidence": 0.9}]
        res = compute_crowd_clusters(dets, 0.8, 1.2)
        self.assertEqual(len(res), 0)

    def test_two_people_close(self):
        dets = [
            {"bbox": [0, 0, 10, 10], "confidence": 0.9},
            {"bbox": [5, 5, 15, 15], "confidence": 0.9}
        ]
        res = compute_crowd_clusters(dets, 0.8, 1.2)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["size"], 2)

    def test_two_people_far(self):
        dets = [
            {"bbox": [0, 0, 10, 10], "confidence": 0.9},
            {"bbox": [100, 100, 110, 110], "confidence": 0.9}
        ]
        res = compute_crowd_clusters(dets, 0.8, 1.2)
        self.assertEqual(len(res), 0)

    def test_three_people_connected(self):
        dets = [
            {"bbox": [0, 0, 10, 10], "confidence": 0.9},
            {"bbox": [5, 5, 15, 15], "confidence": 0.9},
            {"bbox": [10, 10, 20, 20], "confidence": 0.9}
        ]
        res = compute_crowd_clusters(dets, 0.8, 1.2)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["size"], 3)

if __name__ == "__main__":
    unittest.main()
