import numpy as np
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix

from django.conf import settings


def calculate_metrics(records):
    labels = list(settings.EIC_CATEGORIES)
    valid = [
        record
        for record in records
        if record.validation_status == "ok" and record.predicted_label in labels and record.reference_label in labels
    ]
    if not valid:
        return {
            "successful": 0,
            "failed": len(records),
            "accuracy": None,
            "macro_precision": None,
            "macro_recall": None,
            "macro_f1": None,
            "weighted_precision": None,
            "weighted_recall": None,
            "weighted_f1": None,
            "confusion_matrix": [[0 for _ in labels] for _ in labels],
            "per_class": {label: {"precision": 0, "recall": 0, "f1": 0, "support": 0} for label in labels},
        }

    truth = [record.reference_label for record in valid]
    predicted = [record.predicted_label for record in valid]
    report = classification_report(truth, predicted, labels=labels, output_dict=True, zero_division=0)
    per_class = {
        label: {
            "precision": report[label]["precision"],
            "recall": report[label]["recall"],
            "f1": report[label]["f1-score"],
            "support": int(report[label]["support"]),
        }
        for label in labels
    }
    processing = [record.processing_time_ms for record in valid if record.processing_time_ms is not None]
    roundtrip = [record.client_roundtrip_ms for record in valid if record.client_roundtrip_ms is not None]
    return {
        "successful": len(valid),
        "failed": len(records) - len(valid),
        "accuracy": accuracy_score(truth, predicted),
        "macro_precision": report["macro avg"]["precision"],
        "macro_recall": report["macro avg"]["recall"],
        "macro_f1": report["macro avg"]["f1-score"],
        "weighted_precision": report["weighted avg"]["precision"],
        "weighted_recall": report["weighted avg"]["recall"],
        "weighted_f1": report["weighted avg"]["f1-score"],
        "confusion_matrix": confusion_matrix(truth, predicted, labels=labels).tolist(),
        "per_class": per_class,
        "mean_ms": float(np.mean(processing)) if processing else None,
        "median_ms": float(np.median(processing)) if processing else None,
        "min_ms": min(processing) if processing else None,
        "max_ms": max(processing) if processing else None,
        "mean_roundtrip_ms": float(np.mean(roundtrip)) if roundtrip else None,
    }


def finish_test_run(run):
    records = list(run.records.all())
    metrics = calculate_metrics(records)
    for field, value in metrics.items():
        setattr(run, field, value)
    run.total_images = len(records)
    run.status = "done"
    from django.utils import timezone

    run.finished_at = timezone.now()
    run.save()
    return run
