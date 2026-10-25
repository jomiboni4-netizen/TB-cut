import unittest

from final_quality_gate import CANDIDATE_FORBIDDEN, CONTRACT


class CandidateFilterContractTest(unittest.TestCase):
    def test_final_gate_imports_current_candidate_filter(self):
        self.assertEqual(CONTRACT, "final-gate-v8-candidate-filter-import")
        self.assertIsNotNone(CANDIDATE_FORBIDDEN.search("优惠券"))
        self.assertIsNone(CANDIDATE_FORBIDDEN.search("面料柔软"))
