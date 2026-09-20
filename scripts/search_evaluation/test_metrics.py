import unittest

from run_benchmark import metrics, percentile


class MetricTests(unittest.TestCase):
    def test_duplicate_hits_cannot_inflate_relevance(self):
        result = metrics([7, 7, 9], {7, 9})
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(result["recall10"], 1)
        self.assertLess(result["ndcg10"], 1)

    def test_scope_with_no_relevant_items_is_not_a_relevance_failure(self):
        result = metrics([7], set())
        self.assertIsNone(result["ndcg10"])
        self.assertIsNone(result["hit10"])

    def test_missing_results_are_a_failure_when_targets_exist(self):
        self.assertEqual(metrics([], {7})["ndcg10"], 0)
        self.assertEqual(metrics([], {7})["recall10"], 0)

    def test_perfect_order_and_rank_cutoff(self):
        self.assertEqual(metrics([7, 9], {7, 9})["ndcg10"], 1)
        self.assertEqual(metrics(list(range(11)), {10})["hit10"], 0)

    def test_nearest_rank_percentiles(self):
        self.assertEqual(percentile(list(range(1, 101)), 0.95), 95)


if __name__ == "__main__":
    unittest.main()
