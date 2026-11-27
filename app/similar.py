"""Rank a patient's other studies by how alike they are to one study, so the
viewer can suggest a prior to compare against (same modality, body part and
wording, nearest in time). Works on the decrypted study dicts only."""
from __future__ import annotations

import re
from datetime import date

# Modalities that are the same kind of picture for comparison purposes.
_MOD_GROUP = {"CR": "XR", "DX": "XR", "DR": "XR", "XR": "XR", "MR": "MR", "MRI": "MR", "PT": "PET", "PET": "PET"}
_MOD_NAME = {"XR": "X-ray", "MR": "MRI", "CT": "CT", "US": "ultrasound", "MG": "mammogram", "NM": "nuclear medicine", "PET": "PET"}
# Words that say nothing about which part of the body was imaged.
_STOP = set(
    "a an and the of with without w wo o contrast iv non noncon study exam imaging image images series scan routine "
    "protocol view views screening followup follow up limited complete bilateral single".split()
)
_ALIAS = {"mri": "mr", "xray": "xr", "x": "xr", "ray": "xr", "radiograph": "xr", "lt": "left", "rt": "right", "l": "left", "r": "right",
          "knees": "knee", "hips": "hip", "wrists": "wrist", "shoulders": "shoulder", "ankles": "ankle", "brain": "head", "skull": "head",
          "thorax": "chest", "lungs": "chest", "lung": "chest", "abd": "abdomen", "abdominal": "abdomen", "mammo": "breast", "mammogram": "breast",
          "mammography": "breast"}
_SIDES = {"left", "right"}


def _mod(s: dict) -> str:
    m = (s.get("modality") or "").strip().upper()
    return _MOD_GROUP.get(m, m)


def _words(s: dict) -> set[str]:
    text = " ".join(str(s.get(k) or "") for k in ("title", "description", "body_part")).lower()
    out = set()
    for w in re.findall(r"[a-z]+", text):
        w = _ALIAS.get(w, w)
        if w not in _STOP and len(w) > 1:
            out.add(w)
    # the modality is scored on its own, don't double count it as a word
    return out - {"mr", "ct", "xr", "us", "dx", "cr", "mg", "nm", "pet", "pt"}


def _day(s: dict) -> date | None:
    try:
        return date.fromisoformat((s.get("study_date") or "")[:10])
    except ValueError:
        return None


def score(base: dict, other: dict) -> tuple[float, list[str]]:
    """How alike two studies are (0 = unrelated, about 7 = same exam), with reasons a person can read."""
    pts, why = 0.0, []
    mb, mo = _mod(base), _mod(other)
    if mb and mb == mo:
        pts += 2
        why.append(f"Same type ({_MOD_NAME.get(mb, mb)})")
    bb, bo = (base.get("body_part") or "").strip().lower(), (other.get("body_part") or "").strip().lower()
    wb, wo = _words(base), _words(other)
    if bb and bb == bo:
        pts += 2
        why.append(f"Same body part ({bb})")
    parts_b, parts_o = wb - _SIDES, wo - _SIDES
    common = parts_b & parts_o
    if common and parts_b | parts_o:
        pts += 3 * len(common) / len(parts_b | parts_o)
        if not (bb and bb == bo):
            why.append("Similar description")
    sb, so = wb & _SIDES, wo & _SIDES
    if pts and sb and so and sb != so:
        pts -= 1.5
        why.append("Other side")
    return round(max(pts, 0.0), 2), why


def rank(base: dict, others: list[dict]) -> list[dict]:
    """Annotate and sort candidates: suggested matches first, then by likeness and closeness in time."""
    d0 = _day(base)
    out = []
    for s in others:
        pts, why = score(base, s)
        d1 = _day(s)
        gap = abs((d1 - d0).days) if d0 and d1 else None
        suggested = pts >= 3 and "Other side" not in why
        out.append({**s, "similarity": pts, "suggested": suggested, "match_reasons": why, "days_apart": gap,
                    "relation": None if not (d0 and d1) else "prior" if d1 < d0 else "later" if d1 > d0 else "same day"})
    out.sort(key=lambda s: (not s["suggested"], -s["similarity"], s["days_apart"] if s["days_apart"] is not None else 10**6))
    return out
