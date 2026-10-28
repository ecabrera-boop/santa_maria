from types import SimpleNamespace

from django.test import SimpleTestCase

from evaluation.metrics import calculate_metrics


class EvaluationMetricTests(SimpleTestCase):
    def test_metrics_use_fixed_category_order_and_exclude_failed_records(self):
        records = [
            SimpleNamespace(validation_status="ok", reference_label="fire", predicted_label="fire", processing_time_ms=10.0, client_roundtrip_ms=20.0),
            SimpleNamespace(validation_status="ok", reference_label="fire", predicted_label="crime", processing_time_ms=30.0, client_roundtrip_ms=40.0),
            SimpleNamespace(validation_status="ok", reference_label="crime", predicted_label="crime", processing_time_ms=20.0, client_roundtrip_ms=None),
            SimpleNamespace(validation_status="failed", reference_label="medical", predicted_label=None, processing_time_ms=None, client_roundtrip_ms=None),
        ]
        metrics = calculate_metrics(records)
        self.assertEqual(metrics["successful"], 3)
        self.assertEqual(metrics["failed"], 1)
        self.assertAlmostEqual(metrics["accuracy"], 2 / 3)
        self.assertEqual(metrics["confusion_matrix"][0][0], 1)
        self.assertEqual(metrics["confusion_matrix"][0][1], 1)
        self.assertEqual(metrics["confusion_matrix"][1][1], 1)
        self.assertEqual(metrics["per_class"]["medical"]["support"], 0)
        self.assertEqual(metrics["mean_ms"], 20.0)
        self.assertEqual(metrics["mean_roundtrip_ms"], 30.0)

    def test_empty_run_has_empty_metrics_and_fixed_zero_matrix(self):
        metrics = calculate_metrics([])
        self.assertIsNone(metrics["accuracy"])
        self.assertEqual(len(metrics["confusion_matrix"]), 5)
        self.assertEqual(metrics["successful"], 0)
