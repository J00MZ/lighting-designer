# =============================================================================
#  LightStudio Pro — תוכנת תכנון תאורה מקצועית
#  גרסה 2.0 | Python + PySide6 | קובץ יחיד
#
#  הרצה:
#      pip install PySide6
#      python lighting_designer.py
# =============================================================================

import sys
import math
from dataclasses import dataclass, field
from typing import List, Optional

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QLineEdit, QComboBox, QPushButton, QFrame,
    QScrollArea, QSplitter, QGroupBox, QSlider, QSpinBox, QDoubleSpinBox,
    QTabWidget, QTextEdit, QSizePolicy, QStatusBar
)
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QTimer
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QLinearGradient,
    QRadialGradient, QPainterPath, QFontMetrics
)


# =============================================================================
#  FIXTURE LIBRARY
# =============================================================================

FIXTURE_LIBRARY = {
    "downlight": {
        "name": "Downlight",
        "lumens": 800,
        "watts": 10,
        "beam": 60,
    },
    "spot": {
        "name": "Spot",
        "lumens": 600,
        "watts": 8,
        "beam": 36,
    },
    "panel": {
        "name": "פאנל לד",
        "lumens": 3600,
        "watts": 36,
        "beam": 120,
    },
    "linear": {
        "name": "גוף תאורה ליניארי",
        "lumens": 1200,
        "watts": 12,
        "beam": 90,
    },
    "wallwasher": {
        "name": "מתקן קיר",
        "lumens": 900,
        "watts": 12,
        "beam": 20,
    },
    "strip": {
        "name": "פס לד",
        "lumens": 1000,
        "watts": 10,
        "beam": 120,
    },
}


# =============================================================================
#  DATA MODELS
# =============================================================================

@dataclass
class Room:
    """מודל חדר"""
    name: str = "חדר חדש"
    width: float = 5.0       # מטר
    length: float = 6.0      # מטר
    height: float = 2.7      # מטר
    room_type: str = "office"
    reflectance_ceiling: float = 0.70
    reflectance_walls: float = 0.50
    reflectance_floor: float = 0.20
    work_plane_height: float = 0.85

    @property
    def area(self) -> float:
        return self.width * self.length

    @property
    def room_index(self) -> float:
        """מקדם חדר (RI)"""
        h_eff = self.height - self.work_plane_height
        if h_eff <= 0:
            return 1.0
        return (self.width * self.length) / (h_eff * (self.width + self.length))


@dataclass
class LightingLayer:
    """שכבת תאורה"""
    name: str
    name_he: str
    percentage: float      # אחוז מסך הלומן
    color: str             # צבע לתצוגה
    lumens: float = 0.0
    watts: float = 0.0
    fixtures: int = 0


@dataclass
class Fixture:
    """גוף תאורה"""
    x: float
    y: float
    layer: str
    lumens: float
    watts: float
    fixture_type: str = "downlight"  # downlight / panel / strip / spot


@dataclass
class CalculationResult:
    """תוצאת חישוב מלאה"""
    avg_lux: float = 0.0
    required_lux: float = 0.0
    total_lumens: float = 0.0
    total_watts: float = 0.0
    room_index: float = 0.0
    utilization_factor: float = 0.0
    maintenance_factor: float = 0.80
    uniformity: float = 0.0
    efficacy: float = 0.0         # lm/W
    power_density: float = 0.0    # W/m²
    quality_score: float = 0.0
    layers: List[LightingLayer] = field(default_factory=list)
    fixtures: List[Fixture] = field(default_factory=list)
    strip_length: float = 0.0
    num_spotlights: int = 0
    energy_per_year: float = 0.0   # kWh
    passes_standard: bool = False
    standard_name: str = "EN 12464"


# =============================================================================
#  LUX STANDARDS
# =============================================================================

class LuxStandards:
    """
    תקני תאורה לפי EN 12464-1:2021 ו-IESNA RP
    """

    ROOM_TYPES = {
        "living_room":  {"name_he": "סלון",         "lux": 150,  "ugr_max": 22, "standard": "EN 12464"},
        "bedroom":      {"name_he": "חדר שינה",     "lux": 100,  "ugr_max": 19, "standard": "EN 12464"},
        "kitchen":      {"name_he": "מטבח",          "lux": 400,  "ugr_max": 22, "standard": "EN 12464"},
        "bathroom":     {"name_he": "חדר אמבטיה",   "lux": 200,  "ugr_max": 25, "standard": "EN 12464"},
        "office":       {"name_he": "משרד",          "lux": 500,  "ugr_max": 19, "standard": "EN 12464"},
        "conference":   {"name_he": "חדר ישיבות",   "lux": 500,  "ugr_max": 19, "standard": "EN 12464"},
        "classroom":    {"name_he": "כיתת לימוד",   "lux": 500,  "ugr_max": 19, "standard": "EN 12464"},
        "retail":       {"name_he": "חנות",          "lux": 800,  "ugr_max": 22, "standard": "EN 12464"},
        "supermarket":  {"name_he": "סופרמרקט",     "lux": 1000, "ugr_max": 22, "standard": "EN 12464"},
        "warehouse":    {"name_he": "מחסן",          "lux": 200,  "ugr_max": 25, "standard": "EN 12464"},
        "corridor":     {"name_he": "מסדרון",        "lux": 100,  "ugr_max": 28, "standard": "EN 12464"},
        "lobby":        {"name_he": "לובי / כניסה",  "lux": 300,  "ugr_max": 22, "standard": "EN 12464"},
        "restaurant":   {"name_he": "מסעדה",         "lux": 200,  "ugr_max": 22, "standard": "EN 12464"},
        "gym":          {"name_he": "חדר כושר",      "lux": 500,  "ugr_max": 25, "standard": "EN 12464"},
        "hospital":     {"name_he": "בית חולים",     "lux": 500,  "ugr_max": 19, "standard": "EN 12464"},
        "outdoor":      {"name_he": "חוץ",           "lux": 50,   "ugr_max": 25, "standard": "EN 13201"},
    }

    LAYER_DEFINITIONS = [
        LightingLayer("general",    "תאורה כללית",       0.60, "#F5A623"),
        LightingLayer("functional", "תאורה פונקציונלית", 0.30, "#4A9EFF"),
        LightingLayer("ambient",    "תאורת אווירה",      0.10, "#9B59B6"),
    ]

    @classmethod
    def get_required_lux(cls, room_type: str) -> float:
        return cls.ROOM_TYPES.get(room_type, {}).get("lux", 300)

    @classmethod
    def get_room_name_he(cls, room_type: str) -> str:
        return cls.ROOM_TYPES.get(room_type, {}).get("name_he", room_type)

    @classmethod
    def get_ugr_max(cls, room_type: str) -> int:
        return cls.ROOM_TYPES.get(room_type, {}).get("ugr_max", 22)


# =============================================================================
#  LIGHTING CALCULATOR
# =============================================================================

class LightingCalculator:
    """
    מנוע חישוב תאורה
    מיישם שיטת הלומן (CIE Lumen Method) + חישוב שכבות
    """

    MAINTENANCE_FACTOR = 0.80

    def get_utilization_factor(self, room: Room) -> float:
        """
        מקדם ניצולת (UF) מטבלאות CIE — אינטרפולציה פשוטה
        """
        ri = room.room_index
        rc = room.reflectance_ceiling
        rw = room.reflectance_walls

        # בסיס לפי RI
        if ri < 0.6:
            base = 0.28
        elif ri < 1.0:
            base = 0.35 + (ri - 0.6) * 0.15
        elif ri < 2.0:
            base = 0.41 + (ri - 1.0) * 0.10
        elif ri < 3.0:
            base = 0.51 + (ri - 2.0) * 0.06
        else:
            base = 0.57 + min((ri - 3.0) * 0.02, 0.08)

        # תוספת לפי החזרות
        bonus = rc * 0.12 + rw * 0.06
        return min(0.88, base + bonus)

    def calculate_total_lumens(self, room: Room, required_lux: float) -> float:
        """
        נוסחת הלומן:
        Φ_total = (E × A) / (UF × MF)
        """
        UF = self.get_utilization_factor(room)
        MF = self.MAINTENANCE_FACTOR
        return (required_lux * room.area) / (UF * MF)

    def calculate_layers(
        self, total_lumens: float, room: Room
    ) -> List[LightingLayer]:
        """חישוב שכבות תאורה עם נתוני גופים"""
        import copy
        layers = []

        layer_configs = [
            ("general",    "תאורה כללית",       0.60, "#F5A623", "panel",    90, 36),
            ("functional", "תאורה פונקציונלית", 0.30, "#4A9EFF", "downlight",60, 12),
            ("ambient",    "תאורת אווירה",      0.10, "#9B59B6", "strip",    100, 14),
        ]

        for name, name_he, pct, color, ftype, lm_per, w_per in layer_configs:
            layer_lumens = total_lumens * pct
            # הערכת מספר גופים
            fixtures_count = max(1, math.ceil(layer_lumens / (lm_per * 36)))
            layer_watts = fixtures_count * w_per

            layer = LightingLayer(
                name=name,
                name_he=name_he,
                percentage=pct,
                color=color,
                lumens=round(layer_lumens),
                watts=round(layer_watts),
                fixtures=fixtures_count
            )
            layers.append(layer)

        return layers

    def estimate_strip_length(
        self, room: Room, lumens_needed: float, lm_per_meter: float = 1000
    ) -> float:
        """חישוב אורך רצועת LED"""
        if lm_per_meter <= 0:
            return 0
        return round(lumens_needed / lm_per_meter, 1)

    def estimate_spotlights(
        self, room: Room, spacing: float = 1.5
    ) -> int:
        """הערכת מספר ספוטים לפי רווח"""
        cols = max(1, math.floor(room.width / spacing))
        rows = max(1, math.floor(room.length / spacing))
        return cols * rows

    def place_fixtures(
        self, room: Room, layers: List[LightingLayer]
    ) -> List[Fixture]:
        """
        הצבת גופי תאורה אוטומטית בחדר
        """
        fixtures = []

        for layer in layers:
            n = layer.fixtures
            if n == 0:
                continue

            lm_per = layer.lumens / n
            w_per  = layer.watts  / n

            if layer.name == "general":
                # רשת אחידה — תאורה כללית
                cols = max(1, round(math.sqrt(n * room.width / room.length)))
                rows = max(1, math.ceil(n / cols))
                x_step = room.width / (cols + 1)
                y_step = room.length / (rows + 1)
                for r in range(rows):
                    for c in range(cols):
                        if len([f for f in fixtures if f.layer == "general"]) >= n:
                            break
                        fixtures.append(Fixture(
                            x=(c + 1) * x_step,
                            y=(r + 1) * y_step,
                            layer="general",
                            lumens=lm_per,
                            watts=w_per,
                            fixture_type="panel"
                        ))

            elif layer.name == "functional":
                # ספוטים על קצוות
                margin = 0.6
                positions = []
                step = (room.width - 2 * margin) / max(1, n - 1) if n > 1 else 0
                for i in range(n):
                    positions.append((margin + i * step, room.length * 0.75))
                for i, (fx, fy) in enumerate(positions):
                    fixtures.append(Fixture(
                        x=fx, y=fy,
                        layer="functional",
                        lumens=lm_per,
                        watts=w_per,
                        fixture_type="downlight"
                    ))

            elif layer.name == "ambient":
                # רצועות LED לאורך הקירות
                # קיר עליון
                for i in range(n):
                    x = (i + 0.5) * room.width / n
                    fixtures.append(Fixture(
                        x=x, y=0.3,
                        layer="ambient",
                        lumens=lm_per,
                        watts=w_per,
                        fixture_type="strip"
                    ))

        return fixtures

    def calculate_quality_score(
        self,
        avg_lux: float,
        required_lux: float,
        layers: List[LightingLayer],
        room: Room
    ) -> float:
        """
        ציון איכות תאורה 0–100

        מבוסס על:
        - עמידה בדרישות לוקס (40%)
        - איזון שכבות (30%)
        - יעילות אנרגטית (30%)
        """
        # ציון לוקס
        if required_lux > 0:
            lux_ratio = avg_lux / required_lux
            if 0.9 <= lux_ratio <= 1.3:
                lux_score = 100
            elif lux_ratio < 0.9:
                lux_score = max(0, lux_ratio / 0.9 * 100)
            else:
                lux_score = max(60, 100 - (lux_ratio - 1.3) * 50)
        else:
            lux_score = 50

        # ציון איזון שכבות
        actual_pcts = [l.lumens for l in layers]
        total = sum(actual_pcts) or 1
        target_pcts = [0.60, 0.30, 0.10]
        balance_error = sum(
            abs(actual_pcts[i] / total - target_pcts[i])
            for i in range(len(layers))
        )
        balance_score = max(0, 100 - balance_error * 200)

        # ציון יעילות (W/m² — מטרה: < 10 למשרד)
        total_watts = sum(l.watts for l in layers)
        power_density = total_watts / room.area if room.area > 0 else 999
        if power_density <= 8:
            efficiency_score = 100
        elif power_density <= 12:
            efficiency_score = 100 - (power_density - 8) * 10
        else:
            efficiency_score = max(0, 60 - (power_density - 12) * 5)

        return round(lux_score * 0.4 + balance_score * 0.3 + efficiency_score * 0.3, 1)

    def run(self, room: Room, lm_per_meter: float = 1000) -> CalculationResult:
        """חישוב מלא"""
        required_lux = LuxStandards.get_required_lux(room.room_type)
        total_lumens = self.calculate_total_lumens(room, required_lux)
        uf = self.get_utilization_factor(room)

        layers = self.calculate_layers(total_lumens, room)
        fixtures = self.place_fixtures(room, layers)

        total_watts = sum(l.watts for l in layers)
        avg_lux = (total_lumens * uf * self.MAINTENANCE_FACTOR) / room.area

        # שכבת ambient → רצועות LED
        ambient_layer = next((l for l in layers if l.name == "ambient"), None)
        strip_length = 0.0
        if ambient_layer:
            strip_length = self.estimate_strip_length(
                room, ambient_layer.lumens, lm_per_meter
            )

        num_spots = self.estimate_spotlights(room)
        energy_per_year = total_watts * 8 * 250 / 1000  # kWh/שנה (8שעות*250ימים)
        quality = self.calculate_quality_score(avg_lux, required_lux, layers, room)

        return CalculationResult(
            avg_lux=round(avg_lux),
            required_lux=required_lux,
            total_lumens=round(total_lumens),
            total_watts=round(total_watts),
            room_index=round(room.room_index, 2),
            utilization_factor=round(uf, 2),
            maintenance_factor=self.MAINTENANCE_FACTOR,
            uniformity=round(0.55 + min(uf * 0.3, 0.35), 2),
            efficacy=round(total_lumens / total_watts, 1) if total_watts else 0,
            power_density=round(total_watts / room.area, 1),
            quality_score=quality,
            layers=layers,
            fixtures=fixtures,
            strip_length=strip_length,
            num_spotlights=num_spots,
            energy_per_year=round(energy_per_year),
            passes_standard=avg_lux >= required_lux * 0.9,
            standard_name="EN 12464-1"
        )


# =============================================================================
#  FIXTURE PLANNER
# =============================================================================

class FixturePlanner:
    """תכנון גופי תאורה ומיקומם"""

    FIXTURE_SPECS = {
        "panel":     {"icon": "▣", "color": "#F5A623", "lm": 3600, "w": 36, "beam": 120},
        "downlight": {"icon": "●", "color": "#4A9EFF", "lm": 900,  "w": 12, "beam": 60},
        "strip":     {"icon": "▬", "color": "#9B59B6", "lm": 1400, "w": 14, "beam": 150},
        "spot":      {"icon": "◎", "color": "#E74C3C", "lm": 600,  "w": 8,  "beam": 30},
    }

    @classmethod
    def get_fixture_color(cls, fixture_type: str) -> str:
        return cls.FIXTURE_SPECS.get(fixture_type, {}).get("color", "#FFFFFF")


# =============================================================================
#  ROOM CANVAS — ציור חדר וגופי תאורה
# =============================================================================

class RoomCanvas(QWidget):
    """
    Canvas לציור תוכנית החדר עם גופי תאורה ואפקטי אור
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.room: Optional[Room] = None
        self.result: Optional[CalculationResult] = None
        self.setMinimumSize(400, 380)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background-color: #0D1117; border-radius: 8px;")

    def update_data(self, room: Room, result: CalculationResult):
        self.room = room
        self.result = result
        self.update()

    def paintEvent(self, event):
        if not self.room or not self.result:
            self._draw_empty()
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        self._draw_scene(painter)
        painter.end()

    def _draw_empty(self):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0D1117"))
        painter.setPen(QColor("#2D3548"))
        painter.setFont(QFont("Arial", 14))
        painter.drawText(self.rect(), Qt.AlignCenter, "הזן נתוני חדר וחשב")
        painter.end()

    def _draw_scene(self, painter: QPainter):
        w = self.width()
        h = self.height()

        # רקע
        painter.fillRect(self.rect(), QColor("#0D1117"))

        # מרווחים
        margin = 48
        room_w = self.room.width
        room_l = self.room.length

        scale = min(
            (w - margin * 2) / room_w,
            (h - margin * 2) / room_l
        )

        ox = (w - room_w * scale) / 2
        oy = (h - room_l * scale) / 2

        # ── אפקטי אור (גלואו מגופים) ──────────────────
        for fixture in self.result.fixtures:
            fx = ox + fixture.x * scale
            fy = oy + fixture.y * scale

            color = QColor(FixturePlanner.get_fixture_color(fixture.fixture_type))
            glow = QRadialGradient(fx, fy, scale * 1.5)
            glow.setColorAt(0, QColor(color.red(), color.green(), color.blue(), 60))
            glow.setColorAt(1, QColor(0, 0, 0, 0))
            painter.setBrush(QBrush(glow))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(fx, fy), scale * 1.5, scale * 1.5)

        # ── רצפה ──────────────────────────────────────
        floor_brush = QLinearGradient(ox, oy, ox + room_w * scale, oy + room_l * scale)
        floor_brush.setColorAt(0, QColor("#16202A"))
        floor_brush.setColorAt(1, QColor("#1A2436"))
        painter.setBrush(QBrush(floor_brush))
        painter.setPen(QPen(QColor("#F5A623"), 2))
        painter.drawRect(int(ox), int(oy), int(room_w * scale), int(room_l * scale))

        # ── גריד ──────────────────────────────────────
        painter.setPen(QPen(QColor("#1E2530"), 1, Qt.DotLine))
        for x in range(1, int(room_w)):
            px = ox + x * scale
            painter.drawLine(int(px), int(oy), int(px), int(oy + room_l * scale))
        for y in range(1, int(room_l)):
            py = oy + y * scale
            painter.drawLine(int(ox), int(py), int(ox + room_w * scale), int(py))

        # ── גופי תאורה ────────────────────────────────
        for fixture in self.result.fixtures:
            fx = ox + fixture.x * scale
            fy = oy + fixture.y * scale
            color = QColor(FixturePlanner.get_fixture_color(fixture.fixture_type))

            r = max(5, min(14, scale * 0.22))

            # צל
            painter.setBrush(QBrush(QColor(0, 0, 0, 80)))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(fx + 2, fy + 2), r, r)

            # גוף
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(QColor("#FFFFFF"), 1.2))
            painter.drawEllipse(QPointF(fx, fy), r, r)

            # מרכז לבן
            painter.setBrush(QBrush(QColor(255, 255, 255, 200)))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(fx, fy), r * 0.35, r * 0.35)

        # ── מידות ─────────────────────────────────────
        painter.setPen(QColor("#8892A4"))
        painter.setFont(QFont("Arial", 9))

        # רוחב
        painter.drawText(
            int(ox + room_w * scale / 2 - 20), int(oy - 12),
            f"{room_w:.1f}מ׳"
        )
        # אורך
        painter.save()
        painter.translate(int(ox - 10), int(oy + room_l * scale / 2))
        painter.rotate(-90)
        painter.drawText(-20, 0, f"{room_l:.1f}מ׳")
        painter.restore()

        # ── כיתוב מספר גופים ──────────────────────────
        painter.setFont(QFont("Arial", 9, QFont.Bold))
        total_fix = len(self.result.fixtures)
        painter.setPen(QColor("#F5A623"))
        painter.drawText(
            int(ox + 6), int(oy + room_l * scale - 8),
            f"{total_fix} גופי תאורה"
        )

        # ── מקרא ──────────────────────────────────────
        legend_items = [
            ("▣ תאורה כללית",    "#F5A623"),
            ("● פונקציונלית",    "#4A9EFF"),
            ("▬ אווירה/רצועה",   "#9B59B6"),
        ]
        ly = int(oy + 8)
        painter.setFont(QFont("Arial", 9))
        for text, color in legend_items:
            painter.setPen(QColor(color))
            painter.drawText(int(ox + 8), ly, text)
            ly += 16


# =============================================================================
#  QUALITY GAUGE — מד ציון איכות
# =============================================================================

class QualityGauge(QWidget):
    """מד ציון איכות תאורה חצי-עיגול"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.score = 0.0
        self.setFixedHeight(120)
        self.setStyleSheet("background: transparent;")

    def set_score(self, score: float):
        self.score = score
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w / 2, h - 10
        r = min(w / 2 - 20, h - 20)

        # רקע חצי-עיגול
        path = QPainterPath()
        path.moveTo(cx - r, cy)
        path.arcTo(QRectF(cx - r, cy - r, 2 * r, 2 * r), 180, 180)
        path.lineTo(cx + r, cy)
        painter.fillPath(path, QColor("#1A1E27"))

        # קשת ציון
        angle_span = 180 * (self.score / 100)
        gradient = QLinearGradient(cx - r, cy, cx + r, cy)
        gradient.setColorAt(0, QColor("#E74C3C"))
        gradient.setColorAt(0.5, QColor("#F5A623"))
        gradient.setColorAt(1, QColor("#2ECC71"))

        pen = QPen(QBrush(gradient), 12, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(
            QRectF(cx - r + 6, cy - r + 6, 2 * (r - 6), 2 * (r - 6)),
            180 * 16,
            int(angle_span * 16)
        )

        # מחוג
        angle_rad = math.radians(180 - angle_span)
        needle_r = r - 10
        nx = cx + needle_r * math.cos(angle_rad)
        ny = cy - needle_r * math.sin(angle_rad)
        painter.setPen(QPen(QColor("#FFFFFF"), 2))
        painter.drawLine(QPointF(cx, cy), QPointF(nx, ny))

        # ציון
        painter.setPen(QColor("#F5A623"))
        painter.setFont(QFont("Arial", 22, QFont.Bold))
        score_text = f"{int(self.score)}"
        painter.drawText(
            QRectF(cx - 30, cy - 40, 60, 35),
            Qt.AlignCenter, score_text
        )

        # תווית
        painter.setPen(QColor("#8892A4"))
        painter.setFont(QFont("Arial", 9))
        painter.drawText(
            QRectF(cx - 40, cy - 12, 80, 16),
            Qt.AlignCenter, "ציון איכות"
        )

        painter.end()


# =============================================================================
#  STYLED WIDGETS — רכיבים מעוצבים
# =============================================================================

STYLE_DARK = """
QMainWindow {
    background-color: #0A0B0E;
}
QWidget {
    background-color: #0A0B0E;
    color: #E8EAF0;
    font-family: Arial;
}
QGroupBox {
    background-color: #13161C;
    border: 1px solid #1E2530;
    border-radius: 8px;
    margin-top: 14px;
    padding: 10px;
    font-weight: bold;
    font-size: 13px;
    color: #E8EAF0;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top right;
    padding: 0 8px;
    color: #F5A623;
    font-size: 13px;
}
QLabel {
    color: #8892A4;
    font-size: 12px;
}
QLabel#value_label {
    color: #E8EAF0;
    font-size: 13px;
    font-weight: bold;
}
QLabel#metric_value {
    color: #F5A623;
    font-size: 22px;
    font-weight: bold;
}
QLabel#metric_unit {
    color: #4A5568;
    font-size: 11px;
}
QLineEdit, QDoubleSpinBox, QSpinBox {
    background-color: #1A1E27;
    border: 1px solid #2D3548;
    border-radius: 5px;
    padding: 6px 10px;
    color: #E8EAF0;
    font-size: 13px;
    selection-background-color: #F5A623;
}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus {
    border-color: #F5A623;
}
QComboBox {
    background-color: #1A1E27;
    border: 1px solid #2D3548;
    border-radius: 5px;
    padding: 6px 10px;
    color: #E8EAF0;
    font-size: 13px;
}
QComboBox:focus {
    border-color: #F5A623;
}
QComboBox::drop-down {
    border: none;
    padding-left: 8px;
}
QComboBox QAbstractItemView {
    background-color: #1A1E27;
    border: 1px solid #2D3548;
    selection-background-color: #F5A623;
    selection-color: #000000;
}
QPushButton {
    background-color: #1A1E27;
    border: 1px solid #2D3548;
    border-radius: 6px;
    padding: 8px 16px;
    color: #E8EAF0;
    font-size: 13px;
    font-weight: bold;
}
QPushButton:hover {
    background-color: #1F2430;
    border-color: #F5A623;
    color: #F5A623;
}
QPushButton#primary_btn {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 #C8860A, stop:1 #F5A623);
    color: #000000;
    border: none;
    font-size: 14px;
    padding: 10px 24px;
}
QPushButton#primary_btn:hover {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 #F5A623, stop:1 #FFD700);
    color: #000000;
}
QTabWidget::pane {
    border: 1px solid #1E2530;
    border-radius: 6px;
    background-color: #13161C;
}
QTabBar::tab {
    background-color: #0A0B0E;
    color: #8892A4;
    padding: 8px 16px;
    border: 1px solid #1E2530;
    border-bottom: none;
    font-size: 12px;
}
QTabBar::tab:selected {
    background-color: #13161C;
    color: #F5A623;
    border-top: 2px solid #F5A623;
}
QScrollArea {
    border: none;
    background: transparent;
}
QTextEdit {
    background-color: #13161C;
    border: 1px solid #1E2530;
    border-radius: 6px;
    color: #8892A4;
    font-size: 11px;
    font-family: Courier New;
    padding: 8px;
}
QStatusBar {
    background-color: #111318;
    color: #8892A4;
    font-size: 11px;
    border-top: 1px solid #1E2530;
}
QSplitter::handle {
    background-color: #1E2530;
    width: 2px;
}
"""


def make_label(text: str, bold: bool = False, color: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if bold:
        lbl.setStyleSheet(f"font-weight: bold; font-size: 13px;" +
                          (f" color: {color};" if color else ""))
    elif color:
        lbl.setStyleSheet(f"color: {color};")
    return lbl


def make_input(value, min_v=0.1, max_v=999.9, decimals=1, suffix="") -> QDoubleSpinBox:
    sb = QDoubleSpinBox()
    sb.setRange(min_v, max_v)
    sb.setDecimals(decimals)
    sb.setValue(float(value))
    sb.setSuffix(f" {suffix}" if suffix else "")
    sb.setAlignment(Qt.AlignLeft)
    return sb


def metric_card(title: str, value: str, unit: str, color: str = "#F5A623") -> QFrame:
    frame = QFrame()
    frame.setStyleSheet(f"""
        QFrame {{
            background-color: #1A1E27;
            border: 1px solid #1E2530;
            border-radius: 8px;
            padding: 4px;
        }}
    """)
    layout = QVBoxLayout(frame)
    layout.setSpacing(2)
    layout.setContentsMargins(10, 8, 10, 8)

    title_lbl = QLabel(title)
    title_lbl.setStyleSheet("color: #4A5568; font-size: 10px; text-transform: uppercase;")
    title_lbl.setAlignment(Qt.AlignRight)

    value_lbl = QLabel(value)
    value_lbl.setStyleSheet(f"color: {color}; font-size: 20px; font-weight: bold;")
    value_lbl.setAlignment(Qt.AlignRight)
    value_lbl.setObjectName("metric_val")

    unit_lbl = QLabel(unit)
    unit_lbl.setStyleSheet("color: #4A5568; font-size: 10px;")
    unit_lbl.setAlignment(Qt.AlignRight)

    layout.addWidget(title_lbl)
    layout.addWidget(value_lbl)
    layout.addWidget(unit_lbl)

    frame._value_label = value_lbl
    return frame


# =============================================================================
#  LAYER BAR — פס שכבות תאורה
# =============================================================================

class LayerBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layers: List[LightingLayer] = []
        self.setFixedHeight(28)

    def set_layers(self, layers: List[LightingLayer]):
        self.layers = layers
        self.update()

    def paintEvent(self, event):
        if not self.layers:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        total = sum(l.lumens for l in self.layers) or 1
        x = 0
        w = self.width()
        h = self.height()
        for i, layer in enumerate(self.layers):
            seg_w = int(w * layer.lumens / total)
            if i == len(self.layers) - 1:
                seg_w = w - x
            color = QColor(layer.color)
            painter.fillRect(x, 0, seg_w, h, color)
            if seg_w > 40:
                painter.setPen(QColor(0, 0, 0, 180))
                painter.setFont(QFont("Arial", 9, QFont.Bold))
                painter.drawText(x + 4, 0, seg_w - 4, h, Qt.AlignVCenter | Qt.AlignLeft,
                                 f"{int(layer.percentage*100)}%")
            x += seg_w
        painter.end()


# =============================================================================
#  MAIN APPLICATION WINDOW
# =============================================================================

class LightingApp(QMainWindow):
    """
    חלון ראשי — LightStudio Pro
    """

    def __init__(self):
        super().__init__()
        self.calculator = LightingCalculator()
        self.current_result: Optional[CalculationResult] = None
        self.current_room: Optional[Room] = None

        self.setWindowTitle("LightStudio Pro — תכנון תאורה מקצועי 💡")
        self.setMinimumSize(1200, 750)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setStyleSheet(STYLE_DARK)

        self._build_ui()
        self._run_initial()

    # ── UI BUILD ──────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # כותרת עליונה
        main_layout.addWidget(self._build_header())

        # תוכן ראשי
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(2)

        # פאנל שמאל — קלט + תוצאות
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)

        tabs = QTabWidget()
        tabs.addTab(self._build_room_input_tab(), "🏢 נתוני חדר")
        tabs.addTab(self._build_advanced_tab(), "⚙️ הגדרות מתקדמות")
        left_layout.addWidget(tabs)

        # כפתור חשב
        calc_btn = QPushButton("⚡  חשב תאורה")
        calc_btn.setObjectName("primary_btn")
        calc_btn.setCursor(Qt.PointingHandCursor)
        calc_btn.clicked.connect(self._calculate)
        left_layout.addWidget(calc_btn)

        # תוצאות מהירות
        left_layout.addWidget(self._build_quick_results())

        splitter.addWidget(left_panel)

        # פאנל ימין — ויזואליזציה + פירוט
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 14, 14, 14)
        right_layout.setSpacing(10)

        # Canvas
        self.canvas = RoomCanvas()
        right_layout.addWidget(self.canvas, stretch=3)

        # פירוט שכבות + ציון
        right_layout.addWidget(self._build_layers_panel(), stretch=2)

        splitter.addWidget(right_panel)
        splitter.setSizes([420, 720])

        main_layout.addWidget(splitter, stretch=1)

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("מוכן לחישוב | LightStudio Pro v2.0")

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setFixedHeight(52)
        header.setStyleSheet("""
            QWidget {
                background-color: #111318;
                border-bottom: 1px solid #1E2530;
            }
        """)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 0, 20, 0)

        logo = QLabel("💡 LightStudio Pro")
        logo.setStyleSheet("""
            font-size: 18px;
            font-weight: 800;
            color: #F5A623;
            background: transparent;
        """)

        subtitle = QLabel("תכנון תאורה מקצועי | עברית ראשית")
        subtitle.setStyleSheet("color: #4A5568; font-size: 11px; background: transparent;")

        version = QLabel("v2.0 | EN 12464")
        version.setStyleSheet("color: #2D3548; font-size: 10px; background: transparent;")

        layout.addWidget(logo)
        layout.addStretch()
        layout.addWidget(subtitle)
        layout.addSpacing(20)
        layout.addWidget(version)
        return header

    def _build_room_input_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(10)

        # קבוצת מידות חדר
        room_group = QGroupBox("מידות החדר")
        room_grid = QGridLayout(room_group)
        room_grid.setSpacing(8)

        room_grid.addWidget(make_label("סוג חדר:"), 0, 1)
        self.room_type_combo = QComboBox()
        for key, val in LuxStandards.ROOM_TYPES.items():
            self.room_type_combo.addItem(
                f"{val['name_he']}  ({val['lux']} lux)", key
            )
        self.room_type_combo.setCurrentIndex(4)  # Office
        self.room_type_combo.currentIndexChanged.connect(self._on_room_type_change)
        room_grid.addWidget(self.room_type_combo, 0, 0)

        room_grid.addWidget(make_label("שם החדר:"), 1, 1)
        self.room_name_input = QLineEdit("חדר חדש")
        self.room_name_input.setAlignment(Qt.AlignRight)
        room_grid.addWidget(self.room_name_input, 1, 0)

        room_grid.addWidget(make_label("רוחב (מ׳):"), 2, 1)
        self.width_input = make_input(5.0, 1, 100, 1, "מ׳")
        room_grid.addWidget(self.width_input, 2, 0)

        room_grid.addWidget(make_label("אורך (מ׳):"), 3, 1)
        self.length_input = make_input(6.0, 1, 100, 1, "מ׳")
        room_grid.addWidget(self.length_input, 3, 0)

        room_grid.addWidget(make_label("גובה תקרה (מ׳):"), 4, 1)
        self.height_input = make_input(2.7, 1.8, 12, 1, "מ׳")
        room_grid.addWidget(self.height_input, 4, 0)

        layout.addWidget(room_group)

        # קבוצת החזרות
        refl_group = QGroupBox("ערכי החזרת אור (Reflectance)")
        refl_grid = QGridLayout(refl_group)
        refl_grid.setSpacing(8)

        refl_grid.addWidget(make_label("תקרה %:"), 0, 1)
        self.refl_ceiling = make_input(70, 0, 100, 0, "%")
        refl_grid.addWidget(self.refl_ceiling, 0, 0)

        refl_grid.addWidget(make_label("קירות %:"), 1, 1)
        self.refl_walls = make_input(50, 0, 100, 0, "%")
        refl_grid.addWidget(self.refl_walls, 1, 0)

        refl_grid.addWidget(make_label("רצפה %:"), 2, 1)
        self.refl_floor = make_input(20, 0, 100, 0, "%")
        refl_grid.addWidget(self.refl_floor, 2, 0)

        layout.addWidget(refl_group)

        # דרישת לוקס
        lux_group = QGroupBox("דרישת תאורה")
        lux_grid = QGridLayout(lux_group)

        lux_grid.addWidget(make_label("לוקס נדרש:"), 0, 1)
        self.lux_display = QLabel("500 lux")
        self.lux_display.setStyleSheet("color: #F5A623; font-size: 16px; font-weight: bold;")
        self.lux_display.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        lux_grid.addWidget(self.lux_display, 0, 0)

        lux_grid.addWidget(make_label("תקן:"), 1, 1)
        self.standard_display = QLabel("EN 12464-1")
        self.standard_display.setAlignment(Qt.AlignLeft)
        lux_grid.addWidget(self.standard_display, 1, 0)

        layout.addWidget(lux_group)
        layout.addStretch()

        return widget

    def _build_advanced_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(10)

        # רצועת LED
        strip_group = QGroupBox("רצועות LED / פרופיל קו")
        strip_grid = QGridLayout(strip_group)

        strip_grid.addWidget(make_label("לומן למטר:"), 0, 1)
        self.lm_per_meter = make_input(1000, 100, 5000, 0, "lm/m")
        strip_grid.addWidget(self.lm_per_meter, 0, 0)

        strip_grid.addWidget(make_label("הערה:"), 1, 1)
        hint = QLabel("אורך הרצועה מחושב אוטומטית")
        hint.setStyleSheet("color: #4A5568; font-size: 11px;")
        strip_grid.addWidget(hint, 1, 0)

        layout.addWidget(strip_group)

        # ספוטים
        spot_group = QGroupBox("הערכת ספוטים")
        spot_grid = QGridLayout(spot_group)

        spot_grid.addWidget(make_label("רווח בין ספוטים (מ׳):"), 0, 1)
        self.spot_spacing = make_input(1.5, 0.5, 5.0, 1, "מ׳")
        spot_grid.addWidget(self.spot_spacing, 0, 0)

        layout.addWidget(spot_group)

        # גובה עבודה
        work_group = QGroupBox("גובה מישור עבודה")
        work_grid = QGridLayout(work_group)
        work_grid.addWidget(make_label("גובה (מ׳):"), 0, 1)
        self.work_plane = make_input(0.85, 0, 2, 2, "מ׳")
        work_grid.addWidget(self.work_plane, 0, 0)
        layout.addWidget(work_group)

        # לוג
        log_group = QGroupBox("יומן חישוב")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(160)
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_group)

        layout.addStretch()
        return widget

    def _build_quick_results(self) -> QGroupBox:
        group = QGroupBox("תוצאות חישוב")
        grid = QGridLayout(group)
        grid.setSpacing(6)

        self.card_lux     = metric_card("לוקס ממוצע",     "—", "lux",  "#F5A623")
        self.card_lumens  = metric_card("סה״כ לומן",      "—", "lm",   "#4A9EFF")
        self.card_watts   = metric_card("סה״כ וואט",      "—", "W",    "#2ECC71")
        self.card_quality = metric_card("ציון איכות",     "—", "/100", "#9B59B6")

        grid.addWidget(self.card_lux,     0, 0)
        grid.addWidget(self.card_lumens,  0, 1)
        grid.addWidget(self.card_watts,   1, 0)
        grid.addWidget(self.card_quality, 1, 1)

        return group

    def _build_layers_panel(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # שכבות
        layers_group = QGroupBox("שכבות תאורה")
        layers_layout = QVBoxLayout(layers_group)

        self.layer_bar = LayerBar()
        layers_layout.addWidget(self.layer_bar)

        self.layer_labels = []
        colors = ["#F5A623", "#4A9EFF", "#9B59B6"]
        names  = ["🔆 תאורה כללית (60%)", "💼 פונקציונלית (30%)", "🌙 אווירה (10%)"]
        for i, (name, color) in enumerate(zip(names, colors)):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            lbl_name = QLabel(name)
            lbl_name.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
            lbl_val = QLabel("—")
            lbl_val.setStyleSheet("color: #E8EAF0; font-size: 11px;")
            lbl_val.setAlignment(Qt.AlignLeft)
            row_layout.addWidget(lbl_name)
            row_layout.addStretch()
            row_layout.addWidget(lbl_val)
            self.layer_labels.append(lbl_val)
            layers_layout.addWidget(row)

        # פס אנרגיה
        energy_lbl = QLabel("⚡ צריכת אנרגיה שנתית: —")
        energy_lbl.setStyleSheet("color: #4A5568; font-size: 11px; margin-top: 4px;")
        self.energy_label = energy_lbl
        layers_layout.addWidget(energy_lbl)

        # רצועת LED
        strip_lbl = QLabel("〰️ אורך רצועת LED: —")
        strip_lbl.setStyleSheet("color: #4A5568; font-size: 11px;")
        self.strip_label = strip_lbl
        layers_layout.addWidget(strip_lbl)

        layout.addWidget(layers_group, stretch=2)

        # ציון איכות
        quality_group = QGroupBox("ציון איכות")
        quality_layout = QVBoxLayout(quality_group)
        self.quality_gauge = QualityGauge()
        quality_layout.addWidget(self.quality_gauge)

        self.status_lbl = QLabel("לא חושב")
        self.status_lbl.setAlignment(Qt.AlignCenter)
        self.status_lbl.setStyleSheet("color: #4A5568; font-size: 11px;")
        quality_layout.addWidget(self.status_lbl)

        layout.addWidget(quality_group, stretch=1)

        return widget

    # ── LOGIC ─────────────────────────────────────────────────────────

    def _on_room_type_change(self, idx):
        room_type = self.room_type_combo.currentData()
        lux = LuxStandards.get_required_lux(room_type)
        self.lux_display.setText(f"{lux} lux")

    def _build_room(self) -> Room:
        room_type = self.room_type_combo.currentData()
        return Room(
            name=self.room_name_input.text() or "חדר",
            width=self.width_input.value(),
            length=self.length_input.value(),
            height=self.height_input.value(),
            room_type=room_type,
            reflectance_ceiling=self.refl_ceiling.value() / 100,
            reflectance_walls=self.refl_walls.value() / 100,
            reflectance_floor=self.refl_floor.value() / 100,
            work_plane_height=self.work_plane.value() if hasattr(self, 'work_plane') else 0.85,
        )

    def _calculate(self):
        room = self._build_room()
        lm_per_m = self.lm_per_meter.value() if hasattr(self, 'lm_per_meter') else 1000

        result = self.calculator.run(room, lm_per_m)
        self.current_room = room
        self.current_result = result

        self._update_ui(room, result)
        self._write_log(room, result)

    def _run_initial(self):
        """חישוב ראשוני עם ערכי ברירת מחדל"""
        QTimer.singleShot(100, self._calculate)

    def _update_ui(self, room: Room, result: CalculationResult):
        # כרטיסי מדדים
        self.card_lux._value_label.setText(str(result.avg_lux))
        self.card_lumens._value_label.setText(f"{result.total_lumens:,}")
        self.card_watts._value_label.setText(str(result.total_watts))
        self.card_quality._value_label.setText(str(result.quality_score))

        # שכבות
        self.layer_bar.set_layers(result.layers)
        for i, layer in enumerate(result.layers):
            if i < len(self.layer_labels):
                self.layer_labels[i].setText(
                    f"{layer.lumens:,} lm | {layer.watts} W | {layer.fixtures} גופים"
                )

        # אנרגיה
        self.energy_label.setText(
            f"⚡ צריכה שנתית (8שע/יום): {result.energy_per_year:,} kWh"
        )

        # רצועת LED
        self.strip_label.setText(
            f"〰️ אורך רצועת LED מומלץ: {result.strip_length} מ׳"
        )

        # ציון
        self.quality_gauge.set_score(result.quality_score)

        # סטטוס
        if result.passes_standard:
            self.status_lbl.setText("✅ עומד בתקן EN 12464")
            self.status_lbl.setStyleSheet("color: #2ECC71; font-size: 11px; font-weight: bold;")
        else:
            self.status_lbl.setText("⚠️ לא עומד בתקן")
            self.status_lbl.setStyleSheet("color: #E74C3C; font-size: 11px; font-weight: bold;")

        # Canvas
        self.canvas.update_data(room, result)

        # Status bar
        self.status.showMessage(
            f"חושב ✓  |  שטח: {room.area:.1f} מ\"ר  |  RI: {result.room_index}  "
            f"|  UF: {result.utilization_factor}  |  יעילות: {result.efficacy} lm/W  "
            f"|  W/m²: {result.power_density}  |  גופים: {len(result.fixtures)}"
        )

    def _write_log(self, room: Room, result: CalculationResult):
        if not hasattr(self, 'log_text'):
            return
        log = f"""
══════════════════════════════
 חישוב תאורה — {room.name}
══════════════════════════════
חדר:         {room.width}×{room.length}×{room.height} מ׳
שטח:         {room.area:.1f} מ"ר
סוג:         {LuxStandards.get_room_name_he(room.room_type)}
מקדם חדר:    RI = {result.room_index}
מקדם ניצולת: UF = {result.utilization_factor}
מקדם תחזוקה: MF = {result.maintenance_factor}

לוקס נדרש:  {result.required_lux} lux
לוקס מחושב: {result.avg_lux} lux
{'✅ עומד בתקן' if result.passes_standard else '❌ לא עומד בתקן'}

סה"כ לומן:  {result.total_lumens:,} lm
סה"כ וואט:  {result.total_watts} W
W/m²:        {result.power_density}
יעילות:     {result.efficacy} lm/W

שכבות:
  כללית:     {result.layers[0].lumens:,} lm / {result.layers[0].watts}W
  פונקציונלית:{result.layers[1].lumens:,} lm / {result.layers[1].watts}W
  אווירה:    {result.layers[2].lumens:,} lm / {result.layers[2].watts}W

רצועת LED:  {result.strip_length} מ׳
ספוטים:     {result.num_spotlights}
אנרגיה/שנה: {result.energy_per_year:,} kWh

ציון איכות: {result.quality_score}/100
══════════════════════════════
""".strip()
        self.log_text.setPlainText(log)


# =============================================================================
#  ENTRY POINT
# =============================================================================

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("LightStudio Pro")
    app.setApplicationVersion("2.0")

    # גופן ברירת מחדל
    font = QFont("Arial", 12)
    app.setFont(font)

    window = LightingApp()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
