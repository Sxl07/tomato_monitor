from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

import cv2
import torch
import torch.nn as nn
from PIL import Image
from torchvision import transforms
from torchvision.models import ResNet18_Weights, resnet18

from config.settings import DEVICE, HEALTH_MODEL_B_PATH
from config.thresholds import HEALTH_B_THRESHOLD, HEALTH_IMAGE_SIZE


def build_health_model_resnet(
    weights_path: Path | None = None,
) -> Tuple[torch.nn.Module, transforms.Compose]:
    model_path = Path(weights_path) if weights_path else HEALTH_MODEL_B_PATH

    weights = ResNet18_Weights.DEFAULT
    model = resnet18(weights=None)
    model.fc = nn.Linear(model.fc.in_features, 2)

    checkpoint = torch.load(model_path, map_location=DEVICE)

    if isinstance(checkpoint, dict) and "conv1.weight" in checkpoint:
        state_dict = checkpoint
    elif (
        isinstance(checkpoint, dict)
        and "model" in checkpoint
        and isinstance(checkpoint["model"], dict)
    ):
        state_dict = checkpoint["model"]
    else:
        raise ValueError(
            f"No pude interpretar el checkpoint ResNet en {model_path}. "
            f"Claves: {list(checkpoint.keys()) if isinstance(checkpoint, dict) else type(checkpoint)}"
        )

    model.load_state_dict(state_dict, strict=True)
    model.to(DEVICE)
    model.eval()

    transform = transforms.Compose([
        transforms.Resize((HEALTH_IMAGE_SIZE, HEALTH_IMAGE_SIZE)),
        transforms.ToTensor(),
        transforms.Normalize(
            mean=weights.transforms().mean,
            std=weights.transforms().std,
        ),
    ])

    return model, transform


@torch.no_grad()
def predict_health(
    model: torch.nn.Module,
    transform: transforms.Compose,
    crop_bgr,
    threshold: float = HEALTH_B_THRESHOLD,
) -> Dict[str, Any]:
    if crop_bgr is None or crop_bgr.size == 0:
        raise ValueError("crop_bgr está vacío o es None")

    crop_rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(crop_rgb)

    x = transform(pil_img).unsqueeze(0).to(DEVICE)

    logits = model(x)
    probs = torch.softmax(logits, dim=1)[0].cpu().numpy()

    prob_healthy = float(probs[0])
    prob_unhealthy = float(probs[1])

    label = "unhealthy" if prob_unhealthy >= threshold else "healthy"
    confidence = prob_unhealthy if label == "unhealthy" else prob_healthy

    return {
        "label": label,
        "confidence": float(confidence),
        "prob_healthy": prob_healthy,
        "prob_unhealthy": prob_unhealthy,
    }