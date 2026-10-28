import hashlib
import logging
import time
from functools import lru_cache

import numpy as np
from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image, ImageOps, UnidentifiedImageError

from .models import ModelVersion

logger = logging.getLogger(__name__)

MIME_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


class ImageValidationError(ValidationError):
    pass


@lru_cache(maxsize=2)
def _load_predictor(version_id, model_path, architecture, class_names, input_size):
    """Load a trained model once per model version; return a callable or None."""
    if not model_path:
        return None
    if model_path.lower().endswith(".keras"):
        try:
            from tensorflow.keras.models import load_model

            model = load_model(model_path)
            return ("keras", model)
        except ImportError:
            logger.warning("TensorFlow is not installed; using a labeled demo prediction.")
            return None
        except Exception:
            logger.exception("Could not load classifier model %s", version_id)
            return None
    if model_path.lower().endswith(".tflite"):
        try:
            try:
                import tensorflow as tf

                interpreter = tf.lite.Interpreter(model_path=model_path)
            except ImportError:
                from ai_edge_litert.interpreter import Interpreter

                interpreter = Interpreter(model_path=model_path)
            interpreter.allocate_tensors()
            return ("tflite", interpreter)
        except ImportError:
            logger.warning("No TensorFlow Lite interpreter is installed; using a labeled demo prediction.")
            return None
        except Exception:
            logger.exception("Could not load TFLite model %s", version_id)
            return None
    logger.warning("Unsupported classifier model format: %s", model_path)
    return None


def _preprocess(image, architecture, input_size):
    resized = image.resize((input_size, input_size), Image.Resampling.LANCZOS)
    array = np.asarray(resized, dtype=np.float32)
    if architecture == ModelVersion.Architecture.MOBILENET_V2:
        array = array / 127.5 - 1
    return np.expand_dims(array, axis=0)


def _predict(predictor, tensor):
    kind, model = predictor
    if kind == "keras":
        output = model.predict(tensor, verbose=0)
    else:
        input_details = model.get_input_details()
        output_details = model.get_output_details()
        model.set_tensor(input_details[0]["index"], tensor.astype(input_details[0]["dtype"]))
        model.invoke()
        output = model.get_tensor(output_details[0]["index"])
    return np.asarray(output).reshape(-1).astype(float)


def _demo_prediction(image_bytes):
    digest = hashlib.sha256(image_bytes).digest()
    categories = settings.EIC_CATEGORIES
    winner = digest[0] % len(categories)
    confidence = 0.55 + (digest[1] / 255) * 0.39
    remaining = [digest[index + 2] + 1 for index in range(len(categories) - 1)]
    remaining_total = sum(remaining) or 1
    probabilities = {}
    cursor = 0
    for index, category in enumerate(categories):
        if index == winner:
            probabilities[category] = confidence
        else:
            probabilities[category] = (1 - confidence) * remaining[cursor] / remaining_total
            cursor += 1
    return categories[winner], confidence, probabilities


def classify_image(uploaded_file, model_version=None):
    started = time.perf_counter()
    max_bytes = settings.EIC_MAX_UPLOAD_MB * 1024 * 1024
    content_type = getattr(uploaded_file, "content_type", "") or ""
    if content_type not in settings.EIC_ALLOWED_IMAGE_TYPES:
        raise ImageValidationError("Upload a JPEG, PNG, or WEBP image.")
    if getattr(uploaded_file, "size", 0) > max_bytes:
        raise ImageValidationError(f"Image must be smaller than {settings.EIC_MAX_UPLOAD_MB} MB.")

    try:
        uploaded_file.seek(0)
        image_bytes = uploaded_file.read()
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as opened:
            expected_format = MIME_FORMATS.get(content_type)
            if opened.format != expected_format:
                raise ImageValidationError("The file contents do not match the selected image type.")
            opened.verify()
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            image_width, image_height = image.size
    except ImageValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError):
        raise ImageValidationError("This file is not a valid image. Choose another photo.")

    active = model_version or ModelVersion.objects.filter(is_active=True).first()
    predictor = None
    categories = list(settings.EIC_CATEGORIES)
    input_size = 224
    architecture = ModelVersion.Architecture.EFFICIENTNET
    if active:
        categories = active.class_names
        input_size = active.input_size
        architecture = active.architecture
        model_path = active.model_file.path if active.model_file else ""
        predictor = _load_predictor(
            active.pk,
            model_path,
            architecture,
            tuple(categories),
            input_size,
        )

    if predictor is not None:
        tensor = _preprocess(image, architecture, input_size)
        scores = _predict(predictor, tensor)
        if len(scores) != len(categories):
            logger.error("Classifier output count does not match class_names for model %s", active.pk)
            predictor = None
        else:
            scores = np.clip(scores, 0, None)
            total = float(scores.sum())
            probabilities_list = scores / total if total > 0 else np.full(len(scores), 1 / len(scores))
            probabilities = {category: float(probabilities_list[index]) for index, category in enumerate(categories)}
            category = max(probabilities, key=probabilities.get)
            confidence = probabilities[category]
            is_demo = False

    if predictor is None:
        logger.warning("Demo prediction generated; no usable trained classifier is loaded.")
        category, confidence, probabilities = _demo_prediction(image_bytes)
        is_demo = True

    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "category": category,
        "confidence": float(confidence),
        "probabilities": probabilities,
        "processing_time_ms": elapsed_ms,
        "is_low_confidence": confidence < settings.EIC_LOW_CONFIDENCE_THRESHOLD,
        "is_demo": is_demo,
        "model_version_id": active.pk if active and not is_demo else None,
        "image_width": image_width,
        "image_height": image_height,
        "file_size_bytes": len(image_bytes),
    }
