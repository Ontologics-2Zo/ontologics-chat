"""Retrieval measurer: binary relevance metrics and refusal of unsafe inputs."""
import unittest

from dev.evaluate_retrieval import (EvaluationError, aggregate, score_case, valid_base_url,
                                    validate_cases)


def hit(kind, text):
    return {"kind": kind, "text": text}


class RetrievalMeasurerTests(unittest.TestCase):
    def test_metrics_follow_the_first_relevant_rank(self):
        relevant = [{"kind": "source_row", "text": "A1"}, {"kind": "ledger_entity", "text": "B2"}]
        hits = [hit("source_row", "x"), hit("source_row", "row A1"), hit("ledger_entity", "z"),
                hit("ledger_entity", "B2 item"), hit("source_row", "y"), hit("source_row", "late")]
        m = score_case(hits, relevant, 3)
        self.assertEqual((m["recall_at_k"], m["recall_at_limit"]), (0.5, 1.0))
        self.assertAlmostEqual(m["precision_at_k"], 1 / 3)
        self.assertEqual(m["reciprocal_rank"], 0.5)
        self.assertEqual(m["hit_at_k"], 1.0)
        self.assertGreater(m["ndcg_at_k"], 0)
        empty = score_case([], relevant, 3)
        self.assertEqual((empty["recall_at_limit"], empty["reciprocal_rank"], empty["ndcg_at_k"]), (0, 0.0, 0.0))

    def test_kind_must_match_as_well_as_text(self):
        m = score_case([hit("source_row", "B2")], [{"kind": "ledger_entity", "text": "B2"}], 1)
        self.assertEqual(m["recall_at_limit"], 0)

    def test_errors_are_counted_but_not_scored(self):
        rows = [{"status": "HIT", "latency_ms": 1, "metrics": score_case([hit("source_row", "A")], [{"kind": "source_row", "text": "A"}], 1)},
                {"status": "HTTP_409", "latency_ms": 3, "metrics": score_case([], [{"kind": "source_row", "text": "A"}], 1)}]
        result = aggregate(rows)
        self.assertEqual((result["cases"], result["scored"], result["recall_at_k"]), (2, 1, 1.0))
        self.assertEqual(result["status"], {"HIT": 1, "HTTP_409": 1})

    def test_only_isolated_loopback_servers(self):
        self.assertEqual(valid_base_url("http://localhost:8874/"), "http://localhost:8874")
        for url in ("http://127.0.0.1:8870", "https://localhost:8874", "http://remote.invalid:8874", "http://localhost:8874/x"):
            with self.subTest(url=url), self.assertRaises(EvaluationError):
                valid_base_url(url)

    def test_case_file_shape_is_strict(self):
        good = {"schema_version": 1, "ledger_version": 9, "cases": [{"id": "r01", "group": "exact_id", "query": "q",
                "relevant": [{"kind": "source_row", "text": "A"}]}]}
        self.assertEqual(validate_cases(good)[0], 9)
        for change in ({"schema_version": 2}, {"cases": []}, {"ledger_version": 0}):
            with self.subTest(change=change), self.assertRaises(EvaluationError):
                validate_cases({**good, **change})
        bad_case = dict(good["cases"][0], relevant=[{"kind": "unknown", "text": "A"}])
        with self.assertRaises(EvaluationError):
            validate_cases({**good, "cases": [bad_case]})
        with self.assertRaises(EvaluationError):
            validate_cases({**good, "cases": [good["cases"][0], good["cases"][0]]})


if __name__ == "__main__":
    unittest.main()
