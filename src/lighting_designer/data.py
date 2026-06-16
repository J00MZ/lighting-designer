# -*- coding: utf-8 -*-
"""Reference data tables, standards and design presets.

Public API surface for the lighting design data. The values themselves live in
:mod:`lighting_designer._core`; this module re-exports them so callers can use a
stable, well-named import path.
"""
from lighting_designer._core import (
    BEAM_ANGLES,
    CCT_PRESETS,
    CRI_STANDARDS,
    DEFAULT_FIXTURES,
    DESIGN_PRESETS,
    FIXTURE_CATEGORIES,
    LPD_LIMITS_W_M2,
    LUX_AMBIENT_ZONES,
    LUX_STANDARDS,
    MAGNETIC_TRACK_WIDTHS,
    NON_WORKPLACE_ROOM_TYPES,
    PENDANT_TYPES,
    ROOM_TYPES,
    SPACE_TEMPLATES,
    UGR_LIMITS,
    UI_LABELS,
    UNIFORMITY_TARGETS,
    P,
    T,
    cct_preset_for_kelvin,
    clamp,
    uniformity_target,
)

__all__ = [
    "BEAM_ANGLES", "CCT_PRESETS", "CRI_STANDARDS", "DEFAULT_FIXTURES",
    "DESIGN_PRESETS", "FIXTURE_CATEGORIES", "LPD_LIMITS_W_M2", "LUX_AMBIENT_ZONES",
    "LUX_STANDARDS", "MAGNETIC_TRACK_WIDTHS", "NON_WORKPLACE_ROOM_TYPES",
    "PENDANT_TYPES", "ROOM_TYPES", "SPACE_TEMPLATES", "UGR_LIMITS", "UI_LABELS",
    "UNIFORMITY_TARGETS", "P", "T", "cct_preset_for_kelvin", "clamp",
    "uniformity_target",
]
