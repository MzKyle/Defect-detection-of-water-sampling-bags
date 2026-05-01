from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_VISIBILITY_MATRIX_PATH = ROOT_DIR / "config" / "multilight_visibility_matrix.yaml"


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class DefectVisibilityProfile:
    defect_type: str
    display_name: str
    visibility: dict[str, float]
    raw_visibility: dict[str, str]
    aliases: set[str] = field(default_factory=set)
    primary_lights: list[str] = field(default_factory=list)
    corroborating_lights: list[str] = field(default_factory=list)
    required_cues: set[str] = field(default_factory=set)
    suppress_cues: set[str] = field(default_factory=set)
    false_positive_risks: list[str] = field(default_factory=list)
    decision_logic: str = ""

    def normalized_weights(self, light_order: Sequence[str]) -> dict[str, float]:
        total = sum(self.visibility.get(light, 0.0) for light in light_order)
        if total <= 0:
            return {light: 1.0 / len(light_order) for light in light_order}
        return {light: self.visibility.get(light, 0.0) / total for light in light_order}


@dataclass(frozen=True)
class VisibilityMatrix:
    light_order: list[str]
    rating_values: dict[str, float]
    decision_thresholds: dict[str, float]
    defects: dict[str, DefectVisibilityProfile]
    aliases: dict[str, str]

    def resolve_defect_type(self, defect_type: str) -> str:
        normalized = defect_type.strip().lower()
        if normalized in self.defects:
            return normalized
        if normalized in self.aliases:
            return self.aliases[normalized]
        raise KeyError(f"Unknown defect type: {defect_type}")

    def profile_for(self, defect_type: str) -> DefectVisibilityProfile:
        return self.defects[self.resolve_defect_type(defect_type)]


@dataclass(frozen=True)
class VisibilityAssessment:
    defect_type: str
    display_name: str
    recommended_action: str
    final_score: float
    support_score: float
    consistency_score: float
    cue_score: float
    suppress_penalty: float
    unexpected_response_score: float
    model_confidence: float
    light_scores: dict[str, float]
    expected_weights: dict[str, float]
    matched_required_cues: list[str]
    missing_required_cues: list[str]
    matched_suppress_cues: list[str]
    primary_lights: list[str]
    explanation: str
    source_label: str | None = None
    profile_inferred: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "defect_type": self.defect_type,
            "display_name": self.display_name,
            "recommended_action": self.recommended_action,
            "final_score": round(self.final_score, 4),
            "support_score": round(self.support_score, 4),
            "consistency_score": round(self.consistency_score, 4),
            "cue_score": round(self.cue_score, 4),
            "suppress_penalty": round(self.suppress_penalty, 4),
            "unexpected_response_score": round(self.unexpected_response_score, 4),
            "model_confidence": round(self.model_confidence, 4),
            "light_scores": self.light_scores,
            "expected_weights": {
                light: round(weight, 4)
                for light, weight in self.expected_weights.items()
            },
            "matched_required_cues": self.matched_required_cues,
            "missing_required_cues": self.missing_required_cues,
            "matched_suppress_cues": self.matched_suppress_cues,
            "primary_lights": self.primary_lights,
            "explanation": self.explanation,
            "source_label": self.source_label,
            "profile_inferred": self.profile_inferred,
        }


@dataclass(frozen=True)
class VisibilityEvidence:
    light_scores: dict[str, float]
    consistency_score: float
    morphology_cues: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "light_scores": {
                light: round(score, 4)
                for light, score in self.light_scores.items()
            },
            "consistency_score": round(self.consistency_score, 4),
            "morphology_cues": self.morphology_cues,
        }


def load_visibility_matrix(path: str | Path | None = None) -> VisibilityMatrix:
    source = Path(path or DEFAULT_VISIBILITY_MATRIX_PATH)
    with source.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return visibility_matrix_from_dict(payload)


def visibility_matrix_from_dict(payload: Mapping[str, Any]) -> VisibilityMatrix:
    light_order = [str(item) for item in payload.get("light_order", [])]
    if not light_order:
        raise ValueError("visibility matrix requires light_order.")

    rating_values = {
        str(name): float(value)
        for name, value in (payload.get("rating_values") or {}).items()
    }
    if not rating_values:
        raise ValueError("visibility matrix requires rating_values.")

    decision_thresholds = {
        "ng": 0.68,
        "review": 0.42,
        **{
            str(name): float(value)
            for name, value in (payload.get("decision_thresholds") or {}).items()
        },
    }

    defects: dict[str, DefectVisibilityProfile] = {}
    aliases: dict[str, str] = {}
    for defect_type, raw_profile in (payload.get("defects") or {}).items():
        key = str(defect_type).strip().lower()
        raw_visibility = {
            str(light): str(rating)
            for light, rating in (raw_profile.get("visibility") or {}).items()
        }
        missing = [light for light in light_order if light not in raw_visibility]
        if missing:
            raise ValueError(f"{key} missing visibility for: {', '.join(missing)}")

        visibility = {}
        for light in light_order:
            rating = raw_visibility[light]
            if rating not in rating_values:
                raise ValueError(f"{key}.{light} uses unknown rating: {rating}")
            visibility[light] = rating_values[rating]

        profile_aliases = {str(item).strip().lower() for item in raw_profile.get("aliases", [])}
        profile = DefectVisibilityProfile(
            defect_type=key,
            display_name=str(raw_profile.get("display_name", key)),
            visibility=visibility,
            raw_visibility=raw_visibility,
            aliases=profile_aliases,
            primary_lights=[str(item) for item in raw_profile.get("primary_lights", [])],
            corroborating_lights=[
                str(item) for item in raw_profile.get("corroborating_lights", [])
            ],
            required_cues={
                str(item).strip().lower()
                for item in raw_profile.get("required_cues", [])
            },
            suppress_cues={
                str(item).strip().lower()
                for item in raw_profile.get("suppress_cues", [])
            },
            false_positive_risks=[
                str(item) for item in raw_profile.get("false_positive_risks", [])
            ],
            decision_logic=str(raw_profile.get("decision_logic", "")),
        )
        defects[key] = profile
        aliases[key] = key
        for alias in profile_aliases:
            aliases[alias] = key

    if not defects:
        raise ValueError("visibility matrix requires at least one defect profile.")

    return VisibilityMatrix(
        light_order=light_order,
        rating_values=rating_values,
        decision_thresholds=decision_thresholds,
        defects=defects,
        aliases=aliases,
    )


def assess_visibility_evidence(
    matrix: VisibilityMatrix,
    defect_type: str,
    light_scores: Mapping[str, float],
    *,
    consistency_score: float = 0.0,
    morphology_cues: Sequence[str] | None = None,
    model_confidence: float | None = None,
) -> VisibilityAssessment:
    profile = matrix.profile_for(defect_type)
    normalized_light_scores = {
        light: _clip01(light_scores.get(light, 0.0))
        for light in matrix.light_order
    }
    expected_weights = profile.normalized_weights(matrix.light_order)
    support_score = sum(
        expected_weights[light] * normalized_light_scores[light]
        for light in matrix.light_order
    )

    unexpected_response_score = sum(
        normalized_light_scores[light] * (1.0 - profile.visibility.get(light, 0.0))
        for light in matrix.light_order
    ) / len(matrix.light_order)

    cues = {str(item).strip().lower() for item in (morphology_cues or [])}
    matched_required = sorted(profile.required_cues & cues)
    missing_required = sorted(profile.required_cues - cues)
    cue_score = (
        len(matched_required) / len(profile.required_cues)
        if profile.required_cues
        else 1.0
    )
    matched_suppress = sorted(profile.suppress_cues & cues)
    suppress_penalty = min(0.45, 0.15 * len(matched_suppress))

    confidence = _clip01(model_confidence if model_confidence is not None else support_score)
    consistency = _clip01(consistency_score)
    final_score = _clip01(
        0.45 * support_score
        + 0.20 * consistency
        + 0.20 * cue_score
        + 0.15 * confidence
        - suppress_penalty
    )

    if final_score >= matrix.decision_thresholds["ng"]:
        action = "ng"
    elif final_score >= matrix.decision_thresholds["review"]:
        action = "review"
    else:
        action = "suppress"

    explanation = (
        f"{profile.display_name}: support={support_score:.2f}, "
        f"consistency={consistency:.2f}, cues={cue_score:.2f}, "
        f"suppress_penalty={suppress_penalty:.2f}. {profile.decision_logic}"
    )

    return VisibilityAssessment(
        defect_type=profile.defect_type,
        display_name=profile.display_name,
        recommended_action=action,
        final_score=final_score,
        support_score=support_score,
        consistency_score=consistency,
        cue_score=cue_score,
        suppress_penalty=suppress_penalty,
        unexpected_response_score=unexpected_response_score,
        model_confidence=confidence,
        light_scores=normalized_light_scores,
        expected_weights=expected_weights,
        matched_required_cues=matched_required,
        missing_required_cues=missing_required,
        matched_suppress_cues=matched_suppress,
        primary_lights=profile.primary_lights,
        explanation=explanation,
        source_label=defect_type,
        profile_inferred=False,
    )


def assess_best_visibility_evidence(
    matrix: VisibilityMatrix,
    defect_type: str,
    light_scores: Mapping[str, float],
    *,
    consistency_score: float = 0.0,
    morphology_cues: Sequence[str] | None = None,
    model_confidence: float | None = None,
) -> VisibilityAssessment:
    try:
        return assess_visibility_evidence(
            matrix,
            defect_type,
            light_scores,
            consistency_score=consistency_score,
            morphology_cues=morphology_cues,
            model_confidence=model_confidence,
        )
    except KeyError:
        assessments = [
            assess_visibility_evidence(
                matrix,
                profile.defect_type,
                light_scores,
                consistency_score=consistency_score,
                morphology_cues=morphology_cues,
                model_confidence=model_confidence,
            )
            for profile in matrix.defects.values()
        ]
        best = max(assessments, key=lambda item: item.final_score)
        return VisibilityAssessment(
            **{
                **best.__dict__,
                "source_label": defect_type,
                "profile_inferred": True,
            }
        )


def _box_value(box: Any, name: str) -> float:
    if isinstance(box, Mapping):
        return float(box[name])
    return float(getattr(box, name))


def _clip_box(box: Any, width: int, height: int) -> tuple[int, int, int, int]:
    x1 = int(round(min(_box_value(box, "x1"), _box_value(box, "x2"))))
    y1 = int(round(min(_box_value(box, "y1"), _box_value(box, "y2"))))
    x2 = int(round(max(_box_value(box, "x1"), _box_value(box, "x2"))))
    y2 = int(round(max(_box_value(box, "y1"), _box_value(box, "y2"))))
    return (
        max(0, min(width - 1, x1)),
        max(0, min(height - 1, y1)),
        max(1, min(width, x2)),
        max(1, min(height, y2)),
    )


def _roi_response(gray: Any, box: Any) -> float:
    import cv2
    import numpy as np

    height, width = gray.shape[:2]
    x1, y1, x2, y2 = _clip_box(box, width, height)
    if x2 <= x1 or y2 <= y1:
        return 0.0

    roi = gray[y1:y2, x1:x2].astype("float32")
    box_width = max(1, x2 - x1)
    box_height = max(1, y2 - y1)
    pad_x = max(4, box_width // 2)
    pad_y = max(4, box_height // 2)
    cx1 = max(0, x1 - pad_x)
    cy1 = max(0, y1 - pad_y)
    cx2 = min(width, x2 + pad_x)
    cy2 = min(height, y2 + pad_y)
    context = gray[cy1:cy2, cx1:cx2].astype("float32")

    contrast = abs(float(roi.mean()) - float(context.mean())) / 80.0
    texture = float(roi.std()) / 64.0
    sobel_x = cv2.Sobel(roi, cv2.CV_32F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(roi, cv2.CV_32F, 0, 1, ksize=3)
    edge = float(np.mean(np.abs(sobel_x) + np.abs(sobel_y))) / 96.0
    return _clip01(0.45 * contrast + 0.35 * texture + 0.20 * edge)


def _infer_morphology_cues(
    box: Any,
    image_shape: tuple[int, int],
    light_scores: Mapping[str, float],
    consistency_score: float,
) -> list[str]:
    height, width = image_shape
    x1, y1, x2, y2 = _clip_box(box, width, height)
    box_width = max(1, x2 - x1)
    box_height = max(1, y2 - y1)
    area_ratio = (box_width * box_height) / max(1, width * height)
    aspect_ratio = max(box_width / box_height, box_height / box_width)
    max_response = max(light_scores.values(), default=0.0)

    cues: set[str] = set()
    if aspect_ratio >= 3.0:
        cues.update({"linear_structure", "high_aspect_ratio"})
    if aspect_ratio >= 4.0 and min(box_width, box_height) <= 24:
        cues.update({"slender", "thin_continuous"})
    if aspect_ratio >= 3.0 and min(box_width, box_height) > 24:
        cues.add("wide_continuous")
    if aspect_ratio < 2.0 and area_ratio <= 0.03:
        cues.add("localized_blob")
    if area_ratio <= 0.003 and light_scores.get("backlight", 0.0) >= 0.45:
        cues.update({"tiny_bright_point", "high_local_contrast"})
    if max_response >= 0.35:
        cues.add("clear_boundary")
    if consistency_score >= 0.55:
        cues.update({"stable_position", "consistent_position"})
    if light_scores.get("darkfield", 0.0) >= 0.45 and area_ratio <= 0.08:
        cues.add("local_relief")
    return sorted(cues)


def estimate_visibility_evidence(
    light_image_paths: Mapping[str, str],
    box: Any,
    light_order: Sequence[str],
) -> VisibilityEvidence:
    import cv2
    import numpy as np

    light_scores: dict[str, float] = {}
    image_shape = (1, 1)
    for light in light_order:
        image_path = light_image_paths.get(light)
        if not image_path:
            light_scores[light] = 0.0
            continue
        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            light_scores[light] = 0.0
            continue
        image_shape = image.shape[:2]
        light_scores[light] = _roi_response(image, box)

    values = np.array([light_scores[light] for light in light_order], dtype="float32")
    if float(values.mean()) <= 1e-6:
        consistency_score = 0.0
    else:
        presence = float((values >= 0.20).sum()) / max(1, len(values))
        balance = 1.0 - min(1.0, float(values.std() / (values.mean() + 1e-6)))
        consistency_score = _clip01(0.60 * presence + 0.40 * balance)

    cues = _infer_morphology_cues(box, image_shape, light_scores, consistency_score)
    return VisibilityEvidence(
        light_scores=light_scores,
        consistency_score=consistency_score,
        morphology_cues=cues,
    )
