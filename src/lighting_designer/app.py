# -*- coding: utf-8 -*-
"""
Lighting Design Pro - V7.6
==========================
Single-file PySide6 desktop application for architects and lighting designers.

Run:
    pip install PySide6 reportlab openpyxl
    python lighting_mvp_v7_1.py

V7.2 keeps the V7.1 single-file application and adds commercial-planning modules:
    - Ceiling pendants / chandeliers layer
    - Point lux calculation using E = I*cos^3(theta)/h^2
    - 20x20 lux heatmap overlay
    - JSON/CSV fixture catalogue import
    - Energy consumption report for hour/day/month operation
    - EN 12464-style compliance panel
    - Zones, validation, daylight estimate, scenes, branded quotation and DXF export hooks
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import logging
import math
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "Lighting Design Pro V7.7"
APP_VERSION = "7.7.0"
POINT_CALC_CALIBRATION = 0.16
AMBIENT_SHAPES = ["קו ישר", "L-shape", "U-shape", "היקפי"]

P = {
    "bg": "#0F1117",
    "surface": "#171A22",
    "card": "#1E2230",
    "card2": "#252A3A",
    "input": "#1A1E2A",
    "border": "#2A3048",
    "border2": "#3A4468",
    "text": "#F0F4FF",
    "muted": "#8A93A8",
    "blue": "#3D8EF0",
    "green": "#2ECC7A",
    "amber": "#F0A030",
    "red": "#EF4444",
    "purple": "#9F7AEA",
    "cyan": "#22D3EE",
    "gold": "#D4A850",
}

LOG_DIR = os.path.join(tempfile.gettempdir(), "lighting_design_pro")
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(LOG_DIR, "lighting_design_pro.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("LightingDesignPro")

STYLESHEET = f"""
QMainWindow, QWidget {{
    background: #0B0D12;
    color: {P['text']};
    font-family: 'Segoe UI Variable', 'Segoe UI', Arial, sans-serif;
    font-size: 13px;
}}
QTabWidget::pane {{
    border: 1px solid {P['border']};
    background: {P['surface']};
    border-radius: 8px;
}}
QTabBar::tab {{
    background: #10131B;
    color: {P['muted']};
    padding: 10px 16px;
    margin-right: 2px;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
}}
QTabBar::tab:selected {{
    color: #DDEBFF;
    background: #181D2A;
    border-bottom: 2px solid {P['blue']};
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: #111723;
    border: 1px solid #2D3650;
    border-radius: 7px;
    padding: 5px 8px;
    color: {P['text']};
    min-height: 28px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {P['blue']};
}}
QPushButton {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #4A9BFF, stop:1 #2468D8);
    color: white;
    border: none;
    border-radius: 7px;
    padding: 8px 14px;
    font-weight: 700;
}}
QPushButton:hover {{ background: #5AA8FF; }}
QPushButton#secondary {{
    background: #111723;
    color: {P['text']};
    border: 1px solid {P['border']};
}}
QPushButton#green {{ background: {P['green']}; }}
QPushButton#amber {{ background: {P['amber']}; }}
QPushButton#danger {{ background: {P['red']}; }}
QTextEdit {{
    background: {P['card']};
    border: 1px solid {P['border']};
    border-radius: 7px;
    color: {P['text']};
    padding: 8px;
}}
QScrollArea {{ border: none; background: transparent; }}
QStatusBar {{
    background: {P['surface']};
    color: {P['muted']};
    border-top: 1px solid {P['border']};
}}
QToolBar {{
    background: {P['surface']};
    border-bottom: 1px solid {P['border']};
    spacing: 4px;
}}
"""

LUX_STANDARDS = {
    "סלון": 150,
    "מטבח": 400,
    "חדר שינה": 120,
    "משרד": 500,
    "חדר עבודה": 500,
    "מסדרון": 100,
    "חדר אמבטיה": 250,
    "חנות": 800,
    "מסעדה": 200,
    "ספריה": 400,
}
CRI_STANDARDS = {**{k: 80 for k in LUX_STANDARDS}, "מטבח": 90, "חדר אמבטיה": 90, "חנות": 95, "מסעדה": 90}
UGR_LIMITS = {**{k: 22 for k in LUX_STANDARDS}, "משרד": 19, "חדר עבודה": 19, "ספריה": 19, "חדר שינה": 28}
LPD_LIMITS_W_M2 = {
    "סלון": 10,
    "מטבח": 14,
    "חדר שינה": 8,
    "משרד": 11,
    "חדר עבודה": 11,
    "מסדרון": 6,
    "חדר אמבטיה": 10,
    "חנות": 18,
    "מסעדה": 10,
    "ספריה": 12,
}
ROOM_TYPES = list(LUX_STANDARDS)
BEAM_ANGLES = [15, 24, 36, 45, 60, 90]
CCT_PRESETS = {
    "חמים (2700K)": (2700, 0.85),
    "נייטרל (3000K)": (3000, 1.00),
    "פוקוס (4000K)": (4000, 1.15),
    "הוספיטליטי (2200K)": (2200, 0.70),
}

DEFAULT_FIXTURES: Dict[str, Dict] = {
    "ספוט שקוע 36deg": {"lm": 800, "w": 8, "cri": 90, "beam": 36, "cct": 3000, "brand": "Generic", "price": 95},
    "ספוט מתכוונן 24deg": {"lm": 900, "w": 9, "cri": 90, "beam": 24, "cct": 3000, "brand": "Generic", "price": 125},
    "פס ליניארי 90cm": {"lm": 1800, "w": 18, "cri": 90, "beam": 120, "cct": 3000, "brand": "Generic", "price": 220},
    "ספוט מסלול 24deg": {"lm": 950, "w": 10, "cri": 92, "beam": 24, "cct": 3000, "brand": "TrackCo", "price": 155},
    "תלוי פנדנט": {"lm": 1500, "w": 15, "cri": 90, "beam": 90, "cct": 3000, "brand": "PendantCo", "price": 280},
    "נברשת דקורטיבית": {"lm": 4500, "w": 42, "cri": 90, "beam": 120, "cct": 2700, "brand": "PendantCo", "price": 980},
    "פנדנט אקוסטי": {"lm": 2200, "w": 22, "cri": 90, "beam": 100, "cct": 3000, "brand": "Acoustic", "price": 520},
}

PENDANT_TYPES = ["פנדנט בודד", "שורת פנדנטים", "נברשת", "פנדנט אקוסטי"]


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


@dataclass
class LightingLayer:
    name: str
    enabled: bool = True
    intensity: int = 100

    @property
    def factor(self) -> float:
        return self.intensity / 100 if self.enabled else 0.0

    def to_dict(self) -> Dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: Dict) -> "LightingLayer":
        return cls(d.get("name", "שכבה"), d.get("enabled", True), d.get("intensity", 100))


@dataclass
class ProfileConfig:
    name: str = "פרופיל"
    enabled: bool = False
    shape: str = "Linear"
    length_m: float = 3.0
    width_m: float = 0.035
    height_m: float = 0.02
    lm_per_m: int = 600
    x: float = 0.5
    y: float = 0.5
    angle_deg: float = 0.0

    @property
    def total_lm(self) -> float:
        return self.length_m * self.lm_per_m

    @property
    def watts(self) -> float:
        return self.total_lm / 100

    def to_dict(self) -> Dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: Dict) -> "ProfileConfig":
        return cls(**{k: d.get(k, v) for k, v in cls().__dict__.items()})


@dataclass
class TrackFixture:
    fixture_type: str
    pos_along: float = 0.5

    def to_dict(self) -> Dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: Dict) -> "TrackFixture":
        return cls(d.get("fixture_type", "ספוט מסלול 24deg"), d.get("pos_along", 0.5))


@dataclass
class MagneticTrack:
    name: str = "מסלול"
    enabled: bool = False
    shape: str = "Linear"
    length_m: float = 3.0
    width_cm: float = 2.3
    x: float = 0.5
    y: float = 0.4
    angle_deg: float = 0.0
    fixtures: List[TrackFixture] = field(default_factory=list)

    def fixture_points(self, room: "RoomModel") -> Iterable[Tuple[float, float, TrackFixture]]:
        rad = math.radians(self.angle_deg)
        dx, dy = math.cos(rad), math.sin(rad)
        cx, cy = room.width * self.x, room.length * self.y
        half = self.length_m / 2
        for f in self.fixtures:
            t = (f.pos_along - 0.5) * 2 * half
            yield cx + t * dx, cy + t * dy, f

    def to_dict(self) -> Dict:
        return {**self.__dict__, "fixtures": [f.to_dict() for f in self.fixtures]}

    @classmethod
    def from_dict(cls, d: Dict) -> "MagneticTrack":
        t = cls(
            d.get("name", "מסלול"),
            d.get("enabled", False),
            d.get("shape", "Linear"),
            d.get("length_m", 3.0),
            d.get("width_cm", 2.3),
            d.get("x", 0.5),
            d.get("y", 0.4),
            d.get("angle_deg", 0.0),
        )
        t.fixtures = [TrackFixture.from_dict(x) for x in d.get("fixtures", [])]
        return t


@dataclass
class PendantConfig:
    name: str = "תלוי"
    pendant_type: str = "פנדנט בודד"
    fixture_type: str = "תלוי פנדנט"
    enabled: bool = True
    quantity: int = 1
    x: float = 0.5
    y: float = 0.5
    spacing_m: float = 0.75
    angle_deg: float = 0.0
    drop_m: float = 0.8

    def points(self, room: "RoomModel") -> Iterable[Tuple[float, float]]:
        cx, cy = room.width * self.x, room.length * self.y
        n = max(1, self.quantity)
        if self.pendant_type in ("פנדנט בודד", "נברשת"):
            n = 1
        rad = math.radians(self.angle_deg)
        dx, dy = math.cos(rad), math.sin(rad)
        start = -(n - 1) / 2
        for i in range(n):
            t = (start + i) * self.spacing_m
            yield cx + t * dx, cy + t * dy

    def to_dict(self) -> Dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: Dict) -> "PendantConfig":
        return cls(**{k: d.get(k, v) for k, v in cls().__dict__.items()})


@dataclass
class AmbientConfig:
    enabled: bool = False
    shape: str = "קו ישר"
    length_m: float = 4.0
    lm_per_m: int = 300
    x: float = 0.5
    y: float = 0.8
    angle_deg: float = 0.0

    @property
    def total_lm(self) -> float:
        mult = 2 if self.shape == "L-shape" else 3 if self.shape == "U-shape" else 1
        return self.length_m * self.lm_per_m * mult

    @property
    def watts(self) -> float:
        return self.total_lm / 95

    def to_dict(self) -> Dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: Dict) -> "AmbientConfig":
        return cls(**{k: d.get(k, v) for k, v in cls().__dict__.items()})


@dataclass
class LightingZone:
    name: str = "Workspace"
    visible: bool = True
    locked: bool = False
    x: float = 0.15
    y: float = 0.15
    width: float = 0.35
    length: float = 0.30
    lux_target: int = 500
    layer_indices: List[int] = field(default_factory=lambda: [0, 1, 2])
    fixture_names: List[str] = field(default_factory=list)

    def bounds(self, room: "RoomModel") -> Tuple[float, float, float, float]:
        x0 = clamp(self.x * room.width, 0, room.width)
        y0 = clamp(self.y * room.length, 0, room.length)
        w = clamp(self.width * room.width, 0.1, room.width)
        l = clamp(self.length * room.length, 0.1, room.length)
        return x0, y0, min(room.width - x0, w), min(room.length - y0, l)

    def sample_points(self, room: "RoomModel", steps: int = 4) -> List[Tuple[float, float]]:
        x0, y0, w, l = self.bounds(room)
        return [
            (x0 + (c + 0.5) * w / steps, y0 + (r + 0.5) * l / steps)
            for r in range(steps)
            for c in range(steps)
        ]

    def to_dict(self) -> Dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: Dict) -> "LightingZone":
        return cls(**{k: d.get(k, v) for k, v in cls().__dict__.items()})


@dataclass
class FixtureAim:
    pan_deg: float = 0.0
    tilt_deg: float = 0.0
    rotation_deg: float = 0.0

    def to_dict(self) -> Dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: Dict) -> "FixtureAim":
        return cls(**{k: d.get(k, v) for k, v in cls().__dict__.items()})


@dataclass
class BeamOpticsSettings:
    show_beams: bool = True
    beam_opacity: int = 70
    beam_type: str = "Medium"
    default_beam_angle: int = 36
    show_zone_guides: bool = True
    show_helper_guides: bool = True
    functional_aim: FixtureAim = field(default_factory=FixtureAim)

    def effective_beam_angle(self) -> int:
        presets = {"Narrow": 24, "Medium": 36, "Wide": 60}
        return int(self.default_beam_angle or presets.get(self.beam_type, 36))

    def to_dict(self) -> Dict:
        return {
            **{k: v for k, v in self.__dict__.items() if k != "functional_aim"},
            "functional_aim": self.functional_aim.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "BeamOpticsSettings":
        s = cls(**{k: d.get(k, v) for k, v in cls().__dict__.items() if k != "functional_aim"})
        s.functional_aim = FixtureAim.from_dict(d.get("functional_aim", {}))
        return s


@dataclass
class BeamFootprint:
    source_name: str
    layer_name: str
    x: float
    y: float
    target_x: float
    target_y: float
    mounting_height: float
    beam_angle: float
    diameter_m: float
    throw_distance_m: float
    lux_center: float
    overlap_count: int = 0
    hotspot: bool = False
    shadow_gap: bool = False


@dataclass
class SimulationSnapshot:
    lux: "LuxEngine"
    planner: "SpotlightPlanner"
    spots: List[Tuple[float, float]]
    heatmap: List[List[float]]
    avg_lux: float
    min_lux: float
    max_lux: float
    watts: float
    cri: float
    ugr: float
    beam_metrics: Dict[str, float]
    elapsed_ms: float


class AppEventBus:
    def __init__(self):
        self._subscribers: Dict[str, List] = {}

    def subscribe(self, event_name: str, callback) -> None:
        self._subscribers.setdefault(event_name, []).append(callback)

    def emit(self, event_name: str, payload=None) -> None:
        for callback in self._subscribers.get(event_name, []):
            try:
                callback(payload)
            except Exception:
                LOGGER.exception("Event subscriber failed: %s", event_name)


class ProjectStateManager:
    def __init__(self):
        self.event_bus = AppEventBus()
        self.dirty = False
        self.last_error = ""

    def mark_dirty(self) -> None:
        self.dirty = True
        self.event_bus.emit("project.changed")

    def mark_saved(self) -> None:
        self.dirty = False
        self.event_bus.emit("project.saved")

    def report_error(self, message: str) -> None:
        self.last_error = message
        LOGGER.error(message)
        self.event_bus.emit("project.error", message)


class ModelGuard:
    @staticmethod
    def sanitize_room(room: "RoomModel") -> None:
        room.width = clamp(float(room.width), 0.5, 200)
        room.length = clamp(float(room.length), 0.5, 200)
        room.ceiling_height = clamp(float(room.ceiling_height), 1.5, 20)
        room.maintenance_factor = clamp(float(room.maintenance_factor), 0.1, 1.0)
        room.reflectance_ceiling = clamp(float(room.reflectance_ceiling), 0.0, 0.95)
        room.reflectance_walls = clamp(float(room.reflectance_walls), 0.0, 0.95)
        room.reflectance_floor = clamp(float(room.reflectance_floor), 0.0, 0.95)
        room.wall_offset = clamp(float(room.wall_offset), 0.0, min(room.width, room.length) / 2 - 0.01)
        room.heatmap_opacity = int(clamp(room.heatmap_opacity, 10, 220))
        for layer in room.layers:
            layer.intensity = int(clamp(layer.intensity, 0, 100))
        for name, data in list(room.fixture_catalogue.items()):
            try:
                data["lm"] = max(1.0, float(data.get("lm", 800)))
                data["w"] = max(0.1, float(data.get("w", 8)))
                data["cri"] = clamp(float(data.get("cri", 90)), 0, 100)
                data["beam"] = clamp(float(data.get("beam", 36)), 5, 160)
                data["cct"] = int(clamp(float(data.get("cct", 3000)), 1500, 10000))
            except Exception:
                LOGGER.warning("Removing invalid fixture catalogue row: %s", name)
                room.fixture_catalogue.pop(name, None)
        if not room.fixture_catalogue:
            room.fixture_catalogue = dict(DEFAULT_FIXTURES)


class LightingSimulationService:
    def __init__(self):
        self._heatmap_cache_key: Optional[Tuple] = None
        self._heatmap_cache: List[List[float]] = []

    def _fingerprint(self, room: "RoomModel", grid_n: int) -> Tuple:
        source_count = len(room.manual_spots) + len(room.profiles) + len(room.tracks) + len(room.pendants)
        layer_state = tuple((x.enabled, x.intensity) for x in room.layers)
        furniture_state = tuple((f.enabled, round(f.x, 3), round(f.y, 3), round(f.rotation_deg, 1)) for f in room.furniture)
        return (
            round(room.width, 3),
            round(room.length, 3),
            round(room.ceiling_height, 3),
            room.default_spot_fixture,
            room.beam_angle,
            room.spot_quantity_override,
            round(room.wall_offset, 3),
            layer_state,
            source_count,
            furniture_state,
            room.daylight.enabled,
            round(room.daylight.time_of_day, 2),
            room.curtain_lighting.enabled,
            grid_n,
        )

    def _grid_size(self, room: "RoomModel") -> int:
        if room.area > 120:
            return 16
        if room.area > 60:
            return 18
        return 20

    def compute(self, room: "RoomModel") -> SimulationSnapshot:
        start = time.perf_counter()
        ModelGuard.sanitize_room(room)
        lux = LuxEngine(room)
        planner = SpotlightPlanner(room)
        spots = planner.active_positions() if room.layer(1).enabled else []
        grid_n = self._grid_size(room)
        key = self._fingerprint(room, grid_n)
        if key == self._heatmap_cache_key:
            heat = self._heatmap_cache
        else:
            heat = lux.heatmap(grid_n)
            self._heatmap_cache_key = key
            self._heatmap_cache = heat
        vals = [v for row in heat for v in row]
        avg = sum(vals) / len(vals) if vals else 0
        beam = BeamAnalysisEngine(room, lux).metrics()
        return SimulationSnapshot(
            lux=lux,
            planner=planner,
            spots=spots,
            heatmap=heat,
            avg_lux=avg,
            min_lux=min(vals) if vals else 0,
            max_lux=max(vals) if vals else 0,
            watts=lux.watts_total(),
            cri=lux.avg_cri(),
            ugr=lux.ugr_estimate(),
            beam_metrics=beam,
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )


@dataclass
class FurnitureObject:
    name: str = "Dining table"
    furniture_type: str = "Dining table"
    enabled: bool = True
    x: float = 0.50
    y: float = 0.62
    width_m: float = 1.80
    length_m: float = 0.95
    height_m: float = 0.75
    rotation_deg: float = 0.0
    shadow_factor: float = 0.12

    def center(self, room: "RoomModel") -> Tuple[float, float]:
        return self.x * room.width, self.y * room.length

    def bounds(self, room: "RoomModel") -> Tuple[float, float, float, float]:
        cx, cy = self.center(room)
        return cx - self.width_m / 2, cy - self.length_m / 2, self.width_m, self.length_m

    def contains(self, room: "RoomModel", px: float, py: float, pad: float = 0.0) -> bool:
        x, y, w, l = self.bounds(room)
        return x - pad <= px <= x + w + pad and y - pad <= py <= y + l + pad

    def to_dict(self) -> Dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: Dict) -> "FurnitureObject":
        return cls(**{k: d.get(k, v) for k, v in cls().__dict__.items()})


@dataclass
class RoomEnvelope:
    wall_color: str = "Neutral"
    wall_cladding: bool = False
    cladding_tone: str = "ללא חיפוי"
    tambour_ral: str = "RAL 9016 / OW221P לבן"
    floor_material: str = "Matte tile"
    ceiling_material: str = "White gypsum"
    gypsum_drop_m: float = 0.0
    ceiling_recess_m: float = 0.0
    cornice_depth_m: float = 0.08

    def to_dict(self) -> Dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: Dict) -> "RoomEnvelope":
        return cls(**{k: d.get(k, v) for k, v in cls().__dict__.items()})


@dataclass
class CurtainLightingConfig:
    enabled: bool = False
    mode: str = "Full wall"
    wall: str = "North"
    start_m: float = 0.0
    end_m: float = 3.0
    length_m: float = 3.0
    centered: bool = True
    intensity: int = 70
    lm_per_m: int = 650

    @property
    def total_lm(self) -> float:
        return self.length_m * self.lm_per_m * self.intensity / 100

    @property
    def watts(self) -> float:
        return self.total_lm / 90

    def to_dict(self) -> Dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: Dict) -> "CurtainLightingConfig":
        return cls(**{k: d.get(k, v) for k, v in cls().__dict__.items()})


@dataclass
class DaylightConfig:
    enabled: bool = False
    window_width_m: float = 2.0
    window_height_m: float = 1.4
    orientation: str = "South"
    time_of_day: float = 12.0
    glazing_factor: float = 0.55

    def to_dict(self) -> Dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: Dict) -> "DaylightConfig":
        return cls(**{k: d.get(k, v) for k, v in cls().__dict__.items()})


@dataclass
class LightingScene:
    name: str = "Work"
    layer_intensities: Dict[str, int] = field(default_factory=lambda: {"0": 100, "1": 100, "2": 80})

    def to_dict(self) -> Dict:
        return {"name": self.name, "layer_intensities": dict(self.layer_intensities)}

    @classmethod
    def from_dict(cls, d: Dict) -> "LightingScene":
        return cls(d.get("name", "Scene"), {str(k): int(v) for k, v in d.get("layer_intensities", {}).items()})


@dataclass
class BrandingSettings:
    company_name: str = "Lighting Design Studio"
    company_logo: str = ""
    default_units: str = "metric"
    default_labour_rate: float = 180.0
    default_markup_pct: float = 15.0

    def to_dict(self) -> Dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: Dict) -> "BrandingSettings":
        return cls(**{k: d.get(k, v) for k, v in cls().__dict__.items()})


@dataclass
class FloorPlanUnderlay:
    path: str = ""
    source_path: str = ""
    scale_m_per_px: float = 0.01
    opacity: int = 45
    detected_walls: List[Tuple[float, float, float, float]] = field(default_factory=list)
    detected_openings: List[Dict] = field(default_factory=list)
    detected_ceiling_features: List[Dict] = field(default_factory=list)
    import_confidence: float = 0.0
    cleanup_notes: List[str] = field(default_factory=list)
    analysis_summary: str = ""

    def to_dict(self) -> Dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: Dict) -> "FloorPlanUnderlay":
        fp = cls(d.get("path", ""), d.get("source_path", ""), d.get("scale_m_per_px", 0.01), d.get("opacity", 45))
        fp.detected_walls = [tuple(x) for x in d.get("detected_walls", [])]
        fp.detected_openings = list(d.get("detected_openings", []))
        fp.detected_ceiling_features = list(d.get("detected_ceiling_features", []))
        fp.import_confidence = float(d.get("import_confidence", 0.0))
        fp.cleanup_notes = list(d.get("cleanup_notes", []))
        fp.analysis_summary = d.get("analysis_summary", "")
        return fp


@dataclass
class ImportInsight:
    category: str
    name: str
    confidence: float
    x: float = 0.5
    y: float = 0.5
    width: float = 0.2
    length: float = 0.2
    recommendation: str = ""
    confirmed: bool = False

    def to_dict(self) -> Dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: Dict) -> "ImportInsight":
        return cls(**{k: d.get(k, v) for k, v in cls().__dict__.items()})


@dataclass
class ArchitecturalUnderstanding:
    source_path: str = ""
    scale_confidence: float = 0.0
    estimated_scale_m_per_px: float = 0.01
    room_boundary: List[Tuple[float, float]] = field(default_factory=list)
    walls: List[Tuple[float, float, float, float]] = field(default_factory=list)
    doors: List[ImportInsight] = field(default_factory=list)
    windows: List[ImportInsight] = field(default_factory=list)
    furniture: List[ImportInsight] = field(default_factory=list)
    zones: List[ImportInsight] = field(default_factory=list)
    ceiling_features: List[ImportInsight] = field(default_factory=list)
    lighting_opportunities: List[ImportInsight] = field(default_factory=list)
    cleanup_notes: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    requires_confirmation: bool = True

    def to_dict(self) -> Dict:
        return {
            "source_path": self.source_path,
            "scale_confidence": self.scale_confidence,
            "estimated_scale_m_per_px": self.estimated_scale_m_per_px,
            "room_boundary": self.room_boundary,
            "walls": self.walls,
            "doors": [x.to_dict() for x in self.doors],
            "windows": [x.to_dict() for x in self.windows],
            "furniture": [x.to_dict() for x in self.furniture],
            "zones": [x.to_dict() for x in self.zones],
            "ceiling_features": [x.to_dict() for x in self.ceiling_features],
            "lighting_opportunities": [x.to_dict() for x in self.lighting_opportunities],
            "cleanup_notes": list(self.cleanup_notes),
            "suggestions": list(self.suggestions),
            "requires_confirmation": self.requires_confirmation,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "ArchitecturalUnderstanding":
        u = cls(
            d.get("source_path", ""),
            float(d.get("scale_confidence", 0.0)),
            float(d.get("estimated_scale_m_per_px", 0.01)),
        )
        u.room_boundary = [tuple(x) for x in d.get("room_boundary", [])]
        u.walls = [tuple(x) for x in d.get("walls", [])]
        u.doors = [ImportInsight.from_dict(x) for x in d.get("doors", [])]
        u.windows = [ImportInsight.from_dict(x) for x in d.get("windows", [])]
        u.furniture = [ImportInsight.from_dict(x) for x in d.get("furniture", [])]
        u.zones = [ImportInsight.from_dict(x) for x in d.get("zones", [])]
        u.ceiling_features = [ImportInsight.from_dict(x) for x in d.get("ceiling_features", [])]
        u.lighting_opportunities = [ImportInsight.from_dict(x) for x in d.get("lighting_opportunities", [])]
        u.cleanup_notes = list(d.get("cleanup_notes", []))
        u.suggestions = list(d.get("suggestions", []))
        u.requires_confirmation = bool(d.get("requires_confirmation", True))
        return u


@dataclass
class RoomModel:
    width: float = 5.0
    length: float = 6.0
    ceiling_height: float = 2.7
    room_type: str = "סלון"
    target_unit: str = "lux"
    lux_override: Optional[int] = None
    lumens_override: Optional[int] = None
    maintenance_factor: float = 0.8
    reflectance_ceiling: float = 0.7
    reflectance_walls: float = 0.5
    reflectance_floor: float = 0.3
    beam_angle: int = 36
    wall_offset: float = 0.4
    cct_preset: str = "נייטרל (3000K)"
    spot_quantity_override: Optional[int] = None
    show_heatmap: bool = True
    heatmap_opacity: int = 110
    show_point_values: bool = False
    default_spot_fixture: str = "ספוט שקוע 36deg"
    electricity_rate: float = 0.65
    labour_rate: float = 180.0
    labour_hours: float = 4.0
    material_markup_pct: float = 15.0
    project_name: str = "פרויקט חדש"
    client_name: str = ""
    layers: List[LightingLayer] = field(default_factory=list)
    profiles: List[ProfileConfig] = field(default_factory=list)
    tracks: List[MagneticTrack] = field(default_factory=list)
    pendants: List[PendantConfig] = field(default_factory=list)
    ambient: AmbientConfig = field(default_factory=AmbientConfig)
    manual_spots: List[Tuple[float, float]] = field(default_factory=list)
    fixture_catalogue: Dict[str, Dict] = field(default_factory=lambda: dict(DEFAULT_FIXTURES))
    zones: List[LightingZone] = field(default_factory=list)
    daylight: DaylightConfig = field(default_factory=DaylightConfig)
    scenes: List[LightingScene] = field(default_factory=list)
    branding: BrandingSettings = field(default_factory=BrandingSettings)
    floor_plan: FloorPlanUnderlay = field(default_factory=FloorPlanUnderlay)
    furniture: List[FurnitureObject] = field(default_factory=list)
    envelope: RoomEnvelope = field(default_factory=RoomEnvelope)
    curtain_lighting: CurtainLightingConfig = field(default_factory=CurtainLightingConfig)
    optics: BeamOpticsSettings = field(default_factory=BeamOpticsSettings)
    architectural_understanding: ArchitecturalUnderstanding = field(default_factory=ArchitecturalUnderstanding)
    project_folder: str = ""
    last_modified: str = ""

    def __post_init__(self) -> None:
        if not self.layers:
            self.layers = [
                LightingLayer("כללי - פרופילים/מסלולים", False, 100),
                LightingLayer("משימה - ספוטים", False, 100),
                LightingLayer("תלויים - פנדנטים", False, 100),
            ]
        if not self.profiles:
            self.profiles = [ProfileConfig("קורניש", False, "Perimeter", 2 * (self.width + self.length), 0.035, 0.02, 400, 0.5, 0.5, 0)]
        if not self.pendants:
            self.pendants = [PendantConfig(enabled=False)]
        if not self.zones:
            self.zones = [
                LightingZone(name="Kitchen island", x=0.30, y=0.35, width=0.40, length=0.20, lux_target=500),
                LightingZone(name="Dining table", x=0.25, y=0.62, width=0.50, length=0.25, lux_target=300),
            ]
        if not self.scenes:
            self.scenes = [
                LightingScene("Work", {"0": 100, "1": 100, "2": 80}),
                LightingScene("Evening", {"0": 55, "1": 25, "2": 65}),
                LightingScene("Hospitality", {"0": 70, "1": 45, "2": 90}),
                LightingScene("Cleaning", {"0": 100, "1": 100, "2": 100}),
            ]
        if not self.furniture:
            self.furniture = [
                FurnitureObject("Dining table", "Dining table", True, 0.50, 0.62, 1.80, 0.95, 0.75),
                FurnitureObject("Kitchen island", "Kitchen island", True, 0.50, 0.35, 2.20, 0.90, 0.90),
            ]

    @property
    def area(self) -> float:
        return self.width * self.length

    @property
    def lux_target(self) -> int:
        if self.target_unit == "lumens" and self.lumens_override:
            lux = self.lumens_override * self.utilisation_factor * self.maintenance_factor / max(self.area, 0.01)
            return max(1, int(lux))
        if self.lux_override:
            return self.lux_override
        base = LUX_STANDARDS.get(self.room_type, 200)
        return max(1, int(base * CCT_PRESETS.get(self.cct_preset, (3000, 1))[1]))

    @property
    def target_lumens(self) -> float:
        if self.target_unit == "lumens" and self.lumens_override:
            return float(self.lumens_override)
        return self.lux_target * self.area / max(self.utilisation_factor * self.maintenance_factor, 0.01)

    @property
    def cct_kelvin(self) -> int:
        return CCT_PRESETS.get(self.cct_preset, (3000, 1))[0]

    @property
    def room_index(self) -> float:
        denom = self.ceiling_height * (self.width + self.length)
        return clamp(self.area / denom if denom else 1.0, 0.6, 5.0)

    @property
    def utilisation_factor(self) -> float:
        return round(clamp(0.35 + 0.12 * math.log(self.room_index + 0.5) + self.reflectance_ceiling * 0.05 + self.reflectance_walls * 0.04, 0.25, 0.85), 3)

    def layer(self, i: int) -> LightingLayer:
        return self.layers[i] if i < len(self.layers) else LightingLayer("חסר", False, 0)

    def to_dict(self) -> Dict:
        return {
            "version": APP_VERSION,
            **{k: v for k, v in self.__dict__.items() if k not in {"layers", "profiles", "tracks", "pendants", "ambient", "zones", "daylight", "scenes", "branding", "floor_plan", "furniture", "envelope", "curtain_lighting", "optics", "architectural_understanding"}},
            "layers": [x.to_dict() for x in self.layers],
            "profiles": [x.to_dict() for x in self.profiles],
            "tracks": [x.to_dict() for x in self.tracks],
            "pendants": [x.to_dict() for x in self.pendants],
            "ambient": self.ambient.to_dict(),
            "zones": [x.to_dict() for x in self.zones],
            "daylight": self.daylight.to_dict(),
            "scenes": [x.to_dict() for x in self.scenes],
            "branding": self.branding.to_dict(),
            "floor_plan": self.floor_plan.to_dict(),
            "furniture": [x.to_dict() for x in self.furniture],
            "envelope": self.envelope.to_dict(),
            "curtain_lighting": self.curtain_lighting.to_dict(),
            "optics": self.optics.to_dict(),
            "architectural_understanding": self.architectural_understanding.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "RoomModel":
        fields = {k for k in cls().__dict__ if k not in {"layers", "profiles", "tracks", "pendants", "ambient", "zones", "daylight", "scenes", "branding", "floor_plan", "furniture", "envelope", "curtain_lighting", "optics", "architectural_understanding"}}
        room = cls(**{k: d[k] for k in fields if k in d})
        room.layers = [LightingLayer.from_dict(x) for x in d.get("layers", [])] or room.layers
        room.profiles = [ProfileConfig.from_dict(x) for x in d.get("profiles", [])] or room.profiles
        room.tracks = [MagneticTrack.from_dict(x) for x in d.get("tracks", [])]
        room.pendants = [PendantConfig.from_dict(x) for x in d.get("pendants", [])] or room.pendants
        room.ambient = AmbientConfig.from_dict(d.get("ambient", {}))
        room.fixture_catalogue = d.get("fixture_catalogue", dict(DEFAULT_FIXTURES))
        room.zones = [LightingZone.from_dict(x) for x in d.get("zones", [])] or room.zones
        room.daylight = DaylightConfig.from_dict(d.get("daylight", {}))
        room.scenes = [LightingScene.from_dict(x) for x in d.get("scenes", [])] or room.scenes
        room.branding = BrandingSettings.from_dict(d.get("branding", {}))
        room.floor_plan = FloorPlanUnderlay.from_dict(d.get("floor_plan", {}))
        room.furniture = [FurnitureObject.from_dict(x) for x in d.get("furniture", [])] or room.furniture
        room.envelope = RoomEnvelope.from_dict(d.get("envelope", {}))
        room.curtain_lighting = CurtainLightingConfig.from_dict(d.get("curtain_lighting", {}))
        room.optics = BeamOpticsSettings.from_dict(d.get("optics", {}))
        room.architectural_understanding = ArchitecturalUnderstanding.from_dict(d.get("architectural_understanding", {}))
        return room


class SpotlightPlanner:
    def __init__(self, room: RoomModel):
        self.room = room

    def recommended_spacing(self) -> float:
        radius = self.room.ceiling_height * math.tan(math.radians(self.room.beam_angle / 2))
        return round(clamp(2 * radius * 0.85, 0.55, 3.0), 2)

    def grid_count(self) -> Tuple[int, int, float, float]:
        off = self.room.wall_offset
        s = self.recommended_spacing()
        uw, ul = max(0.01, self.room.width - 2 * off), max(0.01, self.room.length - 2 * off)
        if self.room.spot_quantity_override:
            n = self.room.spot_quantity_override
        else:
            spot_lm = float(self.room.fixture_catalogue.get(self.room.default_spot_fixture, {}).get("lm", 800))
            required = self.room.lux_target * self.room.area / max(self.room.utilisation_factor * self.room.maintenance_factor, 0.01)
            n = max(1, math.ceil(required * 0.55 / max(spot_lm, 1)))
            max_by_spacing = max(1, (max(1, round(uw / s) + 1)) * (max(1, round(ul / s) + 1)))
            n = min(n, max_by_spacing)
        ratio = self.room.width / max(self.room.length, 0.01)
        cols = max(1, round(math.sqrt(n * ratio)))
        rows = max(1, math.ceil(n / cols))
        sx = uw / (cols - 1) if cols > 1 else uw
        sy = ul / (rows - 1) if rows > 1 else ul
        return cols, rows, round(sx, 2), round(sy, 2)

    def auto_positions(self) -> List[Tuple[float, float]]:
        cols, rows, sx, sy = self.grid_count()
        off = self.room.wall_offset
        pts = [(round(off + c * sx, 3), round(off + r * sy, 3)) for r in range(rows) for c in range(cols)]
        return pts[: self.room.spot_quantity_override] if self.room.spot_quantity_override else pts

    def active_positions(self) -> List[Tuple[float, float]]:
        return self.room.manual_spots or self.auto_positions()


class LuxEngine:
    def __init__(self, room: RoomModel):
        self.room = room

    def fixture(self, name: str) -> Dict:
        return self.room.fixture_catalogue.get(name, next(iter(self.room.fixture_catalogue.values())))

    def required_lumens(self) -> float:
        if self.room.area <= 0:
            return 0
        return self.room.target_lumens

    def spot_lm(self) -> float:
        info = self.fixture(self.room.default_spot_fixture)
        return float(info.get("lm", 800))

    def spot_watts(self) -> float:
        return float(self.fixture(self.room.default_spot_fixture).get("w", 8))

    def spot_points(self) -> List[Tuple[float, float, str, float]]:
        return [(x, y, self.room.default_spot_fixture, self.room.ceiling_height) for x, y in SpotlightPlanner(self.room).active_positions()]

    def profile_points(self) -> List[Tuple[float, float, str, float]]:
        out = []
        for p in self.room.profiles:
            if not p.enabled:
                continue
            n = max(2, int(p.length_m / 0.5))
            rad = math.radians(p.angle_deg)
            dx, dy = math.cos(rad), math.sin(rad)
            cx, cy = self.room.width * p.x, self.room.length * p.y
            for i in range(n):
                t = (i / (n - 1) - 0.5) * p.length_m
                out.append((cx + t * dx, cy + t * dy, "__profile__", self.room.ceiling_height))
        return out

    def track_points(self) -> List[Tuple[float, float, str, float]]:
        out = []
        for t in self.room.tracks:
            if not t.enabled:
                continue
            out.extend((x, y, f.fixture_type, self.room.ceiling_height) for x, y, f in t.fixture_points(self.room))
        return out

    def pendant_points(self) -> List[Tuple[float, float, str, float]]:
        out = []
        for p in self.room.pendants:
            if not p.enabled:
                continue
            height = max(0.35, self.room.ceiling_height - p.drop_m)
            out.extend((x, y, p.fixture_type, height) for x, y in p.points(self.room))
        return out

    def ambient_points(self) -> List[Tuple[float, float, str, float]]:
        a = self.room.ambient
        if not a.enabled:
            return []
        if a.shape == "היקפי":
            pts = []
            step = max(0.35, min(self.room.width, self.room.length) / 12)
            x = 0.15
            while x <= self.room.width - 0.15:
                pts.append((x, 0.15, "__ambient__", self.room.ceiling_height))
                pts.append((x, self.room.length - 0.15, "__ambient__", self.room.ceiling_height))
                x += step
            y = 0.15
            while y <= self.room.length - 0.15:
                pts.append((0.15, y, "__ambient__", self.room.ceiling_height))
                pts.append((self.room.width - 0.15, y, "__ambient__", self.room.ceiling_height))
                y += step
            return pts
        segments = 1 if a.shape == "קו ישר" else 2 if a.shape == "L-shape" else 3
        n = max(2, int(a.length_m * segments / 0.5))
        rad = math.radians(a.angle_deg)
        dx, dy = math.cos(rad), math.sin(rad)
        cx, cy = self.room.width * a.x, self.room.length * a.y
        return [(cx + (i / max(n - 1, 1) - 0.5) * a.length_m * dx, cy + (i / max(n - 1, 1) - 0.5) * a.length_m * dy, "__ambient__", self.room.ceiling_height) for i in range(n)]

    def curtain_points(self) -> List[Tuple[float, float, str, float]]:
        c = self.room.curtain_lighting
        if not c.enabled:
            return []
        length = c.length_m if c.mode != "Full wall" else (self.room.width if c.wall in ("North", "South") else self.room.length)
        n = max(2, int(length / 0.35))
        pts = []
        for i in range(n):
            t = (i / max(n - 1, 1)) * length
            if c.wall == "North":
                x, y = t, 0.08
            elif c.wall == "South":
                x, y = t, self.room.length - 0.08
            elif c.wall == "East":
                x, y = self.room.width - 0.08, t
            else:
                x, y = 0.08, t
            pts.append((clamp(x, 0, self.room.width), clamp(y, 0, self.room.length), "__curtain__", self.room.ceiling_height))
        return pts

    def all_sources(self) -> List[Tuple[float, float, str, float, float]]:
        sources = []
        if self.room.layer(1).enabled:
            for x, y, name, h in self.spot_points():
                sources.append((x, y, name, h, self.room.layer(1).factor))
        if self.room.layer(0).enabled:
            for x, y, name, h in self.profile_points():
                sources.append((x, y, name, h, self.room.layer(0).factor))
            for x, y, name, h in self.track_points():
                sources.append((x, y, name, h, self.room.layer(0).factor))
        if self.room.layer(2).enabled:
            for x, y, name, h in self.pendant_points():
                sources.append((x, y, name, h, self.room.layer(2).factor))
            for x, y, name, h in self.ambient_points():
                sources.append((x, y, name, h, self.room.layer(2).factor))
            for x, y, name, h in self.curtain_points():
                sources.append((x, y, name, h, self.room.layer(2).factor))
        return sources

    def source_lumens(self, name: str) -> float:
        if name == "__profile__":
            active = [p for p in self.room.profiles if p.enabled]
            count = sum(max(2, int(p.length_m / 0.5)) for p in active) or 1
            return sum(p.total_lm for p in active) / count
        if name == "__ambient__":
            count = len(self.ambient_points()) or 1
            return self.room.ambient.total_lm / count
        if name == "__curtain__":
            count = len(self.curtain_points()) or 1
            return self.room.curtain_lighting.total_lm / count
        return float(self.fixture(name).get("lm", 800))

    def point_lux(self, px: float, py: float) -> float:
        total = 0.0
        for sx, sy, name, h, layer_factor in self.all_sources():
            dx, dy = px - sx, py - sy
            dist2 = dx * dx + dy * dy
            d = math.sqrt(dist2 + h * h)
            cos_theta = clamp(h / d, 0, 1)
            lumens = self.source_lumens(name) * layer_factor
            beam = float(self.fixture(name).get("beam", 90)) if name != "__profile__" else 120
            solid_angle = 2 * math.pi * (1 - math.cos(math.radians(clamp(beam, 5, 160) / 2)))
            intensity_cd = lumens / max(solid_angle, 0.01)
            total += intensity_cd * (cos_theta ** 3) / max(h * h, 0.05)
        artificial = total * self.room.maintenance_factor * POINT_CALC_CALIBRATION
        value = artificial + DaylightEngine(self.room).point_lux(px, py)
        for furn in self.room.furniture:
            if furn.enabled and furn.contains(self.room, px, py):
                value *= max(0.0, 1.0 - furn.shadow_factor)
        return value

    def heatmap(self, n: int = 20) -> List[List[float]]:
        return [[self.point_lux((c + 0.5) * self.room.width / n, (r + 0.5) * self.room.length / n) for c in range(n)] for r in range(n)]

    def achieved_average_lux(self) -> float:
        grid = self.heatmap(12)
        vals = [v for row in grid for v in row]
        return sum(vals) / len(vals) if vals else 0

    def watts_total(self) -> float:
        spot_count = len(SpotlightPlanner(self.room).active_positions())
        total = spot_count * self.spot_watts() * self.room.layer(1).factor
        total += sum(p.watts for p in self.room.profiles if p.enabled) * self.room.layer(0).factor
        for t in self.room.tracks:
            if t.enabled:
                total += sum(float(self.fixture(f.fixture_type).get("w", 8)) for f in t.fixtures) * self.room.layer(0).factor
        for p in self.room.pendants:
            if p.enabled:
                qty = 1 if p.pendant_type in ("פנדנט בודד", "נברשת") else p.quantity
                total += qty * float(self.fixture(p.fixture_type).get("w", 15)) * self.room.layer(2).factor
        if self.room.ambient.enabled:
            total += self.room.ambient.watts * self.room.layer(2).factor
        if self.room.curtain_lighting.enabled:
            total += self.room.curtain_lighting.watts * self.room.layer(2).factor
        return total

    def avg_cri(self) -> float:
        pairs = []
        for _, _, name, _, factor in self.all_sources():
            lm = self.source_lumens(name) * factor
            cri = 90 if name == "__profile__" else float(self.fixture(name).get("cri", 90))
            pairs.append((lm, cri))
        total = sum(lm for lm, _ in pairs)
        return sum(lm * cri for lm, cri in pairs) / total if total else 90

    def ugr_estimate(self) -> float:
        avg = max(self.achieved_average_lux(), 1)
        return round(clamp(8 * math.log10(0.32 * avg / max(self.room.room_index ** 2, 0.01)) + 10, 6, 34), 1)


class PricingEngine:
    def __init__(self, room: RoomModel):
        self.room = room
        self.lux = LuxEngine(room)

    def line_items(self) -> List[Tuple[str, int, float, float]]:
        items: Dict[str, Tuple[int, float]] = {}

        def add(name: str, qty: int, unit: float) -> None:
            old_qty, old_unit = items.get(name, (0, unit))
            items[name] = (old_qty + qty, old_unit)

        if self.room.layer(1).enabled:
            add(self.room.default_spot_fixture, len(SpotlightPlanner(self.room).active_positions()), float(self.lux.fixture(self.room.default_spot_fixture).get("price", 0)))
        for p in self.room.profiles:
            if p.enabled:
                add(f"{p.name} LED profile ({p.lm_per_m} lm/m)", 1, p.length_m * 80)
        for t in self.room.tracks:
            if t.enabled:
                add(f"{t.name} magnetic track", 1, t.length_m * 120)
                for f in t.fixtures:
                    add(f.fixture_type, 1, float(self.lux.fixture(f.fixture_type).get("price", 0)))
        for p in self.room.pendants:
            if p.enabled:
                qty = 1 if p.pendant_type in ("פנדנט בודד", "נברשת") else p.quantity
                add(p.fixture_type, qty, float(self.lux.fixture(p.fixture_type).get("price", 0)))
        if self.room.curtain_lighting.enabled:
            add("Curtain LED lighting", 1, self.room.curtain_lighting.length_m * 110)
        return [(name, qty, unit, qty * unit) for name, (qty, unit) in items.items() if qty > 0]

    def totals(self) -> Dict[str, float]:
        material = sum(x[3] for x in self.line_items())
        markup = material * self.room.material_markup_pct / 100
        labour = self.room.labour_hours * self.room.labour_rate
        return {"material": material, "markup": markup, "labour": labour, "total": material + markup + labour}


class ComplianceEngine:
    def __init__(self, room: RoomModel, lux: LuxEngine):
        self.room = room
        self.lux = lux

    def checks(self) -> List[Tuple[str, bool, str]]:
        target = self.room.lux_target
        avg = self.lux.achieved_average_lux()
        heat = self.lux.heatmap(10)
        vals = [v for row in heat for v in row]
        min_lux = min(vals) if vals else 0
        uniformity = min_lux / avg if avg else 0
        ugr = self.lux.ugr_estimate()
        cri = self.lux.avg_cri()
        lpd = self.lux.watts_total() / max(self.room.area, 0.01)
        return [
            ("EN 12464 Illuminance", 0.9 * target <= avg <= 1.25 * target, f"{avg:.0f} lx מול יעד {target} lx"),
            ("EN 12464 Uniformity", uniformity >= 0.40, f"U0={uniformity:.2f} (מינימום 0.40)"),
            ("EN 12464 UGR", ugr <= UGR_LIMITS.get(self.room.room_type, 22), f"UGR {ugr} / {UGR_LIMITS.get(self.room.room_type, 22)}"),
            ("CRI", cri >= CRI_STANDARDS.get(self.room.room_type, 80), f"CRI {cri:.0f} / {CRI_STANDARDS.get(self.room.room_type, 80)}"),
            ("ASHRAE-style LPD", lpd <= LPD_LIMITS_W_M2.get(self.room.room_type, 12), f"{lpd:.1f} W/m2 / {LPD_LIMITS_W_M2.get(self.room.room_type, 12)}"),
        ]

    def leed_score(self) -> int:
        passed = sum(1 for _, ok, _ in self.checks() if ok)
        lpd = self.lux.watts_total() / max(self.room.area, 0.01)
        base = min(5, passed)
        if lpd < LPD_LIMITS_W_M2.get(self.room.room_type, 12) * 0.75:
            base += 1
        return min(6, base)


class DaylightEngine:
    ORIENTATION_FACTOR = {"North": 0.45, "East": 0.75, "South": 1.0, "West": 0.75}

    def __init__(self, room: RoomModel):
        self.room = room

    def exterior_lux(self) -> float:
        d = self.room.daylight
        if not d.enabled:
            return 0.0
        solar_shape = max(0.0, math.cos((d.time_of_day - 12.0) / 6.0))
        return 7500 * self.ORIENTATION_FACTOR.get(d.orientation, 0.7) * solar_shape * d.glazing_factor

    def point_lux(self, px: float, py: float) -> float:
        d = self.room.daylight
        if not d.enabled:
            return 0.0
        window_area = d.window_width_m * d.window_height_m
        depth_decay = math.exp(-py / max(self.room.length * 0.45, 0.1))
        side_decay = 0.55 + 0.45 * (1 - abs(px - self.room.width / 2) / max(self.room.width / 2, 0.1))
        return self.exterior_lux() * min(window_area / max(self.room.area, 0.1), 0.35) * depth_decay * side_decay

    def average_lux(self) -> float:
        if not self.room.daylight.enabled:
            return 0.0
        vals = [
            self.point_lux((c + 0.5) * self.room.width / 8, (r + 0.5) * self.room.length / 8)
            for r in range(8)
            for c in range(8)
        ]
        return sum(vals) / len(vals) if vals else 0.0


class ZoneEngine:
    def __init__(self, room: RoomModel, lux: LuxEngine):
        self.room = room
        self.lux = lux

    def metrics(self) -> List[Dict]:
        rows = []
        for zone in self.room.zones:
            vals = [self.lux.point_lux(x, y) for x, y in zone.sample_points(self.room)]
            avg = sum(vals) / len(vals) if vals else 0
            min_lux = min(vals) if vals else 0
            rows.append(
                {
                    "name": zone.name,
                    "target": zone.lux_target,
                    "avg": avg,
                    "min": min_lux,
                    "uniformity": min_lux / avg if avg else 0,
                    "ok": 0.9 * zone.lux_target <= avg <= 1.3 * zone.lux_target and (min_lux / avg if avg else 0) >= 0.35,
                }
            )
        return rows


class AutoLayoutAdvisor:
    def __init__(self, room: RoomModel, lux: LuxEngine):
        self.room = room
        self.lux = lux

    def suggestions(self) -> List[str]:
        planner = SpotlightPlanner(self.room)
        cols, rows, sx, sy = planner.grid_count()
        avg = self.lux.achieved_average_lux()
        target = self.room.lux_target
        out = [
            f"Spot grid: {cols} x {rows}, spacing {sx:.2f}m / {sy:.2f}m, wall offset {self.room.wall_offset:.2f}m.",
            f"Recommended spacing from beam angle {self.room.beam_angle}deg: {planner.recommended_spacing():.2f}m.",
        ]
        if avg < target * 0.9:
            out.append("Increase task layer intensity, add spots over dark zones, or select a higher lumen fixture.")
        elif avg > target * 1.3:
            out.append("Reduce quantity or dim the task/general layers to avoid overlighting.")
        if self.room.width > 4 and not self.room.tracks:
            out.append("Consider a central magnetic track for flexible accent fixtures.")
        if self.room.length > 5 and not any(p.enabled for p in self.room.pendants):
            out.append("For tables/islands, add a pendant row aligned with the main zone.")
        return out


class ValidationEngine:
    def __init__(self, room: RoomModel, lux: LuxEngine):
        self.room = room
        self.lux = lux

    def issues(self) -> List[Tuple[str, str, str]]:
        heat = self.lux.heatmap(12)
        vals = [v for row in heat for v in row]
        avg = sum(vals) / len(vals) if vals else 0
        min_lux = min(vals) if vals else 0
        max_lux = max(vals) if vals else 0
        issues: List[Tuple[str, str, str]] = []
        if avg < self.room.lux_target * 0.9:
            issues.append(("Underlighting", f"Average {avg:.0f} lx is below target {self.room.lux_target} lx.", "Add fixtures, raise output, or focus task lighting in zones."))
        if avg > self.room.lux_target * 1.35:
            issues.append(("Overlighting", f"Average {avg:.0f} lx is significantly above target.", "Reduce quantity, dim layers, or lower fixture lumen output."))
        if avg and min_lux / avg < 0.35:
            issues.append(("Uniformity", f"Minimum/average ratio is {min_lux / avg:.2f}.", "Tighten spacing, add fill light, or move fixtures away from clusters."))
        if max_lux > self.room.lux_target * 2.5:
            issues.append(("Hotspots", f"Peak grid value is {max_lux:.0f} lx.", "Widen beam angle or reduce overlapping fixtures."))
        for i, (x, y) in enumerate(SpotlightPlanner(self.room).active_positions(), 1):
            if min(x, y, self.room.width - x, self.room.length - y) < 0.18:
                issues.append(("Collision", f"Spot {i} is too close to a wall.", "Move it at least 0.18m from room boundaries."))
            for furn in self.room.furniture:
                if furn.enabled and furn.contains(self.room, x, y, 0.12):
                    issues.append(("Furniture overlap", f"Spot {i} overlaps {furn.name}.", "Move the fixture or use this furniture as a task-lighting zone."))
        for p in self.room.pendants:
            if p.enabled and self.room.ceiling_height - p.drop_m < 1.9:
                issues.append(("Pendant clearance", f"{p.name} bottom height is below 1.90m.", "Reduce drop or place over a table/island."))
            for x, y in p.points(self.room):
                for furn in self.room.furniture:
                    if furn.enabled and furn.contains(self.room, x, y, 0.20) and self.room.ceiling_height - p.drop_m < furn.height_m + 0.75:
                        issues.append(("Pendant/furniture collision", f"{p.name} is too low above {furn.name}.", "Raise the pendant or keep at least 0.75m clearance above furniture."))
        issues.extend(BeamAnalysisEngine(self.room, self.lux).issues())
        return issues


class BeamAnalysisEngine:
    def __init__(self, room: RoomModel, lux: LuxEngine):
        self.room = room
        self.lux = lux

    def _beam_for_source(self, name: str) -> float:
        if name in {"__profile__", "__ambient__", "__curtain__"}:
            return 120.0
        return float(self.lux.fixture(name).get("beam", self.room.optics.effective_beam_angle()))

    def _target_for_source(self, x: float, y: float, h: float, name: str) -> Tuple[float, float]:
        if name == self.room.default_spot_fixture:
            aim = self.room.optics.functional_aim
        else:
            aim = FixtureAim()
        tilt = math.radians(clamp(aim.tilt_deg, -60, 60))
        pan = math.radians(aim.pan_deg + aim.rotation_deg)
        offset = math.tan(tilt) * h
        return clamp(x + math.cos(pan) * offset, 0, self.room.width), clamp(y + math.sin(pan) * offset, 0, self.room.length)

    def footprints(self) -> List[BeamFootprint]:
        fps: List[BeamFootprint] = []
        surface_gain = 0.74 + self.room.reflectance_walls * 0.16 + self.room.reflectance_ceiling * 0.08 + self.room.reflectance_floor * 0.10
        for sx, sy, name, h, _factor in self.lux.all_sources():
            beam = clamp(self._beam_for_source(name), 5, 160)
            tx, ty = self._target_for_source(sx, sy, h, name)
            throw = math.sqrt((tx - sx) ** 2 + (ty - sy) ** 2 + h ** 2)
            diameter = 2 * h * math.tan(math.radians(beam / 2))
            center_lux = self.lux.point_lux(tx, ty) * surface_gain
            layer_name = "Functional" if name == self.room.default_spot_fixture else "General/Ambient"
            fps.append(BeamFootprint(name, layer_name, sx, sy, tx, ty, h, beam, diameter, throw, center_lux))
        for i, a in enumerate(fps):
            for j, b in enumerate(fps):
                if i == j:
                    continue
                dist = math.hypot(a.target_x - b.target_x, a.target_y - b.target_y)
                if dist < (a.diameter_m + b.diameter_m) * 0.28:
                    a.overlap_count += 1
            a.hotspot = a.lux_center > self.room.lux_target * 2.2 or a.overlap_count >= 3
        self._mark_shadow_gaps(fps)
        return fps

    def _mark_shadow_gaps(self, fps: List[BeamFootprint]) -> None:
        if not fps:
            return
        for r in range(5):
            for c in range(5):
                px = (c + 0.5) * self.room.width / 5
                py = (r + 0.5) * self.room.length / 5
                covered = any(math.hypot(px - f.target_x, py - f.target_y) <= f.diameter_m * 0.45 for f in fps)
                if not covered and self.lux.point_lux(px, py) < self.room.lux_target * 0.55:
                    nearest = min(fps, key=lambda f: math.hypot(px - f.target_x, py - f.target_y))
                    nearest.shadow_gap = True

    def metrics(self) -> Dict[str, float]:
        fps = self.footprints()
        if not fps:
            return {"count": 0, "avg_diameter": 0, "max_overlap": 0, "hotspots": 0, "gaps": 0}
        return {
            "count": len(fps),
            "avg_diameter": sum(f.diameter_m for f in fps) / len(fps),
            "max_overlap": max(f.overlap_count for f in fps),
            "hotspots": sum(1 for f in fps if f.hotspot),
            "gaps": sum(1 for f in fps if f.shadow_gap),
        }

    def issues(self) -> List[Tuple[str, str, str]]:
        fps = self.footprints()
        issues: List[Tuple[str, str, str]] = []
        for idx, fp in enumerate(fps, 1):
            where = f"x={fp.target_x:.2f}m, y={fp.target_y:.2f}m"
            if fp.overlap_count >= 3:
                issues.append(("Excessive beam overlap", f"Beam {idx} has {fp.overlap_count} overlaps near {where}.", "Increase spacing, lower intensity, or use a narrower beam."))
            if fp.hotspot:
                issues.append(("Beam hotspot", f"Beam {idx} center is {fp.lux_center:.0f} lx near {where}.", "Reduce output, tilt away from the hotspot, or widen distribution."))
            if fp.shadow_gap:
                issues.append(("Shadow gap", f"Coverage drops between beam footprints near {where}.", "Add fill light or reduce fixture spacing."))
        if self.room.room_type in ("משרד", "Office", "Workspace") and self.room.beam_angle < 24:
            issues.append(("Wrong beam angle", "Narrow beams can create scallops in work areas.", "Use 36-60 degree optics for uniform task lighting."))
        if self.room.reflectance_floor < 0.18 or self.room.reflectance_walls < 0.25:
            issues.append(("Low reflectance absorption", "Dark surfaces reduce beam diffusion and increase contrast.", "Increase fixture density or use wider optics/indirect light."))
        return issues


class ElectricalEngine:
    def __init__(self, room: RoomModel, lux: LuxEngine):
        self.room = room
        self.lux = lux

    def summary(self, voltage: float = 230.0) -> Dict[str, float]:
        watts = self.lux.watts_total()
        amps = watts / max(voltage, 1)
        recommended_circuits = max(1, math.ceil(amps / 8.0))
        lpd = watts / max(self.room.area, 0.01)
        limit = LPD_LIMITS_W_M2.get(self.room.room_type, 12)
        score = clamp(100 - (lpd / max(limit, 0.1) - 0.6) * 80, 0, 100)
        monthly_kwh = watts / 1000 * 8 * 22
        co2_kg = monthly_kwh * 0.45
        return {"watts": watts, "amps": amps, "circuits": recommended_circuits, "voltage": voltage, "efficiency_score": score, "monthly_kwh": monthly_kwh, "co2_kg": co2_kg}


class FixtureLibraryEngine:
    def __init__(self, catalogue: Dict[str, Dict]):
        self.catalogue = catalogue

    def filter(
        self,
        min_lm: float = 0,
        max_w: float = 9999,
        min_cri: float = 0,
        cct: Optional[int] = None,
        beam: Optional[float] = None,
        brand: str = "",
        favorites_only: bool = False,
    ) -> Dict[str, Dict]:
        out: Dict[str, Dict] = {}
        for name, item in self.catalogue.items():
            if float(item.get("lm", 0)) < min_lm:
                continue
            if float(item.get("w", 0)) > max_w:
                continue
            if float(item.get("cri", 0)) < min_cri:
                continue
            if cct and int(float(item.get("cct", 0))) != cct:
                continue
            if beam and abs(float(item.get("beam", 0)) - beam) > 0.1:
                continue
            if brand and brand.lower() not in str(item.get("brand", "")).lower():
                continue
            if favorites_only and not item.get("favorite", False):
                continue
            out[name] = item
        return out


class ImportCleanupEngine:
    NOISE_KEYWORDS = ("dim", "dimension", "text", "hatch", "furniture-tag", "annotation", "axis", "grid")

    def cleanup_notes_for_path(self, path: str) -> List[str]:
        ext = os.path.splitext(path)[1].lower()
        notes = ["AI Suggests: imported geometry is kept editable; no automatic design was forced."]
        if ext in {".dxf", ".dwg"}:
            notes.append("Ignored likely annotation, dimension and hatch layers when classifying architectural entities.")
        elif ext in {".png", ".jpg", ".jpeg", ".pdf"}:
            notes.append("Raster/PDF plan normalized as underlay; OpenCV contour cleanup is optional when installed.")
        elif ext == ".svg":
            notes.append("SVG registered as vector underlay; path classes can be mapped to walls/openings in a future pass.")
        return notes

    def is_noise_layer(self, layer_name: str) -> bool:
        low = layer_name.lower()
        return any(k in low for k in self.NOISE_KEYWORDS)


class AutoScaleDetector:
    def __init__(self, room: RoomModel):
        self.room = room

    def estimate(self, understanding: ArchitecturalUnderstanding) -> Tuple[float, float, str]:
        door_candidates = [d for d in understanding.doors if d.width > 0]
        if door_candidates:
            candidate = door_candidates[0]
            scale = 0.90 / max(candidate.width * self.room.width, 0.01)
            return clamp(scale, 0.001, 1.0), 0.70, "Estimated from standard 0.90m door width."
        furniture = [f for f in understanding.furniture if "dining" in f.name.lower() or "bed" in f.name.lower()]
        if furniture:
            item = furniture[0]
            known = 1.80 if "dining" in item.name.lower() else 2.00
            scale = known / max(item.width * self.room.width, 0.01)
            return clamp(scale, 0.001, 1.0), 0.55, f"Estimated from known {item.name} size."
        return self.room.floor_plan.scale_m_per_px, 0.25, "Scale remains approximate; confirm manually."


class ArchitecturalAnalysisEngine:
    def __init__(self, room: RoomModel):
        self.room = room
        self.cleanup = ImportCleanupEngine()

    def analyze(self, path: str) -> ArchitecturalUnderstanding:
        ext = os.path.splitext(path)[1].lower()
        understanding = ArchitecturalUnderstanding(source_path=path)
        understanding.cleanup_notes = self.cleanup.cleanup_notes_for_path(path)
        if ext == ".dxf":
            self._analyze_dxf(path, understanding)
        elif ext in {".png", ".jpg", ".jpeg"}:
            self._analyze_raster(path, understanding)
        elif ext == ".pdf":
            self._analyze_raster(self.room.floor_plan.path or path, understanding)
        else:
            self._fallback_architectural_guess(understanding)
        self._ensure_boundary(understanding)
        self._infer_openings(understanding)
        self._infer_furniture(understanding)
        self._generate_zones(understanding)
        self._analyze_daylight(understanding)
        self._analyze_ceiling(understanding)
        self._generate_lighting_opportunities(understanding)
        scale, conf, note = AutoScaleDetector(self.room).estimate(understanding)
        understanding.estimated_scale_m_per_px = scale
        understanding.scale_confidence = conf
        understanding.cleanup_notes.append(note)
        understanding.suggestions.extend(self._consultant_suggestions(understanding))
        return understanding

    def _analyze_dxf(self, path: str, understanding: ArchitecturalUnderstanding) -> None:
        try:
            import ezdxf  # type: ignore

            doc = ezdxf.readfile(path)
            msp = doc.modelspace()
            for entity in msp:
                layer = getattr(entity.dxf, "layer", "")
                if self.cleanup.is_noise_layer(layer):
                    continue
                etype = entity.dxftype()
                if etype == "LINE":
                    x1, y1 = float(entity.dxf.start.x), float(entity.dxf.start.y)
                    x2, y2 = float(entity.dxf.end.x), float(entity.dxf.end.y)
                    if math.hypot(x2 - x1, y2 - y1) > 0.25:
                        understanding.walls.append((x1, y1, x2, y2))
                elif etype in {"LWPOLYLINE", "POLYLINE"}:
                    pts = [(float(p[0]), float(p[1])) for p in entity.get_points()]
                    for a, b in zip(pts, pts[1:]):
                        understanding.walls.append((a[0], a[1], b[0], b[1]))
            understanding.cleanup_notes.append(f"DXF parsed with {len(understanding.walls)} candidate wall segments.")
        except Exception as exc:
            understanding.cleanup_notes.append(f"DXF semantic parser unavailable or failed: {exc}. Used safe heuristic analysis.")
            self._fallback_architectural_guess(understanding)

    def _analyze_raster(self, path: str, understanding: ArchitecturalUnderstanding) -> None:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore

            img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if img is None:
                raise RuntimeError("Raster could not be decoded.")
            img = cv2.GaussianBlur(img, (3, 3), 0)
            edges = cv2.Canny(img, 60, 160)
            lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80, minLineLength=60, maxLineGap=12)
            if lines is not None:
                h, w = img.shape[:2]
                for line in lines[:120]:
                    x1, y1, x2, y2 = line[0]
                    understanding.walls.append((x1 / w * self.room.width, y1 / h * self.room.length, x2 / w * self.room.width, y2 / h * self.room.length))
            understanding.cleanup_notes.append(f"OpenCV extracted {len(understanding.walls)} candidate wall/opening strokes.")
        except Exception as exc:
            understanding.cleanup_notes.append(f"OpenCV not available or raster analysis failed: {exc}. Used safe heuristic analysis.")
            self._fallback_architectural_guess(understanding)

    def _fallback_architectural_guess(self, understanding: ArchitecturalUnderstanding) -> None:
        understanding.walls = [
            (0, 0, self.room.width, 0),
            (self.room.width, 0, self.room.width, self.room.length),
            (self.room.width, self.room.length, 0, self.room.length),
            (0, self.room.length, 0, 0),
        ]
        understanding.cleanup_notes.append("Fallback room-boundary model generated from current room dimensions.")

    def _ensure_boundary(self, understanding: ArchitecturalUnderstanding) -> None:
        if not understanding.room_boundary:
            understanding.room_boundary = [(0, 0), (self.room.width, 0), (self.room.width, self.room.length), (0, self.room.length)]

    def _infer_openings(self, understanding: ArchitecturalUnderstanding) -> None:
        if not understanding.doors:
            understanding.doors.append(ImportInsight("Door", "Main door", 0.42, 0.06, 0.88, 0.14, 0.05, "Keep switch/control lighting near entry."))
        if not understanding.windows:
            understanding.windows.append(ImportInsight("Window", "Primary window", 0.45, 0.50, 0.02, 0.32, 0.06, "Evaluate daylight and glare risk near this facade."))

    def _infer_furniture(self, understanding: ArchitecturalUnderstanding) -> None:
        names = {x.name.lower() for x in understanding.furniture}
        for furn in self.room.furniture:
            if furn.enabled and furn.name.lower() not in names:
                understanding.furniture.append(
                    ImportInsight("Furniture", furn.name, 0.62, furn.x, furn.y, furn.width_m / max(self.room.width, 0.01), furn.length_m / max(self.room.length, 0.01), f"Use {furn.name} as a lighting task/reference object.")
                )

    def _generate_zones(self, understanding: ArchitecturalUnderstanding) -> None:
        existing = {z.name.lower() for z in understanding.zones}
        for furn in understanding.furniture:
            low = furn.name.lower()
            if "dining" in low and "dining zone" not in existing:
                understanding.zones.append(ImportInsight("Zone", "Dining zone", 0.76, furn.x, furn.y, max(furn.width, 0.32), max(furn.length, 0.20), "Suggested 300 lx, ambient layer plus centered pendant."))
            elif "kitchen" in low and "kitchen work area" not in existing:
                understanding.zones.append(ImportInsight("Zone", "Kitchen work area", 0.74, furn.x, furn.y, max(furn.width, 0.38), max(furn.length, 0.18), "Suggested 500 lx, functional layer and glare-controlled beam."))
        if not understanding.zones:
            understanding.zones.append(ImportInsight("Zone", "General circulation", 0.45, 0.50, 0.50, 0.65, 0.18, "Suggested 100-150 lx with low-glare general lighting."))

    def _analyze_daylight(self, understanding: ArchitecturalUnderstanding) -> None:
        if understanding.windows:
            win = understanding.windows[0]
            self.room.daylight.enabled = True
            self.room.daylight.window_width_m = clamp(win.width * self.room.width, 0.6, 6.0)
            self.room.daylight.window_height_m = clamp(self.room.daylight.window_height_m, 0.8, 2.4)
            understanding.suggestions.append("Window detected: review daylight contribution, glare risk and artificial-light compensation.")

    def _analyze_ceiling(self, understanding: ArchitecturalUnderstanding) -> None:
        if self.room.curtain_lighting.enabled:
            understanding.ceiling_features.append(ImportInsight("Ceiling", "Curtain recess", 0.68, 0.5, 0.03, 0.8, 0.04, "Opportunity for curtain lighting or wall washing."))
        else:
            understanding.ceiling_features.append(ImportInsight("Ceiling", "Perimeter opportunity", 0.38, 0.5, 0.5, 0.92, 0.92, "Confirm gypsum drops/cornices before adding indirect profiles."))

    def _generate_lighting_opportunities(self, understanding: ArchitecturalUnderstanding) -> None:
        for zone in understanding.zones:
            low = zone.name.lower()
            if "dining" in low:
                understanding.lighting_opportunities.append(ImportInsight("Lighting", "Centered pendant over dining", 0.82, zone.x, zone.y, 0.18, 0.18, "Place pendant group centered on table; keep dimmable ambient scene."))
            elif "kitchen" in low:
                understanding.lighting_opportunities.append(ImportInsight("Lighting", "Task spots over kitchen work area", 0.80, zone.x, zone.y, zone.width, zone.length, "Use 36-60deg optics, CRI 90+, and avoid shadows from user position."))
        for window in understanding.windows:
            understanding.lighting_opportunities.append(ImportInsight("Lighting", "Daylight compensation row", 0.55, window.x, min(0.85, window.y + 0.15), 0.5, 0.08, "Balance window-side contrast with dimmable general or indirect lighting."))

    def _consultant_suggestions(self, understanding: ArchitecturalUnderstanding) -> List[str]:
        suggestions = [
            "AI Suggests -> User Confirms: accept generated zones only after reviewing room intent.",
            "Keep imported plan as underlay; model geometry remains editable and reversible.",
        ]
        for opp in understanding.lighting_opportunities:
            suggestions.append(f"{opp.name}: {opp.recommendation}")
        return suggestions


class ArchitecturalUnderstandingApplier:
    def __init__(self, room: RoomModel):
        self.room = room

    def stage(self, understanding: ArchitecturalUnderstanding) -> None:
        self.room.architectural_understanding = understanding
        self.room.floor_plan.detected_walls = understanding.walls[:80] or self.room.floor_plan.detected_walls
        self.room.floor_plan.detected_openings = [x.to_dict() for x in understanding.doors + understanding.windows]
        self.room.floor_plan.detected_ceiling_features = [x.to_dict() for x in understanding.ceiling_features]
        self.room.floor_plan.import_confidence = max(understanding.scale_confidence, 0.35 if understanding.walls else 0.15)
        self.room.floor_plan.cleanup_notes = list(understanding.cleanup_notes)
        self.room.floor_plan.analysis_summary = self.summary_text(understanding)

    def confirm_suggestions(self) -> None:
        u = self.room.architectural_understanding
        if not u.zones:
            return
        self.room.zones = []
        for z in u.zones[:8]:
            target = 500 if "kitchen" in z.name.lower() or "work" in z.name.lower() else 300 if "dining" in z.name.lower() else 150
            self.room.zones.append(LightingZone(name=z.name, x=clamp(z.x - z.width / 2, 0, 1), y=clamp(z.y - z.length / 2, 0, 1), width=clamp(z.width, 0.05, 1), length=clamp(z.length, 0.05, 1), lux_target=target))
        u.requires_confirmation = False

    def summary_text(self, u: ArchitecturalUnderstanding) -> str:
        return f"{len(u.walls)} walls, {len(u.windows)} windows, {len(u.doors)} doors, {len(u.furniture)} furniture candidates, {len(u.zones)} suggested zones."


class FloorPlanImportPipeline:
    SUPPORTED = {".dxf", ".dwg", ".svg", ".pdf", ".png", ".jpg", ".jpeg"}

    def __init__(self, room: RoomModel):
        self.room = room

    def attach_underlay(self, path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        if ext not in self.SUPPORTED:
            raise ValueError(f"Unsupported plan format: {ext}")
        self.room.floor_plan.source_path = path
        if ext == ".dwg":
            self.room.floor_plan.path = path
            self._run_understanding(path)
            return "DWG registered as underlay. Convert to DXF for deeper entity extraction; heuristic understanding was staged for review."
        if ext == ".dxf":
            self.room.floor_plan.path = path
            self._run_understanding(path)
            return "DXF underlay registered and analyzed. Review AI suggestions before confirming generated zones."
        if ext == ".pdf":
            self.room.floor_plan.path = self._render_pdf_preview(path)
            self._register_boundary_estimate()
            self._run_understanding(path)
            return "PDF converted to first-page PNG underlay and analyzed. Review AI suggestions before confirming."
        if ext in {".png", ".jpg", ".jpeg"}:
            self.room.floor_plan.path = path
            self._register_boundary_estimate()
            self._run_understanding(path)
            return "Raster underlay registered and analyzed. Install opencv-python for stronger wall/opening detection."
        self.room.floor_plan.path = path
        self._run_understanding(path)
        return "SVG underlay registered and staged for semantic review."

    def _render_pdf_preview(self, path: str) -> str:
        try:
            from PySide6.QtPdf import QPdfDocument
        except Exception as exc:
            raise RuntimeError("PDF preview requires PySide6.QtPdf, or install pdf2image + Poppler for an alternate conversion pipeline.") from exc

        doc = QPdfDocument()
        doc.load(path)
        if doc.pageCount() < 1:
            raise RuntimeError("The selected PDF has no renderable pages.")
        size_pt = doc.pagePointSize(0)
        width = int(clamp(size_pt.width() * 2.0, 800, 2400))
        height = int(clamp(size_pt.height() * 2.0, 800, 2400))
        image = doc.render(0, QSize(width, height))
        if image.isNull():
            raise RuntimeError("QtPdf could not render the first PDF page.")
        cache_dir = os.path.join(tempfile.gettempdir(), "lighting_design_pro_underlays")
        os.makedirs(cache_dir, exist_ok=True)
        digest = hashlib.sha1((path + str(os.path.getmtime(path))).encode("utf-8", errors="ignore")).hexdigest()[:16]
        out_path = os.path.join(cache_dir, f"underlay_{digest}.png")
        if not image.save(out_path):
            raise RuntimeError("Could not save the rendered PDF underlay preview.")
        return out_path

    def _register_boundary_estimate(self) -> None:
        self.room.floor_plan.detected_walls = [
            (0.0, 0.0, self.room.width, 0.0),
            (self.room.width, 0.0, self.room.width, self.room.length),
            (self.room.width, self.room.length, 0.0, self.room.length),
            (0.0, self.room.length, 0.0, 0.0),
        ]

    def _run_understanding(self, path: str) -> None:
        understanding = ArchitecturalAnalysisEngine(self.room).analyze(path)
        ArchitecturalUnderstandingApplier(self.room).stage(understanding)


class ProfessionalExporter:
    def __init__(self, room: RoomModel):
        self.room = room

    def quotation_text(self) -> str:
        price = PricingEngine(self.room)
        totals = price.totals()
        lines = [
            f"{self.room.branding.company_name} - Lighting Quotation",
            f"Project: {self.room.project_name}",
            f"Client: {self.room.client_name}",
            f"Date: {dt.datetime.now():%Y-%m-%d}",
            "",
            "Items:",
        ]
        for name, qty, unit, total in price.line_items():
            lines.append(f"- {name}: {qty} x {unit:.2f} = {total:.2f}")
        lines.extend(
            [
                "",
                f"Material: {totals['material']:.2f}",
                f"Markup: {totals['markup']:.2f}",
                f"Labor: {totals['labour']:.2f}",
                f"Total estimate: {totals['total']:.2f}",
            ]
        )
        return "\n".join(lines)

    def write_basic_dxf(self, path: str) -> None:
        planner = SpotlightPlanner(self.room)
        lines = ["0", "SECTION", "2", "ENTITIES"]

        def line(x1: float, y1: float, x2: float, y2: float, layer: str) -> None:
            lines.extend(["0", "LINE", "8", layer, "10", str(x1), "20", str(y1), "11", str(x2), "21", str(y2)])

        line(0, 0, self.room.width, 0, "ROOM")
        line(self.room.width, 0, self.room.width, self.room.length, "ROOM")
        line(self.room.width, self.room.length, 0, self.room.length, "ROOM")
        line(0, self.room.length, 0, 0, "ROOM")
        for x, y in planner.active_positions():
            lines.extend(["0", "CIRCLE", "8", "SPOTLIGHTS", "10", str(x), "20", str(y), "40", "0.08"])
        for prof in self.room.profiles:
            if prof.enabled:
                rad = math.radians(prof.angle_deg)
                dx, dy = math.cos(rad), math.sin(rad)
                cx, cy = self.room.width * prof.x, self.room.length * prof.y
                line(cx - dx * prof.length_m / 2, cy - dy * prof.length_m / 2, cx + dx * prof.length_m / 2, cy + dy * prof.length_m / 2, "PROFILES")
        for track in self.room.tracks:
            if track.enabled:
                rad = math.radians(track.angle_deg)
                dx, dy = math.cos(rad), math.sin(rad)
                cx, cy = self.room.width * track.x, self.room.length * track.y
                line(cx - dx * track.length_m / 2, cy - dy * track.length_m / 2, cx + dx * track.length_m / 2, cy + dy * track.length_m / 2, "TRACKS")
        for furn in self.room.furniture:
            if furn.enabled:
                x, y, w, l = furn.bounds(self.room)
                line(x, y, x + w, y, "FURNITURE")
                line(x + w, y, x + w, y + l, "FURNITURE")
                line(x + w, y + l, x, y + l, "FURNITURE")
                line(x, y + l, x, y, "FURNITURE")
        curtain = LuxEngine(self.room).curtain_points()
        for a, b in zip(curtain, curtain[1:]):
            line(a[0], a[1], b[0], b[1], "CURTAIN_LIGHTING")
        lines.extend(["0", "ENDSEC", "0", "EOF"])
        with open(path, "w", encoding="ascii") as f:
            f.write("\n".join(lines))


class RoomRenderer(QWidget):
    spotMoved = Signal(list)

    def __init__(self):
        super().__init__()
        self.room: Optional[RoomModel] = None
        self.spots: List[Tuple[float, float]] = []
        self.heatmap: List[List[float]] = []
        self._drag_idx: Optional[int] = None
        self.setMinimumSize(520, 360)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.ArrowCursor))

    def update_scene(self, room: RoomModel, spots: List[Tuple[float, float]], heatmap: List[List[float]]) -> None:
        self.room, self.spots, self.heatmap = room, spots, heatmap
        self.update()

    def _scale(self) -> Tuple[float, float, float]:
        if not self.room:
            return 1, 0, 0
        margin = 50
        s = min((self.width() - 2 * margin) / self.room.width, (self.height() - 2 * margin) / self.room.length)
        ox = margin + ((self.width() - 2 * margin) - self.room.width * s) / 2
        oy = margin + ((self.height() - 2 * margin) - self.room.length * s) / 2
        return s, ox, oy

    def m2p(self, x: float, y: float) -> QPointF:
        s, ox, oy = self._scale()
        return QPointF(ox + x * s, oy + y * s)

    def p2m(self, x: float, y: float) -> Tuple[float, float]:
        s, ox, oy = self._scale()
        return ((x - ox) / s, (y - oy) / s)

    def _nearest(self, x: float, y: float) -> Optional[int]:
        best, dist = None, 18
        for i, (mx, my) in enumerate(self.spots):
            pt = self.m2p(mx, my)
            d = math.hypot(pt.x() - x, pt.y() - y)
            if d < dist:
                best, dist = i, d
        return best

    def mousePressEvent(self, e) -> None:
        if self.room and not self.room.layer(1).enabled:
            return
        if e.button() == Qt.LeftButton:
            self._drag_idx = self._nearest(e.position().x(), e.position().y())
            if self._drag_idx is not None:
                self.setCursor(QCursor(Qt.ClosedHandCursor))

    def mouseMoveEvent(self, e) -> None:
        if self._drag_idx is not None and self.room:
            x, y = self.p2m(e.position().x(), e.position().y())
            self.spots[self._drag_idx] = (round(clamp(x, 0, self.room.width), 3), round(clamp(y, 0, self.room.length), 3))
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._drag_idx is not None:
            self._drag_idx = None
            self.setCursor(QCursor(Qt.ArrowCursor))
            self.spotMoved.emit(list(self.spots))

    def paintEvent(self, _) -> None:
        if not self.room:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        self._draw_workspace_background(p)
        self._draw_underlay(p)
        self._draw_heatmap(p)
        self._draw_room(p)
        self._draw_zones(p)
        self._draw_furniture(p)
        self._draw_profiles(p)
        self._draw_tracks(p)
        self._draw_pendants(p)
        self._draw_ambient(p)
        self._draw_curtain_lighting(p)
        self._draw_beams(p)
        self._draw_spots(p)
        self._draw_labels(p)
        p.end()

    def _draw_workspace_background(self, p: QPainter) -> None:
        grad = QLinearGradient(self.rect().topLeft(), self.rect().bottomRight())
        grad.setColorAt(0.0, QColor("#070910"))
        grad.setColorAt(0.55, QColor("#101522"))
        grad.setColorAt(1.0, QColor("#080B12"))
        p.fillRect(self.rect(), grad)
        if not self.room:
            return
        r = QRectF(self.m2p(0, 0), self.m2p(self.room.width, self.room.length)).adjusted(-18, -18, 18, 18)
        for i, alpha in enumerate((34, 22, 12)):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(30, 60, 120, alpha))
            p.drawRoundedRect(r.adjusted(-i * 7, -i * 7, i * 7, i * 7), 18 + i * 3, 18 + i * 3)

    def _draw_room(self, p: QPainter) -> None:
        r = QRectF(self.m2p(0, 0), self.m2p(self.room.width, self.room.length))
        room_grad = QLinearGradient(r.topLeft(), r.bottomRight())
        room_grad.setColorAt(0.0, QColor(18, 24, 36, 210))
        room_grad.setColorAt(1.0, QColor(11, 15, 24, 230))
        p.setPen(QPen(QColor("#60708F"), 1.7))
        p.setBrush(room_grad)
        p.drawRoundedRect(r, 4, 4)
        if self.room.floor_plan.detected_walls:
            p.setPen(QPen(QColor("#78D8FF"), 2, Qt.DashDotLine))
            for x1, y1, x2, y2 in self.room.floor_plan.detected_walls:
                p.drawLine(self.m2p(x1, y1), self.m2p(x2, y2))
        if self.room.floor_plan.detected_openings:
            for item in self.room.floor_plan.detected_openings:
                x, y = float(item.get("x", 0.5)) * self.room.width, float(item.get("y", 0.5)) * self.room.length
                w, l = float(item.get("width", 0.08)) * self.room.width, float(item.get("length", 0.04)) * self.room.length
                col = QColor("#7FE7B6") if item.get("category") == "Window" else QColor("#FFD166")
                p.setPen(QPen(col, 2.4, Qt.SolidLine))
                p.setBrush(QColor(col.red(), col.green(), col.blue(), 42))
                p.drawRoundedRect(QRectF(self.m2p(x - w / 2, y - l / 2), self.m2p(x + w / 2, y + l / 2)), 3, 3)
        if self.room.floor_plan.detected_ceiling_features:
            p.setPen(QPen(QColor("#C084FC"), 1.6, Qt.DashLine))
            p.setBrush(QColor(192, 132, 252, 28))
            for item in self.room.floor_plan.detected_ceiling_features:
                x, y = float(item.get("x", 0.5)) * self.room.width, float(item.get("y", 0.5)) * self.room.length
                w, l = float(item.get("width", 0.15)) * self.room.width, float(item.get("length", 0.08)) * self.room.length
                p.drawRoundedRect(QRectF(self.m2p(x - w / 2, y - l / 2), self.m2p(x + w / 2, y + l / 2)), 5, 5)
        if not self.room.optics.show_helper_guides:
            return
        minor = QColor(P["border"])
        minor.setAlpha(115)
        major = QColor("#506080")
        major.setAlpha(145)
        x = 0
        while x <= self.room.width:
            p.setPen(QPen(major if abs(x - round(x / 5) * 5) < 0.01 else minor, 1, Qt.DotLine))
            p.drawLine(self.m2p(x, 0), self.m2p(x, self.room.length))
            x += 1
        y = 0
        while y <= self.room.length:
            p.setPen(QPen(major if abs(y - round(y / 5) * 5) < 0.01 else minor, 1, Qt.DotLine))
            p.drawLine(self.m2p(0, y), self.m2p(self.room.width, y))
            y += 1

    def _draw_underlay(self, p: QPainter) -> None:
        fp = self.room.floor_plan
        if not fp.path or not os.path.exists(fp.path):
            return
        ext = os.path.splitext(fp.path)[1].lower()
        if ext not in {".png", ".jpg", ".jpeg", ".svg"}:
            return
        pix = QPixmap(fp.path)
        if pix.isNull():
            return
        p.save()
        p.setOpacity(clamp(fp.opacity / 100, 0.05, 0.9))
        target = QRectF(self.m2p(0, 0), self.m2p(self.room.width, self.room.length))
        p.drawPixmap(target, pix, pix.rect())
        p.restore()

    def _draw_zones(self, p: QPainter) -> None:
        if not self.room.optics.show_zone_guides:
            return
        colors = [QColor(61, 142, 240, 80), QColor(46, 204, 122, 70), QColor(240, 160, 48, 75)]
        p.setFont(QFont("Segoe UI", 8))
        for i, zone in enumerate(self.room.zones):
            if not zone.visible:
                continue
            x, y, w, l = zone.bounds(self.room)
            rect = QRectF(self.m2p(x, y), self.m2p(x + w, y + l))
            col = colors[i % len(colors)]
            if zone.locked:
                col.setAlpha(45)
            p.setBrush(col)
            p.setPen(QPen(col.lighter(160), 1, Qt.DashLine))
            p.drawRoundedRect(rect, 4, 4)
            p.setPen(QColor(P["text"]))
            lock = " locked" if zone.locked else ""
            p.drawText(rect.adjusted(4, 4, -4, -4), Qt.AlignTop | Qt.AlignLeft, f"{zone.name}{lock}\n{zone.lux_target} lx")

    def _draw_furniture(self, p: QPainter) -> None:
        for furn in self.room.furniture:
            if not furn.enabled:
                continue
            x, y, w, l = furn.bounds(self.room)
            rect = QRectF(self.m2p(x, y), self.m2p(x + w, y + l))
            p.save()
            p.translate(rect.center())
            p.rotate(furn.rotation_deg)
            local = QRectF(-rect.width() / 2, -rect.height() / 2, rect.width(), rect.height())
            if furn.furniture_type == "Kitchen island":
                fill, stroke = QColor(92, 120, 145, 115), QColor("#90B8D8")
            elif furn.furniture_type == "Dining table":
                fill, stroke = QColor(120, 95, 75, 120), QColor("#D0A070")
            else:
                fill, stroke = QColor(120, 130, 145, 100), QColor(P["muted"])
            p.setBrush(fill)
            p.setPen(QPen(stroke, 1.5))
            p.drawRoundedRect(local, 5, 5)
            p.setPen(QColor(P["text"]))
            p.drawText(local.adjusted(4, 4, -4, -4), Qt.AlignCenter, furn.name)
            p.restore()

    def _draw_heatmap(self, p: QPainter) -> None:
        if not self.room.show_heatmap or not self.heatmap:
            return
        vals = [v for row in self.heatmap for v in row]
        hi = max(max(vals), self.room.lux_target * 1.5, 1)
        n = len(self.heatmap)
        cw, ch = self.room.width / n, self.room.length / n
        for r, row in enumerate(self.heatmap):
            for c, val in enumerate(row):
                ratio = clamp(val / hi, 0, 1)
                if ratio < 0.5:
                    k = ratio / 0.5
                    col = QColor(int(30 + 30 * k), int(110 + 90 * k), int(220 - 130 * k), self.room.heatmap_opacity)
                else:
                    k = (ratio - 0.5) / 0.5
                    col = QColor(int(220 + 35 * k), int(185 - 105 * k), int(50 - 20 * k), self.room.heatmap_opacity)
                p.fillRect(QRectF(self.m2p(c * cw, r * ch), self.m2p((c + 1) * cw, (r + 1) * ch)), col)
        legend = QRectF(self.width() - 185, 18, 150, 12)
        for i in range(50):
            ratio = i / 49
            if ratio < 0.5:
                k = ratio / 0.5
                col = QColor(int(30 + 30 * k), int(110 + 90 * k), int(220 - 130 * k))
            else:
                k = (ratio - 0.5) / 0.5
                col = QColor(int(220 + 35 * k), int(185 - 105 * k), int(50 - 20 * k))
            p.fillRect(QRectF(legend.x() + i * 3, legend.y(), 3, legend.height()), col)
        p.setPen(QColor(P["text"]))
        p.drawText(legend.adjusted(0, 14, 0, 18), Qt.AlignCenter, f"0 - {hi:.0f} lx")

    def _draw_profiles(self, p: QPainter) -> None:
        for prof in self.room.profiles:
            if not prof.enabled:
                continue
            s, _, _ = self._scale()
            p.setPen(QPen(QColor(P["blue"]), max(3, prof.width_m * s), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            rad = math.radians(prof.angle_deg)
            dx, dy = math.cos(rad), math.sin(rad)
            cx, cy = self.room.width * prof.x, self.room.length * prof.y
            if prof.shape in ("Rectangle", "Perimeter"):
                inset = 0.18 if prof.shape == "Perimeter" else max(0.15, prof.width_m)
                p.drawRoundedRect(QRectF(self.m2p(inset, inset), self.m2p(self.room.width - inset, self.room.length - inset)), 5, 5)
            else:
                p0 = self.m2p(cx - dx * prof.length_m / 2, cy - dy * prof.length_m / 2)
                p1 = self.m2p(cx + dx * prof.length_m / 2, cy + dy * prof.length_m / 2)
                p.drawLine(p0, p1)
                if prof.shape in ("L shape", "U shape"):
                    p.drawLine(p1, p1 + QPointF(-dy * 70, dx * 70))
                if prof.shape == "U shape":
                    p.drawLine(p0, p0 + QPointF(-dy * 70, dx * 70))

    def _draw_tracks(self, p: QPainter) -> None:
        for track in self.room.tracks:
            if not track.enabled:
                continue
            rad = math.radians(track.angle_deg)
            dx, dy = math.cos(rad), math.sin(rad)
            cx, cy = self.room.width * track.x, self.room.length * track.y
            p.setPen(QPen(QColor(P["gold"]), max(3, track.width_cm * 1.8), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p0 = self.m2p(cx - dx * track.length_m / 2, cy - dy * track.length_m / 2)
            p1 = self.m2p(cx + dx * track.length_m / 2, cy + dy * track.length_m / 2)
            if track.shape == "Rectangle":
                p.drawRoundedRect(QRectF(self.m2p(0.25, 0.25), self.m2p(self.room.width - 0.25, self.room.length - 0.25)), 4, 4)
            else:
                p.drawLine(p0, p1)
                if track.shape in ("L shape", "U shape", "Custom segments"):
                    p.drawLine(p1, p1 + QPointF(-dy * 70, dx * 70))
                if track.shape == "U shape":
                    p.drawLine(p0, p0 + QPointF(-dy * 70, dx * 70))
            p.setBrush(QColor("#FFE060"))
            for x, y, _ in track.fixture_points(self.room):
                p.drawEllipse(self.m2p(x, y), 5, 5)

    def _draw_pendants(self, p: QPainter) -> None:
        p.setPen(QPen(QColor(P["purple"]), 2))
        p.setBrush(QColor(P["purple"]))
        for pend in self.room.pendants:
            if not pend.enabled:
                continue
            for x, y in pend.points(self.room):
                pt = self.m2p(x, y)
                if pend.pendant_type == "נברשת":
                    p.drawEllipse(pt, 9, 9)
                    p.drawLine(pt + QPointF(-8, 0), pt + QPointF(8, 0))
                    p.drawLine(pt + QPointF(0, -8), pt + QPointF(0, 8))
                elif pend.pendant_type == "פנדנט אקוסטי":
                    p.drawRoundedRect(QRectF(pt.x() - 10, pt.y() - 5, 20, 10), 3, 3)
                else:
                    p.drawEllipse(pt, 6, 6)
                    p.drawLine(pt, pt + QPointF(0, -16))

    def _draw_ambient(self, p: QPainter) -> None:
        if not self.room.layer(2).enabled or not self.room.ambient.enabled:
            return
        a = self.room.ambient
        p.setPen(QPen(QColor(P["cyan"]), 3, Qt.SolidLine))
        if a.shape == "היקפי":
            inset = 0.15
            pts = [
                self.m2p(inset, inset),
                self.m2p(self.room.width - inset, inset),
                self.m2p(self.room.width - inset, self.room.length - inset),
                self.m2p(inset, self.room.length - inset),
            ]
            for i in range(4):
                p.drawLine(pts[i], pts[(i + 1) % 4])
            return
        rad = math.radians(a.angle_deg)
        dx, dy = math.cos(rad), math.sin(rad)
        cx, cy = self.room.width * a.x, self.room.length * a.y
        p0 = self.m2p(cx - dx * a.length_m / 2, cy - dy * a.length_m / 2)
        p1 = self.m2p(cx + dx * a.length_m / 2, cy + dy * a.length_m / 2)
        p.drawLine(p0, p1)
        if a.shape in ("L-shape", "U-shape"):
            p.drawLine(p1, p1 + QPointF(-dy * 45, dx * 45))
        if a.shape == "U-shape":
            p.drawLine(p0, p0 + QPointF(-dy * 45, dx * 45))

    def _draw_curtain_lighting(self, p: QPainter) -> None:
        c = self.room.curtain_lighting
        if not c.enabled:
            return
        pts = LuxEngine(self.room).curtain_points()
        if len(pts) < 2:
            return
        p.setPen(QPen(QColor("#FF8A3D"), 4, Qt.DotLine))
        for a, b in zip(pts, pts[1:]):
            p.drawLine(self.m2p(a[0], a[1]), self.m2p(b[0], b[1]))

    def _draw_beams(self, p: QPainter) -> None:
        if not self.room.optics.show_beams:
            return
        alpha = int(clamp(self.room.optics.beam_opacity, 10, 180))
        for fp in BeamAnalysisEngine(self.room, LuxEngine(self.room)).footprints():
            center = self.m2p(fp.target_x, fp.target_y)
            edge = self.m2p(fp.target_x + fp.diameter_m / 2, fp.target_y)
            radius = abs(edge.x() - center.x())
            fill = QColor(255, 210, 70, alpha)
            outline = QColor(255, 235, 130, min(230, alpha + 80))
            if fp.hotspot:
                fill = QColor(255, 70, 55, min(170, alpha + 45))
                outline = QColor(255, 95, 85, 230)
            elif fp.shadow_gap:
                fill = QColor(60, 130, 235, min(140, alpha + 25))
                outline = QColor(100, 170, 255, 210)
            elif fp.overlap_count:
                fill = QColor(255, 155, 40, min(155, alpha + 30))
            p.setBrush(fill)
            p.setPen(QPen(outline, 1.2, Qt.DashLine))
            p.drawEllipse(center, radius, radius)
            if abs(fp.target_x - fp.x) > 0.01 or abs(fp.target_y - fp.y) > 0.01:
                p.setPen(QPen(QColor(255, 255, 255, 150), 1, Qt.DotLine))
                p.drawLine(self.m2p(fp.x, fp.y), center)

    def _draw_spots(self, p: QPainter) -> None:
        if not self.room.layer(1).enabled:
            return
        p.setPen(QPen(QColor("#FFE060"), 1))
        p.setBrush(QColor(255, 220, 80, 190))
        for x, y in self.spots:
            pt = self.m2p(x, y)
            p.drawEllipse(pt, 5, 5)
            p.drawLine(pt + QPointF(-7, 0), pt + QPointF(7, 0))
            p.drawLine(pt + QPointF(0, -7), pt + QPointF(0, 7))

    def _draw_labels(self, p: QPainter) -> None:
        p.setFont(QFont("Segoe UI", 9))
        p.setPen(QColor(P["muted"]))
        p.drawText(self.m2p(self.room.width / 2, self.room.length) + QPointF(-25, 22), f"{self.room.width:.1f} m")
        p.drawText(self.m2p(0, 0) + QPointF(8, -10), "Heatmap lux | גרירת ספוטים פעילה")


def make_card(title: str, color: str = P["blue"]) -> Tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setStyleSheet(
        "QFrame {"
        "background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #1B2030, stop:1 #121722);"
        f"border:1px solid {P['border']};"
        f"border-right:3px solid {color};"
        "border-radius:8px;"
        "}"
    )
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 10, 12, 12)
    layout.setSpacing(8)
    label = QLabel(title)
    label.setStyleSheet(f"color:{color}; font-weight:800; background:transparent; border:none;")
    layout.addWidget(label)
    return frame, layout


class LuxurySwitch(QWidget):
    toggled = Signal(bool)

    def __init__(self, checked: bool = True, color: str = P["green"], parent=None):
        super().__init__(parent)
        self._checked = checked
        self._color = QColor(color)
        self.setFixedSize(58, 30)
        self.setCursor(QCursor(Qt.PointingHandCursor))

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        if self._checked == checked:
            return
        self._checked = checked
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._checked = not self._checked
            self.update()
            self.toggled.emit(self._checked)

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        base = self._color if self._checked else QColor(P["border2"])
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0, base.lighter(145))
        grad.setColorAt(0.45, base)
        grad.setColorAt(1, base.darker(150))
        p.setPen(QPen(QColor(255, 255, 255, 55), 1))
        p.setBrush(grad)
        p.drawRoundedRect(rect, 15, 15)
        knob_d = 24
        x = self.width() - knob_d - 4 if self._checked else 4
        knob = QRectF(x, 3, knob_d, knob_d)
        kgrad = QLinearGradient(knob.topLeft(), knob.bottomRight())
        kgrad.setColorAt(0, QColor("#FFFFFF"))
        kgrad.setColorAt(1, QColor("#BFC8D8"))
        p.setPen(QPen(QColor(0, 0, 0, 70), 1))
        p.setBrush(kgrad)
        p.drawEllipse(knob)
        p.end()


def secondary_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("secondary")
    return b


class PremiumStartupSplash(QDialog):
    def __init__(self):
        super().__init__()
        self._tick = 0
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.SplashScreen)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(620, 340)
        self.title = QLabel(APP_NAME)
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet("font-size:28px;font-weight:800;color:#F6F8FF;background:transparent;border:none;")
        self.subtitle = QLabel("Calculating luxury villa lighting...")
        self.subtitle.setAlignment(Qt.AlignCenter)
        self.subtitle.setStyleSheet(f"font-size:14px;color:{P['muted']};background:transparent;border:none;")
        self.status_lbl = QLabel("Ray tracing beams | lux heatmap | architectural AI")
        self.status_lbl.setAlignment(Qt.AlignCenter)
        self.status_lbl.setStyleSheet(f"font-size:12px;color:{P['cyan']};background:transparent;border:none;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 44, 30, 34)
        layout.addStretch()
        layout.addWidget(self.title)
        layout.addWidget(self.subtitle)
        layout.addWidget(self.status_lbl)
        layout.addStretch()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._animate)
        self.timer.start(90)

    def _animate(self) -> None:
        self._tick += 1
        dots = "." * ((self._tick % 4) + 1)
        self.subtitle.setText(f"Calculating luxury villa lighting{dots}")
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        outer = QRectF(8, 8, self.width() - 16, self.height() - 16)
        grad = QLinearGradient(outer.topLeft(), outer.bottomRight())
        grad.setColorAt(0, QColor("#111827"))
        grad.setColorAt(0.5, QColor("#172033"))
        grad.setColorAt(1, QColor("#070A11"))
        p.setPen(QPen(QColor(100, 130, 190, 120), 1))
        p.setBrush(grad)
        p.drawRoundedRect(outer, 22, 22)
        glow = QColor(61, 142, 240, 45 + (self._tick % 8) * 8)
        p.setPen(QPen(glow, 2))
        for i in range(6):
            y = 74 + i * 28
            p.drawLine(QPointF(84, y), QPointF(540, y + math.sin((self._tick + i) / 2) * 14))
        p.setPen(QPen(QColor(255, 215, 120, 130), 1.4))
        for i in range(5):
            x = 150 + i * 78
            p.drawEllipse(QPointF(x, 115 + math.sin((self._tick + i) / 3) * 12), 8, 8)
        p.end()


class LightingApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.room = RoomModel()
        self.current_file: Optional[str] = None
        self._building = False
        self.state = ProjectStateManager()
        self.simulation = LightingSimulationService()
        self._last_snapshot: Optional[SimulationSnapshot] = None
        self.setWindowTitle(APP_NAME)
        self.resize(1560, 930)
        self.setMinimumSize(1180, 760)
        self.setLayoutDirection(Qt.RightToLeft)
        self._build_menu()
        self._build_toolbar()
        self._build_ui()
        self.recalculate()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("קובץ")
        actions = [
            ("חדש", self.new_project, "Ctrl+N"),
            ("פתח...", self.open_project, "Ctrl+O"),
            ("שמור", self.save_project, "Ctrl+S"),
            ("שמור בשם...", self.save_project_as, "Ctrl+Shift+S"),
            ("ייבא קטלוג גופים...", self.import_catalogue, ""),
            ("ייבא תכנית רקע...", self.import_floor_plan, ""),
            ("ייצא DXF...", self.export_dxf, ""),
            ("ייצא הצעת מחיר...", self.export_quote, ""),
            ("ייצא דוח צריכת חשמל...", self.export_energy_report, ""),
            ("יציאה", self.close, "Ctrl+Q"),
        ]
        for text, slot, shortcut in actions:
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(shortcut)
            a.triggered.connect(slot)
            file_menu.addAction(a)

    def _build_toolbar(self) -> None:
        tb = QToolBar("כלים", self)
        tb.setMovable(False)
        self.addToolBar(tb)
        for text, slot in [("חדש", self.new_project), ("פתח", self.open_project), ("שמור", self.save_project), ("קטלוג", self.import_catalogue), ("תכנית", self.import_floor_plan), ("DXF", self.export_dxf), ("הצעה", self.export_quote), ("חשמל", self.export_energy_report)]:
            a = QAction(text, self)
            a.triggered.connect(slot)
            tb.addAction(a)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        left = QTabWidget()
        left.setFixedWidth(430)
        left.addTab(self._scroll(self._build_basic_tab()), "בסיס")
        left.addTab(self._scroll(self._build_layers_tab()), "גופים ושכבות")
        left.addTab(self._scroll(self._build_energy_tab()), "צריכת חשמל")
        left.addTab(self._scroll(self._build_professional_tab()), "Professional")
        left.addTab(self._scroll(self._build_project_tab()), "Project")

        right = QSplitter(Qt.Vertical)
        self.renderer = RoomRenderer()
        self.renderer.spotMoved.connect(self._spots_moved)
        right.addWidget(self.renderer)
        self.results = QTabWidget()
        self.summary_text = QTextEdit(readOnly=True)
        self.point_text = QTextEdit(readOnly=True)
        self.compliance_text = QTextEdit(readOnly=True)
        self.catalogue_text = QTextEdit(readOnly=True)
        self.energy_text = QTextEdit(readOnly=True)
        self.zones_text = QTextEdit(readOnly=True)
        self.arch_ai_text = QTextEdit(readOnly=True)
        self.validation_text = QTextEdit(readOnly=True)
        self.pricing_text = QTextEdit(readOnly=True)
        self.preview3d_text = QTextEdit(readOnly=True)
        for w, name in [
            (self.summary_text, "סיכום"),
            (self.point_text, "חישוב נקודתי"),
            (self.compliance_text, "Compliance"),
            (self.zones_text, "Zones"),
            (self.arch_ai_text, "Architectural AI"),
            (self.validation_text, "Validation"),
            (self.catalogue_text, "קטלוג"),
            (self.energy_text, "צריכת חשמל"),
            (self.pricing_text, "Pricing"),
            (self.preview3d_text, "3D"),
        ]:
            self.results.addTab(w, name)
        right.addWidget(self.results)
        right.setSizes([590, 280])

        root.addWidget(left)
        root.addWidget(right, 1)
        self.status = QStatusBar()
        self.setStatusBar(self.status)

    def _scroll(self, widget: QWidget) -> QScrollArea:
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(widget)
        return sc

    def _build_basic_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        title_card, tl = make_card(APP_NAME, P["blue"])
        sub = QLabel("V7.1: Pendants | Point Lux | Heatmap | Catalogue | Pricing | EN Compliance")
        sub.setStyleSheet(f"color:{P['muted']}; background:transparent; border:none;")
        sub.setWordWrap(True)
        tl.addWidget(sub)
        layout.addWidget(title_card)

        room_card, room_l = make_card("פרמטרי חדר", P["blue"])
        form = QFormLayout()
        self.room_type = QComboBox()
        self.room_type.addItems(ROOM_TYPES)
        self.width_in = QDoubleSpinBox()
        self.width_in.setRange(1, 100)
        self.width_in.setValue(self.room.width)
        self.width_in.setSuffix(" m")
        self.length_in = QDoubleSpinBox()
        self.length_in.setRange(1, 100)
        self.length_in.setValue(self.room.length)
        self.length_in.setSuffix(" m")
        self.height_in = QDoubleSpinBox()
        self.height_in.setRange(1.5, 20)
        self.height_in.setValue(self.room.ceiling_height)
        self.height_in.setSuffix(" m")
        self.gypsum_drop_in = QDoubleSpinBox()
        self.gypsum_drop_in.setRange(0, 3)
        self.gypsum_drop_in.setDecimals(2)
        self.gypsum_drop_in.setValue(self.room.envelope.gypsum_drop_m)
        self.gypsum_drop_in.setSuffix(" m")
        self.wall_cladding_chk = QCheckBox("יש חיפוי על הקירות")
        self.wall_cladding_chk.setChecked(self.room.envelope.wall_cladding)
        self.cladding_tone = QComboBox()
        self.cladding_tone.addItems(["ללא חיפוי", "בהיר", "בינוני", "כהה", "עץ טבעי", "אבן / בטון", "לפי RAL / טמבור"])
        self.cladding_tone.setCurrentText(self.room.envelope.cladding_tone)
        self.tambour_ral = QComboBox()
        self.tambour_ral.addItems([
            "RAL 9016 / OW221P לבן",
            "RAL 9003 לבן אות",
            "RAL 1013 שמנת",
            "RAL 7044 אפור משי",
            "RAL 7035 אפור בהיר",
            "RAL 7016 אפור אנתרציט",
            "RAL 9005 שחור",
            "טמבור NWC 040",
            "טמבור IS 0187",
        ])
        self.tambour_ral.setCurrentText(self.room.envelope.tambour_ral)
        self.lux_in = QSpinBox()
        self.lux_in.setRange(0, 200000)
        self.lux_in.setSpecialValueText("אוטומטי")
        self.target_unit = QComboBox()
        self.target_unit.addItems(["lux", "lumens"])
        self.cct = QComboBox()
        self.cct.addItems(CCT_PRESETS.keys())
        form.addRow("סוג חדר:", self.room_type)
        form.addRow("רוחב:", self.width_in)
        form.addRow("אורך:", self.length_in)
        form.addRow("גובה:", self.height_in)
        form.addRow("הנמכת תקרה:", self.gypsum_drop_in)
        form.addRow(self.wall_cladding_chk)
        form.addRow("גוון / סוג חיפוי:", self.cladding_tone)
        form.addRow("RAL / טמבור:", self.tambour_ral)
        target_row = QHBoxLayout()
        target_row.addWidget(self.lux_in, 1)
        target_row.addWidget(self.target_unit)
        form.addRow("יעד:", target_row)
        form.addRow("CCT:", self.cct)
        room_l.addLayout(form)
        layout.addWidget(room_card)

        calc = QPushButton("חשב תאורה")
        calc.clicked.connect(self.recalculate)
        layout.addWidget(calc)
        layout.addStretch()
        for obj in [self.room_type, self.width_in, self.length_in, self.height_in, self.gypsum_drop_in, self.wall_cladding_chk, self.cladding_tone, self.tambour_ral, self.lux_in, self.target_unit, self.cct]:
            self._connect_change(obj)
        return w

    def _build_layers_tab(self) -> QWidget:
        w = QWidget()
        self.layers_layout = QVBoxLayout(w)
        self.layers_layout.setContentsMargins(8, 8, 8, 8)
        self._rebuild_layers_tab()
        return w

    def _rebuild_layers_tab(self) -> None:
        while self.layers_layout.count():
            item = self.layers_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        layer_card, ll = make_card("שכבות", P["green"])
        layer_colors = [P["blue"], P["amber"], P["purple"]]
        for i, layer in enumerate(self.room.layers):
            row = QHBoxLayout()
            chk = LuxurySwitch(layer.enabled, layer_colors[i % len(layer_colors)])
            name_lbl = QLabel(layer.name)
            name_lbl.setStyleSheet("background:transparent;border:none;font-weight:700;")
            spin = QSpinBox()
            spin.setRange(0, 100)
            spin.setValue(layer.intensity)
            spin.setSuffix("%")
            row.addWidget(chk)
            row.addWidget(name_lbl, 1)
            row.addWidget(spin)
            ll.addLayout(row)
            chk.toggled.connect(lambda state, idx=i: self._set_layer_enabled(idx, state))
            spin.valueChanged.connect(lambda v, idx=i: self._set_layer_intensity(idx, v))
        self.layers_layout.addWidget(layer_card)

        spot_card, spot_l = make_card("ספוטים - שכבת משימה", P["amber"])
        spot_hint = QLabel("כיבוי שכבת המשימה מכבה את הספוטים גם בחישוב, גם במפה וגם בצריכת החשמל.")
        spot_hint.setWordWrap(True)
        spot_hint.setStyleSheet(f"color:{P['muted']}; background:transparent; border:none;")
        spot_l.addWidget(spot_hint)
        form2 = QFormLayout()
        self.spot_fixture = QComboBox()
        self.spot_fixture.addItems(self.room.fixture_catalogue.keys())
        self.spot_fixture.setCurrentText(self.room.default_spot_fixture)
        self.beam = QComboBox()
        self.beam.addItems([f"{x} deg" for x in BEAM_ANGLES])
        self.beam.setCurrentText(f"{self.room.beam_angle} deg")
        self.offset = QDoubleSpinBox()
        self.offset.setRange(0, 10)
        self.offset.setValue(self.room.wall_offset)
        self.offset.setSuffix(" m")
        self.spot_qty = QSpinBox()
        self.spot_qty.setRange(0, 999)
        self.spot_qty.setSpecialValueText("אוטומטי")
        self.spot_qty.setValue(self.room.spot_quantity_override or 0)
        self.heatmap_chk = QCheckBox("הצג מפת חום")
        self.heatmap_chk.setChecked(self.room.show_heatmap)
        self.point_chk = QCheckBox("הצג ערכי נקודה בטקסט")
        self.point_chk.setChecked(self.room.show_point_values)
        form2.addRow("גוף ספוט:", self.spot_fixture)
        form2.addRow("זווית:", self.beam)
        form2.addRow("מרחק קיר:", self.offset)
        form2.addRow("כמות:", self.spot_qty)
        form2.addRow(self.heatmap_chk)
        form2.addRow(self.point_chk)
        spot_l.addLayout(form2)
        spot_actions = QHBoxLayout()
        self.fit_spots_btn = QPushButton("התאם ליעד")
        self.fit_spots_btn.setObjectName("amber")
        self.reset_spots_btn = secondary_button("אפס גרירה")
        self.fit_spots_btn.clicked.connect(self._fit_spots_to_target)
        self.reset_spots_btn.clicked.connect(self._reset_spots)
        spot_actions.addWidget(self.fit_spots_btn)
        spot_actions.addWidget(self.reset_spots_btn)
        spot_l.addLayout(spot_actions)
        self.layers_layout.addWidget(spot_card)
        for obj in [self.spot_fixture, self.beam, self.offset, self.spot_qty, self.heatmap_chk, self.point_chk]:
            self._connect_change(obj)
        self._sync_spot_controls_enabled()

        profile_card, pl = make_card("פרופיל LED", P["blue"])
        self.profile_enabled = LuxurySwitch(self.room.profiles[0].enabled, P["blue"])
        self.profile_shape = QComboBox()
        self.profile_shape.addItems(["Linear", "L shape", "U shape", "Rectangle", "Custom polyline", "Perimeter"])
        self.profile_shape.setCurrentText(self.room.profiles[0].shape)
        self.profile_len = QDoubleSpinBox()
        self.profile_len.setRange(0.1, 200)
        self.profile_len.setValue(self.room.profiles[0].length_m)
        self.profile_len.setSuffix(" m")
        self.profile_width = QDoubleSpinBox()
        self.profile_width.setRange(0.005, 1.0)
        self.profile_width.setDecimals(3)
        self.profile_width.setValue(self.room.profiles[0].width_m)
        self.profile_width.setSuffix(" m")
        self.profile_lmm = QSpinBox()
        self.profile_lmm.setRange(50, 5000)
        self.profile_lmm.setValue(self.room.profiles[0].lm_per_m)
        self.profile_angle = QDoubleSpinBox()
        self.profile_angle.setRange(-180, 180)
        self.profile_angle.setValue(self.room.profiles[0].angle_deg)
        self.profile_lmm_hint = QLabel("")
        self.profile_lmm_hint.setStyleSheet(f"color:{P['muted']}; background:transparent; border:none;")
        self.profile_fit_lmm = secondary_button("חשב lm/m לשורה")
        self.profile_fit_lmm.clicked.connect(self._fit_profile_lmm)
        pf = QFormLayout()
        pf.addRow("פעיל:", self.profile_enabled)
        pf.addRow("צורה:", self.profile_shape)
        pf.addRow("אורך:", self.profile_len)
        pf.addRow("רוחב:", self.profile_width)
        pf.addRow("lm/m:", self.profile_lmm)
        pf.addRow("זווית:", self.profile_angle)
        pf.addRow("", self.profile_fit_lmm)
        pf.addRow("", self.profile_lmm_hint)
        pl.addLayout(pf)
        self.layers_layout.addWidget(profile_card)
        for obj in [self.profile_enabled, self.profile_shape, self.profile_len, self.profile_width, self.profile_lmm, self.profile_angle]:
            self._connect_change(obj)

        track_card, trl = make_card("מסלול מגנטי", P["gold"])
        self.track_enabled = LuxurySwitch(bool(self.room.tracks and self.room.tracks[0].enabled), P["gold"])
        self.track_shape = QComboBox()
        self.track_shape.addItems(["Linear", "L shape", "U shape", "Rectangle", "Custom segments"])
        self.track_shape.setCurrentText(self.room.tracks[0].shape if self.room.tracks else "Linear")
        self.track_len = QDoubleSpinBox()
        self.track_len.setRange(0.3, 50)
        self.track_len.setValue(self.room.tracks[0].length_m if self.room.tracks else 3)
        self.track_len.setSuffix(" m")
        self.track_width = QComboBox()
        self.track_width.addItems(["0.5 cm", "2.3 cm", "2.5 cm"])
        self.track_width.setCurrentText(f"{self.room.tracks[0].width_cm if self.room.tracks else 2.3:g} cm")
        self.track_fix = QComboBox()
        self.track_fix.addItems(self.room.fixture_catalogue.keys())
        self.track_fix.setCurrentText("ספוט מסלול 24deg")
        self.track_qty = QSpinBox()
        self.track_qty.setRange(0, 50)
        self.track_qty.setValue(len(self.room.tracks[0].fixtures) if self.room.tracks else 0)
        tf = QFormLayout()
        tf.addRow("פעיל:", self.track_enabled)
        tf.addRow("צורה:", self.track_shape)
        tf.addRow("אורך:", self.track_len)
        tf.addRow("רוחב מערכת:", self.track_width)
        tf.addRow("גוף:", self.track_fix)
        tf.addRow("כמות גופים:", self.track_qty)
        trl.addLayout(tf)
        self.layers_layout.addWidget(track_card)
        for obj in [self.track_enabled, self.track_shape, self.track_len, self.track_width, self.track_fix, self.track_qty]:
            self._connect_change(obj)

        pendant_card, pel = make_card("תלויי תקרה / Pendants", P["purple"])
        p0 = self.room.pendants[0]
        self.pendant_enabled = LuxurySwitch(p0.enabled, P["purple"])
        self.pendant_type = QComboBox()
        self.pendant_type.addItems(PENDANT_TYPES)
        self.pendant_type.setCurrentText(p0.pendant_type)
        self.pendant_fixture = QComboBox()
        self.pendant_fixture.addItems(self.room.fixture_catalogue.keys())
        self.pendant_fixture.setCurrentText(p0.fixture_type)
        self.pendant_qty = QSpinBox()
        self.pendant_qty.setRange(1, 40)
        self.pendant_qty.setValue(p0.quantity)
        self.pendant_drop = QDoubleSpinBox()
        self.pendant_drop.setRange(0.05, 10)
        self.pendant_drop.setValue(p0.drop_m)
        self.pendant_drop.setSuffix(" m")
        self.pendant_spacing = QDoubleSpinBox()
        self.pendant_spacing.setRange(0.1, 10)
        self.pendant_spacing.setValue(p0.spacing_m)
        self.pendant_spacing.setSuffix(" m")
        self.pendant_angle = QDoubleSpinBox()
        self.pendant_angle.setRange(-180, 180)
        self.pendant_angle.setValue(p0.angle_deg)
        self.pendant_x = QDoubleSpinBox()
        self.pendant_x.setRange(0, 1)
        self.pendant_x.setSingleStep(0.05)
        self.pendant_x.setValue(p0.x)
        self.pendant_y = QDoubleSpinBox()
        self.pendant_y.setRange(0, 1)
        self.pendant_y.setSingleStep(0.05)
        self.pendant_y.setValue(p0.y)
        pef = QFormLayout()
        pef.addRow("פעיל:", self.pendant_enabled)
        pef.addRow("סוג:", self.pendant_type)
        pef.addRow("גוף:", self.pendant_fixture)
        pef.addRow("כמות:", self.pendant_qty)
        pef.addRow("הנמכה מהתקרה:", self.pendant_drop)
        pef.addRow("מרווח:", self.pendant_spacing)
        pef.addRow("זווית שורה:", self.pendant_angle)
        pef.addRow("X יחסי:", self.pendant_x)
        pef.addRow("Y יחסי:", self.pendant_y)
        pel.addLayout(pef)
        self.layers_layout.addWidget(pendant_card)
        for obj in [self.pendant_enabled, self.pendant_type, self.pendant_fixture, self.pendant_qty, self.pendant_drop, self.pendant_spacing, self.pendant_angle, self.pendant_x, self.pendant_y]:
            self._connect_change(obj)

        ambient_card, aml = make_card("תאורת אווירה", P["cyan"])
        a0 = self.room.ambient
        self.ambient_enabled = LuxurySwitch(a0.enabled, P["cyan"])
        self.ambient_shape = QComboBox()
        self.ambient_shape.addItems(AMBIENT_SHAPES)
        self.ambient_shape.setCurrentText(a0.shape)
        self.ambient_len = QDoubleSpinBox()
        self.ambient_len.setRange(0.1, 200)
        self.ambient_len.setValue(a0.length_m)
        self.ambient_len.setSuffix(" m")
        self.ambient_lmm = QSpinBox()
        self.ambient_lmm.setRange(20, 5000)
        self.ambient_lmm.setValue(a0.lm_per_m)
        self.ambient_angle = QDoubleSpinBox()
        self.ambient_angle.setRange(-180, 180)
        self.ambient_angle.setValue(a0.angle_deg)
        self.ambient_lmm_hint = QLabel("")
        self.ambient_lmm_hint.setStyleSheet(f"color:{P['muted']}; background:transparent; border:none;")
        self.ambient_fit_lmm = secondary_button("חשב lm/m לאווירה")
        self.ambient_fit_lmm.clicked.connect(self._fit_ambient_lmm)
        amf = QFormLayout()
        amf.addRow("פעיל:", self.ambient_enabled)
        amf.addRow("צורת גוף:", self.ambient_shape)
        amf.addRow("אורך:", self.ambient_len)
        amf.addRow("lm/m:", self.ambient_lmm)
        amf.addRow("זווית:", self.ambient_angle)
        amf.addRow("", self.ambient_fit_lmm)
        amf.addRow("", self.ambient_lmm_hint)
        aml.addLayout(amf)
        self.layers_layout.addWidget(ambient_card)
        for obj in [self.ambient_enabled, self.ambient_shape, self.ambient_len, self.ambient_lmm, self.ambient_angle]:
            self._connect_change(obj)
        self.layers_layout.addStretch()

    def _build_energy_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        card, l = make_card("צריכת חשמל", P["green"])
        form = QFormLayout()
        self.energy_rate = QDoubleSpinBox()
        self.energy_rate.setRange(0, 20)
        self.energy_rate.setDecimals(2)
        self.energy_rate.setValue(self.room.electricity_rate)
        self.energy_rate.setPrefix("₪ ")
        self.energy_rate.setSuffix(" / kWh")
        form.addRow("תעריף חשמל:", self.energy_rate)
        l.addLayout(form)
        btn = QPushButton("חשב צריכת חשמל")
        btn.clicked.connect(self.recalculate)
        l.addWidget(btn)
        layout.addWidget(card)
        layout.addStretch()
        for obj in [self.energy_rate]:
            self._connect_change(obj)
        return w

    def _build_professional_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)

        heat_card, hl = make_card("Lux heatmap", P["cyan"])
        self.heatmap_opacity = QSpinBox()
        self.heatmap_opacity.setRange(15, 220)
        self.heatmap_opacity.setValue(self.room.heatmap_opacity)
        self.heatmap_opacity.setSuffix(" alpha")
        hf = QFormLayout()
        hf.addRow("Opacity:", self.heatmap_opacity)
        hl.addLayout(hf)
        layout.addWidget(heat_card)

        optics_card, ol = make_card("Fixture optics / beam analysis", P["amber"])
        self.show_beams_chk = QCheckBox("Show beam cones and floor projections")
        self.show_beams_chk.setChecked(self.room.optics.show_beams)
        self.beam_opacity = QSpinBox()
        self.beam_opacity.setRange(10, 180)
        self.beam_opacity.setValue(self.room.optics.beam_opacity)
        self.beam_opacity.setSuffix(" alpha")
        self.beam_type = QComboBox()
        self.beam_type.addItems(["Narrow", "Medium", "Wide"])
        self.beam_type.setCurrentText(self.room.optics.beam_type)
        self.optics_beam_angle = QSpinBox()
        self.optics_beam_angle.setRange(5, 160)
        self.optics_beam_angle.setValue(self.room.optics.default_beam_angle)
        self.optics_beam_angle.setSuffix(" deg")
        self.fixture_pan = QDoubleSpinBox()
        self.fixture_pan.setRange(-180, 180)
        self.fixture_pan.setValue(self.room.optics.functional_aim.pan_deg)
        self.fixture_tilt = QDoubleSpinBox()
        self.fixture_tilt.setRange(-60, 60)
        self.fixture_tilt.setValue(self.room.optics.functional_aim.tilt_deg)
        self.fixture_rotation = QDoubleSpinBox()
        self.fixture_rotation.setRange(-180, 180)
        self.fixture_rotation.setValue(self.room.optics.functional_aim.rotation_deg)
        of = QFormLayout()
        of.addRow(self.show_beams_chk)
        of.addRow("Beam opacity:", self.beam_opacity)
        of.addRow("Beam type:", self.beam_type)
        of.addRow("Beam angle:", self.optics_beam_angle)
        of.addRow("Pan:", self.fixture_pan)
        of.addRow("Tilt:", self.fixture_tilt)
        of.addRow("Rotation:", self.fixture_rotation)
        ol.addLayout(of)
        layout.addWidget(optics_card)

        zone_card, zl = make_card("Lighting zones", P["green"])
        self.show_zones_chk = QCheckBox("Show zones")
        self.show_zones_chk.setChecked(self.room.optics.show_zone_guides)
        self.show_helpers_chk = QCheckBox("Show helper grid / guides")
        self.show_helpers_chk.setChecked(self.room.optics.show_helper_guides)
        self.zone1_name = QLineEdit(self.room.zones[0].name)
        self.zone1_visible = QCheckBox("Zone 1 visible")
        self.zone1_visible.setChecked(self.room.zones[0].visible)
        self.zone1_locked = QCheckBox("Zone 1 locked")
        self.zone1_locked.setChecked(self.room.zones[0].locked)
        self.zone1_lux = QSpinBox()
        self.zone1_lux.setRange(50, 3000)
        self.zone1_lux.setValue(self.room.zones[0].lux_target)
        self.zone2_name = QLineEdit(self.room.zones[1].name if len(self.room.zones) > 1 else "Dining table")
        self.zone2_visible = QCheckBox("Zone 2 visible")
        self.zone2_visible.setChecked(self.room.zones[1].visible if len(self.room.zones) > 1 else True)
        self.zone2_locked = QCheckBox("Zone 2 locked")
        self.zone2_locked.setChecked(self.room.zones[1].locked if len(self.room.zones) > 1 else False)
        self.zone2_lux = QSpinBox()
        self.zone2_lux.setRange(50, 3000)
        self.zone2_lux.setValue(self.room.zones[1].lux_target if len(self.room.zones) > 1 else 300)
        zf = QFormLayout()
        zf.addRow(self.show_zones_chk)
        zf.addRow(self.show_helpers_chk)
        zf.addRow("Zone 1:", self.zone1_name)
        zf.addRow(self.zone1_visible)
        zf.addRow(self.zone1_locked)
        zf.addRow("Zone 1 target:", self.zone1_lux)
        zf.addRow("Zone 2:", self.zone2_name)
        zf.addRow(self.zone2_visible)
        zf.addRow(self.zone2_locked)
        zf.addRow("Zone 2 target:", self.zone2_lux)
        zl.addLayout(zf)
        layout.addWidget(zone_card)

        furniture_card, fl = make_card("Furniture objects", P["amber"])
        while len(self.room.furniture) < 2:
            self.room.furniture.append(FurnitureObject())
        self.dining_enabled = QCheckBox("Show dining table")
        self.dining_enabled.setChecked(self.room.furniture[0].enabled)
        self.kitchen_enabled = QCheckBox("Show kitchen island")
        self.kitchen_enabled.setChecked(self.room.furniture[1].enabled)
        self.dining_rotation = QDoubleSpinBox()
        self.dining_rotation.setRange(-180, 180)
        self.dining_rotation.setValue(self.room.furniture[0].rotation_deg)
        self.kitchen_rotation = QDoubleSpinBox()
        self.kitchen_rotation.setRange(-180, 180)
        self.kitchen_rotation.setValue(self.room.furniture[1].rotation_deg)
        ff = QFormLayout()
        ff.addRow(self.dining_enabled)
        ff.addRow("Dining rotation:", self.dining_rotation)
        ff.addRow(self.kitchen_enabled)
        ff.addRow("Island rotation:", self.kitchen_rotation)
        fl.addLayout(ff)
        self.hide_table_kitchen_btn = secondary_button("Hide table + kitchen overlays")
        self.hide_table_kitchen_btn.clicked.connect(self.hide_table_kitchen_overlays)
        fl.addWidget(self.hide_table_kitchen_btn)
        furniture_hint = QLabel("Uncheck table/island to remove them from the plan, validation and 3D preview.")
        furniture_hint.setWordWrap(True)
        furniture_hint.setStyleSheet(f"color:{P['muted']}; background:transparent; border:none;")
        fl.addWidget(furniture_hint)
        layout.addWidget(furniture_card)

        daylight_card, dl = make_card("Daylight simulation", P["amber"])
        self.daylight_enabled = QCheckBox("Enable daylight contribution")
        self.daylight_enabled.setChecked(self.room.daylight.enabled)
        self.window_w = QDoubleSpinBox()
        self.window_w.setRange(0.1, 20)
        self.window_w.setValue(self.room.daylight.window_width_m)
        self.window_w.setSuffix(" m")
        self.window_h = QDoubleSpinBox()
        self.window_h.setRange(0.1, 10)
        self.window_h.setValue(self.room.daylight.window_height_m)
        self.window_h.setSuffix(" m")
        self.window_orientation = QComboBox()
        self.window_orientation.addItems(["North", "East", "South", "West"])
        self.window_orientation.setCurrentText(self.room.daylight.orientation)
        self.daylight_time = QDoubleSpinBox()
        self.daylight_time.setRange(6, 20)
        self.daylight_time.setValue(self.room.daylight.time_of_day)
        self.daylight_time.setSuffix(":00")
        df = QFormLayout()
        df.addRow(self.daylight_enabled)
        df.addRow("Window width:", self.window_w)
        df.addRow("Window height:", self.window_h)
        df.addRow("Orientation:", self.window_orientation)
        df.addRow("Time:", self.daylight_time)
        dl.addLayout(df)
        layout.addWidget(daylight_card)

        curtain_card, cl = make_card("Curtain lighting", "#FF8A3D")
        self.curtain_enabled = QCheckBox("Enable curtain lighting")
        self.curtain_enabled.setChecked(self.room.curtain_lighting.enabled)
        self.curtain_wall = QComboBox()
        self.curtain_wall.addItems(["North", "South", "East", "West"])
        self.curtain_wall.setCurrentText(self.room.curtain_lighting.wall)
        self.curtain_len = QDoubleSpinBox()
        self.curtain_len.setRange(0.2, 50)
        self.curtain_len.setValue(self.room.curtain_lighting.length_m)
        self.curtain_len.setSuffix(" m")
        self.curtain_lmm = QSpinBox()
        self.curtain_lmm.setRange(50, 5000)
        self.curtain_lmm.setValue(self.room.curtain_lighting.lm_per_m)
        self.curtain_intensity = QSpinBox()
        self.curtain_intensity.setRange(0, 100)
        self.curtain_intensity.setValue(self.room.curtain_lighting.intensity)
        self.curtain_intensity.setSuffix("%")
        cf = QFormLayout()
        cf.addRow(self.curtain_enabled)
        cf.addRow("Wall:", self.curtain_wall)
        cf.addRow("Length:", self.curtain_len)
        cf.addRow("lm/m:", self.curtain_lmm)
        cf.addRow("Intensity:", self.curtain_intensity)
        cl.addLayout(cf)
        layout.addWidget(curtain_card)

        scenes_card, sl = make_card("Scenes", P["purple"])
        scene_buttons = QHBoxLayout()
        for scene in self.room.scenes:
            btn = secondary_button(scene.name)
            btn.clicked.connect(lambda _=False, s=scene: self.apply_scene(s.name))
            scene_buttons.addWidget(btn)
        sl.addLayout(scene_buttons)
        layout.addWidget(scenes_card)

        io_card, il = make_card("Plan import / export", P["blue"])
        import_btn = QPushButton("Import floor plan underlay")
        export_dxf_btn = secondary_button("Export DXF")
        quote_btn = secondary_button("Export quotation")
        import_btn.clicked.connect(self.import_floor_plan)
        export_dxf_btn.clicked.connect(self.export_dxf)
        quote_btn.clicked.connect(self.export_quote)
        io_row = QHBoxLayout()
        io_row.addWidget(import_btn)
        io_row.addWidget(export_dxf_btn)
        io_row.addWidget(quote_btn)
        il.addLayout(io_row)
        self.confirm_import_btn = secondary_button("Confirm AI zones")
        self.confirm_import_btn.clicked.connect(self.confirm_import_suggestions)
        il.addWidget(self.confirm_import_btn)
        layout.addWidget(io_card)

        for obj in [
            self.heatmap_opacity,
            self.show_beams_chk,
            self.beam_opacity,
            self.beam_type,
            self.optics_beam_angle,
            self.fixture_pan,
            self.fixture_tilt,
            self.fixture_rotation,
            self.show_zones_chk,
            self.show_helpers_chk,
            self.zone1_name,
            self.zone1_visible,
            self.zone1_locked,
            self.zone1_lux,
            self.zone2_name,
            self.zone2_visible,
            self.zone2_locked,
            self.zone2_lux,
            self.dining_enabled,
            self.kitchen_enabled,
            self.dining_rotation,
            self.kitchen_rotation,
            self.daylight_enabled,
            self.window_w,
            self.window_h,
            self.window_orientation,
            self.daylight_time,
            self.curtain_enabled,
            self.curtain_wall,
            self.curtain_len,
            self.curtain_lmm,
            self.curtain_intensity,
        ]:
            self._connect_change(obj)
        layout.addStretch()
        return w

    def _build_project_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        project_card, pl = make_card("Project dashboard metadata", P["blue"])
        self.project_name_in = QLineEdit(self.room.project_name)
        self.client_name_in = QLineEdit(self.room.client_name)
        self.company_name_in = QLineEdit(self.room.branding.company_name)
        self.logo_path_in = QLineEdit(self.room.branding.company_logo)
        pf = QFormLayout()
        pf.addRow("Project:", self.project_name_in)
        pf.addRow("Client:", self.client_name_in)
        pf.addRow("Company:", self.company_name_in)
        pf.addRow("Logo path:", self.logo_path_in)
        pl.addLayout(pf)
        layout.addWidget(project_card)

        pricing_card, prl = make_card("Default pricing", P["green"])
        self.labour_rate_in = QDoubleSpinBox()
        self.labour_rate_in.setRange(0, 5000)
        self.labour_rate_in.setValue(self.room.labour_rate)
        self.labour_rate_in.setPrefix("NIS ")
        self.labour_hours_in = QDoubleSpinBox()
        self.labour_hours_in.setRange(0, 1000)
        self.labour_hours_in.setValue(self.room.labour_hours)
        self.markup_in = QDoubleSpinBox()
        self.markup_in.setRange(0, 300)
        self.markup_in.setValue(self.room.material_markup_pct)
        self.markup_in.setSuffix("%")
        prf = QFormLayout()
        prf.addRow("Labor rate:", self.labour_rate_in)
        prf.addRow("Labor hours:", self.labour_hours_in)
        prf.addRow("Markup:", self.markup_in)
        prl.addLayout(prf)
        layout.addWidget(pricing_card)

        for obj in [self.project_name_in, self.client_name_in, self.company_name_in, self.logo_path_in, self.labour_rate_in, self.labour_hours_in, self.markup_in]:
            self._connect_change(obj)
        layout.addStretch()
        return w

    def _connect_change(self, obj) -> None:
        if hasattr(obj, "toggled"):
            obj.toggled.connect(self.recalculate)
        if hasattr(obj, "valueChanged"):
            obj.valueChanged.connect(self.recalculate)
        if hasattr(obj, "currentTextChanged"):
            obj.currentTextChanged.connect(self.recalculate)
        if hasattr(obj, "stateChanged"):
            obj.stateChanged.connect(self.recalculate)
        if hasattr(obj, "textChanged"):
            obj.textChanged.connect(self.recalculate)

    def _set_layer_enabled(self, idx: int, value: bool) -> None:
        self.room.layers[idx].enabled = value
        if idx == 1:
            self._sync_spot_controls_enabled()
        self.recalculate()

    def _set_layer_intensity(self, idx: int, value: int) -> None:
        self.room.layers[idx].intensity = value
        self.recalculate()

    def _sync_spot_controls_enabled(self) -> None:
        if not hasattr(self, "spot_fixture"):
            return
        enabled = self.room.layer(1).enabled
        for widget in [self.spot_fixture, self.beam, self.offset, self.spot_qty, self.fit_spots_btn, self.reset_spots_btn]:
            widget.setEnabled(enabled)

    def hide_table_kitchen_overlays(self) -> None:
        if hasattr(self, "dining_enabled"):
            self.dining_enabled.setChecked(False)
        if hasattr(self, "kitchen_enabled"):
            self.kitchen_enabled.setChecked(False)
        if hasattr(self, "zone1_visible"):
            self.zone1_visible.setChecked(False)
        if hasattr(self, "zone2_visible"):
            self.zone2_visible.setChecked(False)
        if hasattr(self, "show_zones_chk"):
            self.show_zones_chk.setChecked(False)
        self.recalculate()
        self.status.showMessage("Dining table, kitchen island and their zone overlays are hidden.")

    def confirm_import_suggestions(self) -> None:
        ArchitecturalUnderstandingApplier(self.room).confirm_suggestions()
        self._refresh_all_controls()
        self.status.showMessage("Architectural AI suggestions confirmed as editable lighting zones.")

    def _reset_spots(self) -> None:
        self.room.manual_spots = []
        self.spot_qty.setValue(0)
        self.recalculate()

    def _fit_spots_to_target(self) -> None:
        self._read_inputs()
        spot_lm = float(self.room.fixture_catalogue.get(self.room.default_spot_fixture, {}).get("lm", 800))
        required = self.room.target_lumens
        qty = max(1, math.ceil(required * 0.55 / max(spot_lm, 1)))
        self.room.manual_spots = []
        self.spot_qty.setValue(qty)
        self.recalculate()

    def _fit_profile_lmm(self) -> None:
        self._read_inputs()
        length = max(self.profile_len.value(), 0.1)
        lmm = max(50, min(5000, round(self.room.target_lumens * 0.45 / length)))
        self.profile_lmm.setValue(lmm)
        self.recalculate()

    def _fit_ambient_lmm(self) -> None:
        self._read_inputs()
        shape = self.ambient_shape.currentText()
        mult = 2 if shape == "L-shape" else 3 if shape == "U-shape" else 1
        length = max(self.ambient_len.value() * mult, 0.1)
        lmm = max(20, min(5000, round(self.room.target_lumens * 0.18 / length)))
        self.ambient_lmm.setValue(lmm)
        self.recalculate()

    def _read_inputs(self) -> None:
        self.room.room_type = self.room_type.currentText()
        self.room.width = self.width_in.value()
        self.room.length = self.length_in.value()
        self.room.ceiling_height = self.height_in.value()
        self.room.envelope.gypsum_drop_m = self.gypsum_drop_in.value()
        self.room.envelope.wall_cladding = self.wall_cladding_chk.isChecked()
        self.room.envelope.cladding_tone = self.cladding_tone.currentText()
        self.room.envelope.tambour_ral = self.tambour_ral.currentText()
        if self.room.envelope.wall_cladding:
            tone = self.room.envelope.cladding_tone
            if tone == "כהה":
                self.room.reflectance_walls = 0.25
            elif tone in ("אבן / בטון", "בינוני"):
                self.room.reflectance_walls = 0.45
            elif tone == "עץ טבעי":
                self.room.reflectance_walls = 0.38
            else:
                self.room.reflectance_walls = 0.62
        self.room.target_unit = self.target_unit.currentText()
        if self.room.target_unit == "lumens":
            self.room.lumens_override = self.lux_in.value() or None
            self.room.lux_override = None
        else:
            self.room.lux_override = self.lux_in.value() or None
            self.room.lumens_override = None
        self.room.cct_preset = self.cct.currentText()
        self.room.default_spot_fixture = self.spot_fixture.currentText()
        self.room.beam_angle = int(self.beam.currentText().split()[0])
        self.room.wall_offset = min(self.offset.value(), min(self.room.width, self.room.length) / 2 - 0.01)
        self.room.spot_quantity_override = self.spot_qty.value() or None
        self.room.show_heatmap = self.heatmap_chk.isChecked()
        self.room.show_point_values = self.point_chk.isChecked()
        self.room.profiles[0].enabled = self.profile_enabled.isChecked()
        self.room.profiles[0].shape = self.profile_shape.currentText()
        self.room.profiles[0].length_m = self.profile_len.value()
        self.room.profiles[0].width_m = self.profile_width.value()
        self.room.profiles[0].lm_per_m = self.profile_lmm.value()
        self.room.profiles[0].angle_deg = self.profile_angle.value()
        self.room.ambient.enabled = self.ambient_enabled.isChecked()
        self.room.ambient.shape = self.ambient_shape.currentText()
        self.room.ambient.length_m = self.ambient_len.value()
        self.room.ambient.lm_per_m = self.ambient_lmm.value()
        self.room.ambient.angle_deg = self.ambient_angle.value()
        if self.track_enabled.isChecked() or self.track_qty.value() > 0:
            if not self.room.tracks:
                self.room.tracks.append(MagneticTrack())
            t = self.room.tracks[0]
            t.enabled = self.track_enabled.isChecked()
            t.shape = self.track_shape.currentText()
            t.length_m = self.track_len.value()
            t.width_cm = float(self.track_width.currentText().split()[0])
            qty = self.track_qty.value()
            t.fixtures = [TrackFixture(self.track_fix.currentText(), (i + 1) / (qty + 1)) for i in range(qty)]
        elif self.room.tracks:
            self.room.tracks[0].enabled = False
            self.room.tracks[0].fixtures = []
        p0 = self.room.pendants[0]
        p0.enabled = self.pendant_enabled.isChecked()
        p0.pendant_type = self.pendant_type.currentText()
        p0.fixture_type = self.pendant_fixture.currentText()
        p0.quantity = self.pendant_qty.value()
        p0.drop_m = self.pendant_drop.value()
        p0.spacing_m = self.pendant_spacing.value()
        p0.angle_deg = self.pendant_angle.value()
        p0.x = self.pendant_x.value()
        p0.y = self.pendant_y.value()
        self.room.electricity_rate = self.energy_rate.value()
        if hasattr(self, "heatmap_opacity"):
            self.room.heatmap_opacity = self.heatmap_opacity.value()
            self.room.optics.show_beams = self.show_beams_chk.isChecked()
            self.room.optics.beam_opacity = self.beam_opacity.value()
            self.room.optics.beam_type = self.beam_type.currentText()
            self.room.optics.default_beam_angle = self.optics_beam_angle.value()
            self.room.optics.show_zone_guides = self.show_zones_chk.isChecked()
            self.room.optics.show_helper_guides = self.show_helpers_chk.isChecked()
            self.room.optics.functional_aim.pan_deg = self.fixture_pan.value()
            self.room.optics.functional_aim.tilt_deg = self.fixture_tilt.value()
            self.room.optics.functional_aim.rotation_deg = self.fixture_rotation.value()
            self.room.beam_angle = self.room.optics.default_beam_angle
            while len(self.room.zones) < 2:
                self.room.zones.append(LightingZone())
            self.room.zones[0].name = self.zone1_name.text() or "Zone 1"
            self.room.zones[0].visible = self.zone1_visible.isChecked()
            self.room.zones[0].locked = self.zone1_locked.isChecked()
            self.room.zones[0].lux_target = self.zone1_lux.value()
            self.room.zones[1].name = self.zone2_name.text() or "Zone 2"
            self.room.zones[1].visible = self.zone2_visible.isChecked()
            self.room.zones[1].locked = self.zone2_locked.isChecked()
            self.room.zones[1].lux_target = self.zone2_lux.value()
            while len(self.room.furniture) < 2:
                self.room.furniture.append(FurnitureObject())
            self.room.furniture[0].enabled = self.dining_enabled.isChecked()
            self.room.furniture[0].rotation_deg = self.dining_rotation.value()
            self.room.furniture[1].enabled = self.kitchen_enabled.isChecked()
            self.room.furniture[1].rotation_deg = self.kitchen_rotation.value()
            if not self.room.furniture[1].enabled and "kitchen" in self.room.zones[0].name.lower():
                self.room.zones[0].visible = False
                self.zone1_visible.setChecked(False)
            if not self.room.furniture[0].enabled and "dining" in self.room.zones[1].name.lower():
                self.room.zones[1].visible = False
                self.zone2_visible.setChecked(False)
            self.room.daylight.enabled = self.daylight_enabled.isChecked()
            self.room.daylight.window_width_m = self.window_w.value()
            self.room.daylight.window_height_m = self.window_h.value()
            self.room.daylight.orientation = self.window_orientation.currentText()
            self.room.daylight.time_of_day = self.daylight_time.value()
            self.room.curtain_lighting.enabled = self.curtain_enabled.isChecked()
            self.room.curtain_lighting.wall = self.curtain_wall.currentText()
            self.room.curtain_lighting.length_m = self.curtain_len.value()
            self.room.curtain_lighting.lm_per_m = self.curtain_lmm.value()
            self.room.curtain_lighting.intensity = self.curtain_intensity.value()
        if hasattr(self, "project_name_in"):
            self.room.project_name = self.project_name_in.text() or self.room.project_name
            self.room.client_name = self.client_name_in.text()
            self.room.branding.company_name = self.company_name_in.text() or self.room.branding.company_name
            self.room.branding.company_logo = self.logo_path_in.text()
            self.room.labour_rate = self.labour_rate_in.value()
            self.room.labour_hours = self.labour_hours_in.value()
            self.room.material_markup_pct = self.markup_in.value()
            self.room.branding.default_labour_rate = self.room.labour_rate
            self.room.branding.default_markup_pct = self.room.material_markup_pct

    def recalculate(self, *_args) -> None:
        if self._building:
            return
        try:
            self._read_inputs()
            snap = self.simulation.compute(self.room)
            self._last_snapshot = snap
            self.renderer.update_scene(self.room, snap.spots, snap.heatmap)
            self._render_summary(snap.avg_lux, snap.watts, snap.cri, snap.ugr, snap.planner, snap.lux)
            self._render_point_values(snap.heatmap)
            self._render_compliance(snap.lux)
            self._render_zones(snap.lux)
            self._render_architectural_ai()
            self._render_validation(snap.lux)
            self._render_catalogue()
            self._render_energy()
            self._render_pricing()
            self._render_3d_preview(snap.lux)
            self.state.mark_dirty()
            spot_status = f"{len(snap.spots)} ספוטים" if self.room.layer(1).enabled else "ספוטים כבויים"
            perf = f"{snap.elapsed_ms:.0f} ms"
            self.status.showMessage(f"{self.room.width:.1f}x{self.room.length:.1f}m | יעד {self.room.lux_target} lx | ממוצע {snap.avg_lux:.0f} lx | {spot_status} | UGR {snap.ugr} | CRI {snap.cri:.0f} | Sim {perf}")
        except Exception as exc:
            self.state.report_error(f"Recalculate failed: {exc}")
            QMessageBox.warning(self, "Simulation", f"Calculation failed safely:\n{exc}")

    def _render_summary(self, avg: float, watts: float, cri: float, ugr: float, planner: SpotlightPlanner, lux: LuxEngine) -> None:
        cols, rows, sx, sy = planner.grid_count()
        ratio = avg / max(self.room.lux_target, 1)
        color = P["green"] if 0.9 <= ratio <= 1.25 else P["amber"] if 0.75 <= ratio <= 1.5 else P["red"]
        spot_count = len(planner.active_positions()) if self.room.layer(1).enabled else 0
        spot_desc = f"{spot_count} ({cols}x{rows})" if self.room.layer(1).enabled else "כבוי"
        kwh_hour = watts / 1000
        cost_hour = kwh_hour * self.room.electricity_rate
        cost_day = cost_hour * 24
        cost_month = cost_day * 30
        target_label = f"{self.room.lumens_override:,.0f} lm" if self.room.target_unit == "lumens" and self.room.lumens_override else f"{self.room.lux_target} lx"
        if hasattr(self, "profile_lmm_hint"):
            self.profile_lmm_hint.setText(f"סהכ שורה: {self.room.profiles[0].total_lm:,.0f} lm | יעד מחושב: {self.room.target_lumens:,.0f} lm")
        if hasattr(self, "ambient_lmm_hint"):
            self.ambient_lmm_hint.setText(f"תאורת אווירה: {self.room.ambient.total_lm:,.0f} lm | {self.room.ambient.watts:.1f} W")
        self.summary_text.setHtml(f"""
<style>body{{direction:rtl;color:{P['text']};font-family:Segoe UI;line-height:1.7}} .v{{color:{P['green']};font-weight:700}} .w{{color:{P['amber']}}}</style>
<h3 style="color:{P['blue']}">סיכום תאורה V7.1</h3>
<div>חדר: <span class="v">{self.room.room_type}</span> | שטח: <span class="v">{self.room.area:.1f} m²</span> | CCT: <span class="v">{self.room.cct_kelvin}K</span></div>
<div>יעד: <span class="v">{target_label}</span> | ממוצע נקודתי: <span style="color:{color};font-weight:800">{avg:.0f} lx</span> ({ratio*100:.0f}%)</div>
<div>ספוטים: <span class="v">{spot_desc}</span> | מרווח X/Y: <span class="v">{sx}/{sy}m</span></div>
<div>לומן נדרש בשיטת Lumen Method: <span class="v">{lux.required_lumens():,.0f} lm</span></div>
<div>הספק: <span class="v">{watts:.0f} W</span> | LPD: <span class="v">{watts/max(self.room.area,0.01):.1f} W/m²</span></div>
<div>חשמל: <span class="v">{kwh_hour:.2f} kWh לשעה</span> | יום מלא: <span class="v">{kwh_hour*24:.2f} kWh</span> | חודש מלא: <span class="v">{kwh_hour*24*30:.1f} kWh</span></div>
<div>עלות משוערת: שעה <span class="v">₪{cost_hour:.2f}</span> | יום <span class="v">₪{cost_day:.2f}</span> | חודש <span class="v">₪{cost_month:.0f}</span></div>
<div>UGR: <span class="v">{ugr}</span> | CRI ממוצע: <span class="v">{cri:.0f}</span></div>
<hr>
<div class="w">החישוב הנקודתי משתמש ב-E = I·cos³θ/h² לכל מקור אור, כולל הנמכת פנדנטים.</div>
""")

    def _render_point_values(self, heat: List[List[float]]) -> None:
        vals = [v for row in heat for v in row]
        if not vals:
            return
        center = LuxEngine(self.room).point_lux(self.room.width / 2, self.room.length / 2)
        avg_val = sum(vals) / len(vals) if vals else 0
        uniformity = min(vals) / avg_val if avg_val > 0 else 0
        rows = ""
        if self.room.show_point_values:
            last = len(heat) - 1
            idxs = sorted(set([0, max(0, last // 4), max(0, last // 2), max(0, last * 3 // 4), last]))
            sample = [heat[i][j] for i in idxs for j in idxs]
            rows = "<br>".join(f"נקודה {i+1:02d}: {v:.0f} lx" for i, v in enumerate(sample))
        self.point_text.setHtml(f"""
<style>body{{direction:rtl;color:{P['text']};font-family:Segoe UI;line-height:1.7}} .v{{color:{P['green']};font-weight:700}}</style>
<h3 style="color:{P['cyan']}">חישוב נקודתי</h3>
<div>מרכז החדר: <span class="v">{center:.0f} lx</span></div>
<div>מינימום: <span class="v">{min(vals):.0f} lx</span> | מקסימום: <span class="v">{max(vals):.0f} lx</span> | ממוצע: <span class="v">{avg_val:.0f} lx</span></div>
<div>Uniformity U0: <span class="v">{uniformity:.2f}</span></div>
<hr>{rows or "סמן 'הצג ערכי נקודה בטקסט' כדי לראות דגימת 25 נקודות."}
""")

    def _render_compliance(self, lux: LuxEngine) -> None:
        comp = ComplianceEngine(self.room, lux)
        rows = "".join(
            f"<tr><td>{name}</td><td style='color:{P['green'] if ok else P['red']};font-weight:800'>{'PASS' if ok else 'FAIL'}</td><td>{detail}</td></tr>"
            for name, ok, detail in comp.checks()
        )
        self.compliance_text.setHtml(f"""
<style>body{{direction:rtl;color:{P['text']};font-family:Segoe UI}} table{{width:100%;border-collapse:collapse}} td{{border-bottom:1px solid {P['border']};padding:6px}}</style>
<h3 style="color:{P['green']}">Compliance</h3>
<table>{rows}</table>
<p>LEED-style efficiency credit estimate: <b style="color:{P['green']}">{comp.leed_score()} / 6</b></p>
""")

    def _render_zones(self, lux: LuxEngine) -> None:
        rows = "".join(
            f"<tr><td>{m['name']}</td><td>{m['target']} lx</td><td>{m['avg']:.0f} lx</td><td>{m['min']:.0f} lx</td><td>{m['uniformity']:.2f}</td><td style='color:{P['green'] if m['ok'] else P['red']};font-weight:800'>{'PASS' if m['ok'] else 'FAIL'}</td></tr>"
            for m in ZoneEngine(self.room, lux).metrics()
        )
        daylight = DaylightEngine(self.room).average_lux()
        self.zones_text.setHtml(f"""
<style>body{{direction:rtl;color:{P['text']};font-family:Segoe UI}} table{{width:100%;border-collapse:collapse}} td{{border-bottom:1px solid {P['border']};padding:6px}}</style>
<h3 style="color:{P['green']}">Zones</h3>
<table><tr><td>Zone</td><td>Target</td><td>Avg</td><td>Min</td><td>U0</td><td>Status</td></tr>{rows}</table>
<p>Estimated daylight contribution: <b style="color:{P['amber']}">{daylight:.0f} lx average</b></p>
""")

    def _render_architectural_ai(self) -> None:
        u = self.room.architectural_understanding
        if not u.source_path:
            self.arch_ai_text.setHtml(f"""
<style>body{{direction:rtl;color:{P['text']};font-family:Segoe UI;line-height:1.6}}</style>
<h3 style="color:{P['blue']}">Architectural AI</h3>
<p>Import a DXF, PDF, SVG or raster plan to stage architectural understanding. The workflow is AI Suggests -> User Confirms.</p>
""")
            return
        def rows(items: List[ImportInsight]) -> str:
            return "".join(f"<tr><td>{x.category}</td><td>{x.name}</td><td>{x.confidence:.0%}</td><td>{x.recommendation}</td></tr>" for x in items)
        detected = rows(u.doors + u.windows + u.furniture + u.zones + u.ceiling_features + u.lighting_opportunities)
        notes = "".join(f"<li>{x}</li>" for x in u.cleanup_notes)
        suggestions = "".join(f"<li>{x}</li>" for x in u.suggestions)
        status = "Needs user confirmation" if u.requires_confirmation else "Confirmed"
        self.arch_ai_text.setHtml(f"""
<style>body{{direction:rtl;color:{P['text']};font-family:Segoe UI;line-height:1.6}} table{{width:100%;border-collapse:collapse}} td{{border-bottom:1px solid {P['border']};padding:6px;vertical-align:top}}</style>
<h3 style="color:{P['cyan']}">Architectural AI Import Understanding</h3>
<p><b>Source:</b> {os.path.basename(u.source_path)} | <b>Status:</b> {status} | <b>Scale confidence:</b> {u.scale_confidence:.0%}</p>
<p>{self.room.floor_plan.analysis_summary}</p>
<table><tr><td>Type</td><td>Name</td><td>Confidence</td><td>Consultant recommendation</td></tr>{detected}</table>
<h4>Cleanup / scale notes</h4><ul>{notes}</ul>
<h4>Smart lighting suggestions</h4><ul>{suggestions}</ul>
<p style="color:{P['amber']}">AI Suggests -> User Confirms: use the Professional tab button “Confirm AI zones” only after reviewing these suggestions.</p>
""")

    def _render_validation(self, lux: LuxEngine) -> None:
        issues = ValidationEngine(self.room, lux).issues()
        advisor = AutoLayoutAdvisor(self.room, lux).suggestions()
        beam = BeamAnalysisEngine(self.room, lux).metrics()
        issue_rows = "".join(f"<tr><td>{name}</td><td>{why}</td><td>{fix}</td></tr>" for name, why, fix in issues)
        if not issue_rows:
            issue_rows = f"<tr><td colspan='3' style='color:{P['green']};font-weight:800'>No blocking validation issues detected.</td></tr>"
        tips = "".join(f"<li>{x}</li>" for x in advisor)
        self.validation_text.setHtml(f"""
<style>body{{direction:rtl;color:{P['text']};font-family:Segoe UI;line-height:1.6}} table{{width:100%;border-collapse:collapse}} td{{border-bottom:1px solid {P['border']};padding:6px;vertical-align:top}}</style>
<h3 style="color:{P['amber']}">Validation engine</h3>
<p>Beam analysis: <b>{beam['count']:.0f}</b> sources | average spread <b>{beam['avg_diameter']:.2f}m</b> | max overlap <b>{beam['max_overlap']:.0f}</b> | hotspots <b>{beam['hotspots']:.0f}</b> | shadow gaps <b>{beam['gaps']:.0f}</b></p>
<table><tr><td>Issue</td><td>Why</td><td>How to improve</td></tr>{issue_rows}</table>
<h4>Smart layout suggestions</h4><ul>{tips}</ul>
""")

    def _render_catalogue(self) -> None:
        library = FixtureLibraryEngine(self.room.fixture_catalogue)
        fixtures = library.filter(min_cri=0)
        rows = "".join(
            f"<tr><td>{'★ ' if d.get('favorite') else ''}{name}</td><td>{d.get('brand','-')}</td><td>{d.get('lm',0)}</td><td>{d.get('w',0)}</td><td>{d.get('cri',0)}</td><td>{d.get('cct',0)}K</td><td>{d.get('beam',0)}°</td><td>{d.get('datasheet','')}</td></tr>"
            for name, d in fixtures.items()
        )
        self.catalogue_text.setHtml(f"""
<style>body{{direction:rtl;color:{P['text']};font-family:Segoe UI}} table{{width:100%;border-collapse:collapse}} td{{border-bottom:1px solid {P['border']};padding:5px}}</style>
<h3 style="color:{P['blue']}">Fixture Catalogue</h3>
<table><tr><td>שם</td><td>מותג</td><td>lm</td><td>W</td><td>CRI</td><td>CCT</td><td>Beam</td><td>Datasheet</td></tr>{rows}</table>
""")

    def _render_energy(self) -> None:
        lux = LuxEngine(self.room)
        watts = lux.watts_total()
        kwh_hour = watts / 1000
        rows = [
            ("שעת עבודה", 1, kwh_hour),
            ("יום שלם דלוק", 24, kwh_hour * 24),
            ("חודש שלם דלוק", 24 * 30, kwh_hour * 24 * 30),
        ]
        html_rows = "".join(
            f"<tr><td>{name}</td><td>{hours:,}</td><td>{kwh:.2f} kWh</td><td>₪{kwh * self.room.electricity_rate:,.2f}</td></tr>"
            for name, hours, kwh in rows
        )
        self.energy_text.setHtml(f"""
<style>body{{direction:rtl;color:{P['text']};font-family:Segoe UI}} table{{width:100%;border-collapse:collapse}} td{{border-bottom:1px solid {P['border']};padding:7px}} .v{{color:{P['green']};font-weight:800}}</style>
<h3 style="color:{P['green']}">צריכת חשמל</h3>
<p>הספק מערכת פעילה: <span class="v">{watts:.0f} W</span> | תעריף: <span class="v">₪{self.room.electricity_rate:.2f}/kWh</span></p>
<table><tr><td>תרחיש</td><td>שעות</td><td>צריכה</td><td>עלות</td></tr>{html_rows}</table>
<p>החישוב מתייחס רק לשכבות וגופים פעילים כרגע.</p>
""")

    def _render_pricing(self) -> None:
        price = PricingEngine(self.room)
        totals = price.totals()
        electrical = ElectricalEngine(self.room, LuxEngine(self.room)).summary()
        rows = "".join(f"<tr><td>{name}</td><td>{qty}</td><td>{unit:.2f}</td><td>{total:.2f}</td></tr>" for name, qty, unit, total in price.line_items())
        self.pricing_text.setHtml(f"""
<style>body{{direction:rtl;color:{P['text']};font-family:Segoe UI}} table{{width:100%;border-collapse:collapse}} td{{border-bottom:1px solid {P['border']};padding:6px}}</style>
<h3 style="color:{P['green']}">Auto pricing and electrical load</h3>
<table><tr><td>Item</td><td>Qty</td><td>Unit</td><td>Total</td></tr>{rows}</table>
<p>Material: <b>{totals['material']:.2f}</b> | Markup: <b>{totals['markup']:.2f}</b> | Labor: <b>{totals['labour']:.2f}</b> | Total estimate: <b style="color:{P['green']}">{totals['total']:.2f}</b></p>
<p>Electrical load: <b>{electrical['watts']:.0f} W</b> | <b>{electrical['amps']:.2f} A</b> @ {electrical['voltage']:.0f}V | Recommended circuits: <b>{electrical['circuits']:.0f}</b></p>
<p>Energy score: <b style="color:{P['green']}">{electrical['efficiency_score']:.0f}/100</b> | Monthly estimate: <b>{electrical['monthly_kwh']:.1f} kWh</b> | CO2 estimate: <b>{electrical['co2_kg']:.1f} kg/month</b></p>
""")

    def _render_3d_preview(self, lux: LuxEngine) -> None:
        sources = lux.all_sources()
        rows = "".join(f"<li>{name}: x={x:.2f}m, y={y:.2f}m, mounting height={h:.2f}m</li>" for x, y, name, h, _ in sources[:40])
        furniture_rows = "".join(
            f"<li>{f.name}: {f.width_m:.2f}m x {f.length_m:.2f}m x {f.height_m:.2f}m, rotation {f.rotation_deg:.0f}deg</li>"
            for f in self.room.furniture
            if f.enabled
        )
        self.preview3d_text.setHtml(f"""
<style>body{{direction:rtl;color:{P['text']};font-family:Segoe UI;line-height:1.6}}</style>
<h3 style="color:{P['blue']}">Lightweight 3D preview data</h3>
<p>Room wireframe: {self.room.width:.1f}m x {self.room.length:.1f}m x {self.room.ceiling_height:.1f}m. This tab exposes the isometric data model for a future OpenGL/Qt3D viewport without changing project JSON.</p>
<h4>Furniture blocks</h4><ul>{furniture_rows or '<li>No active furniture blocks.</li>'}</ul>
<h4>Lighting sources</h4>
<ul>{rows}</ul>
""")

    def _spots_moved(self, spots: List[Tuple[float, float]]) -> None:
        self.room.manual_spots = spots
        self.recalculate()

    def new_project(self) -> None:
        self._building = True
        self.room = RoomModel()
        self.current_file = None
        self._building = False
        self._refresh_all_controls()

    def _refresh_all_controls(self) -> None:
        self.room_type.setCurrentText(self.room.room_type)
        self.width_in.setValue(self.room.width)
        self.length_in.setValue(self.room.length)
        self.height_in.setValue(self.room.ceiling_height)
        self.gypsum_drop_in.setValue(self.room.envelope.gypsum_drop_m)
        self.wall_cladding_chk.setChecked(self.room.envelope.wall_cladding)
        self.cladding_tone.setCurrentText(self.room.envelope.cladding_tone)
        self.tambour_ral.setCurrentText(self.room.envelope.tambour_ral)
        self.lux_in.setValue(self.room.lux_override or 0)
        self.target_unit.setCurrentText(self.room.target_unit)
        self.lux_in.setValue(self.room.lumens_override if self.room.target_unit == "lumens" and self.room.lumens_override else self.room.lux_override or 0)
        self.cct.setCurrentText(self.room.cct_preset)
        self.spot_fixture.clear()
        self.spot_fixture.addItems(self.room.fixture_catalogue.keys())
        self.spot_fixture.setCurrentText(self.room.default_spot_fixture)
        self.pendant_fixture.clear()
        self.pendant_fixture.addItems(self.room.fixture_catalogue.keys())
        self.track_fix.clear()
        self.track_fix.addItems(self.room.fixture_catalogue.keys())
        self.energy_rate.setValue(self.room.electricity_rate)
        if hasattr(self, "heatmap_opacity"):
            self.heatmap_opacity.setValue(self.room.heatmap_opacity)
            self.show_beams_chk.setChecked(self.room.optics.show_beams)
            self.beam_opacity.setValue(self.room.optics.beam_opacity)
            self.beam_type.setCurrentText(self.room.optics.beam_type)
            self.optics_beam_angle.setValue(self.room.optics.default_beam_angle)
            self.fixture_pan.setValue(self.room.optics.functional_aim.pan_deg)
            self.fixture_tilt.setValue(self.room.optics.functional_aim.tilt_deg)
            self.fixture_rotation.setValue(self.room.optics.functional_aim.rotation_deg)
            self.show_zones_chk.setChecked(self.room.optics.show_zone_guides)
            self.show_helpers_chk.setChecked(self.room.optics.show_helper_guides)
            while len(self.room.zones) < 2:
                self.room.zones.append(LightingZone())
            self.zone1_name.setText(self.room.zones[0].name)
            self.zone1_visible.setChecked(self.room.zones[0].visible)
            self.zone1_locked.setChecked(self.room.zones[0].locked)
            self.zone1_lux.setValue(self.room.zones[0].lux_target)
            self.zone2_name.setText(self.room.zones[1].name)
            self.zone2_visible.setChecked(self.room.zones[1].visible)
            self.zone2_locked.setChecked(self.room.zones[1].locked)
            self.zone2_lux.setValue(self.room.zones[1].lux_target)
            while len(self.room.furniture) < 2:
                self.room.furniture.append(FurnitureObject())
            self.dining_enabled.setChecked(self.room.furniture[0].enabled)
            self.dining_rotation.setValue(self.room.furniture[0].rotation_deg)
            self.kitchen_enabled.setChecked(self.room.furniture[1].enabled)
            self.kitchen_rotation.setValue(self.room.furniture[1].rotation_deg)
            self.daylight_enabled.setChecked(self.room.daylight.enabled)
            self.window_w.setValue(self.room.daylight.window_width_m)
            self.window_h.setValue(self.room.daylight.window_height_m)
            self.window_orientation.setCurrentText(self.room.daylight.orientation)
            self.daylight_time.setValue(self.room.daylight.time_of_day)
            self.curtain_enabled.setChecked(self.room.curtain_lighting.enabled)
            self.curtain_wall.setCurrentText(self.room.curtain_lighting.wall)
            self.curtain_len.setValue(self.room.curtain_lighting.length_m)
            self.curtain_lmm.setValue(self.room.curtain_lighting.lm_per_m)
            self.curtain_intensity.setValue(self.room.curtain_lighting.intensity)
        if hasattr(self, "project_name_in"):
            self.project_name_in.setText(self.room.project_name)
            self.client_name_in.setText(self.room.client_name)
            self.company_name_in.setText(self.room.branding.company_name)
            self.logo_path_in.setText(self.room.branding.company_logo)
            self.labour_rate_in.setValue(self.room.labour_rate)
            self.labour_hours_in.setValue(self.room.labour_hours)
            self.markup_in.setValue(self.room.material_markup_pct)
        self._rebuild_layers_tab()
        self.recalculate()

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "פתח פרויקט", "", "Lighting Design Project (*.ldp);;JSON (*.json);;All Files (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.room = RoomModel.from_dict(json.load(f))
            ModelGuard.sanitize_room(self.room)
            self.current_file = path
            self._refresh_all_controls()
            self.state.mark_saved()
        except Exception as exc:
            self.state.report_error(f"Open failed: {exc}")
            QMessageBox.critical(self, "שגיאה", f"לא ניתן לפתוח:\n{exc}")

    def save_project(self) -> None:
        if not self.current_file:
            self.save_project_as()
            return
        self._save_to(self.current_file)

    def save_project_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "שמור פרויקט", self.room.project_name + ".ldp", "Lighting Design Project (*.ldp);;JSON (*.json)")
        if path:
            if not os.path.splitext(path)[1]:
                path += ".ldp"
            self.current_file = path
            self._save_to(path)

    def _save_to(self, path: str) -> None:
        try:
            self._read_inputs()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.room.to_dict(), f, ensure_ascii=False, indent=2)
            self.state.mark_saved()
            self.status.showMessage(f"נשמר: {path}")
        except Exception as exc:
            self.state.report_error(f"Save failed: {exc}")
            QMessageBox.critical(self, "שגיאה", f"שמירה נכשלה:\n{exc}")

    def import_catalogue(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "ייבא קטלוג", "", "Catalogue (*.json *.csv);;JSON (*.json);;CSV (*.csv)")
        if not path:
            return
        try:
            if path.lower().endswith(".json"):
                with open(path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                rows = data.items() if isinstance(data, dict) else ((x.get("name", f"fixture_{i}"), x) for i, x in enumerate(data))
            else:
                with open(path, "r", encoding="utf-8-sig", newline="") as f:
                    rows = [(r.get("name") or r.get("שם") or f"fixture_{i}", r) for i, r in enumerate(csv.DictReader(f))]
            for name, raw in rows:
                self.room.fixture_catalogue[str(name)] = {
                    "lm": float(raw.get("lm", raw.get("lumens", 800))),
                    "w": float(raw.get("w", raw.get("watts", 8))),
                    "cri": float(raw.get("cri", 90)),
                    "beam": float(raw.get("beam", raw.get("beam_angle", 36))),
                    "cct": int(float(raw.get("cct", 3000))),
                    "brand": raw.get("brand", raw.get("מותג", "")),
                    "price": float(raw.get("price", raw.get("מחיר", 0))),
                    "datasheet": raw.get("datasheet", ""),
                    "favorite": str(raw.get("favorite", raw.get("מועדף", ""))).lower() in {"1", "true", "yes", "y"},
                }
            self._refresh_all_controls()
            QMessageBox.information(self, "הצלחה", "קטלוג הגופים נטען.")
        except Exception as exc:
            QMessageBox.critical(self, "שגיאה", f"ייבוא קטלוג נכשל:\n{exc}")

    def import_floor_plan(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "ייבא תכנית רקע", "", "Plans (*.dxf *.dwg *.svg *.pdf *.png *.jpg *.jpeg);;All Files (*)")
        if not path:
            return
        try:
            message = FloorPlanImportPipeline(self.room).attach_underlay(path)
            self.recalculate()
            QMessageBox.information(self, "Architectural AI Import", message + "\n\nReview the Architectural AI tab, then confirm generated zones only if they match the project intent.")
        except Exception as exc:
            self.state.report_error(f"Import failed: {exc}")
            QMessageBox.critical(self, "שגיאה", f"ייבוא תכנית נכשל:\n{exc}")

    def apply_scene(self, name: str) -> None:
        for scene in self.room.scenes:
            if scene.name == name:
                for idx, layer in enumerate(self.room.layers):
                    layer.intensity = int(scene.layer_intensities.get(str(idx), layer.intensity))
                self._rebuild_layers_tab()
                self.recalculate()
                self.status.showMessage(f"Scene applied: {name}")
                return

    def export_quote(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "ייצא הצעת מחיר", self.room.project_name + "_quote.txt", "Text (*.txt);;PDF (*.pdf)")
        if not path:
            return
        try:
            self._read_inputs()
            text = ProfessionalExporter(self.room).quotation_text()
            if path.lower().endswith(".pdf"):
                try:
                    from reportlab.lib.pagesizes import A4
                    from reportlab.pdfgen import canvas

                    c = canvas.Canvas(path, pagesize=A4)
                    y = 800
                    for line in text.splitlines():
                        c.drawString(40, y, line[:110])
                        y -= 16
                        if y < 40:
                            c.showPage()
                            y = 800
                    c.save()
                except Exception:
                    with open(path + ".txt", "w", encoding="utf-8") as f:
                        f.write(text)
                    raise RuntimeError("PDF export requires reportlab. A TXT fallback was saved next to the requested PDF.")
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            self.status.showMessage(f"Quotation exported: {path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export", str(exc))

    def export_dxf(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "ייצא DXF", self.room.project_name + ".dxf", "DXF (*.dxf)")
        if not path:
            return
        if not path.lower().endswith(".dxf"):
            path += ".dxf"
        try:
            self._read_inputs()
            ProfessionalExporter(self.room).write_basic_dxf(path)
            self.status.showMessage(f"DXF exported: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "שגיאה", f"ייצוא DXF נכשל:\n{exc}")

    def export_energy_report(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "ייצא דוח צריכת חשמל", "lighting_energy.txt", "Text (*.txt)")
        if not path:
            return
        lux = LuxEngine(self.room)
        watts = lux.watts_total()
        kwh_hour = watts / 1000
        lines = [
            f"{APP_NAME} - דוח צריכת חשמל",
            f"תאריך: {dt.datetime.now():%Y-%m-%d %H:%M}",
            f"פרויקט: {self.room.project_name}",
            "",
            f"הספק פעיל: {watts:.0f} W",
            f"תעריף: ₪{self.room.electricity_rate:.2f}/kWh",
            f"שעת עבודה: {kwh_hour:.2f} kWh | ₪{kwh_hour * self.room.electricity_rate:.2f}",
            f"יום שלם דלוק: {kwh_hour * 24:.2f} kWh | ₪{kwh_hour * 24 * self.room.electricity_rate:.2f}",
            f"חודש שלם דלוק: {kwh_hour * 24 * 30:.2f} kWh | ₪{kwh_hour * 24 * 30 * self.room.electricity_rate:.2f}",
        ]
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self.status.showMessage(f"דוח צריכת חשמל נשמר: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "שגיאה", f"ייצוא נכשל:\n{exc}")


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Lighting Design Pro")
    app.setStyleSheet(STYLESHEET)
    app.setLayoutDirection(Qt.RightToLeft)
    splash = PremiumStartupSplash()
    splash.show()
    win = LightingApp()
    win.setWindowOpacity(0.0)
    win.show()
    QTimer.singleShot(1100, splash.close)
    QTimer.singleShot(1150, lambda: win.setWindowOpacity(1.0))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
