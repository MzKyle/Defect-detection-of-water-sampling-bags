from waterbag_inspection.visibility_matrix import (
    assess_visibility_evidence,
    load_visibility_matrix,
)


def test_visibility_matrix_loads_current_three_light_profiles():
    matrix = load_visibility_matrix()

    assert matrix.light_order == ["backlight", "darkfield", "polarized"]
    assert matrix.resolve_defect_type("hair") == "hair_fiber"
    assert matrix.profile_for("rupture_edge").visibility["backlight"] == 1.0


def test_hair_fiber_darkfield_evidence_scores_ng():
    matrix = load_visibility_matrix()

    assessment = assess_visibility_evidence(
        matrix,
        "hair",
        {"backlight": 0.45, "darkfield": 0.95, "polarized": 0.55},
        consistency_score=0.82,
        model_confidence=0.9,
        morphology_cues=["slender", "high_aspect_ratio", "thin_continuous"],
    )

    assert assessment.recommended_action == "ng"
    assert assessment.primary_lights == ["darkfield"]
    assert assessment.missing_required_cues == []


def test_normal_crease_cues_reduce_white_crease_to_review_or_suppress():
    matrix = load_visibility_matrix()

    assessment = assess_visibility_evidence(
        matrix,
        "white_crease",
        {"backlight": 0.2, "darkfield": 0.9, "polarized": 0.88},
        consistency_score=0.7,
        model_confidence=0.8,
        morphology_cues=[
            "linear_structure",
            "wide_continuous",
            "natural_texture_direction",
            "low_edge_sharpness",
        ],
    )

    assert assessment.recommended_action in {"review", "suppress"}
    assert assessment.suppress_penalty > 0.0
    assert assessment.matched_suppress_cues == [
        "low_edge_sharpness",
        "natural_texture_direction",
        "wide_continuous",
    ]
