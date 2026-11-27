from app import similar


def test_score_prefers_same_exam():
    knee = {"title": "MRI left knee without contrast", "modality": "MR", "body_part": "KNEE", "study_date": "2026-03-14"}
    prior = {"title": "MR knee lt w/o contrast", "modality": "MR", "body_part": "KNEE", "study_date": "2024-02-01"}
    other_side = {"title": "MRI right knee", "modality": "MR", "body_part": "KNEE"}
    chest = {"title": "CT chest with contrast", "modality": "CT", "body_part": "CHEST"}
    assert similar.score(knee, prior)[0] >= 6
    assert "Other side" in similar.score(knee, other_side)[1]
    assert similar.score(knee, chest)[0] == 0
    # CR and DX are both plain X-rays
    assert similar.score({"title": "XR wrist", "modality": "CR"}, {"title": "Wrist x-ray", "modality": "DX"})[0] >= 5


def test_rank_orders_suggested_then_nearest():
    base = {"title": "CT head", "modality": "CT", "study_date": "2026-01-10"}
    rows = [
        {"id": "a", "title": "CT head", "modality": "CT", "study_date": "2020-01-01"},
        {"id": "b", "title": "Chest x-ray", "modality": "DX", "study_date": "2026-01-09"},
        {"id": "c", "title": "CT head without contrast", "modality": "CT", "study_date": "2025-12-01"},
        {"id": "d", "title": "CT head", "modality": "CT", "study_date": None},
    ]
    out = similar.rank(base, rows)
    assert [s["id"] for s in out][-1] == "b" and not out[-1]["suggested"]
    a, c = next(s for s in out if s["id"] == "a"), next(s for s in out if s["id"] == "c")
    assert a["suggested"] and c["suggested"] and a["relation"] == "prior" and c["days_apart"] == 40
    assert out[0]["id"] == "c"  # equally alike, so the closer date wins
