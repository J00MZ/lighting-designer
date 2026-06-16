# -*- coding: utf-8 -*-
"""
Lighting Design Pro - V8.0
==========================
Single-file PySide6 desktop application for architects and lighting designers.

Run:
    pip install PySide6 reportlab openpyxl
    python lighting_design_pro_v8.py

V8.0 — Full UX rebuild by cross-functional team.
    - Natural language project input
    - Design packages (Nordic, Hospitality, Gallery, Biophilic, Retail, Bedroom)
    - Scene timeline (hourly schedule)
    - Surface material reflectance calculation
    - IES/LDT photometry import
    - Professional PDF + client HTML export
    - Undo/Redo stack
    - License + activation system
    - Sticky notes, project snapshots
    - Grid snap + dimension lines
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


APP_NAME = "Lighting Design Pro V8.0"
APP_VERSION = "8.0.0"
AMBIENT_SHAPES = ["קו ישר", "L-shape", "U-shape", "היקפי"]

P = {
    "bg": "#0F1117",
    "surface": "#171A22",
    "card": "#1E2230",
    "card2": "#252A3A",
    "panel2": "#202638",
    "panel3": "#141A28",
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


# ──────────────────────────────────────────────────────────────────────
# INLINED: v8_patch.py
# ──────────────────────────────────────────────────────────────────────
# -*- coding: utf-8 -*-
"""
V8 PATCH MODULE — injected additions on top of V7.8 base
=========================================================
Adds:
  1. IES file parser (IESNA LM-63 / EULUMDAT .ldt)
  2. ZIP-based .ldp container (project + assets bundled)
  3. Undo/Redo command stack (Ctrl+Z / Ctrl+Y)
  4. Grid snap & dimension lines in viewport
  5. Dynamic heatmap resolution (area-adaptive, up to 40x40)
  6. Professional PDF export (logo, floor plan, compliance, quote)
  7. License / activation system (offline hash-based)
  8. Onboarding wizard (3-step: room → brief → fixtures)
  9. Update checker (version ping)
 10. AI-powered design review via Anthropic API
"""


import base64
import copy
import io
import json
import os
import re
import struct
import sys
import tempfile
import time
import zipfile
import hashlib
import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# ──────────────────────────────────────────────────────────
# 1. IES FILE PARSER  (IESNA LM-63 and .ldt EULUMDAT)
# ──────────────────────────────────────────────────────────

@dataclass
class IESPhotometry:
    """Parsed IES/LDT photometry data."""
    filename: str = ""
    luminous_flux_lm: float = 0.0
    input_watts: float = 0.0
    candela_values: List[float] = field(default_factory=list)   # flat list, row-major
    vertical_angles: List[float] = field(default_factory=list)  # 0=nadir … 180=zenith
    horizontal_angles: List[float] = field(default_factory=list)
    description: str = ""
    manufacturer: str = ""
    lamp_count: int = 1
    multiplier: float = 1.0
    width_m: float = 0.0
    length_m: float = 0.0
    height_m: float = 0.0

    # ── convenience ──────────────────────────────────────
    def peak_cd(self) -> float:
        return max(self.candela_values, default=0.0) * self.multiplier

    def efficacy_lm_per_w(self) -> float:
        return self.luminous_flux_lm / max(self.input_watts, 0.001)

    def beam_angle_deg(self) -> float:
        """Half-peak (50 % of peak) beam angle, approximate."""
        if not self.candela_values or not self.vertical_angles:
            return 36.0
        peak = self.peak_cd()
        if peak <= 0:
            return 36.0
        half = peak * 0.5
        # use first horizontal plane (index 0)
        n_v = len(self.vertical_angles)
        n_h = len(self.horizontal_angles)
        col0 = [self.candela_values[r * n_h] * self.multiplier for r in range(n_v)]
        crossing = 0.0
        for i in range(len(col0) - 1):
            if col0[i] >= half >= col0[i + 1]:
                t = (col0[i] - half) / max(col0[i] - col0[i + 1], 1e-9)
                crossing = self.vertical_angles[i] + t * (self.vertical_angles[i + 1] - self.vertical_angles[i])
                break
        return round(crossing * 2, 1) or 36.0

    def plane_profile(self) -> Tuple[List[float], List[float]]:
        """Return (vertical_angles, candela) averaged across all horizontal planes.

        We treat luminaires as rotationally symmetric for point calculations, so the
        candela at each vertical angle is the mean over the measured C-planes.
        """
        n_v = len(self.vertical_angles)
        n_h = len(self.horizontal_angles)
        if n_v == 0 or n_h == 0 or len(self.candela_values) < n_v * n_h:
            return list(self.vertical_angles), []
        cd = []
        for r in range(n_v):
            row = [self.candela_values[r * n_h + c] for c in range(n_h)]
            cd.append(sum(row) / n_h * self.multiplier)
        return list(self.vertical_angles), cd

    def intensity_at(self, theta_deg: float) -> float:
        """Interpolate luminous intensity (cd) at vertical angle theta (0 = nadir)."""
        angles, cd = self.plane_profile()
        if not cd:
            return 0.0
        t = abs(theta_deg)
        if t <= angles[0]:
            return max(0.0, cd[0])
        if t >= angles[-1]:
            return max(0.0, cd[-1])
        for i in range(len(angles) - 1):
            a0, a1 = angles[i], angles[i + 1]
            if a0 <= t <= a1:
                f = (t - a0) / max(a1 - a0, 1e-9)
                return max(0.0, cd[i] + f * (cd[i + 1] - cd[i]))
        return max(0.0, cd[-1])

    def to_fixture_dict(self) -> Dict:
        angles, cd = self.plane_profile()
        photometry = {"v": [round(a, 2) for a in angles], "cd": [round(c, 2) for c in cd]} if cd else None
        d = {
            "lm": round(self.luminous_flux_lm, 1),
            "w": round(self.input_watts, 1),
            "cri": 90,
            "beam": self.beam_angle_deg(),
            "cct": 3000,
            "brand": self.manufacturer or "IES Import",
            "price": 0,
            "datasheet": self.filename,
            "ies_peak_cd": round(self.peak_cd(), 1),
            "ies_efficacy": round(self.efficacy_lm_per_w(), 1),
        }
        if photometry:
            d["photometry"] = photometry
        return d


class IESParser:
    """Parse IESNA LM-63 (.ies) files into IESPhotometry objects."""

    @staticmethod
    def parse_file(path: str) -> IESPhotometry:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        return IESParser.parse_text(text, os.path.basename(path))

    @staticmethod
    def parse_text(text: str, filename: str = "") -> IESPhotometry:
        ph = IESPhotometry(filename=filename)
        lines = [l.strip() for l in text.splitlines()]

        # ── header keywords ──────────────────────────────
        for line in lines:
            if line.startswith("[TEST]"):
                ph.description = line[6:].strip()
            elif line.startswith("[MANUFAC]"):
                ph.manufacturer = line[9:].strip()
            elif line.startswith("[LUMCAT]") or line.startswith("[LAMP]"):
                pass  # optional

        # ── find TILT= line ──────────────────────────────
        tilt_idx = next((i for i, l in enumerate(lines) if l.startswith("TILT=")), None)
        if tilt_idx is None:
            raise ValueError("Not a valid IES file: missing TILT= keyword")

        # skip TILT block
        data_start = tilt_idx + 1
        tilt_val = lines[tilt_idx].split("=", 1)[1].strip().upper()
        if tilt_val not in ("NONE", "INCLUDE"):
            data_start = tilt_idx + 1
        if tilt_val == "INCLUDE":
            # skip 3 tilt data lines
            data_start = tilt_idx + 4

        # ── tokenise data block ───────────────────────────
        tokens: List[str] = []
        for line in lines[data_start:]:
            if line.startswith("[") or line.startswith("TILT"):
                continue
            tokens.extend(line.split())

        def nxt(it):
            return next(it)

        it = iter(tokens)
        try:
            lamp_count = int(nxt(it))
            lumens_per_lamp = float(nxt(it))
            multiplier = float(nxt(it))
            n_v = int(nxt(it))
            n_h = int(nxt(it))
            _photometric_type = nxt(it)
            _units_type = nxt(it)
            width = float(nxt(it))
            length = float(nxt(it))
            height = float(nxt(it))
            _ballast_factor = float(nxt(it))
            _future = nxt(it)
            input_watts = float(nxt(it))
            v_angles = [float(nxt(it)) for _ in range(n_v)]
            h_angles = [float(nxt(it)) for _ in range(n_h)]
            candela = [float(nxt(it)) for _ in range(n_v * n_h)]
        except StopIteration:
            raise ValueError("Truncated IES data block")

        ph.lamp_count = lamp_count
        ph.multiplier = multiplier
        ph.luminous_flux_lm = abs(lumens_per_lamp) * lamp_count if lumens_per_lamp > 0 else IESParser._integrate_flux(candela, v_angles, h_angles)
        ph.input_watts = input_watts * lamp_count
        ph.vertical_angles = v_angles
        ph.horizontal_angles = h_angles
        ph.candela_values = candela
        ph.width_m = abs(width)
        ph.length_m = abs(length)
        ph.height_m = abs(height)
        return ph

    @staticmethod
    def _integrate_flux(candela: List[float], v_angles: List[float], h_angles: List[float]) -> float:
        """Numerical integration over sphere zones (zonal flux method)."""
        if len(v_angles) < 2 or len(h_angles) < 1:
            return 0.0
        n_v, n_h = len(v_angles), len(h_angles)
        delta_phi = math.radians(360.0 / n_h)
        total = 0.0
        for i in range(n_v - 1):
            th1 = math.radians(v_angles[i])
            th2 = math.radians(v_angles[i + 1])
            zone_solid = 2 * math.pi * abs(math.cos(th1) - math.cos(th2))
            avg_cd = sum(candela[i * n_h + j] + candela[(i + 1) * n_h + j] for j in range(n_h)) / (2 * n_h)
            total += avg_cd * zone_solid
        return max(total, 0.0)


class LDTParser:
    """Parse EULUMDAT (.ldt) files into IESPhotometry objects."""

    @staticmethod
    def parse_file(path: str) -> IESPhotometry:
        with open(path, "r", encoding="latin-1", errors="replace") as fh:
            lines = [l.strip() for l in fh.readlines()]
        ph = IESPhotometry(filename=os.path.basename(path))
        try:
            ph.manufacturer = lines[0] if len(lines) > 0 else ""
            ph.description = lines[1] if len(lines) > 1 else ""
            # line 26 (0-indexed=25) = luminous flux
            if len(lines) > 25:
                ph.luminous_flux_lm = float(lines[25].split()[0])
            if len(lines) > 26:
                ph.input_watts = float(lines[26].split()[0])
            # C-planes count at line 3
            n_c = int(lines[3]) if len(lines) > 3 else 0
            # gamma angles count at line 6
            n_g = int(lines[6]) if len(lines) > 6 else 0
            # gamma angles start at line 17+n_c
            gamma_start = 17 + n_c
            ph.vertical_angles = [float(x) for x in lines[gamma_start:gamma_start + n_g]]
            c_start = 17
            ph.horizontal_angles = [float(lines[c_start + i]) if c_start + i < len(lines) else float(i * (360 / max(n_c, 1))) for i in range(n_c)]
            cd_start = gamma_start + n_g
            ph.candela_values = []
            for i in range(cd_start, min(cd_start + n_c * n_g, len(lines))):
                for tok in lines[i].split():
                    try:
                        ph.candela_values.append(float(tok))
                    except ValueError:
                        pass
        except (IndexError, ValueError) as exc:
            raise ValueError(f"Invalid EULUMDAT (.ldt) file: {exc}") from exc
        if ph.luminous_flux_lm <= 0 and not ph.candela_values:
            raise ValueError("Invalid EULUMDAT (.ldt) file: zero luminous flux and no candela data")
        if ph.luminous_flux_lm <= 0 and ph.candela_values:
            ph.luminous_flux_lm = IESParser._integrate_flux(
                ph.candela_values, ph.vertical_angles, ph.horizontal_angles)
        return ph


# ──────────────────────────────────────────────────────────
# 2. ZIP-BASED PROJECT CONTAINER
# ──────────────────────────────────────────────────────────

class ProjectContainer:
    """
    .ldp files are ZIP archives with:
      project.json  – RoomModel serialisation
      assets/       – underlay images, IES files, logos
      version.txt   – format version
    """
    FORMAT_VERSION = "8.0"

    @staticmethod
    def save(room_dict: Dict, path: str, asset_paths: Optional[List[str]] = None) -> None:
        # Work on a shallow copy so we can rewrite asset references to portable,
        # relative "assets/<basename>" paths without mutating the live model dict.
        room_dict = json.loads(json.dumps(room_dict, ensure_ascii=False))
        bundled: Dict[str, str] = {}
        if asset_paths:
            for ap in asset_paths:
                if ap and os.path.isfile(ap):
                    bundled[os.path.basename(ap)] = ap

        def _rel_if_bundled(value: str) -> str:
            base = os.path.basename(value) if value else ""
            return f"assets/{base}" if base in bundled else value

        fp = room_dict.get("floor_plan")
        if isinstance(fp, dict) and fp.get("path"):
            fp["path"] = _rel_if_bundled(fp["path"])
        br = room_dict.get("branding")
        if isinstance(br, dict) and br.get("company_logo"):
            br["company_logo"] = _rel_if_bundled(br["company_logo"])

        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("version.txt", ProjectContainer.FORMAT_VERSION)
            zf.writestr("project.json", json.dumps(room_dict, ensure_ascii=False, indent=2))
            for base, ap in bundled.items():
                zf.write(ap, "assets/" + base)

    @staticmethod
    def load(path: str) -> Tuple[Dict, Dict[str, bytes]]:
        """Returns (room_dict, {filename: bytes})."""
        assets: Dict[str, bytes] = {}
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            if "project.json" not in names:
                # Legacy: treat entire file as JSON
                raw = zf.read(names[0]) if names else b"{}"
                return json.loads(raw), {}
            room_dict = json.loads(zf.read("project.json"))
            for name in names:
                if name.startswith("assets/"):
                    assets[os.path.basename(name)] = zf.read(name)
        return room_dict, assets

    @staticmethod
    def is_zip(path: str) -> bool:
        try:
            return zipfile.is_zipfile(path)
        except Exception:
            return False


# ──────────────────────────────────────────────────────────
# 3. UNDO / REDO COMMAND STACK
# ──────────────────────────────────────────────────────────

class UndoStack:
    MAX_DEPTH = 50

    def __init__(self):
        self._stack: List[bytes] = []   # JSON snapshots
        self._idx: int = -1             # current position

    def _snap(self, room_dict: Dict) -> bytes:
        return json.dumps(room_dict, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def push(self, room_dict: Dict) -> None:
        snap = self._snap(room_dict)
        if self._idx >= 0 and self._stack[self._idx] == snap:
            return  # no change
        # drop redo history
        self._stack = self._stack[:self._idx + 1]
        self._stack.append(snap)
        if len(self._stack) > self.MAX_DEPTH:
            self._stack.pop(0)
        self._idx = len(self._stack) - 1

    def undo(self) -> Optional[Dict]:
        if self._idx > 0:
            self._idx -= 1
            return json.loads(self._stack[self._idx])
        return None

    def redo(self) -> Optional[Dict]:
        if self._idx < len(self._stack) - 1:
            self._idx += 1
            return json.loads(self._stack[self._idx])
        return None

    def can_undo(self) -> bool:
        return self._idx > 0

    def can_redo(self) -> bool:
        return self._idx < len(self._stack) - 1

    def clear(self) -> None:
        self._stack.clear()
        self._idx = -1


# ──────────────────────────────────────────────────────────
# 4. GRID SNAP HELPER
# ──────────────────────────────────────────────────────────

class GridSnap:
    """Snaps a coordinate to the nearest grid point."""
    DEFAULT_M = 0.10  # 10 cm grid

    def __init__(self, grid_m: float = DEFAULT_M, enabled: bool = True):
        self.grid_m = grid_m
        self.enabled = enabled

    def snap(self, x: float, y: float) -> Tuple[float, float]:
        if not self.enabled or self.grid_m <= 0:
            return x, y
        g = self.grid_m
        return round(round(x / g) * g, 6), round(round(y / g) * g, 6)

    def nearest_points(self, x: float, y: float, radius_m: float = 0.25) -> List[Tuple[float, float]]:
        g = self.grid_m
        cx = round(x / g) * g
        cy = round(y / g) * g
        pts = []
        for dx in (-g, 0, g):
            for dy in (-g, 0, g):
                px, py = cx + dx, cy + dy
                if math.hypot(px - x, py - y) <= radius_m:
                    pts.append((px, py))
        return pts


# ──────────────────────────────────────────────────────────
# 5. DYNAMIC HEATMAP RESOLUTION
# ──────────────────────────────────────────────────────────

def dynamic_grid_size(area_m2: float, quality: str = "Normal") -> int:
    """
    Returns grid N (NxN) based on room area and quality setting.
    quality: 'Fast' | 'Normal' | 'High' | 'Ultra'
    """
    base = {
        "Fast":   {0: 10, 30: 12, 60: 10, 120: 8},
        "Normal": {0: 20, 30: 22, 60: 20, 120: 16, 250: 14},
        "High":   {0: 30, 30: 32, 60: 28, 120: 24, 250: 20},
        "Ultra":  {0: 40, 30: 40, 60: 36, 120: 32, 250: 28},
    }.get(quality, {0: 20})
    result = 20
    for threshold in sorted(base.keys()):
        if area_m2 >= threshold:
            result = base[threshold]
    return max(8, min(result, 50))


# ──────────────────────────────────────────────────────────
# 6. PROFESSIONAL PDF EXPORT
# ──────────────────────────────────────────────────────────

class ProfessionalPDFExporter:
    """
    Produces a multi-page PDF:
      Page 1: Cover (logo, project info, KPI summary)
      Page 2: Floor plan vector + heatmap legend
      Page 3: Compliance table (EN 12464-1)
      Page 4: Fixture schedule & BOQ
      Page 5: Quotation
    Falls back to plain-text export if reportlab is absent.
    """

    def __init__(self, room, snapshot=None):
        self.room = room
        self.snap = snapshot

    def export(self, path: str) -> str:
        try:
            return self._export_reportlab(path)
        except ImportError:
            txt_path = path.replace(".pdf", "_fallback.txt")
            self._export_text(txt_path)
            return f"reportlab not installed — plain-text fallback saved:\n{txt_path}"

    def _export_reportlab(self, path: str) -> str:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                         Table, TableStyle, PageBreak, HRFlowable)
        from reportlab.platypus import KeepTogether
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

        W, H = A4
        doc = SimpleDocTemplate(path, pagesize=A4,
                                 leftMargin=18*mm, rightMargin=18*mm,
                                 topMargin=20*mm, bottomMargin=18*mm)
        styles = getSampleStyleSheet()
        story = []

        P_BLUE  = colors.HexColor("#3D8EF0")
        P_GREEN = colors.HexColor("#2ECC7A")
        P_AMBER = colors.HexColor("#F0A030")
        P_RED   = colors.HexColor("#EF4444")
        P_BG    = colors.HexColor("#0F1117")
        P_TEXT  = colors.HexColor("#F0F4FF")
        P_MUTED = colors.HexColor("#8A93A8")
        P_CARD  = colors.HexColor("#1E2230")

        from reportlab.platypus import Flowable
        from reportlab.lib.styles import ParagraphStyle

        h1 = ParagraphStyle("h1", parent=styles["Heading1"], textColor=P_BLUE,
                              fontSize=22, spaceAfter=4, fontName="Helvetica-Bold")
        h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=P_GREEN,
                              fontSize=14, spaceAfter=3, fontName="Helvetica-Bold")
        body = ParagraphStyle("body", parent=styles["Normal"], textColor=P_TEXT,
                               fontSize=10, leading=14)
        muted = ParagraphStyle("muted", parent=styles["Normal"], textColor=P_MUTED,
                                fontSize=9, leading=12)
        center_style = ParagraphStyle("center", parent=styles["Normal"],
                                       textColor=P_TEXT, fontSize=11, alignment=TA_CENTER)

        def kv(label, value, color=P_TEXT):
            return [Paragraph(f"<b>{label}</b>", muted),
                    Paragraph(str(value), ParagraphStyle("kv", parent=body, textColor=color))]

        # ── Page 1: Cover ──────────────────────────────────
        story.append(Spacer(1, 20*mm))
        story.append(Paragraph(self.room.branding.company_name, h1))
        story.append(HRFlowable(width="100%", thickness=1, color=P_BLUE))
        story.append(Spacer(1, 6*mm))
        story.append(Paragraph("Lighting Design Report", ParagraphStyle(
            "cover_title", parent=styles["Normal"],
            textColor=P_TEXT, fontSize=26, fontName="Helvetica-Bold")))
        story.append(Spacer(1, 4*mm))

        avg = self.snap.avg_lux if self.snap else 0
        tgt = self.room.lux_target
        watts = self.snap.watts if self.snap else 0
        cri = self.snap.cri if self.snap else 0
        ugr = self.snap.ugr if self.snap else 0
        uniformity = (self.snap.min_lux / avg) if (self.snap and avg > 0) else 0

        cover_data = [
            ["Project:", self.room.project_name,    "Client:", self.room.client_name],
            ["Room type:", self.room.room_type,       "Area:", f"{self.room.area:.1f} m²"],
            ["Target lux:", f"{tgt} lx",             "Achieved:", f"{avg:.0f} lx"],
            ["Watts:", f"{watts:.0f} W",              "LPD:", f"{watts/max(self.room.area,0.01):.1f} W/m²"],
            ["CRI avg:", f"{cri:.0f}",                "UGR est:", str(ugr)],
            ["Uniformity U0:", f"{uniformity:.2f}",  "CCT:", f"{self.room.cct_kelvin}K"],
            ["Date:", dt.datetime.now().strftime("%Y-%m-%d"), "Version:", "V8"],
        ]
        tbl = Table(cover_data, colWidths=[35*mm, 50*mm, 35*mm, 50*mm])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), P_CARD),
            ("TEXTCOLOR",  (0, 0), (-1, -1), P_TEXT),
            ("TEXTCOLOR",  (0, 0), (0, -1), P_MUTED),
            ("TEXTCOLOR",  (2, 0), (2, -1), P_MUTED),
            ("FONTNAME",   (0, 0), (-1, -1), "Helvetica"),
            ("FONTNAME",   (1, 0), (1, -1), "Helvetica-Bold"),
            ("FONTNAME",   (3, 0), (3, -1), "Helvetica-Bold"),
            ("FONTSIZE",   (0, 0), (-1, -1), 10),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [P_CARD, colors.HexColor("#252A3A")]),
            ("GRID",       (0, 0), (-1, -1), 0.3, colors.HexColor("#2A3048")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 8*mm))

        # KPI traffic light row
        def traffic(label, val, ok_color):
            return Paragraph(f"<font color='#{ok_color[1:]}'><b>{label}</b><br/>{val}</font>",
                              center_style)
        lux_ok = 0.9 <= avg / max(tgt, 1) <= 1.35
        uni_ok = uniformity >= uniformity_target(self.room.room_type)
        kpi_row = [[traffic("Lux", f"{avg:.0f} / {tgt}", "#2ECC7A" if lux_ok else "#F0A030"),
                    traffic("Uniformity", f"{uniformity:.2f}", "#2ECC7A" if uni_ok else "#EF4444"),
                    traffic("Watts", f"{watts:.0f} W", "#3D8EF0"),
                    traffic("UGR", str(ugr), "#2ECC7A" if ugr and int(str(ugr).split()[0]) < 22 else "#F0A030")]]
        kpi_tbl = Table(kpi_row, colWidths=[40*mm]*4)
        kpi_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#202638")),
            ("GRID",       (0, 0), (-1, -1), 0.5, colors.HexColor("#3A4468")),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(kpi_tbl)
        story.append(PageBreak())

        # ── Page 2: Floor plan (ASCII grid) ────────────────
        story.append(Paragraph("Floor Plan — Lux Distribution", h2))
        story.append(Spacer(1, 3*mm))

        if self.snap and self.snap.heatmap:
            heat = self.snap.heatmap
            n = len(heat)
            vals = [v for row in heat for v in row]
            hi = max(vals or [1])
            lo = min(vals or [0])

            def lux_color(v):
                ratio = (v - lo) / max(hi - lo, 1)
                if ratio < 0.5:
                    k = ratio / 0.5
                    return colors.Color(28/255 + 45/255*k, 96/255 + 118/255*k, 218/255 - 110/255*k)
                k = (ratio - 0.5) / 0.5
                return colors.Color((225 + 30*k)/255, (188 - 116*k)/255, (58 - 20*k)/255)

            cell_size = min(150*mm / n, 7*mm)
            grid_data = [[f"{heat[r][c]:.0f}" for c in range(n)] for r in range(n)]
            grid_tbl = Table(grid_data, colWidths=[cell_size]*n, rowHeights=[cell_size]*n)
            cell_colors = []
            for r in range(n):
                for c in range(n):
                    cell_colors.append(("BACKGROUND", (c, r), (c, r), lux_color(heat[r][c])))
            grid_tbl.setStyle(TableStyle([
                ("FONTSIZE", (0, 0), (-1, -1), max(4, int(cell_size * 0.35))),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.2, colors.HexColor("#0F1117")),
            ] + cell_colors))
            story.append(grid_tbl)
            story.append(Spacer(1, 3*mm))
            story.append(Paragraph(
                f"Grid: {n}×{n} points | Range: {lo:.0f}–{hi:.0f} lx | "
                f"Room: {self.room.width:.1f}×{self.room.length:.1f} m",
                muted))
        story.append(PageBreak())

        # ── Page 3: Compliance ──────────────────────────────
        story.append(Paragraph("EN 12464-1 Compliance Check", h2))
        story.append(Spacer(1, 3*mm))
        try:
            lux_eng = LuxEngine(self.room)
            comp = ComplianceEngine(self.room, lux_eng)
            checks = comp.checks()
        except Exception:
            checks = []

        comp_data = [["Check", "Result", "Detail"]]
        for name, ok, detail in checks:
            result_para = Paragraph(
                f"<font color='{'#2ECC7A' if ok else '#EF4444'}'><b>{'PASS' if ok else 'FAIL'}</b></font>",
                ParagraphStyle("comp", parent=body, alignment=TA_CENTER))
            comp_data.append([name, result_para, detail])

        if comp_data[1:]:
            comp_tbl = Table(comp_data, colWidths=[55*mm, 25*mm, 90*mm])
            comp_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), P_BLUE),
                ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
                ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [P_CARD, colors.HexColor("#252A3A")]),
                ("TEXTCOLOR",  (0, 1), (-1, -1), P_TEXT),
                ("GRID",       (0, 0), (-1, -1), 0.3, colors.HexColor("#2A3048")),
                ("FONTSIZE",   (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.append(comp_tbl)
        story.append(PageBreak())

        # ── Page 4: Fixture schedule ────────────────────────
        story.append(Paragraph("Fixture Schedule & Bill of Quantities", h2))
        story.append(Spacer(1, 3*mm))
        try:
            pricing = PricingEngine(self.room)
            items = pricing.line_items()
            totals = pricing.totals()
        except Exception:
            items = []
            totals = {}

        fix_data = [["Fixture / System", "Qty", "Unit Price ₪", "Total ₪", "lm", "W", "CRI"]]
        for name, qty, unit, total in items:
            info = self.room.fixture_catalogue.get(name, {})
            fix_data.append([
                name[:45],
                str(int(qty)),
                f"{unit:.2f}",
                f"{total:.2f}",
                str(info.get("lm", "—")),
                str(info.get("w", "—")),
                str(info.get("cri", "—")),
            ])

        if fix_data[1:]:
            fix_tbl = Table(fix_data, colWidths=[65*mm, 15*mm, 22*mm, 22*mm, 15*mm, 12*mm, 12*mm])
            fix_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), P_GREEN),
                ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
                ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [P_CARD, colors.HexColor("#252A3A")]),
                ("TEXTCOLOR",  (0, 1), (-1, -1), P_TEXT),
                ("GRID",       (0, 0), (-1, -1), 0.3, colors.HexColor("#2A3048")),
                ("FONTSIZE",   (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(fix_tbl)
            story.append(Spacer(1, 5*mm))
            totals_data = [
                ["Material subtotal:", f"₪{totals.get('material', 0):.2f}"],
                [f"Markup ({self.room.material_markup_pct:.0f}%):", f"₪{totals.get('markup', 0):.2f}"],
                ["Labour:", f"₪{totals.get('labour', 0):.2f}"],
                ["", ""],
                ["TOTAL ESTIMATE:", f"₪{totals.get('total', 0):.2f}"],
            ]
            tot_tbl = Table(totals_data, colWidths=[100*mm, 70*mm])
            tot_tbl.setStyle(TableStyle([
                ("TEXTCOLOR",  (0, 0), (-1, -1), P_TEXT),
                ("FONTNAME",   (4, 0), (-1, 4), "Helvetica-Bold"),
                ("FONTSIZE",   (0, 0), (-1, -1), 10),
                ("ALIGN",      (1, 0), (1, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(tot_tbl)

        story.append(Spacer(1, 8*mm))
        story.append(Paragraph(
            f"Generated by Lighting Design Pro V8  |  {dt.datetime.now():%Y-%m-%d %H:%M}  |  {self.room.branding.company_name}",
            muted))

        doc.build(story)
        return f"Professional PDF exported: {path}"

    def _export_text(self, path: str) -> None:
        lines = [
            f"Lighting Design Pro V8 — {self.room.branding.company_name}",
            f"Project: {self.room.project_name}  Client: {self.room.client_name}",
            f"Date: {dt.datetime.now():%Y-%m-%d}",
            "=" * 60,
        ]
        if self.snap:
            lines += [
                f"Lux target: {self.room.lux_target} lx   Achieved: {self.snap.avg_lux:.0f} lx",
                f"Watts: {self.snap.watts:.0f} W   CRI: {self.snap.cri:.0f}   UGR: {self.snap.ugr}",
            ]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))


# ──────────────────────────────────────────────────────────
# 7. LICENSE / ACTIVATION SYSTEM
# ──────────────────────────────────────────────────────────

class LicenseManager:
    """
    Offline hash-based license.
    Key format: XXXX-XXXX-XXXX-XXXX (16 hex chars + 4 dashes)
    Validation: sha256(machine_id + product_salt) first 16 chars == stripped key
    """
    PRODUCT_SALT = "LDP-V8-2025-IL"
    LICENSE_FILE = os.path.join(
        os.path.expanduser("~"), ".lighting_design_pro", "license.json")

    @staticmethod
    def machine_id() -> str:
        import uuid
        mid = str(uuid.getnode())
        return hashlib.sha256(mid.encode()).hexdigest()[:16].upper()

    @classmethod
    def validate(cls, key: str) -> Tuple[bool, str]:
        clean = key.upper().replace("-", "").replace(" ", "")
        if len(clean) != 16:
            return False, "Invalid key format (expected XXXX-XXXX-XXXX-XXXX)"
        expected = hashlib.sha256(
            (cls.machine_id() + cls.PRODUCT_SALT).encode()
        ).hexdigest()[:16].upper()
        if clean == expected:
            return True, "License valid ✓"
        # also accept "DEMO2025LIGHTING" as universal demo key
        if clean == "DEMO2025LIGHTING":
            return True, "Demo license active (trial mode)"
        return False, "License key does not match this machine."

    @classmethod
    def load_saved(cls) -> Optional[str]:
        try:
            with open(cls.LICENSE_FILE, "r") as fh:
                return json.load(fh).get("key")
        except Exception:
            return None

    @classmethod
    def save(cls, key: str) -> None:
        os.makedirs(os.path.dirname(cls.LICENSE_FILE), exist_ok=True)
        with open(cls.LICENSE_FILE, "w") as fh:
            json.dump({"key": key, "saved": dt.datetime.now().isoformat()}, fh)

    @classmethod
    def is_activated(cls) -> bool:
        key = cls.load_saved()
        if not key:
            return False
        ok, _ = cls.validate(key)
        return ok

    @classmethod
    def demo_mode(cls) -> bool:
        """True when running in demo/trial (universal key)."""
        key = cls.load_saved()
        return key == "DEMO2025LIGHTING"


# ──────────────────────────────────────────────────────────
# 8. UPDATE CHECKER
# ──────────────────────────────────────────────────────────

class UpdateChecker:
    LATEST_URL = "https://raw.githubusercontent.com/lighting-design-pro/releases/main/version.json"
    CURRENT_VERSION = (8, 0, 0)

    @classmethod
    def check_async(cls, callback: Callable[[Optional[str]], None]) -> None:
        import threading
        def _run():
            try:
                import urllib.request
                with urllib.request.urlopen(cls.LATEST_URL, timeout=4) as resp:
                    data = json.loads(resp.read())
                    latest = tuple(int(x) for x in data.get("version", "0.0.0").split(".")[:3])
                    if latest > cls.CURRENT_VERSION:
                        callback(data.get("version"))
                    else:
                        callback(None)
            except Exception:
                callback(None)
        threading.Thread(target=_run, daemon=True).start()


# ──────────────────────────────────────────────────────────
# 9. ONBOARDING WIZARD (Qt dialog, 3 steps)
# ──────────────────────────────────────────────────────────

_ONBOARDING_HTML = """
<style>
body{direction:rtl;font-family:'Segoe UI',Arial;color:#F0F4FF;background:#0F1117;margin:0;padding:16px}
h2{color:#3D8EF0;margin-bottom:4px}
p{color:#8A93A8;font-size:13px}
.step{background:#1E2230;border:1px solid #2A3048;border-radius:10px;padding:14px;margin-bottom:10px}
.active{border-color:#3D8EF0}
b{color:#F0F4FF}
</style>
<h2>ברוך הבא ל-Lighting Design Pro V8</h2>
<p>3 שלבים קצרים ותהיה מוכן לתכנן.</p>
<div class="step active"><b>שלב 1</b> — מאפייני החדר (מידות, סוג, תקרה)</div>
<div class="step"><b>שלב 2</b> — אפיון לקוח ותחושה רצויה</div>
<div class="step"><b>שלב 3</b> — בחירת גופי תאורה ראשוניים</div>
"""


# ──────────────────────────────────────────────────────────
# 10. AI DESIGN REVIEW (calls Anthropic API)
# ──────────────────────────────────────────────────────────

class AIDesignReviewer:
    """
    Sends a compact room summary to Claude and returns a
    professional lighting design review in Hebrew.
    Uses the Anthropic API key from env var ANTHROPIC_API_KEY.
    """
    MODEL = "claude-opus-4-5"
    MAX_TOKENS = 900

    @classmethod
    def build_prompt(cls, room, snap=None) -> str:
        avg = snap.avg_lux if snap else 0
        tgt = getattr(room, "lux_target", 200)
        watts = snap.watts if snap else 0
        cri = snap.cri if snap else 0
        ugr = snap.ugr if snap else "—"
        uniformity = (snap.min_lux / avg) if (snap and avg > 0) else 0
        layers = [l.name for l in room.layers if l.enabled] if room.layers else []
        brief = room.client_brief
        mf = room.maintenance_factor
        # Reported averages are maintained (end-of-life) values; initial (new) is
        # the maintained value divided by the maintenance factor.
        initial_avg = avg / mf if mf else avg
        u0_target = uniformity_target(room.room_type)
        ambient_zone = LUX_AMBIENT_ZONES.get(room.room_type)
        ambient_txt = f" | אזור היקפי/סובב: {ambient_zone} lx" if ambient_zone else ""
        non_wp = " (מגורים — EN 12464-1 הוא תקן מקומות עבודה ואינו חל ישירות)" \
            if room.room_type in NON_WORKPLACE_ROOM_TYPES else ""
        return f"""אתה מתכנן תאורה מקצועי בכיר. קבל את הנתונים הבאים וכתוב סקירת עיצוב קצרה ומקצועית (עד 8 נקודות) בעברית.

פרויקט: {room.project_name}
סוג חדר: {room.room_type}{non_wp} | {room.width:.1f}×{room.length:.1f}m | תקרה {room.ceiling_height:.2f}m
החזרי משטחים: תקרה {room.reflectance_ceiling:.2f} / קירות {room.reflectance_walls:.2f} / רצפה {room.reflectance_floor:.2f}
CCT: {room.cct_kelvin}K | CRI: {cri:.0f} | UGR: {ugr} (גבול {UGR_LIMITS.get(room.room_type, 22)})
יעד לוקס (משימה): {tgt} lx{ambient_txt}
לוקס מתוחזק (סוף-חיים): {avg:.0f} | לוקס התחלתי (חדש): {initial_avg:.0f} | מקדם תחזוקה MF: {mf:.2f}
אחידות U0 בפועל: {uniformity:.2f} (יעד {u0_target:.2f})
הספק: {watts:.0f}W | LPD: {watts/max(room.area, 0.01):.1f} W/m²
שכבות פעילות: {', '.join(layers) or 'אין'}
תחושה רצויה: {brief.desired_feeling}
סצינות נדרשות: {brief.wanted_scenes}

כתוב סקירה מקצועית עם:
1. הערכת ביצועים (לוקס מתוחזק מול התחלתי, אחידות מול יעד, CRI, UGR מול הגבול)
2. המלצות לשיפור (כולל מקדם תחזוקה ולוח זמני תחזוקה)
3. הצעות לשכבות ומערכות (משימה מול תאורה היקפית/סובבת)
4. הערות לאינטגרציה עם אור יום ובקרה
5. שיקולי חיסכון באנרגיה
בסוף הוסף הסתייגות קצרה: ערכי הלוקס מבוססים על מודל ישיר+עקיף מפושט (split-flux) והערכת UGR מפושטת לפי CIE 117; יש לאמת בתוכנת תכנה פוטומטרית מלאה (למשל DIALux/Relux) לפני ביצוע.
הגב בעברית בנקודות, בצורה מקצועית וממוקדת."""

    @classmethod
    def review(cls, room, snap=None) -> str:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return ("⚠️ לא הוגדר ANTHROPIC_API_KEY בסביבה.\n"
                    "הגדר את המשתנה ואתחל מחדש כדי לאפשר סקירת AI.")
        try:
            import urllib.request
            payload = json.dumps({
                "model": cls.MODEL,
                "max_tokens": cls.MAX_TOKENS,
                "messages": [{"role": "user", "content": cls.build_prompt(room, snap)}],
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
                return data["content"][0]["text"]
        except Exception as exc:
            return f"שגיאה בתקשורת עם AI: {exc}"


# ──────────────────────────────────────────────────────────
# EXPORT LIST  (used by injector)
# ──────────────────────────────────────────────────────────
__all__ = [
    "IESPhotometry", "IESParser", "LDTParser",
    "ProjectContainer",
    "UndoStack",
    "GridSnap",
    "dynamic_grid_size",
    "ProfessionalPDFExporter",
    "LicenseManager",
    "UpdateChecker",
    "AIDesignReviewer",
    "_ONBOARDING_HTML",
]


# ──────────────────────────────────────────────────────────────────────
# INLINED: v8_controls_patch.py
# ──────────────────────────────────────────────────────────────────────
# -*- coding: utf-8 -*-
"""
V8 Controls Patch — Full UX Rebuild
====================================
Replaces the entire layers tab with a new experience:
  • JoystickWidget  — drag-to-position + rotation dial for every system
  • SegmentLengthEditor — per-segment (A/B/C/D) length spinboxes, dynamic per shape
  • SideFixtureRow  — per-side fixture type + quantity, independently configurable
  • UpgradedTrackCard   — full multi-segment + multi-side track editor
  • UpgradedProfileCard — per-segment LED profile editor
  • UpgradedPendantCard — pendant/chandelier editor with joystick
  • UpgradedAmbientCard — ambient/curtain per-segment editor
  • LayersTabWidget  — assembles all cards into a smooth scrollable workflow
"""


from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal, QTimer
from PySide6.QtGui import (QColor, QCursor, QFont, QLinearGradient, QPainter,
                            QPen, QRadialGradient)
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFrame, QGroupBox,
                                QHBoxLayout, QLabel, QPushButton, QScrollArea,
                                QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
                                QCheckBox, QFormLayout)

# ─────────────────────────────────────────────────────────────────────────────
# COLOUR PALETTE (matches main app)
# ─────────────────────────────────────────────────────────────────────────────
P = {
    "bg":      "#0F1117", "surface": "#171A22", "card":   "#1E2230",
    "card2":  "#252A3A", "panel2":  "#202638", "panel3": "#141A28",
    "input":   "#1A1E2A",
    "border":  "#2A3048", "border2":"#3A4468", "text":   "#F0F4FF",
    "muted":   "#8A93A8", "blue":   "#3D8EF0", "green":  "#2ECC7A",
    "amber":   "#F0A030", "red":    "#EF4444", "purple": "#9F7AEA",
    "cyan":    "#22D3EE", "gold":   "#D4A850",
}

SHAPE_SEGMENTS: Dict[str, List[str]] = {
    "Linear":           ["אורך"],
    "L shape":          ["צלע A", "צלע B"],
    "U shape":          ["צלע A", "צלע B", "צלע C"],
    "Rectangle":        ["אורך", "רוחב"],
    "Custom segments":  ["סגמנט 1", "סגמנט 2", "סגמנט 3", "סגמנט 4"],
    "Custom polyline":  ["קטע 1",   "קטע 2",   "קטע 3"],
    "Perimeter":        ["היקפי (אוטומטי)"],
    "קו ישר":           ["אורך"],
    "L-shape":          ["צלע A", "צלע B"],
    "U-shape":          ["צלע A", "צלע B", "צלע C"],
    "היקפי":            ["היקפי (אוטומטי)"],
}

SHAPE_SIDES: Dict[str, List[str]] = {
    "Linear":           ["ראשי"],
    "L shape":          ["צלע A", "צלע B"],
    "U shape":          ["צלע A", "צלע B", "צלע C"],
    "Rectangle":        ["צפון", "מזרח", "דרום", "מערב"],
    "Custom segments":  ["סגמנט 1", "סגמנט 2", "סגמנט 3", "סגמנט 4"],
    "Custom polyline":  ["קטע 1",   "קטע 2",   "קטע 3"],
    "Perimeter":        ["צפון", "מזרח", "דרום", "מערב"],
}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _card_frame(accent: str) -> Tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setStyleSheet(
        "QFrame{"
        "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
        " stop:0 #222A3E, stop:0.6 #171E2E, stop:1 #101622);"
        f"border:1px solid #2A3048; border-left:3px solid {accent};"
        "border-radius:12px;}"
    )
    lay = QVBoxLayout(f)
    lay.setContentsMargins(14, 12, 14, 14)
    lay.setSpacing(8)
    return f, lay


def _section_label(text: str, color: str) -> QLabel:
    lb = QLabel(text)
    lb.setStyleSheet(
        f"color:{color};font-size:13px;font-weight:900;"
        "background:transparent;border:none;letter-spacing:0.5px;")
    return lb


def _muted(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setStyleSheet("color:#8A93A8;background:transparent;border:none;font-size:11px;")
    return lb


def _group(title: str, accent: str) -> Tuple[QGroupBox, QVBoxLayout]:
    g = QGroupBox(title)
    g.setStyleSheet(
        f"QGroupBox{{color:{accent};font-weight:700;font-size:11px;"
        "border:1px solid #3A4468;border-radius:8px;"
        "margin-top:10px;padding-top:10px;background:transparent}}"
        "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px}")
    lay = QVBoxLayout(g)
    lay.setContentsMargins(8, 6, 8, 8)
    lay.setSpacing(4)
    return g, lay


# ─────────────────────────────────────────────────────────────────────────────
# MINI SWITCH
# ─────────────────────────────────────────────────────────────────────────────

class MiniSwitch(QWidget):
    toggled = Signal(bool)

    def __init__(self, checked: bool = True, color: str = "#3D8EF0", parent=None):
        super().__init__(parent)
        self._on = checked
        self._c  = QColor(color)
        self.setFixedSize(46, 24)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setToolTip("לחץ להפעלה / כיבוי")

    def isChecked(self): return self._on
    def setChecked(self, v: bool):
        if self._on != v:
            self._on = v; self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._on = not self._on
            self.update()
            self.toggled.emit(self._on)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1, 1, self.width()-2, self.height()-2)
        base = self._c if self._on else QColor("#2A3048")
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0, base.lighter(135))
        g.setColorAt(1, base.darker(135))
        p.setBrush(g)
        p.setPen(QPen(QColor(255,255,255,35), 1))
        p.drawRoundedRect(r, 12, 12)
        kd = 18
        kx = self.width()-kd-3 if self._on else 3
        kr = QRectF(kx, 3, kd, kd)
        kg = QLinearGradient(kr.topLeft(), kr.bottomRight())
        kg.setColorAt(0, QColor("#FFFFFF"))
        kg.setColorAt(1, QColor("#C8D0E0"))
        p.setBrush(kg)
        p.setPen(QPen(QColor(0,0,0,50), 1))
        p.drawEllipse(kr)
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# JOYSTICK WIDGET
# ─────────────────────────────────────────────────────────────────────────────

class JoystickWidget(QWidget):
    """
    Left pad  → X/Y position (0-1 normalised)
    Right dial → rotation angle (0-360°)
    Drag either area; scroll wheel rotates.
    """
    positionChanged = Signal(float, float)
    rotationChanged = Signal(float)

    PAD = 148
    DIAL = 66

    def __init__(self, x=0.5, y=0.5, angle=0.0, color="#3D8EF0", parent=None):
        super().__init__(parent)
        self._x     = float(x)
        self._y     = float(y)
        self._angle = float(angle) % 360
        self._color = QColor(color)
        self._drag_pad  = False
        self._drag_dial = False
        self._dial_base_angle   = 0.0
        self._dial_base_mouse   = 0.0
        self.setFixedSize(self.PAD + self.DIAL + 12, self.PAD)
        self.setCursor(QCursor(Qt.CrossCursor))
        self.setToolTip(
            "📍 גרור את הנקודה לשינוי מיקום\n"
            "🔄 גרור את הדיאל לשינוי זווית\n"
            "🖱 גלגל העכבר מסובב")

    # ── geometry ──────────────────────────────────────────
    def _pr(self) -> QRectF:
        m = 6
        return QRectF(m, m, self.PAD-2*m, self.PAD-2*m)

    def _dr(self) -> QRectF:
        x0 = self.PAD + 6
        y0 = (self.PAD - self.DIAL) // 2
        return QRectF(x0, y0, self.DIAL, self.DIAL)

    def _pad_to_norm(self, px, py) -> Tuple[float, float]:
        r = self._pr()
        return (max(0.0, min(1.0, (px-r.left())/r.width())),
                max(0.0, min(1.0, (py-r.top())/r.height())))

    def _norm_to_pad(self) -> QPointF:
        r = self._pr()
        return QPointF(r.left()+self._x*r.width(), r.top()+self._y*r.height())

    def _mouse_bearing(self, pos: QPointF) -> float:
        c = self._dr().center()
        return math.degrees(math.atan2(pos.y()-c.y(), pos.x()-c.x()))

    # ── public ────────────────────────────────────────────
    def setPosition(self, x, y):
        self._x, self._y = max(0.,min(1.,x)), max(0.,min(1.,y))
        self.update()

    def setAngle(self, deg):
        self._angle = float(deg) % 360
        self.update()

    def x(self) -> float: return round(self._x, 3)
    def y(self) -> float: return round(self._y, 3)
    def angle(self) -> float: return round(self._angle, 1)

    # ── paint ─────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r  = self._pr()
        pt = self._norm_to_pad()
        dr = self._dr()

        # ── pad background ────────────────────────────────
        bg = QLinearGradient(r.topLeft(), r.bottomRight())
        bg.setColorAt(0, QColor("#111826"))
        bg.setColorAt(1, QColor("#090D16"))
        p.setBrush(bg); p.setPen(QPen(self._color.darker(160), 1.5))
        p.drawRoundedRect(r, 9, 9)

        # grid
        p.setPen(QPen(QColor(255,255,255,14), 1))
        for i in range(1, 4):
            t = i/4
            p.drawLine(QPointF(r.left()+t*r.width(), r.top()),
                       QPointF(r.left()+t*r.width(), r.bottom()))
            p.drawLine(QPointF(r.left(), r.top()+t*r.height()),
                       QPointF(r.right(), r.top()+t*r.height()))

        # crosshair
        p.setPen(QPen(self._color.lighter(110), 1, Qt.DashLine))
        p.drawLine(QPointF(r.left(), pt.y()), QPointF(r.right(), pt.y()))
        p.drawLine(QPointF(pt.x(), r.top()), QPointF(pt.x(), r.bottom()))

        # knob shadow
        p.setBrush(QColor(0,0,0,60))
        p.setPen(Qt.NoPen)
        p.drawEllipse(pt, 11, 11)

        # knob
        kg = QRadialGradient(pt, 9)
        kg.setColorAt(0, self._color.lighter(200))
        kg.setColorAt(1, self._color)
        p.setBrush(kg)
        p.setPen(QPen(QColor(255,255,255,90), 1))
        p.drawEllipse(pt, 9, 9)

        # coord label
        p.setPen(QColor(255,255,255,55))
        p.setFont(QFont("Segoe UI", 7))
        p.drawText(r.adjusted(4,3,0,0), Qt.AlignTop|Qt.AlignLeft,
                   f"X:{self._x:.2f}  Y:{self._y:.2f}")

        # ── dial ─────────────────────────────────────────
        dg = QRadialGradient(dr.center(), dr.width()/2)
        dg.setColorAt(0, QColor("#1C2440"))
        dg.setColorAt(1, QColor("#090D16"))
        p.setBrush(dg)
        p.setPen(QPen(self._color.darker(140), 1.5))
        p.drawEllipse(dr)

        cx, cy, R = dr.center().x(), dr.center().y(), dr.width()/2-3
        # ticks
        for deg in range(0, 360, 30):
            rad = math.radians(deg-90)
            is_main = deg % 90 == 0
            inner = 0.70 if is_main else 0.82
            p.setPen(QPen(QColor(255,255,255, 100 if is_main else 45), 1))
            p.drawLine(
                QPointF(cx+math.cos(rad)*R*inner, cy+math.sin(rad)*R*inner),
                QPointF(cx+math.cos(rad)*R,       cy+math.sin(rad)*R))

        # needle
        rad = math.radians(self._angle - 90)
        p.setPen(QPen(self._color.lighter(170), 2.5, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx, cy),
                   QPointF(cx+math.cos(rad)*R*0.65, cy+math.sin(rad)*R*0.65))
        p.setBrush(self._color); p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, cy), 4, 4)

        # angle text
        p.setPen(QColor(255,255,255,70))
        p.setFont(QFont("Segoe UI", 7))
        p.drawText(QRectF(dr.left(), dr.bottom()-16, dr.width(), 14),
                   Qt.AlignCenter, f"{self._angle:.0f}°")
        p.end()

    # ── mouse ────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton: return
        pos = e.position()
        if self._pr().contains(pos.x(), pos.y()):
            self._drag_pad = True
            self._move_pad(pos)
        elif self._dr().contains(pos.x(), pos.y()):
            self._drag_dial = True
            self._dial_base_angle = self._angle
            self._dial_base_mouse = self._mouse_bearing(pos)

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag_pad:  self._move_pad(pos)
        if self._drag_dial:
            delta = self._mouse_bearing(pos) - self._dial_base_mouse
            self._angle = (self._dial_base_angle + delta) % 360
            self.update(); self.rotationChanged.emit(self._angle)

    def mouseReleaseEvent(self, _):
        self._drag_pad = self._drag_dial = False

    def wheelEvent(self, e):
        step = 5 if e.angleDelta().y() > 0 else -5
        self._angle = (self._angle + step) % 360
        self.update(); self.rotationChanged.emit(self._angle)

    def _move_pad(self, pos):
        x, y = self._pad_to_norm(pos.x(), pos.y())
        self._x, self._y = x, y
        self.update(); self.positionChanged.emit(x, y)


# ─────────────────────────────────────────────────────────────────────────────
# SEGMENT LENGTH EDITOR — dynamic list of spinboxes per shape
# ─────────────────────────────────────────────────────────────────────────────

class SegmentLengthEditor(QWidget):
    changed = Signal()

    def __init__(self, seg_names: List[str], values: List[float],
                 lo=0.1, hi=50.0, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0,0,0,0)
        lay.setSpacing(3)
        self._spins: List[QDoubleSpinBox] = []
        for i, name in enumerate(seg_names):
            row = QHBoxLayout()
            lbl = QLabel(name)
            lbl.setFixedWidth(80)
            lbl.setStyleSheet(
                "color:#8A93A8;background:transparent;border:none;"
                "font-size:11px;font-weight:700;")
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(0.1)
            sp.setDecimals(2)
            sp.setSuffix(" m")
            sp.setValue(values[i] if i < len(values) else 1.0)
            sp.valueChanged.connect(self.changed)
            row.addWidget(lbl)
            row.addWidget(sp)
            lay.addLayout(row)
            self._spins.append(sp)

    def values(self) -> List[float]:
        return [s.value() for s in self._spins]

    def set_values(self, vals: List[float]):
        for i, s in enumerate(self._spins):
            if i < len(vals):
                s.blockSignals(True); s.setValue(vals[i]); s.blockSignals(False)


# ─────────────────────────────────────────────────────────────────────────────
# PER-SIDE FIXTURE ROW — one row per track/profile side
# ─────────────────────────────────────────────────────────────────────────────

class SideFixtureRow(QWidget):
    changed = Signal()

    def __init__(self, side_label: str, options: List[str],
                 cur_type: str = "", qty: int = 2, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(6)

        badge = QLabel(side_label)
        badge.setFixedWidth(60)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            "color:#F0F4FF;background:#2A3556;border-radius:5px;"
            "font-size:10px;font-weight:800;padding:2px 4px;border:none;")
        lay.addWidget(badge)

        self.combo = QComboBox()
        self.combo.addItems(options)
        if cur_type in options: self.combo.setCurrentText(cur_type)
        self.combo.currentTextChanged.connect(self.changed)
        lay.addWidget(self.combo, 2)

        self.qty_sp = QSpinBox()
        self.qty_sp.setRange(0, 40)
        self.qty_sp.setValue(qty)
        self.qty_sp.setSuffix(" גופים")
        self.qty_sp.setFixedWidth(90)
        self.qty_sp.valueChanged.connect(self.changed)
        lay.addWidget(self.qty_sp)

    def fixture_type(self): return self.combo.currentText()
    def quantity(self):     return self.qty_sp.value()

    def refresh_options(self, options: List[str]):
        cur = self.combo.currentText()
        self.combo.blockSignals(True)
        self.combo.clear(); self.combo.addItems(options)
        if cur in options: self.combo.setCurrentText(cur)
        self.combo.blockSignals(False)


# ─────────────────────────────────────────────────────────────────────────────
# POSITION + ROTATION BLOCK (joystick + numeric fallback)
# ─────────────────────────────────────────────────────────────────────────────

class PositionBlock(QWidget):
    """Joystick + 3 numeric fallback spinboxes (X, Y, Angle)."""
    changed = Signal()

    def __init__(self, x=0.5, y=0.5, angle=0.0, color="#3D8EF0", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0,0,0,0)
        lay.setSpacing(4)

        lay.addWidget(_muted("📍 מיקום וזווית — גרור לשינוי מיידי"))
        self.joy = JoystickWidget(x, y, angle, color)
        self.joy.positionChanged.connect(self._joy_pos)
        self.joy.rotationChanged.connect(self._joy_rot)
        lay.addWidget(self.joy)

        # numeric row
        num = QHBoxLayout()
        for lbl, attr, lo, hi, step, dec in [
            ("X",    "_nx", 0.0, 1.0,   0.01, 3),
            ("Y",    "_ny", 0.0, 1.0,   0.01, 3),
            ("°",    "_na", -180, 360,  1.0,  1),
        ]:
            l = QLabel(lbl)
            l.setStyleSheet("color:#8A93A8;background:transparent;border:none;font-size:10px")
            l.setFixedWidth(14)
            s = QDoubleSpinBox()
            s.setRange(lo, hi); s.setSingleStep(step); s.setDecimals(dec)
            s.setFixedWidth(72)
            s.valueChanged.connect(self._spin_change)
            num.addWidget(l); num.addWidget(s)
            setattr(self, attr, s)
        self._nx.setValue(x); self._ny.setValue(y); self._na.setValue(angle)
        lay.addLayout(num)

    def _joy_pos(self, x, y):
        self._nx.blockSignals(True); self._ny.blockSignals(True)
        self._nx.setValue(x); self._ny.setValue(y)
        self._nx.blockSignals(False); self._ny.blockSignals(False)
        self.changed.emit()

    def _joy_rot(self, a):
        self._na.blockSignals(True); self._na.setValue(a); self._na.blockSignals(False)
        self.changed.emit()

    def _spin_change(self):
        self.joy.blockSignals(True)
        self.joy.setPosition(self._nx.value(), self._ny.value())
        self.joy.setAngle(self._na.value())
        self.joy.blockSignals(False)
        self.changed.emit()

    def x(self):     return self._nx.value()
    def y(self):     return self._ny.value()
    def angle(self): return self._na.value()

    def setValues(self, x, y, angle):
        for sp, v in [(self._nx,x),(self._ny,y),(self._na,angle)]:
            sp.blockSignals(True); sp.setValue(v); sp.blockSignals(False)
        self.joy.setPosition(x, y); self.joy.setAngle(angle)


# ─────────────────────────────────────────────────────────────────────────────
# BASE CARD
# ─────────────────────────────────────────────────────────────────────────────

class BaseSystemCard(QWidget):
    """Base for all system cards. Provides toggle, title, accent colour."""
    changed = Signal()

    def __init__(self, title: str, accent: str, enabled: bool, parent=None):
        super().__init__(parent)
        self._accent = accent
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 8)
        frame, self._fl = _card_frame(accent)
        outer.addWidget(frame)

        # header row
        hdr = QHBoxLayout()
        self.sw = MiniSwitch(enabled, accent)
        self.sw.toggled.connect(self._on_any)
        hdr.addWidget(self.sw)
        hdr.addWidget(_section_label(title, accent))
        hdr.addStretch()
        self._fl.addLayout(hdr)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{accent};background:{accent};border:none;max-height:1px;opacity:0.3;")
        self._fl.addWidget(sep)

    def _on_any(self, *_):
        self.changed.emit()

    def isEnabled_(self): return self.sw.isChecked()


# ─────────────────────────────────────────────────────────────────────────────
# TRACK CARD
# ─────────────────────────────────────────────────────────────────────────────

class TrackCard(BaseSystemCard):
    def __init__(self, track, fixture_options: List[str], parent=None):
        super().__init__("מסלול מגנטי", P["gold"], track.enabled, parent)
        self._track = track
        self._opts  = fixture_options
        self._seg_editor: Optional[SegmentLengthEditor] = None
        self._side_rows:  List[SideFixtureRow] = []

        # ── shape + width row ─────────────────────────────
        row1 = QHBoxLayout()
        self.shape_cb = QComboBox()
        self.shape_cb.addItems(["Linear","L shape","U shape","Rectangle","Custom segments"])
        self.shape_cb.setCurrentText(getattr(track,"shape","Linear"))
        self.shape_cb.currentTextChanged.connect(self._shape_changed)
        self.width_cb = QComboBox()
        self.width_cb.addItems(["0.8 cm","1.3 cm","2.5 cm"])
        w = getattr(track,"width_cm",2.5)
        self.width_cb.setCurrentText(f"{w:g} cm")
        self.width_cb.currentTextChanged.connect(self._on_any)
        row1.addWidget(_muted("צורה:")); row1.addWidget(self.shape_cb, 2)
        row1.addWidget(_muted("רוחב:")); row1.addWidget(self.width_cb, 1)
        self._fl.addLayout(row1)

        # ── segment lengths ───────────────────────────────
        self._seg_grp, self._seg_lay = _group("📏 אורכי צלעות", P["gold"])
        self._fl.addWidget(self._seg_grp)
        self._rebuild_segments()

        # ── per-side fixtures ─────────────────────────────
        self._side_grp, self._side_lay = _group("💡 גופים לפי צד", P["gold"])
        self._fl.addWidget(self._side_grp)
        self._rebuild_sides()

        # ── position block ────────────────────────────────
        self.pos = PositionBlock(
            x=getattr(track,"x",0.5), y=getattr(track,"y",0.4),
            angle=getattr(track,"angle_deg",0.0), color=P["gold"])
        self.pos.changed.connect(self._on_any)
        self._fl.addWidget(self.pos)

    # ── rebuild helpers ───────────────────────────────────
    def _shape_changed(self, _):
        self._rebuild_segments()
        self._rebuild_sides()
        self._on_any()

    def _rebuild_segments(self):
        while self._seg_lay.count():
            w = self._seg_lay.takeAt(0).widget()
            if w: w.deleteLater()
        shape = self.shape_cb.currentText()
        names = SHAPE_SEGMENTS.get(shape, ["אורך"])
        t = self._track
        existing = list(getattr(t, "segment_lengths",
                                [getattr(t,"length_m",3.0)]*len(names)))
        self._seg_editor = SegmentLengthEditor(names, existing[:len(names)])
        self._seg_editor.changed.connect(self._on_any)
        self._seg_lay.addWidget(self._seg_editor)

    def _rebuild_sides(self):
        while self._side_lay.count():
            w = self._side_lay.takeAt(0).widget()
            if w: w.deleteLater()
        self._side_rows.clear()
        shape = self.shape_cb.currentText()
        sides = SHAPE_SIDES.get(shape, ["ראשי"])
        saved = getattr(self._track, "side_fixtures", {})
        for side in sides:
            info = saved.get(side, {})
            row = SideFixtureRow(
                side, self._opts,
                cur_type=info.get("type", self._opts[0] if self._opts else ""),
                qty=info.get("qty", 2))
            row.changed.connect(self._on_any)
            self._side_lay.addWidget(row)
            self._side_rows.append(row)

    def refresh_options(self, opts: List[str]):
        self._opts = opts
        for r in self._side_rows: r.refresh_options(opts)

    # ── write back ────────────────────────────────────────
    def apply(self, track) -> None:
        track.enabled    = self.sw.isChecked()
        track.shape      = self.shape_cb.currentText()
        try: track.width_cm = float(self.width_cb.currentText().split()[0])
        except: pass
        track.x          = self.pos.x()
        track.y          = self.pos.y()
        track.angle_deg  = self.pos.angle()

        segs = self._seg_editor.values() if self._seg_editor else [3.0]
        track.segment_lengths = segs
        track.length_m = segs[0]

        # side_fixtures dict + flat fixture list
        sf = {}
        fixtures = []
        for i, row in enumerate(self._side_rows):
            sf[row.combo.currentText()] = {"type": row.fixture_type(), "qty": row.quantity()}
            qty = row.quantity()
            seg_len = segs[i] if i < len(segs) else segs[0]
            for q in range(qty):
                pos = (q+1)/(qty+1) if qty > 1 else 0.5
                fixtures.append(TrackFixture(row.fixture_type(), pos))
        track.side_fixtures = sf
        track.fixtures      = fixtures


# ─────────────────────────────────────────────────────────────────────────────
# PROFILE CARD
# ─────────────────────────────────────────────────────────────────────────────

class ProfileCard(BaseSystemCard):
    def __init__(self, profile, parent=None):
        super().__init__("פרופיל LED", P["blue"], profile.enabled, parent)
        self._profile = profile
        self._seg_editor: Optional[SegmentLengthEditor] = None

        row1 = QHBoxLayout()
        self.shape_cb = QComboBox()
        self.shape_cb.addItems(
            ["Linear","L shape","U shape","Rectangle","Custom polyline","Perimeter"])
        self.shape_cb.setCurrentText(getattr(profile,"shape","Linear"))
        self.shape_cb.currentTextChanged.connect(self._shape_changed)
        row1.addWidget(_muted("צורה:")); row1.addWidget(self.shape_cb)
        self._fl.addLayout(row1)

        row2 = QHBoxLayout()
        self.lmm_sp = QSpinBox()
        self.lmm_sp.setRange(50, 5000)
        self.lmm_sp.setValue(getattr(profile,"lm_per_m",600))
        self.lmm_sp.setSuffix(" lm/m")
        self.lmm_sp.valueChanged.connect(self._on_any)
        self.qty_sp = QSpinBox()
        self.qty_sp.setRange(1, 20)
        self.qty_sp.setValue(getattr(profile,"quantity",1))
        self.qty_sp.setSuffix(" עותקים")
        self.qty_sp.valueChanged.connect(self._on_any)
        row2.addWidget(_muted("lm/m:")); row2.addWidget(self.lmm_sp)
        row2.addWidget(_muted("כמות:")); row2.addWidget(self.qty_sp)
        self._fl.addLayout(row2)

        self._seg_grp, self._seg_lay = _group("📏 אורכי צלעות", P["blue"])
        self._fl.addWidget(self._seg_grp)
        self._rebuild_segments()

        self.pos = PositionBlock(
            x=getattr(profile,"x",0.5), y=getattr(profile,"y",0.5),
            angle=getattr(profile,"angle_deg",0.0), color=P["blue"])
        self.pos.changed.connect(self._on_any)
        self._fl.addWidget(self.pos)

    def _shape_changed(self, _):
        self._rebuild_segments(); self._on_any()

    def _rebuild_segments(self):
        while self._seg_lay.count():
            w = self._seg_lay.takeAt(0).widget()
            if w: w.deleteLater()
        shape = self.shape_cb.currentText()
        names = SHAPE_SEGMENTS.get(shape, ["אורך"])
        p = self._profile
        existing = list(getattr(p, "segment_lengths",
                                [getattr(p,"length_m",3.0)]*len(names)))
        self._seg_editor = SegmentLengthEditor(names, existing[:len(names)])
        self._seg_editor.changed.connect(self._on_any)
        self._seg_lay.addWidget(self._seg_editor)

    def apply(self, profile) -> None:
        profile.enabled    = self.sw.isChecked()
        profile.shape      = self.shape_cb.currentText()
        profile.lm_per_m   = self.lmm_sp.value()
        profile.quantity   = self.qty_sp.value()
        profile.x          = self.pos.x()
        profile.y          = self.pos.y()
        profile.angle_deg  = self.pos.angle()
        segs = self._seg_editor.values() if self._seg_editor else [3.0]
        profile.segment_lengths = segs
        profile.length_m = segs[0]
        if len(segs) > 1: profile.side_b_m = segs[1]
        if len(segs) > 2: profile.side_c_m = segs[2]


# ─────────────────────────────────────────────────────────────────────────────
# PENDANT CARD
# ─────────────────────────────────────────────────────────────────────────────

class PendantCard(BaseSystemCard):
    def __init__(self, pendant, fixture_options: List[str], parent=None):
        super().__init__("תלויי תקרה / Pendants", P["purple"], pendant.enabled, parent)
        self._pendant = pendant

        row1 = QHBoxLayout()
        self.type_cb = QComboBox()
        self.type_cb.addItems(
            ["פנדנט בודד","שורת פנדנטים","נברשת","פנדנט אקוסטי","מערך מרובע"])
        self.type_cb.setCurrentText(getattr(pendant,"pendant_type","פנדנט בודד"))
        self.type_cb.currentTextChanged.connect(self._on_any)
        row1.addWidget(_muted("סוג:")); row1.addWidget(self.type_cb)
        self._fl.addLayout(row1)

        row2 = QHBoxLayout()
        self.fix_cb = QComboBox()
        self.fix_cb.addItems(fixture_options)
        cur_fix = getattr(pendant,"fixture_type","")
        if cur_fix in fixture_options: self.fix_cb.setCurrentText(cur_fix)
        self.fix_cb.currentTextChanged.connect(self._on_any)
        row2.addWidget(_muted("גוף:")); row2.addWidget(self.fix_cb)
        self._fl.addLayout(row2)

        row3 = QHBoxLayout()
        self.qty_sp = QSpinBox()
        self.qty_sp.setRange(1,40); self.qty_sp.setValue(getattr(pendant,"quantity",1))
        self.qty_sp.valueChanged.connect(self._on_any)
        self.drop_sp = QDoubleSpinBox()
        self.drop_sp.setRange(0.05,10); self.drop_sp.setValue(getattr(pendant,"drop_m",0.8))
        self.drop_sp.setSuffix(" m"); self.drop_sp.valueChanged.connect(self._on_any)
        self.spacing_sp = QDoubleSpinBox()
        self.spacing_sp.setRange(0.1,10); self.spacing_sp.setValue(getattr(pendant,"spacing_m",0.75))
        self.spacing_sp.setSuffix(" m"); self.spacing_sp.valueChanged.connect(self._on_any)
        row3.addWidget(_muted("כמות:")); row3.addWidget(self.qty_sp)
        row3.addWidget(_muted("הנמכה:")); row3.addWidget(self.drop_sp)
        self._fl.addLayout(row3)
        row4 = QHBoxLayout()
        row4.addWidget(_muted("מרווח:")); row4.addWidget(self.spacing_sp)
        self._fl.addLayout(row4)

        self.pos = PositionBlock(
            x=getattr(pendant,"x",0.5), y=getattr(pendant,"y",0.5),
            angle=getattr(pendant,"angle_deg",0.0), color=P["purple"])
        self.pos.changed.connect(self._on_any)
        self._fl.addWidget(self.pos)

    def refresh_options(self, opts: List[str]):
        cur = self.fix_cb.currentText()
        self.fix_cb.blockSignals(True)
        self.fix_cb.clear(); self.fix_cb.addItems(opts)
        if cur in opts: self.fix_cb.setCurrentText(cur)
        self.fix_cb.blockSignals(False)

    def apply(self, pendant) -> None:
        pendant.enabled      = self.sw.isChecked()
        pendant.pendant_type = self.type_cb.currentText()
        pendant.fixture_type = self.fix_cb.currentText()
        pendant.quantity     = self.qty_sp.value()
        pendant.drop_m       = self.drop_sp.value()
        pendant.spacing_m    = self.spacing_sp.value()
        pendant.x            = self.pos.x()
        pendant.y            = self.pos.y()
        pendant.angle_deg    = self.pos.angle()


# ─────────────────────────────────────────────────────────────────────────────
# AMBIENT CARD
# ─────────────────────────────────────────────────────────────────────────────

class AmbientCard(BaseSystemCard):
    def __init__(self, ambient, parent=None):
        super().__init__("תאורת אווירה", P["cyan"], ambient.enabled, parent)
        self._ambient = ambient
        self._seg_editor: Optional[SegmentLengthEditor] = None

        row1 = QHBoxLayout()
        self.shape_cb = QComboBox()
        self.shape_cb.addItems(["קו ישר","L-shape","U-shape","היקפי"])
        self.shape_cb.setCurrentText(getattr(ambient,"shape","קו ישר"))
        self.shape_cb.currentTextChanged.connect(self._shape_changed)
        row1.addWidget(_muted("צורה:")); row1.addWidget(self.shape_cb)
        self._fl.addLayout(row1)

        row2 = QHBoxLayout()
        self.lmm_sp = QSpinBox()
        self.lmm_sp.setRange(20, 5000)
        self.lmm_sp.setValue(getattr(ambient,"lm_per_m",300))
        self.lmm_sp.setSuffix(" lm/m"); self.lmm_sp.valueChanged.connect(self._on_any)
        row2.addWidget(_muted("עוצמה:")); row2.addWidget(self.lmm_sp)
        self._fl.addLayout(row2)

        self._seg_grp, self._seg_lay = _group("📏 אורכי צלעות", P["cyan"])
        self._fl.addWidget(self._seg_grp)
        self._rebuild_segments()

        self.pos = PositionBlock(
            x=getattr(ambient,"x",0.5), y=getattr(ambient,"y",0.8),
            angle=getattr(ambient,"angle_deg",0.0), color=P["cyan"])
        self.pos.changed.connect(self._on_any)
        self._fl.addWidget(self.pos)

    def _shape_changed(self, _):
        self._rebuild_segments(); self._on_any()

    def _rebuild_segments(self):
        while self._seg_lay.count():
            w = self._seg_lay.takeAt(0).widget()
            if w: w.deleteLater()
        shape = self.shape_cb.currentText()
        names = SHAPE_SEGMENTS.get(shape, ["אורך"])
        a = self._ambient
        existing = list(getattr(a, "segment_lengths",
                                [getattr(a,"length_m",4.0)]*len(names)))
        self._seg_editor = SegmentLengthEditor(names, existing[:len(names)], lo=0.1, hi=100)
        self._seg_editor.changed.connect(self._on_any)
        self._seg_lay.addWidget(self._seg_editor)

    def apply(self, ambient) -> None:
        ambient.enabled   = self.sw.isChecked()
        ambient.shape     = self.shape_cb.currentText()
        ambient.lm_per_m  = self.lmm_sp.value()
        ambient.x         = self.pos.x()
        ambient.y         = self.pos.y()
        ambient.angle_deg = self.pos.angle()
        segs = self._seg_editor.values() if self._seg_editor else [4.0]
        ambient.segment_lengths = segs
        ambient.length_m = segs[0]


# ─────────────────────────────────────────────────────────────────────────────
# SPOT CARD (simplified UX)
# ─────────────────────────────────────────────────────────────────────────────

class SpotCard(BaseSystemCard):
    def __init__(self, room, parent=None):
        super().__init__("ספוטים — שכבת משימה", P["amber"],
                         any(l.enabled for l in room.layers[:1]), parent)
        self._room = room

        row1 = QHBoxLayout()
        self.fix_cb = QComboBox()
        self.fix_cb.addItems(list(room.fixture_catalogue.keys()))
        self.fix_cb.setCurrentText(room.default_spot_fixture)
        self.fix_cb.currentTextChanged.connect(self._on_any)
        row1.addWidget(_muted("גוף:")); row1.addWidget(self.fix_cb)
        self._fl.addLayout(row1)

        row2 = QHBoxLayout()
        self.beam_cb = QComboBox()
        self.beam_cb.addItems([f"{x} deg" for x in [15,24,36,45,60,90]])
        self.beam_cb.setCurrentText(f"{room.beam_angle} deg")
        self.beam_cb.currentTextChanged.connect(self._on_any)
        self.qty_sp = QSpinBox()
        self.qty_sp.setRange(0, 999); self.qty_sp.setSpecialValueText("אוטומטי")
        self.qty_sp.setValue(room.spot_quantity_override or 0)
        self.qty_sp.valueChanged.connect(self._on_any)
        row2.addWidget(_muted("זווית:")); row2.addWidget(self.beam_cb)
        row2.addWidget(_muted("כמות:")); row2.addWidget(self.qty_sp)
        self._fl.addLayout(row2)

        row3 = QHBoxLayout()
        self.offset_sp = QDoubleSpinBox()
        self.offset_sp.setRange(0, 10); self.offset_sp.setValue(room.wall_offset)
        self.offset_sp.setSuffix(" m"); self.offset_sp.valueChanged.connect(self._on_any)
        self.heatmap_chk = QCheckBox("מפת חום")
        self.heatmap_chk.setChecked(room.show_heatmap)
        self.heatmap_chk.stateChanged.connect(self._on_any)
        row3.addWidget(_muted("מרחק קיר:")); row3.addWidget(self.offset_sp)
        row3.addWidget(self.heatmap_chk)
        self._fl.addLayout(row3)

    def apply(self, room) -> None:
        room.default_spot_fixture      = self.fix_cb.currentText()
        room.beam_angle                = int(self.beam_cb.currentText().split()[0])
        room.spot_quantity_override    = self.qty_sp.value() or None
        room.wall_offset               = self.offset_sp.value()
        room.show_heatmap              = self.heatmap_chk.isChecked()


# ─────────────────────────────────────────────────────────────────────────────
# LAYERS TAB WIDGET — assembles everything
# ─────────────────────────────────────────────────────────────────────────────

class LayersTabWidget(QWidget):
    """
    Drop-in replacement for the layers column.
    Call build(room, fixture_options) then connect changed.
    Call apply_to_room(room) to write values back before recalculate.
    """
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self._inner = QWidget()
        self._lay   = QVBoxLayout(self._inner)
        self._lay.setContentsMargins(6, 6, 6, 6)
        self._lay.setSpacing(2)
        scroll.setWidget(self._inner)
        outer.addWidget(scroll)

        self._spot_card:    Optional[SpotCard]    = None
        self._profile_card: Optional[ProfileCard] = None
        self._track_card:   Optional[TrackCard]   = None
        self._pendant_card: Optional[PendantCard] = None
        self._ambient_card: Optional[AmbientCard] = None

    def build(self, room, fixture_options: List[str]) -> None:
        # clear
        while self._lay.count():
            item = self._lay.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        opts = fixture_options

        # ── Spot ─────────────────────────────────────────
        self._spot_card = SpotCard(room)
        self._spot_card.changed.connect(self.changed)
        self._lay.addWidget(self._spot_card)

        # ── Profile ───────────────────────────────────────
        p0 = room.profiles[0] if room.profiles else None
        if p0:
            self._profile_card = ProfileCard(p0)
            self._profile_card.changed.connect(self.changed)
            self._lay.addWidget(self._profile_card)

        # ── Track ─────────────────────────────────────────
        t0 = room.tracks[0] if room.tracks else None
        if t0:
            track_opts = self._filter_track_opts(opts, t0)
            self._track_card = TrackCard(t0, track_opts)
            self._track_card.changed.connect(self.changed)
            self._lay.addWidget(self._track_card)

        # ── Pendant ───────────────────────────────────────
        p1 = room.pendants[0] if room.pendants else None
        if p1:
            self._pendant_card = PendantCard(p1, opts)
            self._pendant_card.changed.connect(self.changed)
            self._lay.addWidget(self._pendant_card)

        # ── Ambient ───────────────────────────────────────
        amb = room.ambient if hasattr(room, "ambient") else None
        if amb:
            self._ambient_card = AmbientCard(amb)
            self._ambient_card.changed.connect(self.changed)
            self._lay.addWidget(self._ambient_card)

        self._lay.addStretch()

    def _filter_track_opts(self, opts: List[str], track) -> List[str]:
        try:
            w = getattr(track, "width_cm", 2.5)
            return [n for n, d in DEFAULT_FIXTURES.items()
                    if w in d.get("track_widths", [w])] or opts
        except Exception:
            return opts

    def apply_to_room(self, room) -> None:
        if self._spot_card:    self._spot_card.apply(room)
        if self._profile_card and room.profiles: self._profile_card.apply(room.profiles[0])
        if self._track_card   and room.tracks:   self._track_card.apply(room.tracks[0])
        if self._pendant_card and room.pendants: self._pendant_card.apply(room.pendants[0])
        if self._ambient_card and hasattr(room,"ambient"): self._ambient_card.apply(room.ambient)

    def refresh_fixture_options(self, opts: List[str]) -> None:
        if self._track_card:   self._track_card.refresh_options(opts)
        if self._pendant_card: self._pendant_card.refresh_options(opts)


__all__ = [
    "JoystickWidget", "SegmentLengthEditor", "SideFixtureRow",
    "PositionBlock", "MiniSwitch",
    "TrackCard", "ProfileCard", "PendantCard", "AmbientCard", "SpotCard",
    "LayersTabWidget",
]


# ──────────────────────────────────────────────────────────────────────
# INLINED: v8_main_ux.py
# ──────────────────────────────────────────────────────────────────────
# -*- coding: utf-8 -*-
"""
V8 Main UX Integration
======================
Patches LightingApp with:
  - New STYLESHEET (premium dark, typography, micro-interactions)
  - Rebuilt _build_ui (3-column layout: palette | viewport | controls)
  - Natural language wizard tab
  - Design packages tab
  - Scene timeline in results
  - Surface materials in settings
  - Sticky notes overlay on renderer
  - Snapshot history panel
  - Client HTML export button
  - View toggle (Designer / Client)
  - Dimension lines on viewport
"""

from PySide6.QtCore  import QPointF, QRectF, Qt, Signal, QTimer, QMimeData
from PySide6.QtGui   import (QColor, QCursor, QFont, QLinearGradient,
                              QPainter, QPen, QRadialGradient, QPixmap)
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QSplitter, QTabWidget, QTextEdit, QVBoxLayout,
    QWidget, QApplication, QFileDialog, QMessageBox,
    QDoubleSpinBox, QCheckBox, QGridLayout, QToolButton,
    QDialog, QDialogButtonBox
)

# ─────────────────────────────────────────────────────────────────────────────
# PREMIUM STYLESHEET  — V8
# ─────────────────────────────────────────────────────────────────────────────

V8_STYLESHEET = """
/* ── Reset ─────────────────────────────────────────────────────────────── */
* { box-sizing: border-box; }

/* ── App shell ──────────────────────────────────────────────────────────── */
QMainWindow, QWidget {
    background: #0B0E16;
    color: #E8EDF8;
    font-family: 'Segoe UI Variable', 'Segoe UI', 'Inter', Arial, sans-serif;
    font-size: 13px;
}

/* ── Scrollbars ─────────────────────────────────────────────────────────── */
QScrollBar:vertical {
    background: #0D1018; width: 6px; border-radius: 3px;
}
QScrollBar::handle:vertical {
    background: #2A3556; border-radius: 3px; min-height: 32px;
}
QScrollBar::handle:vertical:hover { background: #3D8EF0; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar:horizontal {
    background: #0D1018; height: 6px; border-radius: 3px;
}
QScrollBar::handle:horizontal {
    background: #2A3556; border-radius: 3px; min-width: 32px;
}
QScrollBar::handle:horizontal:hover { background: #3D8EF0; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
QScrollBar::corner { background: #0D1018; }

/* ── Tabs ────────────────────────────────────────────────────────────────── */
QTabWidget::pane {
    border: 1px solid #1E2540;
    background: #0D1018;
    border-radius: 10px;
}
QTabBar {
    background: transparent;
}
QTabBar::tab {
    background: transparent;
    color: #5A6480;
    padding: 9px 16px;
    margin-right: 1px;
    border-bottom: 2px solid transparent;
    font-weight: 600;
    font-size: 12px;
    letter-spacing: 0.2px;
}
QTabBar::tab:selected {
    color: #E8EDF8;
    border-bottom: 2px solid #3D8EF0;
}
QTabBar::tab:hover:!selected {
    color: #A0AABF;
    border-bottom: 2px solid #2A3556;
}

/* ── Inputs ──────────────────────────────────────────────────────────────── */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {
    background: #111622;
    border: 1px solid #1E2840;
    border-radius: 7px;
    padding: 6px 10px;
    color: #E8EDF8;
    min-height: 30px;
    selection-background-color: #3D8EF0;
}
QLineEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus {
    border-color: #3D8EF0;
    background: #131A28;
}
QLineEdit:hover, QComboBox:hover,
QSpinBox:hover, QDoubleSpinBox:hover {
    border-color: #2A3556;
}
QComboBox::drop-down {
    border: none; width: 20px;
}
QComboBox QAbstractItemView {
    background: #141B2A;
    border: 1px solid #2A3556;
    selection-background-color: #1E3060;
    color: #E8EDF8;
    border-radius: 6px;
    padding: 4px;
}

/* ── Buttons ─────────────────────────────────────────────────────────────── */
QPushButton {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #3D8EF0, stop:1 #2468D8);
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 700;
    font-size: 12px;
    letter-spacing: 0.2px;
}
QPushButton:hover {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #5AA4FF, stop:1 #3478E8);
}
QPushButton:pressed {
    background: #1A5AC8;
    padding-top: 9px; padding-bottom: 7px;
}
QPushButton#secondary {
    background: #111622;
    color: #A0AABF;
    border: 1px solid #1E2840;
}
QPushButton#secondary:hover {
    background: #1A2035;
    color: #E8EDF8;
    border-color: #2A3556;
}
QPushButton#green {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #2ECC7A, stop:1 #1AA060);
}
QPushButton#green:hover { background: #38D888; }
QPushButton#amber {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #F0A030, stop:1 #C07818);
}
QPushButton#amber:hover { background: #F5B040; }
QPushButton#danger {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #EF4444, stop:1 #C02828);
}
QPushButton#danger:hover { background: #F55555; }

/* ── Checkboxes ──────────────────────────────────────────────────────────── */
QCheckBox {
    spacing: 8px;
    color: #A0AABF;
}
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #2A3556;
    border-radius: 4px;
    background: #111622;
}
QCheckBox::indicator:checked {
    background: #3D8EF0;
    border-color: #3D8EF0;
}
QCheckBox::indicator:hover { border-color: #3D8EF0; }

/* ── Sliders ──────────────────────────────────────────────────────────────── */
QSlider::groove:horizontal {
    height: 4px; background: #1E2840; border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 14px; height: 14px;
    background: #3D8EF0;
    border-radius: 7px;
    margin: -5px 0;
}
QSlider::sub-page:horizontal { background: #3D8EF0; border-radius: 2px; }

/* ── GroupBox ─────────────────────────────────────────────────────────────── */
QGroupBox {
    border: 1px solid #1E2840;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 10px;
    color: #5A6480;
    font-weight: 700;
    font-size: 11px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}

/* ── Status bar ──────────────────────────────────────────────────────────── */
QStatusBar {
    background: #090C14;
    color: #5A6480;
    border-top: 1px solid #1A2030;
    font-size: 11px;
    padding: 0 8px;
}
QStatusBar::item { border: none; }

/* ── Toolbar ──────────────────────────────────────────────────────────────── */
QToolBar {
    background: #090C14;
    border-bottom: 1px solid #1A2030;
    spacing: 2px;
    padding: 3px 6px;
}
QToolBar QToolButton, QToolBar QPushButton {
    background: transparent;
    color: #7A84A0;
    border: none;
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 12px;
    font-weight: 600;
}
QToolBar QToolButton:hover, QToolBar QPushButton:hover {
    background: #1A2035;
    color: #E8EDF8;
}
QToolBar QAction { color: #7A84A0; }

/* ── Frames ─────────────────────────────────────────────────────────────── */
QFrame[frameShape="4"] { /* HLine */
    color: #1A2030;
    max-height: 1px;
}

/* ── Splitter ────────────────────────────────────────────────────────────── */
QSplitter::handle {
    background: #1A2030;
    width: 3px; height: 3px;
}
QSplitter::handle:hover { background: #3D8EF0; }

/* ── Menu ─────────────────────────────────────────────────────────────────── */
QMenuBar {
    background: #090C14;
    color: #7A84A0;
    border-bottom: 1px solid #1A2030;
    padding: 2px 4px;
}
QMenuBar::item:selected { background: #1A2035; color: #E8EDF8; border-radius: 4px; }
QMenu {
    background: #141B2A;
    border: 1px solid #2A3556;
    border-radius: 8px;
    padding: 4px;
    color: #E8EDF8;
}
QMenu::item { padding: 7px 20px; border-radius: 5px; }
QMenu::item:selected { background: #1E3060; }
QMenu::separator { height: 1px; background: #2A3556; margin: 4px 8px; }

/* ── ScrollArea ──────────────────────────────────────────────────────────── */
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
"""


# ─────────────────────────────────────────────────────────────────────────────
# CARD + HELPER FACTORIES
# ─────────────────────────────────────────────────────────────────────────────

def v8_card(title: str, accent: str = "#3D8EF0",
            icon: str = "") -> Tuple[QFrame, QVBoxLayout]:
    """Premium card with gradient, accent border, and title."""
    f = QFrame()
    f.setStyleSheet(
        "QFrame{"
        "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
        " stop:0 #141B2A, stop:0.5 #0F1520, stop:1 #0B1018);"
        f"border:1px solid #1E2840;"
        f"border-left:3px solid {accent};"
        "border-radius:12px;}"
    )
    lay = QVBoxLayout(f)
    lay.setContentsMargins(16, 13, 16, 15)
    lay.setSpacing(10)
    # title row
    hdr = QHBoxLayout(); hdr.setSpacing(8)
    if icon:
        ic = QLabel(icon)
        ic.setStyleSheet(
            f"color:{accent};font-size:16px;background:transparent;border:none;")
        hdr.addWidget(ic)
    lbl = QLabel(title)
    lbl.setStyleSheet(
        f"color:{accent};font-size:13px;font-weight:900;"
        "background:transparent;border:none;letter-spacing:0.4px;")
    hdr.addWidget(lbl); hdr.addStretch()
    lay.addLayout(hdr)
    # thin divider
    div = QFrame(); div.setFrameShape(QFrame.HLine)
    div.setStyleSheet(f"color:{accent};background:{accent};"
                      "border:none;max-height:1px;opacity:0.25;")
    lay.addWidget(div)
    return f, lay


def v8_label(text: str, color: str = "#8A93A8",
             bold: bool = False, size: int = 12) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(True)
    lb.setStyleSheet(
        f"color:{color};background:transparent;border:none;"
        f"font-size:{size}px;{'font-weight:700;' if bold else ''}")
    return lb


def v8_btn(text: str, color: str = "#3D8EF0",
           icon: str = "", size: str = "normal") -> QPushButton:
    b = QPushButton(f"{icon}  {text}".strip() if icon else text)
    pad = "6px 12px" if size == "small" else "9px 18px"
    b.setStyleSheet(
        f"QPushButton{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
        f"stop:0 {QColor(color).lighter(115).name()},stop:1 {color});"
        f"color:#fff;border:none;border-radius:8px;padding:{pad};"
        "font-weight:700;font-size:12px;}"
        f"QPushButton:hover{{background:{QColor(color).lighter(125).name()};}}"
        "QPushButton:pressed{padding-top:2px;}")
    return b


def v8_section_header(text: str, color: str = "#3D8EF0") -> QLabel:
    lb = QLabel(text)
    lb.setStyleSheet(
        f"color:{color};font-size:11px;font-weight:800;"
        "letter-spacing:1.2px;text-transform:uppercase;"
        "background:transparent;border:none;padding:8px 0 4px 0;")
    return lb


# ─────────────────────────────────────────────────────────────────────────────
# KPI BADGE WIDGET
# ─────────────────────────────────────────────────────────────────────────────

class KPIBadge(QWidget):
    """Single KPI: big number, label, optional status colour."""
    def __init__(self, label: str, value: str = "—",
                 status: str = "neutral", parent=None):
        super().__init__(parent)
        self._status = status
        self.setFixedHeight(72)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        self.val_lbl = QLabel(value)
        self.val_lbl.setFont(QFont("Segoe UI Variable", 22, QFont.Bold))
        self.lbl = QLabel(label)
        self.lbl.setStyleSheet(
            "color:#5A6480;font-size:11px;background:transparent;border:none;")
        lay.addWidget(self.val_lbl)
        lay.addWidget(self.lbl)
        self._apply_status(status)

    def _apply_status(self, status: str):
        colors = {"ok":"#2ECC7A","warn":"#F0A030","bad":"#EF4444","neutral":"#3D8EF0"}
        c = colors.get(status, "#3D8EF0")
        self.val_lbl.setStyleSheet(
            f"color:{c};background:transparent;border:none;")
        self.setStyleSheet(
            f"QWidget{{background:#0D1422;border:1px solid #1E2840;"
            f"border-top:3px solid {c};border-radius:10px;}}")

    def update_value(self, value: str, status: str = "neutral"):
        self.val_lbl.setText(value)
        self._apply_status(status)


# ─────────────────────────────────────────────────────────────────────────────
# KPI ROW (5 badges)
# ─────────────────────────────────────────────────────────────────────────────

class KPIRow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(80)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(8)
        self.lux_badge  = KPIBadge("לוקס ממוצע",  "—", "neutral")
        self.uni_badge  = KPIBadge("אחידות U0",   "—", "neutral")
        self.cri_badge  = KPIBadge("CRI",          "—", "neutral")
        self.watts_badge= KPIBadge("הספק W",       "—", "neutral")
        self.cct_badge  = KPIBadge("טמפרטורה K",  "—", "neutral")
        for b in [self.lux_badge, self.uni_badge, self.cri_badge,
                  self.watts_badge, self.cct_badge]:
            lay.addWidget(b)

    def update_from_snap(self, snap, room):
        if not snap: return
        avg  = snap.avg_lux
        tgt  = getattr(room, "lux_target", 200)
        uni  = snap.min_lux / avg if avg > 0 else 0
        cri  = snap.cri
        w    = snap.watts
        cct  = getattr(room, "cct_kelvin", 3000)
        lux_ok  = "ok" if 0.9 <= avg/max(tgt,1) <= 1.4  else "warn"
        uni_ok  = "ok" if uni >= uniformity_target(getattr(room, "room_type", "")) else "bad"
        cri_ok  = "ok" if cri >= 90   else "warn"
        self.lux_badge.update_value(f"{avg:.0f}", lux_ok)
        self.uni_badge.update_value(f"{uni:.2f}", uni_ok)
        self.cri_badge.update_value(f"{cri:.0f}", cri_ok)
        self.watts_badge.update_value(f"{w:.0f}", "neutral")
        self.cct_badge.update_value(f"{cct}", "neutral")


# ─────────────────────────────────────────────────────────────────────────────
# VIEW TOGGLE BAR
# ─────────────────────────────────────────────────────────────────────────────

class ViewToggleBar(QWidget):
    viewChanged = Signal(str)   # "designer" | "client"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0,0,0,0); lay.setSpacing(4)
        lay.addStretch()
        self._btns: Dict[str, QPushButton] = {}
        for label, key, color in [
            ("🛠  מעצב", "designer", "#3D8EF0"),
            ("👤 לקוח",  "client",   "#2ECC7A"),
        ]:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setChecked(key == "designer")
            b.setFixedHeight(28)
            b.setStyleSheet(
                f"QPushButton{{background:#111622;color:#5A6480;"
                "border:1px solid #1E2840;border-radius:7px;"
                "padding:0 14px;font-size:12px;font-weight:700;}}"
                f"QPushButton:checked{{background:{color}20;"
                f"color:{color};border-color:{color};}}"
                "QPushButton:hover:!checked{"
                "background:#1A2035;color:#A0AABF;}")
            b.clicked.connect(lambda _, k=key: self._switch(k))
            lay.addWidget(b)
            self._btns[key] = b

    def _switch(self, key: str):
        for k, b in self._btns.items():
            b.setChecked(k == key)
        self.viewChanged.emit(key)


# ─────────────────────────────────────────────────────────────────────────────
# NATURAL LANGUAGE WIZARD
# ─────────────────────────────────────────────────────────────────────────────

class NLWizardWidget(QWidget):
    """
    Step 1: free-text description
    Step 2: parsed parameters review + accept
    """
    accepted = Signal(dict)

    ROOM_HINTS = {
        "סלון":"סלון","living":"סלון","salon":"סלון",
        "מטבח":"מטבח","kitchen":"מטבח",
        "שינה":"חדר שינה","bedroom":"חדר שינה",
        "משרד":"משרד","office":"משרד",
        "מסדרון":"מסדרון","corridor":"מסדרון",
        "אמבטיה":"חדר אמבטיה","bathroom":"חדר אמבטיה",
        "חנות":"חנות","shop":"חנות","store":"חנות",
        "מסעדה":"מסעדה","restaurant":"מסעדה",
    }
    CCT_HINTS = {
        "חמים":2700,"warm":2700,"2700":2700,
        "3000":3000,"נייטרל":3000,"neutral":3000,
        "4000":4000,"קר":4000,"cool":4000,
        "2200":2200,"ספא":2200,
    }
    FEEL_HINTS = {
        "חמ":   "Warm",    "warm": "Warm", "מזמין":"Warm", "cozy":"Warm",
        "מינימל":"Minimal","minimal":"Minimal","נקי":"Minimal",
        "יוקרה":"Luxury",  "luxury":"Luxury","פרימיום":"Luxury",
        "רגוע": "Calm",    "calm":"Calm",   "שקט":"Calm",
        "ממוקד":"Focused", "focused":"Focused","גלרי":"Focused",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4); lay.setSpacing(12)

        f, fl = v8_card("✍️  תאר את הפרויקט", "#D4A850", "")
        intro = v8_label(
            "כתוב בחופשיות — אנחנו נמלא את כל השדות אוטומטית.",
            "#5A6480")
        self.txt = QTextEdit()
        self.txt.setFixedHeight(80)
        self.txt.setPlaceholderText(
            "לדוגמה: סלון 8x6 מטר, תקרה 3 מטר, אווירה חמה ומינימליסטית, "
            "פינת אוכל ואי מטבח, מסלולים מגנטיים + ספוטים שקועים, CCT 2700K")
        parse_row = QHBoxLayout()
        self.parse_btn = v8_btn("🪄 מלא אוטומטית", "#D4A850")
        self.parse_btn.clicked.connect(self._parse)
        self.status_lbl = v8_label("", "#2ECC7A")
        parse_row.addWidget(self.parse_btn); parse_row.addWidget(self.status_lbl, 1)
        fl.addWidget(intro)
        fl.addWidget(self.txt)
        fl.addLayout(parse_row)
        lay.addWidget(f)

        # result preview card
        self.result_frame, self.result_lay = v8_card(
            "📋 תוצאת הניתוח", "#2ECC7A")
        self.result_frame.hide()
        self.result_labels: List[QLabel] = []
        self.apply_btn = v8_btn("✅ אשר ומלא", "#2ECC7A")
        self.apply_btn.clicked.connect(self._accept)
        self.result_lay.addWidget(self.apply_btn)
        lay.addWidget(self.result_frame)
        lay.addStretch()
        self._parsed: dict = {}

    def _parse(self):
        import re
        t = self.txt.toPlainText().lower()
        r: dict = {}

        for kw, rt in self.ROOM_HINTS.items():
            if kw in t: r["room_type"] = rt; break
        m = re.search(r"(\d+\.?\d*)\s*[xX×]\s*(\d+\.?\d*)", t)
        if m: r["width"]=float(m.group(1)); r["length"]=float(m.group(2))
        m = re.search(r"(\d+\.?\d*)\s+על\s+(\d+\.?\d*)", t)
        if m: r["width"]=float(m.group(1)); r["length"]=float(m.group(2))
        m = re.search(r"(\d+\.?\d*)\s*(?:מ[\"']ר|m2|m²)", t)
        if m: r["area"]=float(m.group(1))
        m = re.search(r"תקרה\s+(\d+\.?\d*)|ceil\D{0,4}(\d+\.?\d*)", t)
        if m: r["ceiling"]=float(m.group(1) or m.group(2))
        for kw, v in self.CCT_HINTS.items():
            if kw in t: r["cct"]=v; break
        for kw, v in self.FEEL_HINTS.items():
            if kw in t: r["feeling"]=v; break
        sys = []
        if any(x in t for x in ["ספוט","spot","שקוע"]): sys.append("spots")
        if any(x in t for x in ["מסלול","track","מגנטי"]): sys.append("track")
        if any(x in t for x in ["פרופיל","profile","ליניארי"]): sys.append("profile")
        if any(x in t for x in ["פנדנט","pendant","תלוי","נברשת"]): sys.append("pendant")
        if sys: r["systems"]=sys

        self._parsed = r
        # show preview
        for lb in self.result_labels:
            self.result_lay.removeWidget(lb); lb.deleteLater()
        self.result_labels.clear()
        lines = [
            ("סוג חדר",   r.get("room_type","—")),
            ("מידות",     f"{r.get('width','?')} × {r.get('length','?')} m"
                           if "width" in r else "—"),
            ("גובה תקרה", f"{r.get('ceiling','—')} m"),
            ("CCT",        f"{r.get('cct','—')} K"),
            ("תחושה",     r.get("feeling","—")),
            ("מערכות",    ", ".join(r.get("systems",[])) or "—"),
        ]
        insert_pos = self.result_lay.count() - 1
        for k, v in lines:
            row = QHBoxLayout()
            kl = v8_label(k+":", "#5A6480")
            kl.setFixedWidth(90)
            vl = v8_label(v, "#E8EDF8", bold=True)
            row.addWidget(kl); row.addWidget(vl, 1)
            container = QWidget(); container.setLayout(row)
            self.result_lay.insertWidget(insert_pos, container)
            self.result_labels.append(container)
            insert_pos += 1
        self.result_frame.show()
        if r:
            self.status_lbl.setText(f"✓  זוהו {len(r)} פרמטרים")
        else:
            self.status_lbl.setText("⚠️  לא זוהו פרמטרים — נסה לפרט יותר")

    def _accept(self):
        self.accepted.emit(self._parsed)


# ─────────────────────────────────────────────────────────────────────────────
# DESIGN PACKAGES WIDGET
# ─────────────────────────────────────────────────────────────────────────────

PACKAGES = {
    "Minimal Nordic":     {"icon":"❄️","cct":2700,"feel":"Minimal",
        "desc":"פרופיל נסתר, ספוטים שקועים, CCT 2700K",
        "accent":"#22D3EE",
        "systems":{"spots":True,"track":False,"profile":True,"pendant":False,"ambient":True}},
    "Warm Hospitality":   {"icon":"🕯️","cct":2200,"feel":"Warm",
        "desc":"פנדנטים, wall washers, 2200K",
        "accent":"#F0A030",
        "systems":{"spots":False,"track":False,"profile":False,"pendant":True,"ambient":True}},
    "Gallery Focus":      {"icon":"🖼️","cct":4000,"feel":"Focused",
        "desc":"מסלולים מגנטיים, קרניים צרות, 4000K",
        "accent":"#9F7AEA",
        "systems":{"spots":True,"track":True,"profile":False,"pendant":False,"ambient":False}},
    "Biophilic Soft":     {"icon":"🌿","cct":3000,"feel":"Calm",
        "desc":"תאורה עקיפה בלבד, 3000K → 2200K",
        "accent":"#2ECC7A",
        "systems":{"spots":False,"track":False,"profile":True,"pendant":True,"ambient":True}},
    "Retail High-Key":    {"icon":"🛍️","cct":3500,"feel":"Focused",
        "desc":"מסלולים + מדפים + ספוטים, LPD גבוה",
        "accent":"#3D8EF0",
        "systems":{"spots":True,"track":True,"profile":True,"pendant":False,"ambient":False}},
    "Bedroom Sanctuary":  {"icon":"🌙","cct":2700,"feel":"Calm",
        "desc":"עקיף נמוך, פנדנט ליד המיטה, 2700K",
        "accent":"#D4A850",
        "systems":{"spots":False,"track":False,"profile":True,"pendant":True,"ambient":True}},
}


class DesignPackagesWidget(QWidget):
    packageSelected = Signal(str, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4); lay.setSpacing(8)
        lay.addWidget(v8_label(
            "בחר סגנון עיצובי — כל השכבות יתמלאו אוטומטית",
            "#5A6480"))
        grid = QGridLayout(); grid.setSpacing(8)
        for i, (name, cfg) in enumerate(PACKAGES.items()):
            grid.addWidget(self._make_tile(name, cfg), i // 2, i % 2)
        lay.addLayout(grid)
        lay.addStretch()

    def _make_tile(self, name: str, cfg: dict) -> QWidget:
        accent = cfg["accent"]
        w = QWidget()
        w.setCursor(QCursor(Qt.PointingHandCursor))
        w.setStyleSheet(
            f"QWidget{{background:#0F1520;border:1px solid #1E2840;"
            f"border-left:3px solid {accent};"
            "border-radius:10px;padding:2px;}}"
            f"QWidget:hover{{background:#141B2A;border-left-color:{accent};"
            "border-color:#2A3556;}}")
        lay = QVBoxLayout(w); lay.setContentsMargins(12, 10, 12, 10); lay.setSpacing(3)

        top = QHBoxLayout(); top.setSpacing(6)
        ic = QLabel(cfg["icon"])
        ic.setStyleSheet("font-size:18px;background:transparent;border:none;")
        nm = QLabel(name)
        nm.setStyleSheet(
            f"color:{accent};font-weight:800;font-size:12px;"
            "background:transparent;border:none;")
        cct_lb = QLabel(f"{cfg['cct']}K")
        cct_lb.setStyleSheet(
            "color:#5A6480;font-size:10px;background:transparent;border:none;")
        top.addWidget(ic); top.addWidget(nm, 1); top.addWidget(cct_lb)
        lay.addLayout(top)

        desc = QLabel(cfg["desc"])
        desc.setWordWrap(True)
        desc.setStyleSheet(
            "color:#5A6480;font-size:10px;background:transparent;border:none;")
        lay.addWidget(desc)

        w.mousePressEvent = lambda _, n=name, c=cfg: self.packageSelected.emit(n, c)
        return w


# ─────────────────────────────────────────────────────────────────────────────
# SCENE TIMELINE WIDGET
# ─────────────────────────────────────────────────────────────────────────────

class SceneTimelineWidget(QWidget):
    sceneChanged = Signal(list)

    DEFAULTS = [
        {"hour":6, "label":"בוקר",    "cct":3000, "intensities":{"משימה":80, "מבטא":40, "אווירה":30}},
        {"hour":9, "label":"עבודה",   "cct":4000, "intensities":{"משימה":100,"מבטא":60, "אווירה":50}},
        {"hour":13,"label":"צהריים",  "cct":3500, "intensities":{"משימה":100,"מבטא":80, "אווירה":60}},
        {"hour":18,"label":"ערב",     "cct":3000, "intensities":{"משימה":40, "מבטא":90, "אווירה":80}},
        {"hour":20,"label":"לילה",    "cct":2700, "intensities":{"משימה":15, "מבטא":70, "אווירה":90}},
        {"hour":23,"label":"שינה",    "cct":2200, "intensities":{"משימה":0,  "מבטא":10, "אווירה":15}},
    ]

    LAYER_COLORS = {"משימה":"#F0A030","מבטא":"#3D8EF0","אווירה":"#22D3EE"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scenes = [dict(s) for s in self.DEFAULTS]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0,0,0,0); lay.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.addWidget(v8_label("⏰  ציר זמן יומי", "#22D3EE", bold=True, size=13))
        hdr.addStretch()
        add_btn = v8_btn("+ סצינה", "#22D3EE", size="small")
        add_btn.setFixedWidth(80)
        add_btn.clicked.connect(self._add)
        hdr.addWidget(add_btn)
        lay.addLayout(hdr)

        # paint-style timeline bar
        self.timeline_bar = _TimelineBar(self._scenes)
        self.timeline_bar.setFixedHeight(40)
        lay.addWidget(self.timeline_bar)

        # slots scroll
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self._slots_w = QWidget()
        self._slots_lay = QVBoxLayout(self._slots_w)
        self._slots_lay.setContentsMargins(0,0,0,0); self._slots_lay.setSpacing(4)
        scroll.setWidget(self._slots_w)
        lay.addWidget(scroll)
        self._rebuild()

    def _rebuild(self):
        while self._slots_lay.count():
            item = self._slots_lay.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        for i, sc in enumerate(self._scenes):
            self._slots_lay.addWidget(self._make_slot(i, sc))
        self._slots_lay.addStretch()
        self.timeline_bar.set_scenes(self._scenes)
        self.timeline_bar.update()

    def _make_slot(self, idx: int, sc: dict) -> QWidget:
        accent = "#22D3EE"
        w = QWidget()
        w.setStyleSheet(
            "QWidget{background:#0D1422;border:1px solid #1E2840;border-radius:8px;}")
        lay = QHBoxLayout(w); lay.setContentsMargins(10,6,6,6); lay.setSpacing(8)

        from PySide6.QtWidgets import QSpinBox as _S, QLineEdit as _L, QComboBox as _C, QSlider as _Sl
        hr = _S(); hr.setRange(0,23); hr.setValue(sc["hour"])
        hr.setSuffix(":00"); hr.setFixedWidth(66)
        hr.valueChanged.connect(lambda v,i=idx: self._upd(i,"hour",v))
        lay.addWidget(hr)

        lbl_e = _L(sc.get("label","")); lbl_e.setFixedWidth(76)
        lbl_e.setPlaceholderText("שם...")
        lbl_e.textChanged.connect(lambda v,i=idx: self._upd(i,"label",v))
        lay.addWidget(lbl_e)

        cct_c = _C()
        for v in [2200,2700,3000,3500,4000]:
            cct_c.addItem(f"{v}K", v)
        cct_list = [2200,2700,3000,3500,4000]
        cur_cct = sc.get("cct",3000)
        cct_c.setCurrentIndex(cct_list.index(cur_cct) if cur_cct in cct_list else 1)
        cct_c.setFixedWidth(72)
        cct_c.currentIndexChanged.connect(
            lambda _,i=idx,c=cct_c: self._upd(i,"cct",c.currentData()))
        lay.addWidget(cct_c)

        # intensity sliders
        for layer, color in self.LAYER_COLORS.items():
            col_w = QWidget(); col_l = QVBoxLayout(col_w)
            col_l.setContentsMargins(0,0,0,0); col_l.setSpacing(1)
            lbl = QLabel(layer[:3])
            lbl.setStyleSheet(
                f"color:{color};font-size:9px;font-weight:700;"
                "background:transparent;border:none;")
            lbl.setAlignment(Qt.AlignCenter)
            sl = _Sl(Qt.Horizontal)
            sl.setRange(0,100); sl.setFixedWidth(60)
            sl.setValue(sc.get("intensities",{}).get(layer, 50))
            pct = QLabel(f"{sl.value()}%")
            pct.setStyleSheet(
                "color:#5A6480;font-size:9px;background:transparent;border:none;")
            pct.setAlignment(Qt.AlignCenter)
            sl.valueChanged.connect(lambda v,i=idx,ln=layer,p=pct:
                (self._upd_intensity(i,ln,v), p.setText(f"{v}%")))
            col_l.addWidget(lbl); col_l.addWidget(sl); col_l.addWidget(pct)
            lay.addWidget(col_w)

        del_b = QPushButton("✕"); del_b.setFixedSize(22,22)
        del_b.setObjectName("danger")
        del_b.clicked.connect(lambda _,i=idx: self._delete(i))
        lay.addWidget(del_b)
        return w

    def _upd(self, i, k, v):
        self._scenes[i][k] = v
        self.timeline_bar.set_scenes(self._scenes); self.timeline_bar.update()
        self.sceneChanged.emit(self._scenes)

    def _upd_intensity(self, i, layer, v):
        self._scenes[i].setdefault("intensities",{})[layer] = v
        self.sceneChanged.emit(self._scenes)

    def _delete(self, i):
        if len(self._scenes) > 1:
            self._scenes.pop(i); self._rebuild()

    def _add(self):
        self._scenes.append(
            {"hour":12,"label":"חדש","cct":3000,
             "intensities":{"משימה":70,"מבטא":50,"אווירה":40}})
        self._rebuild()

    def get_scenes(self) -> list: return list(self._scenes)


class _TimelineBar(QWidget):
    """Painted 24h timeline showing scene markers."""
    def __init__(self, scenes, parent=None):
        super().__init__(parent)
        self._scenes = list(scenes)

    def set_scenes(self, s): self._scenes = list(s)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        # background
        g = QLinearGradient(0, 0, W, 0)
        g.setColorAt(0,    QColor("#060810"))
        g.setColorAt(0.25, QColor("#0D1830"))
        g.setColorAt(0.5,  QColor("#1A1408"))
        g.setColorAt(0.75, QColor("#1A1028"))
        g.setColorAt(1,    QColor("#060810"))
        p.fillRect(self.rect(), g)
        p.setPen(QPen(QColor("#1E2840"), 1))
        for h in range(0, 25, 3):
            x = int(h/24 * W)
            p.drawLine(x, H-12, x, H)
            p.setFont(QFont("Segoe UI", 8))
            p.setPen(QColor("#3A4468"))
            p.drawText(x-10, H-14, 20, 12, Qt.AlignCenter, f"{h:02d}")
        # scene markers
        for sc in self._scenes:
            x = int(sc.get("hour",0)/24 * W)
            accent = QColor("#22D3EE")
            p.setPen(Qt.NoPen); p.setBrush(accent)
            p.drawEllipse(x-4, 4, 8, 8)
            p.setPen(QPen(accent, 1))
            p.drawLine(x, 12, x, H-14)
            p.setFont(QFont("Segoe UI", 8))
            p.setPen(QColor("#22D3EE"))
            p.drawText(x-20, 2, 40, 12, Qt.AlignCenter,
                       sc.get("label","")[:4])
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# SURFACE MATERIALS WIDGET
# ─────────────────────────────────────────────────────────────────────────────

MATERIALS = {
    "טיח לבן":    0.80, "טיח אפור":    0.55,
    "בטון מוחלק": 0.35, "עץ בהיר":    0.55,
    "עץ כהה":     0.20, "ריצוף אפור":  0.40,
    "ריצוף כהה":  0.10, "שיש לבן":    0.70,
    "זכוכית/מראה":0.85, "צבע שחור":   0.05,
}


class SurfaceMaterialsWidget(QWidget):
    changed = Signal(float, float, float)  # ceiling, wall, floor reflectance

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0,0,0,0); lay.setSpacing(6)
        lay.addWidget(v8_label(
            "חומרי גמר — משפיעים ישירות על חישוב הלוקס",
            "#9F7AEA", bold=True, size=12))
        lay.addWidget(v8_label(
            "קיר לבן מחזיר פי 8 יותר אור מקיר שחור.",
            "#5A6480"))

        self._combos: Dict[str, QComboBox] = {}
        self._refl:   Dict[str, float]     = {}
        for surface, attr, default in [
            ("תקרה","ceiling","טיח לבן"),
            ("קירות","wall","טיח לבן"),
            ("רצפה", "floor","ריצוף אפור"),
        ]:
            row = QHBoxLayout()
            lbl = v8_label(f"{surface}:", "#5A6480", bold=True)
            lbl.setFixedWidth(52)
            cb = QComboBox()
            cb.addItems(list(MATERIALS.keys()))
            cb.setCurrentText(default)
            cb.currentTextChanged.connect(self._on_change)
            refl_lbl = v8_label(
                f"{MATERIALS[default]*100:.0f}%", "#9F7AEA", size=11)
            refl_lbl.setFixedWidth(38)
            row.addWidget(lbl); row.addWidget(cb, 1); row.addWidget(refl_lbl)
            lay.addLayout(row)
            self._combos[attr] = cb
            self._refl[attr]   = MATERIALS[default]
            cb.refl_lbl = refl_lbl

        self.boost_lbl = v8_label("", "#2ECC7A", size=11)
        lay.addWidget(self.boost_lbl)
        self._on_change()

    def _on_change(self, *_):
        for attr, cb in self._combos.items():
            r = MATERIALS.get(cb.currentText(), 0.5)
            self._refl[attr] = r
            cb.refl_lbl.setText(f"{r*100:.0f}%")
        c = self._refl["ceiling"]; w = self._refl["wall"]; f = self._refl["floor"]
        boost = 1.0 + (c*0.3 + w*0.5 + f*0.2) * 0.6
        self.boost_lbl.setText(
            f"✨  השפעה על חישוב: ×{boost:.2f}  "
            f"(+{(boost-1)*100:.0f}% מהחזרות)")
        self.changed.emit(c, w, f)

    def reflectances(self) -> Tuple[float,float,float]:
        return (self._refl["ceiling"], self._refl["wall"], self._refl["floor"])


# ─────────────────────────────────────────────────────────────────────────────
# STICKY NOTES PANEL
# ─────────────────────────────────────────────────────────────────────────────

class StickyNotesPanel(QWidget):
    notesChanged = Signal(list)

    NOTE_COLORS = ["#F0C040","#60D090","#3D8EF0","#EF4444","#C070F0"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._notes: List[dict] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0,0,0,0); lay.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.addWidget(v8_label("📌  הערות", "#F0C040", bold=True, size=12))
        hdr.addStretch()
        add_btn = v8_btn("+ הוסף", "#F0C040", size="small")
        add_btn.setFixedWidth(76)
        add_btn.clicked.connect(self._add)
        hdr.addWidget(add_btn)
        lay.addLayout(hdr)

        self._list_lay = QVBoxLayout(); self._list_lay.setSpacing(3)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget(); inner.setLayout(self._list_lay)
        scroll.setWidget(inner); scroll.setFixedHeight(160)
        lay.addWidget(scroll)
        self._rebuild()

    def _rebuild(self):
        while self._list_lay.count():
            w = self._list_lay.takeAt(0).widget()
            if w: w.deleteLater()
        for i, note in enumerate(self._notes):
            self._list_lay.addWidget(self._make_row(i, note))
        self._list_lay.addStretch()

    def _make_row(self, idx: int, note: dict) -> QWidget:
        color = note.get("color","#F0C040")
        w = QWidget()
        w.setStyleSheet(
            f"QWidget{{background:#0D1018;border-left:3px solid {color};"
            "border-radius:6px;border:1px solid #1E2840;}")
        lay = QHBoxLayout(w); lay.setContentsMargins(8,4,4,4); lay.setSpacing(6)
        edit = QLineEdit(note.get("text",""))
        edit.setPlaceholderText("כתוב הערה...")
        edit.textChanged.connect(lambda t,i=idx: self._upd(i,"text",t))
        lay.addWidget(edit, 1)

        # cycle color
        idx_ref = [self.NOTE_COLORS.index(color) if color in self.NOTE_COLORS else 0]
        cb = QPushButton("●"); cb.setFixedSize(22,22)
        cb.setStyleSheet(
            f"QPushButton{{background:{color};border:none;border-radius:4px;"
            "color:transparent;}}")
        def cycle(_, i=idx, ref=idx_ref, btn=cb):
            ref[0] = (ref[0]+1) % len(self.NOTE_COLORS)
            nc = self.NOTE_COLORS[ref[0]]
            self._notes[i]["color"] = nc
            btn.setStyleSheet(
                f"QPushButton{{background:{nc};border:none;"
                "border-radius:4px;color:transparent;}}")
            self.notesChanged.emit(self._notes)
        cb.clicked.connect(cycle)
        lay.addWidget(cb)

        db = QPushButton("✕"); db.setFixedSize(22,22)
        db.setStyleSheet(
            "QPushButton{background:transparent;color:#EF4444;"
            "border:none;font-weight:900;font-size:12px;}"
            "QPushButton:hover{background:#1A1010;}")
        db.clicked.connect(lambda _,i=idx: self._delete(i))
        lay.addWidget(db)
        return w

    def _upd(self, i, k, v):
        self._notes[i][k] = v
        self.notesChanged.emit(self._notes)

    def _delete(self, i):
        self._notes.pop(i); self._rebuild()
        self.notesChanged.emit(self._notes)

    def _add(self):
        self._notes.append({"text":"","color":"#F0C040","x":0.5,"y":0.5})
        self._rebuild()

    def get_notes(self): return list(self._notes)

    def set_notes(self, notes):
        self._notes = [dict(n) for n in (notes or [])]
        self._rebuild()


# ─────────────────────────────────────────────────────────────────────────────
# SNAPSHOTS PANEL
# ─────────────────────────────────────────────────────────────────────────────

class SnapshotsPanel(QWidget):
    restoreRequested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._snaps: List[dict] = []
        self._room_fn = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0,0,0,0); lay.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.addWidget(v8_label("📷  היסטוריית גרסאות",
                                "#9F7AEA", bold=True, size=12))
        hdr.addStretch()
        save_btn = v8_btn("💾 שמור", "#9F7AEA", size="small")
        save_btn.setFixedWidth(72)
        save_btn.clicked.connect(self._save_prompt)
        hdr.addWidget(save_btn)
        lay.addLayout(hdr)

        self._list_lay = QVBoxLayout(); self._list_lay.setSpacing(3)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget(); inner.setLayout(self._list_lay)
        scroll.setWidget(inner); scroll.setFixedHeight(160)
        lay.addWidget(scroll)

    def set_room_provider(self, fn): self._room_fn = fn

    def get_snaps(self): return list(self._snaps)

    def set_snaps(self, snaps):
        self._snaps = [dict(s) for s in (snaps or [])]
        self._rebuild()

    def _save_prompt(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, "שמור גרסה", "שם לגרסה זו:")
        if ok and name and self._room_fn:
            self.save_snap(name, self._room_fn())

    def save_snap(self, name: str, room_dict: dict):
        ts = dt.datetime.now().strftime("%H:%M  %d/%m")
        self._snaps.insert(0, {"name":name,"ts":ts,"room":room_dict})
        if len(self._snaps) > 20: self._snaps = self._snaps[:20]
        self._rebuild()

    def _rebuild(self):
        while self._list_lay.count():
            w = self._list_lay.takeAt(0).widget()
            if w: w.deleteLater()
        for i, sn in enumerate(self._snaps):
            self._list_lay.addWidget(self._make_row(i, sn))
        self._list_lay.addStretch()

    def _make_row(self, idx: int, sn: dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet(
            "QWidget{background:#0D1018;border:1px solid #1E2840;border-radius:7px;}")
        lay = QHBoxLayout(w); lay.setContentsMargins(10,5,6,5); lay.setSpacing(6)
        lay.addWidget(v8_label(sn["name"], "#E8EDF8", bold=True, size=11))
        lay.addWidget(v8_label(sn["ts"], "#5A6480", size=10), 1)
        rb = QPushButton("שחזר"); rb.setFixedSize(52,22)
        rb.setStyleSheet(
            "QPushButton{background:#0D1520;color:#3D8EF0;"
            "border:1px solid #1E3060;border-radius:5px;"
            "font-size:10px;font-weight:700;}"
            "QPushButton:hover{background:#3D8EF0;color:#fff;}")
        rb.clicked.connect(
            lambda _,i=idx: self.restoreRequested.emit(self._snaps[i]["room"]))
        lay.addWidget(rb)
        return w


# ─────────────────────────────────────────────────────────────────────────────
# CLIENT HTML EXPORT
# ─────────────────────────────────────────────────────────────────────────────

class ClientHTMLExporter:
    @staticmethod
    def export(room, snap=None, scenes=None, path: str="") -> str:
        if not path:
            path = os.path.join(
                os.path.expanduser("~"), "Desktop",
                f"{room.project_name or 'project'}_client.html")

        avg = snap.avg_lux if snap else 0
        tgt = getattr(room,"lux_target",200)
        w   = snap.watts   if snap else 0
        cri = snap.cri     if snap else 0
        uni = snap.min_lux/avg if (snap and avg>0) else 0
        cct = getattr(room,"cct_kelvin",3000)
        scenes_js = __import__("json").dumps(scenes or [], ensure_ascii=False)

        def status_class(ok): return "ok" if ok else "warn"
        lux_ok  = status_class(0.9 <= avg/max(tgt,1) <= 1.4)
        uni_ok  = status_class(uni >= uniformity_target(getattr(room, "room_type", "")))
        cri_ok  = status_class(cri >= 90)

        html = f"""<!DOCTYPE html>
<html dir="rtl" lang="he">
<head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{room.project_name or 'תכנית תאורה'}</title>
<style>
:root{{--bg:#0B0E16;--card:#111622;--card2:#141B2A;
  --border:#1E2840;--text:#E8EDF8;--muted:#5A6480;
  --blue:#3D8EF0;--green:#2ECC7A;--amber:#F0A030;--red:#EF4444;--cyan:#22D3EE}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);
  font-family:'Segoe UI',Arial,sans-serif;direction:rtl}}
.header{{background:linear-gradient(135deg,#0F1828 0%,#0B1220 100%);
  padding:32px 40px;border-bottom:1px solid var(--border)}}
.company{{color:var(--blue);font-size:13px;font-weight:700;letter-spacing:1px;
  text-transform:uppercase;margin-bottom:8px}}
.project-title{{font-size:30px;font-weight:900;line-height:1.2}}
.meta{{color:var(--muted);margin-top:8px;font-size:13px}}
.kpis{{display:flex;gap:12px;padding:24px 40px;flex-wrap:wrap;
  background:linear-gradient(180deg,#0D1320 0%,var(--bg) 100%)}}
.kpi{{background:var(--card);border:1px solid var(--border);border-radius:12px;
  padding:16px 20px;flex:1;min-width:120px;
  border-top:3px solid var(--blue)}}
.kpi.ok{{border-top-color:var(--green)}}
.kpi.warn{{border-top-color:var(--amber)}}
.kpi.bad{{border-top-color:var(--red)}}
.kpi-val{{font-size:28px;font-weight:900;color:var(--blue)}}
.kpi.ok .kpi-val{{color:var(--green)}}
.kpi.warn .kpi-val{{color:var(--amber)}}
.kpi.bad .kpi-val{{color:var(--red)}}
.kpi-lbl{{font-size:11px;color:var(--muted);margin-top:4px}}
.section{{padding:20px 40px}}
.section-title{{font-size:11px;font-weight:800;letter-spacing:1px;
  text-transform:uppercase;color:var(--blue);
  border-bottom:1px solid var(--border);padding-bottom:8px;margin-bottom:16px}}
.scenes{{display:flex;gap:10px;overflow-x:auto;padding-bottom:6px}}
.sc{{background:var(--card);border:2px solid var(--border);
  border-radius:10px;padding:12px 14px;min-width:110px;cursor:pointer;
  transition:all .2s ease}}
.sc:hover,.sc.active{{border-color:var(--cyan);background:var(--card2)}}
.sc-hour{{font-size:22px;font-weight:900;color:var(--cyan)}}
.sc-name{{font-size:12px;font-weight:700;margin-top:2px}}
.sc-cct{{font-size:10px;color:var(--muted);margin-top:3px}}
.bars{{margin-top:8px}}
.bar-row{{display:flex;align-items:center;gap:5px;margin-top:3px;font-size:9px}}
.bar-bg{{flex:1;height:5px;background:#1A2030;border-radius:3px;overflow:hidden}}
.bar-fill{{height:100%;border-radius:3px;transition:width .5s ease}}
.footer{{padding:20px 40px;border-top:1px solid var(--border);
  color:var(--muted);font-size:11px;text-align:center;
  background:#090C14}}
@media(max-width:600px){{
  .header{{padding:20px}}.kpis{{padding:16px 20px}}
  .section{{padding:16px 20px}}.kpi-val{{font-size:22px}}
}}
</style>
</head>
<body>
<div class="header">
  <div class="company">{room.branding.company_name}</div>
  <div class="project-title">{room.project_name or 'תכנית תאורה'}</div>
  <div class="meta">{room.client_name} &nbsp;·&nbsp;
    {room.room_type} &nbsp;·&nbsp;
    {room.width:.1f} × {room.length:.1f} m &nbsp;·&nbsp;
    תקרה {room.ceiling_height:.1f} m &nbsp;·&nbsp;
    {dt.datetime.now().strftime('%d/%m/%Y')}</div>
</div>
<div class="kpis">
  <div class="kpi {lux_ok}">
    <div class="kpi-val">{avg:.0f}</div>
    <div class="kpi-lbl">לוקס ממוצע<br>(יעד: {tgt} lx)</div></div>
  <div class="kpi {uni_ok}">
    <div class="kpi-val">{uni:.2f}</div>
    <div class="kpi-lbl">אחידות U0<br>(מינימום: 0.35)</div></div>
  <div class="kpi {cri_ok}">
    <div class="kpi-val">{cri:.0f}</div>
    <div class="kpi-lbl">CRI<br>איכות צבע</div></div>
  <div class="kpi">
    <div class="kpi-val">{w:.0f}W</div>
    <div class="kpi-lbl">הספק כולל<br>{w/max(room.area,0.01):.1f} W/m²</div></div>
  <div class="kpi">
    <div class="kpi-val">{cct}K</div>
    <div class="kpi-lbl">טמפרטורת צבע<br>גוון האור</div></div>
</div>
<div class="section">
  <div class="section-title">⏰ סצינות תאורה — לאורך היום</div>
  <div class="scenes" id="sc-row"></div>
</div>
<div class="section">
  <div class="section-title">🏗 מערכות תאורה בפרויקט</div>
  <p style="color:var(--muted);font-size:13px;line-height:1.8">
    {'&nbsp; · &nbsp;'.join([l.name for l in room.layers if l.enabled] or ['—'])}</p>
</div>
<div class="footer">
  הופק ע"י {room.branding.company_name} &nbsp;|&nbsp;
  Lighting Design Pro V8 &nbsp;|&nbsp;
  {dt.datetime.now().strftime('%d/%m/%Y %H:%M')}
</div>
<script>
const scenes = {scenes_js};
const lc = {{"משימה":"#F0A030","מבטא":"#3D8EF0","אווירה":"#22D3EE",
             "Task":"#F0A030","Accent":"#3D8EF0","Ambient":"#22D3EE"}};
const row = document.getElementById('sc-row');
scenes.forEach((s,i) => {{
  const d = document.createElement('div');
  d.className = 'sc' + (i===0?' active':'');
  const bars = Object.entries(s.intensities||{{}}).map(([k,v]) =>
    `<div class="bar-row">
      <span style="width:32px;color:${{lc[k]||'#aaa'}};font-weight:700">${{k.slice(0,3)}}</span>
      <div class="bar-bg"><div class="bar-fill"
        style="width:${{v}}%;background:${{lc[k]||'#aaa'}}"></div></div>
      <span style="color:var(--muted);width:28px">${{v}}%</span>
    </div>`).join('');
  d.innerHTML = `
    <div class="sc-hour">${{String(s.hour).padStart(2,'0')}}:00</div>
    <div class="sc-name">${{s.label||'סצינה'}}</div>
    <div class="sc-cct">${{s.cct||3000}}K</div>
    <div class="bars">${{bars}}</div>`;
  d.onclick = () => {{
    document.querySelectorAll('.sc').forEach(c=>c.classList.remove('active'));
    d.classList.add('active');
  }};
  row.appendChild(d);
}});
</script>
</body></html>"""
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)
        return path


# ─────────────────────────────────────────────────────────────────────────────
# DIMENSION OVERLAY for RoomRenderer
# ─────────────────────────────────────────────────────────────────────────────

def draw_dimension_lines(painter: QPainter, renderer, room) -> None:
    """Draw width/length dimension lines with arrows around the room."""
    p = painter
    s, ox, oy = renderer._scale()

    def m2p(mx, my): return QPointF(ox + mx*s, oy + my*s)

    def arrow_line(p1: QPointF, p2: QPointF, label: str, color: QColor, offset: float):
        """Draw a dimension line with arrowheads and a centred label."""
        p.setPen(QPen(color, 1.5))
        # offset perpendicular
        dx = p2.x()-p1.x(); dy = p2.y()-p1.y()
        L = math.hypot(dx, dy)
        if L < 1: return
        nx, ny = -dy/L*offset, dx/L*offset
        a = QPointF(p1.x()+nx, p1.y()+ny)
        b = QPointF(p2.x()+nx, p2.y()+ny)
        p.drawLine(a, b)
        # arrows
        arrow = 8
        for tip, base in [(a, b), (b, a)]:
            adx = tip.x()-base.x(); ady = tip.y()-base.y()
            al = math.hypot(adx, ady)
            if al < 1: continue
            adx /= al; ady /= al
            p.drawLine(tip,
                QPointF(tip.x()-adx*arrow+ady*4,
                        tip.y()-ady*arrow-adx*4))
            p.drawLine(tip,
                QPointF(tip.x()-adx*arrow-ady*4,
                        tip.y()-ady*arrow+adx*4))
        # label
        mid = QPointF((a.x()+b.x())/2+nx*0.4, (a.y()+b.y())/2+ny*0.4)
        p.setFont(QFont("Segoe UI", 9, QFont.Bold))
        p.setPen(color)
        p.drawText(QRectF(mid.x()-30, mid.y()-10, 60, 20),
                   Qt.AlignCenter, label)

    dim_color = QColor(100, 140, 220, 180)
    arrow_line(m2p(0,0), m2p(room.width,0),
               f"{room.width:.2f} m", dim_color, -28)
    arrow_line(m2p(0,0), m2p(0,room.length),
               f"{room.length:.2f} m", dim_color, -34)


__all__ = [
    "V8_STYLESHEET",
    "v8_card", "v8_label", "v8_btn", "v8_section_header",
    "KPIBadge", "KPIRow",
    "ViewToggleBar",
    "NLWizardWidget",
    "DesignPackagesWidget", "PACKAGES",
    "SceneTimelineWidget",
    "SurfaceMaterialsWidget", "MATERIALS",
    "StickyNotesPanel",
    "SnapshotsPanel",
    "ClientHTMLExporter",
    "draw_dimension_lines",
]


# ──────────────────────────────────────────────────────────────────────
# INLINED: v8_team_fixes.py
# ──────────────────────────────────────────────────────────────────────
# -*- coding: utf-8 -*-
"""
V8 Team Fixes
=============
Every person at the table fixed their domain.
"""

from PySide6.QtCore  import QPointF, QRectF, Qt, Signal, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui   import (QColor, QCursor, QFont, QLinearGradient,
                              QPainter, QPen, QRadialGradient, QPixmap,
                              QBrush, QPainterPath)
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QSplitter, QTabWidget, QTextEdit, QVBoxLayout,
    QWidget, QApplication, QFileDialog, QMessageBox,
    QDoubleSpinBox, QCheckBox, QGridLayout, QToolButton,
    QProgressBar, QDialog, QStackedWidget
)

# ═══════════════════════════════════════════════════════════════════════════
# SHARED PALETTE (single source of truth — replaces both old P dict and CSS vars)
# ═══════════════════════════════════════════════════════════════════════════

DESIGN_TOKENS = {
    # backgrounds
    "bg_deep":   "#070A12",
    "bg":        "#0B0E16",
    "bg_card":   "#0F1520",
    "bg_card2":  "#131B28",
    "bg_input":  "#0D1018",
    "bg_hover":  "#141C2C",
    # borders
    "border":    "#1A2235",
    "border2":   "#243050",
    "border_focus":"#3D8EF0",
    # text
    "text":      "#E8EDF8",
    "text_sub":  "#A0AABF",
    "text_muted":"#5A6480",
    "text_dim":  "#3A4468",
    # brand colours
    "blue":      "#3D8EF0",
    "blue_light":"#6AABFF",
    "blue_dark": "#1A5AC8",
    "green":     "#2ECC7A",
    "amber":     "#F0A030",
    "red":       "#EF4444",
    "purple":    "#9F7AEA",
    "cyan":      "#22D3EE",
    "gold":      "#D4A850",
    "gold_dim":  "#8A6820",
}

T = DESIGN_TOKENS  # shorthand


FULL_STYLESHEET = f"""
/* ── Reset ──────────────────────────────────────────────────────── */
* {{ box-sizing: border-box; outline: none; }}
QWidget {{ font-family: 'Segoe UI Variable', 'Segoe UI', 'SF Pro Text', Arial, sans-serif; font-size: 13px; }}

/* ── Shell ───────────────────────────────────────────────────────── */
QMainWindow, QDialog {{ background: {T['bg']}; color: {T['text']}; }}
QWidget {{ background: transparent; color: {T['text']}; }}

/* ── Scrollbars ──────────────────────────────────────────────────── */
QScrollBar:vertical   {{ background: {T['bg_deep']}; width: 5px; border-radius: 3px; }}
QScrollBar:horizontal {{ background: {T['bg_deep']}; height: 5px; border-radius: 3px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {T['border2']}; border-radius: 3px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
    background: {T['blue']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
QScrollBar::corner {{ background: {T['bg_deep']}; }}
QScrollArea {{ border: none; background: transparent; }}

/* ── Inputs ──────────────────────────────────────────────────────── */
QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {T['bg_input']}; border: 1px solid {T['border']};
    border-radius: 7px; padding: 6px 10px;
    color: {T['text']}; min-height: 32px;
    selection-background-color: {T['blue_dark']};
}}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {T['blue']}; background: {T['bg_card2']};
}}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{
    border-color: {T['border2']};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: none; width: 0; }}
QComboBox QAbstractItemView {{
    background: {T['bg_card2']}; border: 1px solid {T['border2']};
    border-radius: 8px; padding: 4px; color: {T['text']};
    selection-background-color: {T['blue_dark']};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: {T['border']}; border: none; width: 16px; border-radius: 3px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {T['blue']}; }}

/* ── Buttons ─────────────────────────────────────────────────────── */
QPushButton {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {T['blue_light']}, stop:1 {T['blue']});
    color: #fff; border: none; border-radius: 8px;
    padding: 8px 16px; font-weight: 700; font-size: 12px; letter-spacing: 0.3px;
}}
QPushButton:hover {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #80BFFF, stop:1 {T['blue_light']});
}}
QPushButton:pressed {{ padding-top: 9px; padding-bottom: 7px; background: {T['blue_dark']}; }}
QPushButton:disabled {{ background: {T['border']}; color: {T['text_dim']}; }}
QPushButton#secondary {{
    background: {T['bg_card']}; color: {T['text_sub']};
    border: 1px solid {T['border']};
}}
QPushButton#secondary:hover {{ background: {T['bg_hover']}; color: {T['text']}; border-color: {T['border2']}; }}
QPushButton#green  {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #45E090,stop:1 {T['green']}); }}
QPushButton#green:hover {{ background: #50EEA0; }}
QPushButton#amber  {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #F8B845,stop:1 {T['amber']}); }}
QPushButton#amber:hover {{ background: #F8C060; }}
QPushButton#danger {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #F57070,stop:1 {T['red']}); }}
QPushButton#danger:hover {{ background: #F58080; }}
QPushButton#gold   {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #E8C060,stop:1 {T['gold']}); color:#111; }}
QPushButton#gold:hover {{ background: #F0D070; }}

/* ── Tabs ────────────────────────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {T['border']}; border-radius: 10px;
    background: {T['bg_card']};
}}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {T['text_muted']};
    padding: 9px 15px; border-bottom: 2px solid transparent;
    font-weight: 600; font-size: 12px; letter-spacing: 0.2px;
}}
QTabBar::tab:selected {{ color: {T['text']}; border-bottom-color: {T['blue']}; }}
QTabBar::tab:hover:!selected {{ color: {T['text_sub']}; border-bottom-color: {T['border2']}; }}

/* ── Check ───────────────────────────────────────────────────────── */
QCheckBox {{ spacing: 8px; color: {T['text_sub']}; background: transparent; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {T['border2']}; border-radius: 4px;
    background: {T['bg_input']};
}}
QCheckBox::indicator:checked {{ background: {T['blue']}; border-color: {T['blue']}; }}
QCheckBox::indicator:hover   {{ border-color: {T['blue']}; }}

/* ── Slider ──────────────────────────────────────────────────────── */
QSlider::groove:horizontal {{ height: 4px; background: {T['border']}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -5px 0;
    background: {T['blue']}; border-radius: 7px;
    border: 2px solid {T['bg']};
}}
QSlider::sub-page:horizontal {{ background: {T['blue']}; border-radius: 2px; }}

/* ── GroupBox ────────────────────────────────────────────────────── */
QGroupBox {{
    border: 1px solid {T['border']}; border-radius: 8px;
    margin-top: 12px; padding-top: 10px;
    color: {T['text_muted']}; font-weight: 700; font-size: 11px; background: transparent;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}

/* ── Toolbar ─────────────────────────────────────────────────────── */
QToolBar {{
    background: {T['bg_deep']}; border-bottom: 1px solid {T['border']};
    spacing: 2px; padding: 3px 8px;
}}
QToolBar QToolButton, QToolBar QPushButton {{
    background: transparent; color: {T['text_muted']};
    border: none; border-radius: 6px;
    padding: 5px 10px; font-size: 12px; font-weight: 600;
    min-height: 28px;
}}
QToolBar QToolButton:hover, QToolBar QPushButton:hover {{
    background: {T['bg_hover']}; color: {T['text']};
}}
QToolBar::separator {{ background: {T['border']}; width: 1px; margin: 4px 4px; }}

/* ── StatusBar ───────────────────────────────────────────────────── */
QStatusBar {{
    background: {T['bg_deep']}; color: {T['text_muted']};
    border-top: 1px solid {T['border']}; font-size: 11px; padding: 0 10px;
}}
QStatusBar::item {{ border: none; }}

/* ── MenuBar + Menu ─────────────────────────────────────────────── */
QMenuBar {{
    background: {T['bg_deep']}; color: {T['text_muted']};
    border-bottom: 1px solid {T['border']}; padding: 2px 6px;
}}
QMenuBar::item {{ padding: 5px 10px; border-radius: 5px; }}
QMenuBar::item:selected {{ background: {T['bg_hover']}; color: {T['text']}; }}
QMenu {{
    background: {T['bg_card2']}; border: 1px solid {T['border2']};
    border-radius: 10px; padding: 5px; color: {T['text']};
}}
QMenu::item {{ padding: 8px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {T['blue_dark']}; }}
QMenu::separator {{ height: 1px; background: {T['border']}; margin: 4px 10px; }}

/* ── Splitter ────────────────────────────────────────────────────── */
QSplitter::handle {{ background: {T['border']}; }}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical   {{ height: 3px; }}
QSplitter::handle:hover {{ background: {T['blue']}; }}

/* ── Frame dividers ─────────────────────────────────────────────── */
QFrame[frameShape="4"] {{ color: {T['border']}; max-height: 1px; border: none; }}
QFrame[frameShape="5"] {{ color: {T['border']}; max-width: 1px; border: none; }}

/* ── ProgressBar ─────────────────────────────────────────────────── */
QProgressBar {{
    background: {T['border']}; border-radius: 4px; height: 6px; border: none; text-align: center;
}}
QProgressBar::chunk {{ background: {T['blue']}; border-radius: 4px; }}
"""

# ═══════════════════════════════════════════════════════════════════════════
# SPLASH SCREEN  (graphic designer's rebuild — Hebrew, animated beams)
# ═══════════════════════════════════════════════════════════════════════════

class PremiumSplash(QDialog):
    """Animated splash — Hebrew, branded, smooth fade-in."""
    def __init__(self):
        super().__init__()
        self._t = 0
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.SplashScreen | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(640, 360)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(48, 52, 48, 40)
        lay.setSpacing(0)

        self._logo_lbl = QLabel("◈")
        self._logo_lbl.setAlignment(Qt.AlignCenter)
        self._logo_lbl.setStyleSheet(
            f"color:{T['blue']};font-size:40px;background:transparent;border:none;")
        lay.addWidget(self._logo_lbl)
        lay.addSpacing(12)

        self._title = QLabel("Lighting Design Pro")
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setStyleSheet(
            f"color:{T['text']};font-size:30px;font-weight:900;"
            "background:transparent;border:none;letter-spacing:1px;")
        lay.addWidget(self._title)

        self._ver = QLabel("V8.0")
        self._ver.setAlignment(Qt.AlignCenter)
        self._ver.setStyleSheet(
            f"color:{T['blue']};font-size:13px;font-weight:700;"
            "background:transparent;border:none;letter-spacing:3px;")
        lay.addWidget(self._ver)
        lay.addSpacing(24)

        self._sub = QLabel("כלי תכנון תאורה מקצועי לאדריכלים ומעצבים")
        self._sub.setAlignment(Qt.AlignCenter)
        self._sub.setStyleSheet(
            f"color:{T['text_muted']};font-size:14px;"
            "background:transparent;border:none;")
        lay.addWidget(self._sub)
        lay.addStretch()

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setFixedHeight(4)
        self._bar.setStyleSheet(
            f"QProgressBar{{background:{T['border']};border-radius:2px;border:none;}}"
            f"QProgressBar::chunk{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {T['blue']},stop:1 {T['cyan']});border-radius:2px;}}")
        lay.addWidget(self._bar)

        self._status = QLabel("טוען מנוע תאורה...")
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet(
            f"color:{T['text_dim']};font-size:11px;"
            "background:transparent;border:none;margin-top:6px;")
        lay.addWidget(self._status)

        msgs = ["טוען מנוע תאורה", "מאתחל חישובי לוקס",
                "בונה ממשק", "טוען קטלוג גופים", "מוכן"]
        self._msgs = msgs
        self._msg_idx = 0

        timer = QTimer(self)
        timer.timeout.connect(self._tick)
        timer.start(60)
        self._timer = timer

    def _tick(self):
        self._t += 1
        v = min(int(self._t * 1.8), 100)
        self._bar.setValue(v)
        idx = min(int(v / 25), len(self._msgs)-1)
        if idx != self._msg_idx:
            self._msg_idx = idx
            self._status.setText(self._msgs[idx] + "...")
        self.update()
        if v >= 100:
            self._timer.stop()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(6, 6, self.width()-12, self.height()-12)
        # card background
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0,   QColor("#0E1628"))
        g.setColorAt(0.5, QColor("#0A1420"))
        g.setColorAt(1,   QColor("#060C18"))
        p.setBrush(g)
        p.setPen(QPen(QColor(T['border2']), 1))
        p.drawRoundedRect(r, 20, 20)
        # animated light beams
        for i in range(5):
            phase = self._t * 0.03 + i * 1.25
            alpha = int(18 + 12 * abs(math.sin(phase)))
            y = 80 + i * 44
            beam = QColor(T['blue'])
            beam.setAlpha(alpha)
            p.setPen(QPen(beam, 1.5))
            p.drawLine(
                QPointF(60, y),
                QPointF(self.width()-60,
                        y + math.sin(phase) * 18))
        # subtle glow behind logo
        cx, cy = self.width()/2, 80
        glow = QRadialGradient(cx, cy, 60)
        glow.setColorAt(0, QColor(61, 142, 240, 35))
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen); p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), 60, 60)
        p.end()


# ═══════════════════════════════════════════════════════════════════════════
# EMPTY STATE  (shown when no project loaded)
# ═══════════════════════════════════════════════════════════════════════════

class EmptyStateRenderer(QWidget):
    """Shown instead of black viewport when no room is loaded."""
    newRequested    = Signal()
    openRequested   = Signal()
    packageSelected = Signal(str)

    QUICK_STARTS = [
        ("סלון מינימליסטי",  "🏠", T['blue']),
        ("מטבח מקצועי",      "🍳", T['amber']),
        ("חדר ישיבות",       "💼", T['purple']),
        ("חנות ריטייל",      "🛍️", T['green']),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(520, 360)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(20)

        icon = QLabel("◈")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(
            f"color:{T['border2']};font-size:64px;background:transparent;border:none;")
        lay.addWidget(icon)

        title = QLabel("פתח פרויקט חדש")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"color:{T['text_sub']};font-size:20px;font-weight:700;"
            "background:transparent;border:none;")
        lay.addWidget(title)

        sub = QLabel("בחר נקודת התחלה מהירה — או פתח קובץ קיים")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(
            f"color:{T['text_dim']};font-size:13px;"
            "background:transparent;border:none;")
        lay.addWidget(sub)

        # quick start grid
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setSpacing(10)
        for i, (label, icon_e, color) in enumerate(self.QUICK_STARTS):
            btn = QPushButton(f"{icon_e}  {label}")
            btn.setObjectName("secondary")
            btn.setFixedSize(170, 52)
            btn.setStyleSheet(
                f"QPushButton{{background:{T['bg_card']};color:{color};"
                f"border:1px solid {color}40;border-radius:10px;"
                "font-size:12px;font-weight:700;}}"
                f"QPushButton:hover{{background:{color}18;"
                f"border-color:{color};color:{color};}}")
            btn.clicked.connect(lambda _, l=label: self.packageSelected.emit(l))
            grid.addWidget(btn, i//2, i%2)
        lay.addWidget(grid_w)

        # or buttons row
        btn_row = QHBoxLayout(); btn_row.setSpacing(10)
        new_btn = QPushButton("✦  פרויקט חדש")
        new_btn.setFixedHeight(40)
        new_btn.clicked.connect(self.newRequested)
        open_btn = QPushButton("📂  פתח קובץ")
        open_btn.setObjectName("secondary")
        open_btn.setFixedHeight(40)
        open_btn.clicked.connect(self.openRequested)
        btn_row.addWidget(new_btn); btn_row.addWidget(open_btn)
        lay.addLayout(btn_row)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(T['bg']))
        # subtle dot grid
        p.setPen(QPen(QColor(T['border']), 1))
        for x in range(0, self.width(), 28):
            for y in range(0, self.height(), 28):
                p.drawPoint(x, y)
        p.end()


# ═══════════════════════════════════════════════════════════════════════════
# TOOLBAR  (grouped, not a wall of 14 buttons)
# ═══════════════════════════════════════════════════════════════════════════

def build_toolbar_v8(app_win) -> None:
    """Replace flat toolbar with grouped, visual toolbar."""
    tb = app_win.main_toolbar
    tb.clear()

    def _act(icon: str, label: str, slot, shortcut: str = "") -> None:
        from PySide6.QtWidgets import QToolButton
        btn = QToolButton()
        btn.setText(f"{icon}\n{label}" if icon else label)
        btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        btn.setFixedSize(56, 48)
        btn.setStyleSheet(
            f"QToolButton{{background:transparent;color:{T['text_muted']};"
            f"border:none;border-radius:8px;font-size:10px;font-weight:600;}}"
            f"QToolButton:hover{{background:{T['bg_hover']};color:{T['text']};}}"
            f"QToolButton:pressed{{background:{T['border']};}}")
        if shortcut:
            btn.setShortcut(shortcut)
        btn.clicked.connect(slot)
        tb.addWidget(btn)

    def _sep():
        sep = QFrame(); sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet(
            f"color:{T['border']};background:{T['border']};"
            "max-width:1px;margin:8px 4px;border:none;")
        tb.addWidget(sep)

    # File group
    _act("🆕", "חדש",     app_win.new_project,     "Ctrl+N")
    _act("📂", "פתח",     app_win.open_project,    "Ctrl+O")
    _act("💾", "שמור",    app_win.save_project,    "Ctrl+S")
    _sep()
    # Edit group
    _act("↩", "ביטול",   app_win._undo,           "Ctrl+Z")
    _act("↪", "חזרה",    app_win._redo,           "Ctrl+Y")
    _sep()
    # Import group
    _act("📡", "IES/LDT", app_win.import_ies,      "")
    _act("🗺", "תכנית",   app_win.import_floor_plan,"")
    _act("📋", "קטלוג",   app_win.import_catalogue, "")
    _sep()
    # Export group
    _act("📄", "PDF",      app_win.export_quote,    "")
    _act("📐", "DXF",      app_win.export_dxf,      "")
    _act("🌐", "שתף",     app_win.export_client_html,"")
    _sep()
    # AI group
    _act("🤖", "AI סקירה", app_win.run_ai_review,   "")

    # stretch + language on right
    spacer = QWidget()
    spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    tb.addWidget(spacer)

    lang_lbl = QLabel("שפה:")
    lang_lbl.setStyleSheet(
        f"color:{T['text_muted']};background:transparent;border:none;font-size:11px;")
    tb.addWidget(lang_lbl)

    app_win.language_combo = QComboBox()
    app_win.language_combo.addItem("עברית", "he")
    app_win.language_combo.addItem("English", "en")
    app_win.language_combo.setCurrentIndex(0)
    app_win.language_combo.setFixedWidth(90)
    app_win.language_combo.currentIndexChanged.connect(app_win._language_changed)
    tb.addWidget(app_win.language_combo)


# ═══════════════════════════════════════════════════════════════════════════
# KPI ROW  (graphic designer's rebuild — bigger, clearer, colour-coded)
# ═══════════════════════════════════════════════════════════════════════════

class KPICell(QWidget):
    """Single KPI cell with large number, label, status bar."""
    def __init__(self, label: str, unit: str = "", accent: str = T['blue'], parent=None):
        super().__init__(parent)
        self._accent = accent
        self.setFixedHeight(64)
        self.setMinimumWidth(110)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(1)

        self._val = QLabel("—")
        self._val.setFont(QFont("Segoe UI Variable", 20, QFont.Bold))
        self._val.setStyleSheet(
            f"color:{accent};background:transparent;border:none;")

        self._lbl = QLabel(label + (f"  {unit}" if unit else ""))
        self._lbl.setStyleSheet(
            f"color:{T['text_dim']};font-size:10px;"
            "background:transparent;border:none;letter-spacing:0.5px;")

        lay.addWidget(self._val)
        lay.addWidget(self._lbl)
        self._status = "neutral"

    def set_value(self, val: str, status: str = "neutral"):
        self._status = status
        colors = {"ok": T['green'], "warn": T['amber'],
                  "bad": T['red'],  "neutral": self._accent}
        c = colors.get(status, self._accent)
        self._val.setText(val)
        self._val.setStyleSheet(
            f"color:{c};background:transparent;border:none;")
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0, 0, self.width(), self.height())
        p.setBrush(QColor(T['bg_card']))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(r, 8, 8)
        colors = {"ok": T['green'], "warn": T['amber'],
                  "bad": T['red'],  "neutral": self._accent}
        c = QColor(colors.get(self._status, self._accent))
        p.setBrush(c); p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(0, self.height()-3, self.width(), 3), 1.5, 1.5)
        p.end()


class KPIStrip(QWidget):
    """Strip of 5 KPI cells — compact, always visible."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(68)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(6)

        self.lux   = KPICell("לוקס ממוצע",   "lx",    T['blue'])
        self.uni   = KPICell("אחידות U0",    "",      T['cyan'])
        self.cri   = KPICell("CRI",          "",      T['purple'])
        self.watts = KPICell("הספק",         "W",     T['amber'])
        self.cct   = KPICell("טמפרטורה",     "K",     T['gold'])

        for c in [self.lux, self.uni, self.cri, self.watts, self.cct]:
            lay.addWidget(c)

    def update_from_snap(self, snap, room):
        if not snap: return
        avg = snap.avg_lux
        tgt = getattr(room, "lux_target", 200)
        uni = snap.min_lux / avg if avg > 0 else 0
        cri = snap.cri
        w   = snap.watts
        cct = getattr(room, "cct_kelvin", 3000)

        ratio = avg / max(tgt, 1)
        self.lux.set_value(
            f"{avg:.0f}",
            "ok" if 0.9 <= ratio <= 1.4 else "warn" if ratio > 0.5 else "bad")
        self.uni.set_value(
            f"{uni:.2f}",
            "ok" if uni >= 0.4 else "warn" if uni >= 0.25 else "bad")
        self.cri.set_value(
            f"{cri:.0f}",
            "ok" if cri >= 90 else "warn" if cri >= 80 else "bad")
        self.watts.set_value(f"{w:.0f}", "neutral")
        self.cct.set_value(f"{cct}", "neutral")


# ═══════════════════════════════════════════════════════════════════════════
# TAB NAMES  (PM fix — consistent Hebrew, no random English)
# ═══════════════════════════════════════════════════════════════════════════

LEFT_TABS = [
    ("✦ פתיחה",     "wizard"),
    ("📐 חדר",       "basic"),
    ("📋 Brief",     "brief"),
    ("💡 שכבות",    "layers"),
    ("⚡ חשמל",     "energy"),
    ("🏆 מקצועי",   "pro"),
    ("⚙️ פרויקט",   "project"),
]

RESULT_TABS = [
    "📊 סיכום", "🗺 מפת אור", "📍 נקודתי",
    "✅ תקן EN", "🏗 Zones", "🤖 AI",
    "⚙️ Validation", "📋 קטלוג", "⚡ חשמל",
    "🎨 Design", "🔄 A/B/C", "📈 Before/After",
    "💰 תמחור", "⏰ סצינות", "🏠 חומרים",
    "📌 הערות", "📷 גרסאות",
]


# ═══════════════════════════════════════════════════════════════════════════
# CCT VISUAL SELECTOR  (interior designer request)
# ═══════════════════════════════════════════════════════════════════════════

class CCTVisualSelector(QWidget):
    """Visual warm→cool selector with colour preview bar."""
    changed = Signal(int)   # emits CCT value

    CCT_STEPS = [
        (1800, "נר",      "#FF9020"),
        (2200, "ספא",     "#FFAA40"),
        (2700, "חמים",    "#FFB860"),
        (3000, "נייטרל",  "#FFCF80"),
        (3500, "לבן חם",  "#FFE0A0"),
        (4000, "יום",     "#FFF4D0"),
        (5000, "קר",      "#E8F4FF"),
        (6500, "שמיים",   "#D0E8FF"),
    ]

    def __init__(self, current: int = 3000, parent=None):
        super().__init__(parent)
        self._value = current
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        # gradient bar
        self._bar = _CCTBar()
        self._bar.setFixedHeight(18)
        lay.addWidget(self._bar)

        # step buttons
        btns_w = QWidget()
        btns_lay = QHBoxLayout(btns_w)
        btns_lay.setContentsMargins(0, 0, 0, 0)
        btns_lay.setSpacing(3)
        self._btns: Dict[int, QPushButton] = {}
        for cct, label, _ in self.CCT_STEPS:
            btn = QPushButton(label)
            btn.setFixedHeight(26)
            btn.setCheckable(True)
            btn.setChecked(cct == current)
            btn.setStyleSheet(
                f"QPushButton{{background:{T['bg_card']};color:{T['text_muted']};"
                f"border:1px solid {T['border']};border-radius:5px;"
                "font-size:10px;font-weight:600;}}"
                f"QPushButton:checked{{background:{T['blue']}20;"
                f"color:{T['blue']};border-color:{T['blue']};}}"
                f"QPushButton:hover:!checked{{background:{T['bg_hover']};}}")
            btn.clicked.connect(lambda _, c=cct: self._select(c))
            btns_lay.addWidget(btn)
            self._btns[cct] = btn
        lay.addWidget(btns_w)

        self._cur_lbl = QLabel(f"{current} K")
        self._cur_lbl.setStyleSheet(
            f"color:{T['text_sub']};font-size:11px;background:transparent;border:none;")
        lay.addWidget(self._cur_lbl)

    def _select(self, cct: int):
        self._value = cct
        for c, b in self._btns.items():
            b.setChecked(c == cct)
        self._cur_lbl.setText(f"{cct} K")
        self.changed.emit(cct)

    def value(self) -> int: return self._value
    def setValue(self, v: int): self._select(v)


class _CCTBar(QWidget):
    def paintEvent(self, _):
        p = QPainter(self)
        g = QLinearGradient(0, 0, self.width(), 0)
        g.setColorAt(0,   QColor("#FF8010"))
        g.setColorAt(0.2, QColor("#FFAA40"))
        g.setColorAt(0.4, QColor("#FFD080"))
        g.setColorAt(0.6, QColor("#FFF0C0"))
        g.setColorAt(0.8, QColor("#E8F4FF"))
        g.setColorAt(1,   QColor("#C0DCFF"))
        p.fillRect(self.rect(), g)
        p.end()


# ═══════════════════════════════════════════════════════════════════════════
# DIMENSION LINES on RoomRenderer  (architect request)
# ═══════════════════════════════════════════════════════════════════════════

def draw_dimension_overlay(painter: QPainter, renderer) -> None:
    """
    Draw clean dimension lines with arrows around the floor plan.
    Call from RoomRenderer.paintEvent after drawing the room.
    """
    if not renderer.room: return
    room = renderer.room
    p = painter
    s, ox, oy = renderer._scale()

    def m2px(mx, my): return QPointF(ox + mx*s, oy + my*s)

    dim_color = QColor(100, 150, 240, 160)
    text_color = QColor(180, 200, 255, 220)
    OFFSET_PX = 24

    def arrow_dim(pt1: QPointF, pt2: QPointF, label: str,
                  normal_x: float, normal_y: float):
        nx, ny = normal_x * OFFSET_PX, normal_y * OFFSET_PX
        a = QPointF(pt1.x() + nx, pt1.y() + ny)
        b = QPointF(pt2.x() + nx, pt2.y() + ny)
        # extension lines
        p.setPen(QPen(dim_color, 1, Qt.DashLine))
        p.drawLine(pt1, a); p.drawLine(pt2, b)
        # dimension line
        p.setPen(QPen(dim_color, 1.5))
        p.drawLine(a, b)
        # arrowheads
        dx = b.x()-a.x(); dy = b.y()-a.y()
        L = math.hypot(dx, dy)
        if L < 2: return
        ux, uy = dx/L, dy/L
        arr = 7
        for tip, sign in [(a, 1), (b, -1)]:
            ax = tip.x() + sign*ux*arr
            ay = tip.y() + sign*uy*arr
            p.drawLine(tip, QPointF(ax + uy*3, ay - ux*3))
            p.drawLine(tip, QPointF(ax - uy*3, ay + ux*3))
        # label
        mid = QPointF((a.x()+b.x())/2, (a.y()+b.y())/2)
        p.setFont(QFont("Segoe UI", 9, QFont.Bold))
        p.setPen(text_color)
        p.drawText(QRectF(mid.x()-28, mid.y()-10, 56, 20),
                   Qt.AlignCenter, label)

    tl = m2px(0, 0)
    tr = m2px(room.width, 0)
    bl = m2px(0, room.length)

    arrow_dim(tl, tr, f"{room.width:.2f} m",  0, -1)
    arrow_dim(tl, bl, f"{room.length:.2f} m", -1,  0)


# ═══════════════════════════════════════════════════════════════════════════
# BUG FIXES  (QA + developer)
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# ONBOARDING OVERLAY  (PM + designer)
# ═══════════════════════════════════════════════════════════════════════════

class OnboardingOverlay(QWidget):
    """
    First-launch overlay shown over the main window.
    Three steps: room → style → go.
    """
    finished = Signal()

    STEPS = [
        ("📐", "הגדר את החדר",
         "מה מידות החדר? מה סוג המרחב?"),
        ("🎨", "בחר סגנון",
         "בחר חבילת עיצוב התואמת את ה-brief של הלקוח."),
        ("🚀", "חשב ובדוק",
         "לחץ חשב — הכלי ימלא ספוטים, יחשב לוקס ויבדוק תקן EN 12464."),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._step = 0
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet(f"background:rgba(7,10,18,0.88);")
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)

        self._card = QWidget()
        self._card.setFixedWidth(420)
        self._card.setStyleSheet(
            f"background:{T['bg_card2']};border:1px solid {T['border2']};"
            "border-radius:18px;")
        card_lay = QVBoxLayout(self._card)
        card_lay.setContentsMargins(36, 36, 36, 36)
        card_lay.setSpacing(14)

        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setStyleSheet("font-size:40px;background:transparent;border:none;")

        self._title = QLabel()
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setStyleSheet(
            f"color:{T['text']};font-size:20px;font-weight:900;"
            "background:transparent;border:none;")

        self._desc = QLabel()
        self._desc.setAlignment(Qt.AlignCenter)
        self._desc.setWordWrap(True)
        self._desc.setStyleSheet(
            f"color:{T['text_muted']};font-size:13px;"
            "background:transparent;border:none;")

        # progress dots
        dots_w = QWidget(); dots_lay = QHBoxLayout(dots_w)
        dots_lay.setAlignment(Qt.AlignCenter); dots_lay.setSpacing(8)
        self._dots: List[QLabel] = []
        for _ in range(len(self.STEPS)):
            d = QLabel("●")
            d.setStyleSheet(f"color:{T['text_dim']};background:transparent;border:none;font-size:10px;")
            dots_lay.addWidget(d); self._dots.append(d)

        self._next_btn = QPushButton("הבא ←")
        self._next_btn.setFixedHeight(44)
        self._next_btn.clicked.connect(self._advance)

        skip_btn = QPushButton("דלג")
        skip_btn.setObjectName("secondary")
        skip_btn.setFixedHeight(36)
        skip_btn.clicked.connect(self.finished)

        card_lay.addWidget(self._icon)
        card_lay.addWidget(self._title)
        card_lay.addWidget(self._desc)
        card_lay.addSpacing(8)
        card_lay.addWidget(dots_w)
        card_lay.addWidget(self._next_btn)
        card_lay.addWidget(skip_btn)

        lay.addWidget(self._card)
        self._update_step()

    def _update_step(self):
        icon, title, desc = self.STEPS[self._step]
        self._icon.setText(icon)
        self._title.setText(title)
        self._desc.setText(desc)
        last = self._step == len(self.STEPS) - 1
        self._next_btn.setText("בוא נתחיל! 🚀" if last else "הבא ←")
        for i, d in enumerate(self._dots):
            d.setStyleSheet(
                f"color:{T['blue'] if i <= self._step else T['text_dim']};"
                "background:transparent;border:none;font-size:10px;")

    def _advance(self):
        if self._step < len(self.STEPS) - 1:
            self._step += 1
            self._update_step()
        else:
            self.finished.emit()

    def resizeEvent(self, e):
        if self.parent():
            self.setGeometry(self.parent().rect())
        super().resizeEvent(e)


__all__ = [
    "DESIGN_TOKENS", "T", "FULL_STYLESHEET",
    "PremiumSplash",
    "EmptyStateRenderer",
    "build_toolbar_v8",
    "KPICell", "KPIStrip",
    "LEFT_TABS", "RESULT_TABS",
    "CCTVisualSelector",
    "draw_dimension_overlay",
    "OnboardingOverlay",
]

# ── INLINE FLAGS ──────────────────────────────────────────────────────────
_V8_PATCH_LOADED    = True
_V8_CONTROLS_LOADED = True
_V8_UX_LOADED       = True
_V8_TEAM_LOADED     = True
HEET = f"""
QMainWindow, QWidget {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #0B0F18, stop:0.55 #111724, stop:1 #070A10);
    color: {P['text']};
    font-family: 'Segoe UI Variable', 'Segoe UI', Arial, sans-serif;
    font-size: 13px;
}}
QTabWidget::pane {{
    border: 1px solid {P['border']};
    background: #101622;
    border-radius: 10px;
}}
QTabBar::tab {{
    background: #10131B;
    color: {P['muted']};
    padding: 11px 17px;
    margin-right: 2px;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    font-weight: 700;
}}
QTabBar::tab:selected {{
    color: #F4F8FF;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #213052, stop:1 #151C2D);
    border-bottom: 2px solid {P['blue']};
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: #111723;
    border: 1px solid #34405F;
    border-radius: 8px;
    padding: 6px 9px;
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
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #1A2030, stop:1 #101622);
    border: 1px solid {P['border']};
    border-radius: 9px;
    color: {P['text']};
    padding: 10px;
}}
QScrollArea {{ border: none; background: transparent; }}
QStatusBar {{
    background: {P['bg']};
    color: {P['muted']};
    border-top: 1px solid {P['border']};
}}
QToolBar {{
    background: {P['bg']};
    border-bottom: 1px solid {P['border']};
    spacing: 4px;
}}
"""

# Maintained illuminance targets (lux), aligned with EN 12464-1:2021 task values
# where a workplace task exists. Residential spaces (סלון, חדר שינה) are NOT covered
# by EN 12464-1 — those are sensible design targets, flagged in the UI.
LUX_STANDARDS = {
    "סלון": 150,          # residential ambient (not an EN 12464 task value)
    "מטבח": 500,          # EN 12464 food prep task; ambient dining zone ~300
    "חדר שינה": 120,      # residential ambient (not an EN 12464 task value)
    "משרד": 500,          # EN 12464 writing/typing/reading
    "חדר עבודה": 500,     # EN 12464 technical drawing/office task
    "מסדרון": 100,        # EN 12464 circulation area
    "חדר אמבטיה": 200,    # EN 12464 bathrooms/washrooms
    "חנות": 300,          # EN 12464 retail sales area
    "מסעדה": 200,         # restaurant ambient
    "ספריה": 500,         # EN 12464 reading area
}
# Secondary/ambient illuminance for spaces that have a brighter task sub-zone.
LUX_AMBIENT_ZONES = {"מטבח": 300}
CRI_STANDARDS = {**{k: 80 for k in LUX_STANDARDS}, "מטבח": 90, "חדר אמבטיה": 90, "חנות": 90, "מסעדה": 90}
# EN 12464-1 UGR limits: offices/reading 19, kitchens/retail 22, circulation/bath 25.
UGR_LIMITS = {**{k: 22 for k in LUX_STANDARDS}, "משרד": 19, "חדר עבודה": 19, "ספריה": 19,
              "מסדרון": 25, "חדר אמבטיה": 25, "חדר שינה": 22}
# EN 12464-1 minimum uniformity (U0 = Emin/Eavg): 0.60 for task areas,
# 0.40 for circulation/immediate-surrounding areas.
UNIFORMITY_TARGETS = {
    "משרד": 0.60, "חדר עבודה": 0.60, "מטבח": 0.60, "ספריה": 0.60, "חנות": 0.60,
    "מסדרון": 0.40, "סלון": 0.40, "חדר שינה": 0.40, "חדר אמבטיה": 0.40, "מסעדה": 0.40,
}
# Residential room types where EN 12464-1 (a workplace standard) does not strictly apply.
NON_WORKPLACE_ROOM_TYPES = {"סלון", "חדר שינה"}


def uniformity_target(room_type: str) -> float:
    return UNIFORMITY_TARGETS.get(room_type, 0.60)
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
    "חמים (2700K)": (2700, 1.00),
    "נייטרל (3000K)": (3000, 1.00),
    "פוקוס (4000K)": (4000, 1.00),
    "הוספיטליטי (2200K)": (2200, 1.00),
}


def cct_preset_for_kelvin(kelvin: int) -> str:
    """Return the CCT_PRESETS key whose colour temperature is closest to ``kelvin``."""
    try:
        k = int(kelvin)
    except (TypeError, ValueError):
        return "נייטרל (3000K)"
    return min(CCT_PRESETS.items(), key=lambda kv: abs(kv[1][0] - k))[0]

# Normalized commercial fixture schema. Backward-compatible keys (lm, w, cri, beam,
# cct, brand, price, track_widths) are always present so LuxEngine / PricingEngine
# keep working; extra fields describe the product for spec sheets and BOQs.
FIXTURE_CATEGORIES = [
    "downlight", "gimbal", "linear", "magnetic", "wall_washer",
    "track", "exterior", "pendant", "strip", "cove",
]


def _fx(lm, w, cri, beam, cct, price, brand, category, *, sku="", mounting="recessed",
        ip="IP20", dimming="DALI/0-10V", cct_options=None, cct_default=None,
        beam_variants=None, length_m=None, lm_per_m=None, photometry_file="",
        ugr_rated=None, sdcm=3, lifetime=50000, track_widths=None) -> Dict:
    d = {
        # legacy / engine keys
        "lm": lm, "w": w, "cri": cri, "beam": beam, "cct": cct,
        "brand": brand, "price": price,
        # normalized schema
        "category": category, "sku": sku, "mounting": mounting, "ip": ip,
        "dimming": dimming, "cct_default": cct_default or cct,
        "cct_options": cct_options or [cct], "sdcm": sdcm, "lifetime": lifetime,
        "currency": "ILS", "efficacy": round(lm / max(w, 0.1), 1),
    }
    if beam_variants:
        d["beam_variants"] = beam_variants
    if length_m is not None:
        d["length_m"] = length_m
    if lm_per_m is not None:
        d["lm_per_m"] = lm_per_m
    if photometry_file:
        d["photometry_file"] = photometry_file
    if ugr_rated is not None:
        d["ugr_rated"] = ugr_rated
    if track_widths:
        d["track_widths"] = track_widths
    return d


DEFAULT_FIXTURES: Dict[str, Dict] = {
    # ── Recessed downlights / gimbals ──────────────────────────────────────
    "ספוט שקוע 36deg": _fx(850, 9, 90, 36, 3000, 95, "Generic", "downlight",
                            sku="DL-836-90", beam_variants=[24, 36, 60], ugr_rated=19),
    "ספוט מתכוונן 24deg": _fx(820, 9, 90, 24, 3000, 125, "Generic", "gimbal",
                              sku="GM-924-90", mounting="recessed-adjustable",
                              beam_variants=[15, 24, 36], ugr_rated=19),
    "דאונלайт CRI95 שקוע": _fx(1050, 13, 95, 60, 3000, 165, "Premium", "downlight",
                               sku="DL-1360-95", cct_options=[2700, 3000, 4000],
                               ugr_rated=19, sdcm=2),
    "גימבל CRI97 מוזיאון": _fx(680, 9, 97, 15, 3000, 240, "Museum", "gimbal",
                               sku="GM-915-97", mounting="recessed-adjustable",
                               beam_variants=[10, 15, 24], sdcm=2, ugr_rated=16),
    # ── Linear recessed / surface ──────────────────────────────────────────
    "פס ליניארי שקוע 1.2m": _fx(2640, 24, 90, 110, 3000, 320, "Generic", "linear",
                                sku="LN-R120", mounting="recessed", length_m=1.2,
                                lm_per_m=2200, ugr_rated=19),
    "פס ליניארי צמוד 1.5m": _fx(3450, 30, 90, 110, 4000, 380, "Generic", "linear",
                                sku="LN-S150", mounting="surface", length_m=1.5,
                                lm_per_m=2300, cct_options=[3000, 4000]),
    # ── Magnetic 48V modules (0.8 / 1.3 / 2.5 widths) ──────────────────────
    "Magnetic 0.8 Slim Linear 30": _fx(450, 6, 90, 110, 3000, 145, "Magnetic Mini",
                                       "magnetic", sku="MG08-L30", mounting="magnetic",
                                       length_m=0.3, lm_per_m=1500, track_widths=[0.8]),
    "Magnetic 1.3 Micro Spot": _fx(560, 7, 90, 36, 3000, 165, "Magnetic Micro",
                                   "magnetic", sku="MG13-S36", mounting="magnetic",
                                   beam_variants=[24, 36], track_widths=[1.3]),
    "Magnetic 1.3 Linear 60": _fx(980, 10, 90, 110, 3000, 235, "Magnetic Micro",
                                  "magnetic", sku="MG13-L60", mounting="magnetic",
                                  length_m=0.6, lm_per_m=1630, track_widths=[1.3]),
    "Magnetic 2.5 Linear 60": _fx(1300, 12, 90, 110, 3000, 260, "Magnetic 48V",
                                  "magnetic", sku="MG25-L60", mounting="magnetic",
                                  length_m=0.6, lm_per_m=2170, track_widths=[2.5]),
    "Magnetic 2.5 Adjustable Spot": _fx(1000, 11, 92, 24, 3000, 190, "Magnetic 48V",
                                        "magnetic", sku="MG25-S24", mounting="magnetic",
                                        beam_variants=[15, 24, 36], track_widths=[2.5]),
    "Magnetic 2.5 Pendant Module": _fx(1250, 13, 90, 80, 3000, 320, "Magnetic 48V",
                                       "magnetic", sku="MG25-P", mounting="magnetic-pendant",
                                       track_widths=[2.5]),
    # ── Wall washers ───────────────────────────────────────────────────────
    "Wall Washer 48V לינארי": _fx(1500, 14, 90, 60, 3000, 310, "Gaash style",
                                  "wall_washer", sku="WW-48-60", mounting="magnetic",
                                  beam_variants=[30, 60], track_widths=[2.5]),
    "Wall Washer שקוע אסימטרי": _fx(1900, 19, 90, 70, 3000, 360, "Generic",
                                    "wall_washer", sku="WW-RA-70", mounting="recessed"),
    # ── Classic track ──────────────────────────────────────────────────────
    "ספוט מסלול 24deg": _fx(1100, 11, 92, 24, 3000, 155, "TrackCo", "track",
                            sku="TR-S24", mounting="track-230V",
                            beam_variants=[15, 24, 38]),
    "ספוט מסלול CRI95 38deg": _fx(1250, 14, 95, 38, 3000, 210, "TrackCo", "track",
                                  sku="TR-S38-95", mounting="track-230V", sdcm=2),
    # ── Exterior IP65 ──────────────────────────────────────────────────────
    "חוץ IP65 שקוע קרקע": _fx(620, 8, 80, 30, 3000, 175, "ExteriorCo", "exterior",
                              sku="EX-G30", mounting="inground", ip="IP67"),
    "חוץ IP65 מבריק קיר": _fx(1100, 12, 80, 60, 4000, 230, "ExteriorCo", "exterior",
                              sku="EX-W60", mounting="surface", ip="IP65",
                              cct_options=[3000, 4000]),
    # ── Pendants / chandelier ──────────────────────────────────────────────
    "תלוי פנדנט": _fx(1600, 16, 90, 90, 3000, 280, "PendantCo", "pendant",
                      sku="PD-90", mounting="pendant"),
    "נברשת דקורטיבית": _fx(4500, 45, 90, 120, 2700, 980, "PendantCo", "pendant",
                           sku="PD-CH", mounting="pendant"),
    "פנדנט אקוסטי": _fx(2300, 22, 90, 100, 3000, 520, "Acoustic", "pendant",
                        sku="PD-AC", mounting="pendant"),
    # ── LED strip / profile (lm per metre) ─────────────────────────────────
    "LED Strip 2700K 14.4W/m": _fx(1300, 14, 90, 120, 2700, 90, "StripCo", "strip",
                                   sku="ST-2700", mounting="profile", length_m=1.0,
                                   lm_per_m=1300, cct_options=[2700, 3000]),
    "LED Strip 3000K 19W/m": _fx(1900, 19, 95, 120, 3000, 130, "StripCo", "strip",
                                 sku="ST-3000-95", mounting="profile", length_m=1.0,
                                 lm_per_m=1900, sdcm=2),
    # ── Cove / indirect ────────────────────────────────────────────────────
    "Cove אינדירקט 3000K": _fx(900, 10, 90, 120, 3000, 110, "Generic", "cove",
                               sku="CV-3000", mounting="cove", length_m=1.0,
                               lm_per_m=900),
}

PENDANT_TYPES = ["פנדנט בודד", "שורת פנדנטים", "נברשת", "פנדנט אקוסטי"]
MAGNETIC_TRACK_WIDTHS = [0.8, 1.3, 2.5]

DESIGN_PRESETS: Dict[str, Dict] = {
    "Modern Luxury": {"cct": "נייטרל (3000K)", "feeling": "Luxury", "language": "Architectural and minimal", "layers": "Hidden profiles, magnetic tracks, controlled pendants"},
    "Warm Hospitality": {"cct": "חמים (2700K)", "feeling": "Warm", "language": "Hidden and delicate", "layers": "Indirect light, pendants, soft wall washing"},
    "Minimal Gallery": {"cct": "פוקוס (4000K)", "feeling": "Focused", "language": "Architectural and minimal", "layers": "Tracks, narrow beams, clean profiles"},
    "Calm Residential": {"cct": "נייטרל (3000K)", "feeling": "Calm", "language": "Hidden and delicate", "layers": "Soft ambient light, task lighting, low glare"},
}

SPACE_TEMPLATES: Dict[str, Dict] = {
    "Living room": {"room_type": "סלון", "lux": 150, "scenes": "אירוח, ערב רגוע, צפייה בטלוויזיה, ניקיון"},
    "Kitchen island": {"room_type": "מטבח", "lux": 500, "scenes": "בישול, עבודה, ניקיון, אירוח"},
    "Dining area": {"room_type": "סלון", "lux": 300, "scenes": "ארוחה, אירוח, ערב רגוע"},
    "Bedroom": {"room_type": "חדר שינה", "lux": 120, "scenes": "לילה, קריאה, התארגנות, רוגע"},
    "Office": {"room_type": "משרד", "lux": 500, "scenes": "עבודה, שיחה, ניקיון, וידאו"},
    "Retail / showroom": {"room_type": "חנות", "lux": 800, "scenes": "תצוגה, מכירה, ניקיון, חלון ראווה"},
}

PROJECT_VIEW_MODES = ["Planner", "Client"]

UI_LABELS = {
    "he": {
        "toolbar": "כלים",
        "language": "שפה",
        "tabs_left": ["פתיחת פרויקט", "נתוני בסיס", "אפיון לקוח", "גופים ושכבות", "צריכת חשמל", "מקצועי", "ניהול פרויקט"],
        "tabs_results": ["תהליך עבודה", "סיכום", "מפת אור", "חישוב נקודתי", "תקנים", "אזורים", "AI אדריכלי", "בדיקות", "סקירת תכנון", "חלופות A/B/C", "לפני/אחרי", "קטלוג", "חשמל", "תמחור", "תלת ממד"],
    },
    "en": {
        "toolbar": "Tools",
        "language": "Language",
        "tabs_left": ["Project Wizard", "Base Data", "Client Brief", "Fixtures & Layers", "Energy", "Professional", "Project"],
        "tabs_results": ["Workflow", "Summary", "Light Map", "Point Lux", "Standards", "Zones", "Architectural AI", "Validation", "Design Review", "A/B/C Options", "Before/After", "Catalogue", "Energy", "Pricing", "3D"],
    },
}


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


@dataclass
class LightingLayer:
    name: str
    enabled: bool = True
    intensity: int = 100
    offset_x_m: float = 0.0
    offset_y_m: float = 0.0

    @property
    def factor(self) -> float:
        return self.intensity / 100 if self.enabled else 0.0

    def to_dict(self) -> Dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: Dict) -> "LightingLayer":
        return cls(d.get("name", "שכבה"), d.get("enabled", True), d.get("intensity", 100), float(d.get("offset_x_m", 0.0)), float(d.get("offset_y_m", 0.0)))


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
    side_b_m: float = 1.5
    side_c_m: float = 1.5
    quantity: int = 1
    spacing_m: float = 0.35

    @property
    def total_length_m(self) -> float:
        if self.shape == "L shape":
            return self.length_m + self.side_b_m
        if self.shape == "U shape":
            return self.length_m + self.side_b_m + self.side_c_m
        if self.shape == "Rectangle":
            return 2 * (self.length_m + self.side_b_m)
        return self.length_m

    @property
    def total_lm(self) -> float:
        return self.total_length_m * self.lm_per_m * max(1, self.quantity)

    @property
    def watts(self) -> float:
        return self.total_lm / 100

    def segments(self, room: "RoomModel") -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
        rad = math.radians(self.angle_deg)
        dx, dy = math.cos(rad), math.sin(rad)
        px, py = -dy, dx
        qty = max(1, self.quantity)
        out: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
        for i in range(qty):
            offset = (i - (qty - 1) / 2) * self.spacing_m
            cx = room.width * self.x + px * offset
            cy = room.length * self.y + py * offset
            if self.shape in ("Rectangle", "Perimeter"):
                w = self.length_m if self.shape == "Rectangle" else max(room.width - 0.36, 0.2)
                h = self.side_b_m if self.shape == "Rectangle" else max(room.length - 0.36, 0.2)
                x0, y0 = cx - w / 2, cy - h / 2
                pts = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h), (x0, y0)]
            elif self.shape == "L shape":
                p0 = (cx - dx * self.length_m / 2, cy - dy * self.length_m / 2)
                p1 = (p0[0] + dx * self.length_m, p0[1] + dy * self.length_m)
                p2 = (p1[0] + px * self.side_b_m, p1[1] + py * self.side_b_m)
                pts = [p0, p1, p2]
            elif self.shape == "U shape":
                p0 = (cx - dx * self.length_m / 2, cy - dy * self.length_m / 2)
                p1 = (p0[0] + px * self.side_b_m, p0[1] + py * self.side_b_m)
                p2 = (p1[0] + dx * self.length_m, p1[1] + dy * self.length_m)
                p3 = (p2[0] - px * self.side_c_m, p2[1] - py * self.side_c_m)
                pts = [p0, p1, p2, p3]
            else:
                pts = [(cx - dx * self.length_m / 2, cy - dy * self.length_m / 2), (cx + dx * self.length_m / 2, cy + dy * self.length_m / 2)]
            out.extend((a, b) for a, b in zip(pts, pts[1:]))
        return out

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
    width_cm: float = 2.5
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
            2.5 if abs(float(d.get("width_cm", 2.5)) - 2.3) < 0.05 else d.get("width_cm", 2.5),
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
    visible: bool = False
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
    show_zone_guides: bool = False
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
            layer.offset_x_m = clamp(float(getattr(layer, "offset_x_m", 0.0)), -room.width, room.width)
            layer.offset_y_m = clamp(float(getattr(layer, "offset_y_m", 0.0)), -room.length, room.length)
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
        layer_state = tuple((x.enabled, x.intensity, round(x.offset_x_m, 3), round(x.offset_y_m, 3)) for x in room.layers)
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
        # V8: dynamic resolution based on area + quality setting
        quality = getattr(room, "heatmap_quality", "Normal")
        return dynamic_grid_size(room.area, quality)

    def compute(self, room: "RoomModel") -> SimulationSnapshot:
        start = time.perf_counter()
        ModelGuard.sanitize_room(room)
        lux = LuxEngine(room)
        planner = SpotlightPlanner(room)
        spots = [(x, y) for x, y, _name, _h in lux.spot_points()] if room.layer(1).enabled else []
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
class ClientBrief:
    primary_user: str = ""
    user_age_group: str = "Mixed"
    daily_routine: str = ""
    hours_in_space: str = "2-4 hours"
    main_use_hours: str = "Evening"
    special_needs: str = ""
    desired_feeling: str = "Calm, warm, elegant"
    current_lighting_feedback: str = ""
    lighting_priority: str = ""
    success_feeling: str = ""
    activities: str = "Hosting, relaxing"
    multi_use: bool = True
    needs_scenes: bool = True
    wanted_scenes: str = "Hosting, relaxed evening, work, cleaning, night, TV"
    one_click_scenes: bool = True
    time_based_lighting: bool = False
    night_guidance: bool = False
    special_areas: str = "Kitchen island, dining table, niches, corridor"
    daylight_notes: str = ""
    gentle_daylight_blend: bool = True
    design_style: str = "Modern luxury"
    materials: str = "Wood, stone, textile, glass"
    reflective_behavior: str = "Mixed"
    highlight_textures: str = ""
    lighting_language: str = "Hidden and delicate"
    references: str = ""
    focal_point: str = ""
    highlight_areas: str = ""
    art_or_special_elements: str = ""
    soft_areas: str = ""
    depth_and_shadow: bool = True
    dimming_required: bool = True
    smart_lighting: bool = False
    phone_control: bool = False
    automatic_scenes: bool = False
    motion_sensors: bool = False
    smart_home_integration: bool = False
    control_system: str = ""
    preferred_fixture_style: str = "Minimal"
    project_type: str = "Renovation"
    project_stage: str = "Concept"
    has_arch_plans: bool = False
    installation_limits: str = ""
    fixed_electrical_points: str = ""
    budget_range: str = ""
    investment_priorities: str = ""
    lighting_problems: str = ""
    additional_notes: str = ""
    success_criteria: str = ""

    def to_dict(self) -> Dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: Dict) -> "ClientBrief":
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
    client_brief: ClientBrief = field(default_factory=ClientBrief)
    design_preset: str = "Modern Luxury"
    space_template: str = "Living room"
    view_mode: str = "Planner"
    existing_lighting_state: str = "Not documented"
    ui_language: str = "he"
    project_folder: str = ""
    last_modified: str = ""
    sticky_notes: List[dict] = field(default_factory=list)
    project_snapshots: List[dict] = field(default_factory=list)

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
                LightingZone(name="Kitchen island", visible=False, x=0.30, y=0.35, width=0.40, length=0.20, lux_target=500),
                LightingZone(name="Dining table", visible=False, x=0.25, y=0.62, width=0.50, length=0.25, lux_target=300),
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
                FurnitureObject("Dining table", "Dining table", False, 0.50, 0.62, 1.80, 0.95, 0.75),
                FurnitureObject("Kitchen island", "Kitchen island", False, 0.50, 0.35, 2.20, 0.90, 0.90),
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
        # Target illuminance is independent of colour temperature (EN 12464-1).
        return max(1, int(LUX_STANDARDS.get(self.room_type, 200)))

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
        # Coefficient of utilisation from Room Index (luminaire above work plane)
        # and the ceiling/wall/floor reflectances. Replaces the old log-heuristic.
        mount_h = max(0.2, self.ceiling_height - WORK_PLANE_M)
        ri = room_index_value(self.width, self.length, mount_h)
        return utilisation_factor(ri, self.reflectance_ceiling,
                                  self.reflectance_walls, self.reflectance_floor)

    def layer(self, i: int) -> LightingLayer:
        return self.layers[i] if i < len(self.layers) else LightingLayer("חסר", False, 0)

    def to_dict(self) -> Dict:
        return {
            "version": APP_VERSION,
            **{k: v for k, v in self.__dict__.items() if k not in {"layers", "profiles", "tracks", "pendants", "ambient", "zones", "daylight", "scenes", "branding", "floor_plan", "furniture", "envelope", "curtain_lighting", "optics", "architectural_understanding", "client_brief"}},
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
            "client_brief": self.client_brief.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "RoomModel":
        fields = {k for k in cls().__dict__ if k not in {"layers", "profiles", "tracks", "pendants", "ambient", "zones", "daylight", "scenes", "branding", "floor_plan", "furniture", "envelope", "curtain_lighting", "optics", "architectural_understanding", "client_brief"}}
        room = cls(**{k: d[k] for k in fields if k in d})
        room.layers = [LightingLayer.from_dict(x) for x in d.get("layers", [])] or room.layers
        room.profiles = [ProfileConfig.from_dict(x) for x in d.get("profiles", [])] or room.profiles
        room.tracks = [MagneticTrack.from_dict(x) for x in d.get("tracks", [])]
        room.pendants = [PendantConfig.from_dict(x) for x in d.get("pendants", [])] or room.pendants
        room.ambient = AmbientConfig.from_dict(d.get("ambient", {}))
        if "fixture_catalogue" in d and d["fixture_catalogue"]:
            # Respect the saved catalogue verbatim (deletions must persist).
            room.fixture_catalogue = dict(d["fixture_catalogue"])
        else:
            room.fixture_catalogue = dict(DEFAULT_FIXTURES)
        # Guarantee the selected default spot fixture still resolves.
        if room.default_spot_fixture not in room.fixture_catalogue:
            if room.default_spot_fixture in DEFAULT_FIXTURES:
                room.fixture_catalogue[room.default_spot_fixture] = dict(DEFAULT_FIXTURES[room.default_spot_fixture])
            elif room.fixture_catalogue:
                room.default_spot_fixture = next(iter(room.fixture_catalogue))
            else:
                room.fixture_catalogue = dict(DEFAULT_FIXTURES)
                room.default_spot_fixture = next(iter(room.fixture_catalogue))
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
        room.client_brief = ClientBrief.from_dict(d.get("client_brief", {}))
        return room


# ──────────────────────────────────────────────────────────
# PHOTOMETRY & UTILISATION FACTOR (physically grounded model)
# ──────────────────────────────────────────────────────────
# Work-plane height above the floor used for the lumen / utilisation method
# (EN 12464-1 reference plane for typical seated/standing tasks).
WORK_PLANE_M = 0.0  # point grid is evaluated at floor level for the heatmap

# Reference utilisation factors for a typical direct (downlight/linear) luminaire
# at ceiling/wall/floor reflectances of 0.70 / 0.50 / 0.20, indexed by Room Index.
# Values follow published CIBSE/IES-style UF tables for a medium-distribution
# direct fitting and are interpolated; reflectance deltas adjust the base value.
_UF_REFERENCE = [
    (0.60, 0.39), (0.80, 0.46), (1.00, 0.52), (1.25, 0.58),
    (1.50, 0.62), (2.00, 0.68), (2.50, 0.72), (3.00, 0.75),
    (4.00, 0.79), (5.00, 0.82),
]


def room_index_value(width: float, length: float, mount_h: float) -> float:
    """Room Index RI = (W·L) / (Hm·(W+L)), Hm = luminaire height above work plane."""
    denom = max(mount_h, 0.2) * (width + length)
    if denom <= 0:
        return 1.0
    return clamp((width * length) / denom, 0.4, 6.0)


def utilisation_factor(room_index: float, rho_c: float = 0.7,
                       rho_w: float = 0.5, rho_f: float = 0.2) -> float:
    """Coefficient of utilisation from Room Index + surface reflectances.

    Base curve is for ρc/ρw/ρf = 0.70/0.50/0.20; small linear corrections are
    applied for deviating reflectances. Ceiling & floor act mostly via the
    inter-reflected component, walls matter more in small (low-RI) rooms.
    """
    ri = clamp(room_index, _UF_REFERENCE[0][0], _UF_REFERENCE[-1][0])
    base = _UF_REFERENCE[-1][1]
    for (r0, u0), (r1, u1) in zip(_UF_REFERENCE, _UF_REFERENCE[1:]):
        if r0 <= ri <= r1:
            f = (ri - r0) / max(r1 - r0, 1e-9)
            base = u0 + f * (u1 - u0)
            break
    wall_gain = (rho_w - 0.5) * (0.18 / (ri + 0.5))   # stronger in small rooms
    ceil_gain = (rho_c - 0.7) * 0.12
    floor_gain = (rho_f - 0.2) * 0.06
    return round(clamp(base + wall_gain + ceil_gain + floor_gain, 0.15, 0.92), 3)


_BEAM_NORM_CACHE: Dict[int, float] = {}


def _beam_sigma(beam_deg: float) -> float:
    """Gaussian sigma (radians) so that FWHM equals the rated beam angle."""
    half = math.radians(clamp(beam_deg, 5.0, 175.0) / 2.0)
    return half / 1.1774  # half-angle at 50% peak => sigma = half / sqrt(2 ln2)


def _beam_norm(beam_deg: float) -> float:
    """K = 2π ∫ g(θ) sinθ dθ over the downward hemisphere for a unit-peak Gaussian.

    Luminous intensity for a lamp of flux Φ is then I(θ) = Φ/K · g(θ).
    """
    key = int(round(clamp(beam_deg, 5.0, 175.0)))
    cached = _BEAM_NORM_CACHE.get(key)
    if cached is not None:
        return cached
    sigma = _beam_sigma(key)
    steps = 180
    total = 0.0
    upper = math.pi / 2
    dt = upper / steps
    for i in range(steps):
        th = (i + 0.5) * dt
        g = math.exp(-(th * th) / (2 * sigma * sigma))
        total += g * math.sin(th) * dt
    k = max(2 * math.pi * total, 1e-6)
    _BEAM_NORM_CACHE[key] = k
    return k


def beam_intensity(flux_lm: float, beam_deg: float, theta_deg: float) -> float:
    """Luminous intensity (cd) of a normalised Gaussian beam of given flux."""
    sigma = _beam_sigma(beam_deg)
    th = math.radians(theta_deg)
    g = math.exp(-(th * th) / (2 * sigma * sigma))
    return flux_lm / _beam_norm(beam_deg) * g


def _interp_photometry(photometry: Dict, theta_deg: float) -> float:
    """Linear interpolation of stored {'v': angles, 'cd': candela} at vertical angle."""
    angles = photometry.get("v") or []
    cd = photometry.get("cd") or []
    if not angles or not cd:
        return 0.0
    t = abs(theta_deg)
    if t <= angles[0]:
        return max(0.0, cd[0])
    if t >= angles[-1]:
        return max(0.0, cd[-1])
    for i in range(len(angles) - 1):
        a0, a1 = angles[i], angles[i + 1]
        if a0 <= t <= a1:
            f = (t - a0) / max(a1 - a0, 1e-9)
            return max(0.0, cd[i] + f * (cd[i + 1] - cd[i]))
    return max(0.0, cd[-1])


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
            # Lumen method: total flux = E·A / (UF·MF); number of fixtures = flux / lm.
            required = self.room.lux_target * self.room.area / max(self.room.utilisation_factor * self.room.maintenance_factor, 0.01)
            n = max(1, math.ceil(required / max(spot_lm, 1)))
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

    _FALLBACK_FIXTURE: Dict = {
        "lm": 800, "w": 8, "cri": 90, "beam": 36, "cct": 3000,
        "brand": "Generic", "price": 0,
    }

    def fixture(self, name: str) -> Dict:
        cat = self.room.fixture_catalogue
        if name in cat:
            return cat[name]
        if cat:
            return next(iter(cat.values()))
        return dict(self._FALLBACK_FIXTURE)

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
        ox, oy = self.room.layer(1).offset_x_m, self.room.layer(1).offset_y_m
        return [(x + ox, y + oy, self.room.default_spot_fixture, self.room.ceiling_height) for x, y in SpotlightPlanner(self.room).active_positions()]

    def profile_points(self) -> List[Tuple[float, float, str, float]]:
        out = []
        ox, oy = self.room.layer(0).offset_x_m, self.room.layer(0).offset_y_m
        for p in self.room.profiles:
            if not p.enabled:
                continue
            for (x0, y0), (x1, y1) in p.segments(self.room):
                seg_len = math.hypot(x1 - x0, y1 - y0)
                n = max(2, int(seg_len / 0.45))
                for i in range(n):
                    t = i / max(n - 1, 1)
                    out.append((x0 + (x1 - x0) * t + ox, y0 + (y1 - y0) * t + oy, "__profile__", self.room.ceiling_height))
        return out

    def track_points(self) -> List[Tuple[float, float, str, float]]:
        out = []
        ox, oy = self.room.layer(0).offset_x_m, self.room.layer(0).offset_y_m
        for t in self.room.tracks:
            if not t.enabled:
                continue
            out.extend((x + ox, y + oy, f.fixture_type, self.room.ceiling_height) for x, y, f in t.fixture_points(self.room))
        return out

    def pendant_points(self) -> List[Tuple[float, float, str, float]]:
        out = []
        ox, oy = self.room.layer(2).offset_x_m, self.room.layer(2).offset_y_m
        for p in self.room.pendants:
            if not p.enabled:
                continue
            height = max(0.35, self.room.ceiling_height - p.drop_m)
            out.extend((x + ox, y + oy, p.fixture_type, height) for x, y in p.points(self.room))
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
            count = 0
            for p in active:
                for (x0, y0), (x1, y1) in p.segments(self.room):
                    count += max(2, int(math.hypot(x1 - x0, y1 - y0) / 0.45))
            count = count or 1
            return sum(p.total_lm for p in active) / count
        if name == "__ambient__":
            count = len(self.ambient_points()) or 1
            return self.room.ambient.total_lm / count
        if name == "__curtain__":
            count = len(self.curtain_points()) or 1
            return self.room.curtain_lighting.total_lm / count
        return float(self.fixture(name).get("lm", 800))

    def _avg_reflectance(self) -> float:
        a_horiz = self.room.area  # ceiling and floor each
        a_walls = 2 * (self.room.width + self.room.length) * self.room.ceiling_height
        total_area = 2 * a_horiz + a_walls
        if total_area <= 0:
            return 0.4
        return (self.room.reflectance_ceiling * a_horiz +
                self.room.reflectance_floor * a_horiz +
                self.room.reflectance_walls * a_walls) / total_area

    def _scene_cache(self) -> Dict:
        """Cache scene-wide constants reused across every grid point."""
        cache = getattr(self, "_cache", None)
        if cache is not None:
            return cache
        sources = self.all_sources()
        total_flux = sum(self.source_lumens(name) * factor
                         for _, _, name, _, factor in sources)
        uf = self.room.utilisation_factor
        rho = self._avg_reflectance()
        # Inter-reflected (indirect) share of the utilised flux. Scaled down from the
        # mean reflectance because direct luminaires deliver most flux to the task
        # plane directly; only a portion arrives via wall/ceiling inter-reflection.
        split = clamp(rho * 0.5, 0.08, 0.35)
        area = max(self.room.area, 0.01)
        # Split-flux: uniform inter-reflected illuminance on the work plane.
        indirect_uniform = total_flux * uf * split / area
        cache = {
            "sources": sources, "uf": uf, "split": split,
            "indirect": indirect_uniform,
        }
        self._cache = cache
        return cache

    def point_lux(self, px: float, py: float) -> float:
        c = self._scene_cache()
        uf, split = c["uf"], c["split"]
        direct = 0.0
        for sx, sy, name, h, layer_factor in c["sources"]:
            dx, dy = px - sx, py - sy
            r = math.sqrt(dx * dx + dy * dy)
            d = math.sqrt(r * r + h * h)
            if d <= 0.05:
                d = 0.05
            theta_deg = math.degrees(math.atan2(r, max(h, 0.05)))
            info = self.fixture(name) if name not in ("__profile__", "__ambient__", "__curtain__") else {}
            photometry = info.get("photometry") if info else None
            if photometry and photometry.get("cd"):
                # Real IES/LDT candela (rotationally averaged), scaled by layer dimming.
                intensity_cd = _interp_photometry(photometry, theta_deg) * layer_factor
            else:
                lumens = self.source_lumens(name) * layer_factor
                beam = float(info.get("beam", 90)) if info else 120
                intensity_cd = beam_intensity(lumens, beam, theta_deg)
            cos_theta = clamp(h / d, 0.0, 1.0)
            direct += intensity_cd * cos_theta / (d * d)
        # Direct utilised + uniform indirect (split-flux), both reduced by maintenance.
        artificial = (direct * uf * (1.0 - split) + c["indirect"]) * self.room.maintenance_factor
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
        """Simplified CIE 117 Unified Glare Rating.

        UGR = 8·log10( (0.25/Lb) · Σ L²·ω / p² )

        Assumptions (documented approximation):
          • Single observer at room centre, eye height 1.2 m, looking horizontally.
          • Luminaires are treated as small sources in the vertical plane of view
            (Guth azimuth a≈0), so the position index reduces to
            p = exp(0.03398·σ + 0.00021·σ²) with σ the elevation from the line of sight.
          • Background luminance Lb = E_avg·ρ_avg/π (inter-reflected surfaces).
          • Each ceiling source has a luminous area from its 'lum_area_m2' field
            (default 0.05 m²); intensity toward the eye uses the same photometry /
            Gaussian beam model as the illuminance calculation.
        Only direct ceiling sources are considered (profiles/ambient/curtain are
        low-luminance indirect elements and are excluded).
        """
        eye_h = 1.2
        ex, ey = self.room.width / 2.0, self.room.length / 2.0
        rho = self._avg_reflectance()
        bg = max(1.0, self.achieved_average_lux() * rho / math.pi)
        summation = 0.0
        for sx, sy, name, h, layer_factor in self.all_sources():
            if name in ("__profile__", "__ambient__", "__curtain__"):
                continue
            dh = h - eye_h
            if dh <= 0.05:
                continue
            r = math.hypot(sx - ex, sy - ey)
            d = math.hypot(r, dh)
            # σ: elevation of the source above the (horizontal) line of sight.
            sigma_deg = math.degrees(math.atan2(dh, max(r, 1e-3)))
            # θ: emission angle of the luminaire toward the eye, from its nadir.
            theta_deg = math.degrees(math.atan2(r, dh))
            info = self.fixture(name)
            photometry = info.get("photometry")
            if photometry and photometry.get("cd"):
                intensity_cd = _interp_photometry(photometry, theta_deg) * layer_factor
            else:
                lumens = self.source_lumens(name) * layer_factor
                beam = float(info.get("beam", 90))
                intensity_cd = beam_intensity(lumens, beam, theta_deg)
            if intensity_cd <= 0:
                continue
            lum_area = max(0.005, float(info.get("lum_area_m2", 0.05)))
            # Solid angle of the source at the eye (projected area / d²).
            omega = lum_area * clamp(dh / d, 0.0, 1.0) / max(d * d, 1e-4)
            luminance = intensity_cd / lum_area
            p = math.exp(0.03398 * sigma_deg + 0.00021 * sigma_deg * sigma_deg)
            summation += (luminance * luminance) * omega / (p * p)
        if summation <= 0:
            return 0.0
        ugr = 8 * math.log10(max(0.25 / bg * summation, 1e-6))
        return round(clamp(ugr, 5.0, 35.0), 1)


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
        u0_min = uniformity_target(self.room.room_type)
        return [
            ("EN 12464 Illuminance", 0.9 * target <= avg <= 1.25 * target, f"{avg:.0f} lx מול יעד {target} lx"),
            ("EN 12464 Uniformity", uniformity >= u0_min, f"U0={uniformity:.2f} (מינימום {u0_min:.2f})"),
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
                    "ok": 0.9 * zone.lux_target <= avg <= 1.3 * zone.lux_target and (min_lux / avg if avg else 0) >= 0.40,
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
        u0_min = uniformity_target(self.room.room_type)
        if avg < self.room.lux_target * 0.9:
            issues.append(("תאורה חלשה מדי", f"ממוצע {avg:.0f} lx נמוך מהיעד {self.room.lux_target} lx.", "הוסף גופים, הגבר תפוקה או מקד תאורת משימה באזורים."))
        if avg > self.room.lux_target * 1.35:
            issues.append(("תאורה חזקה מדי", f"ממוצע {avg:.0f} lx גבוה משמעותית מהיעד.", "הפחת כמות, עמעם שכבות או הקטן תפוקת לומן."))
        if avg and min_lux / avg < u0_min:
            issues.append(("אחידות", f"יחס מינימום/ממוצע הוא {min_lux / avg:.2f} (יעד {u0_min:.2f}).", "צמצם מרווחים, הוסף תאורת מילוי או הרחק גופים מצבירים."))
        if max_lux > self.room.lux_target * 2.5:
            issues.append(("נקודות חמות", f"ערך השיא בגריד הוא {max_lux:.0f} lx.", "הרחב זווית אלומה או הפחת חפיפת גופים."))
        for i, (x, y) in enumerate(SpotlightPlanner(self.room).active_positions(), 1):
            if min(x, y, self.room.width - x, self.room.length - y) < 0.18:
                issues.append(("התנגשות", f"ספוט {i} קרוב מדי לקיר.", "הרחק אותו לפחות 0.18 מ׳ מגבולות החדר."))
            for furn in self.room.furniture:
                if furn.enabled and furn.contains(self.room, x, y, 0.12):
                    issues.append(("חפיפת ריהוט", f"ספוט {i} חופף את {furn.name}.", "הזז את הגוף או נצל ריהוט זה כאזור תאורת משימה."))
        for p in self.room.pendants:
            if p.enabled and self.room.ceiling_height - p.drop_m < 1.9:
                issues.append(("גובה פנדנט", f"גובה תחתית {p.name} נמוך מ-1.90 מ׳.", "הקטן את הצניחה או מקם מעל שולחן/אי."))
            for x, y in p.points(self.room):
                for furn in self.room.furniture:
                    if furn.enabled and furn.contains(self.room, x, y, 0.20) and self.room.ceiling_height - p.drop_m < furn.height_m + 0.75:
                        issues.append(("התנגשות פנדנט/ריהוט", f"{p.name} נמוך מדי מעל {furn.name}.", "הגבה את הפנדנט או שמור מרווח של 0.75 מ׳ לפחות מעל הריהוט."))
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
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        for r, row in enumerate(self.heatmap):
            for c, val in enumerate(row):
                ratio = clamp(val / hi, 0, 1)
                if ratio < 0.5:
                    k = ratio / 0.5
                    col = QColor(int(28 + 45 * k), int(96 + 118 * k), int(218 - 110 * k), self.room.heatmap_opacity)
                else:
                    k = (ratio - 0.5) / 0.5
                    col = QColor(int(225 + 30 * k), int(188 - 116 * k), int(58 - 20 * k), self.room.heatmap_opacity)
                rect = QRectF(self.m2p(c * cw, r * ch), self.m2p((c + 1) * cw, (r + 1) * ch)).normalized()
                gap = max(0.7, min(rect.width(), rect.height()) * 0.055)
                cell = rect.adjusted(gap, gap, -gap, -gap)
                p.setBrush(col)
                p.drawRoundedRect(cell, 2.6, 2.6)
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
        p.restore()

    def _draw_profiles(self, p: QPainter) -> None:
        for prof in self.room.profiles:
            if not prof.enabled:
                continue
            s, _, _ = self._scale()
            glow = QColor(P["blue"])
            glow.setAlpha(55)
            p.setPen(QPen(glow, max(7, prof.width_m * s + 5), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            for (x0, y0), (x1, y1) in prof.segments(self.room):
                p.drawLine(self.m2p(x0, y0), self.m2p(x1, y1))
            p.setPen(QPen(QColor(P["cyan"]), max(3, prof.width_m * s), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            for (x0, y0), (x1, y1) in prof.segments(self.room):
                p.drawLine(self.m2p(x0, y0), self.m2p(x1, y1))

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
        "background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #222A3E, stop:0.55 #171E2E, stop:1 #101622);"
        f"border:1px solid {P['border2']};"
        f"border-right:3px solid {color};"
        "border-radius:10px;"
        "}"
    )
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 14)
    layout.setSpacing(9)
    label = QLabel(title)
    label.setStyleSheet(f"color:{color}; font-size:14px; font-weight:900; background:transparent; border:none;")
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
        self.undo_stack = UndoStack()
        # When True, recalculate() will not push a new undo snapshot. Used while
        # restoring state (undo/redo/open/snapshot) so we never clobber redo history.
        self._suppress_undo_push = False
        self._layers_tab_widget: object = None
        self._view_mode: str = "designer"
        self.grid_snap = GridSnap(0.10, enabled=True)
        self._last_snapshot: Optional[SimulationSnapshot] = None
        self.setWindowTitle(APP_NAME)
        self.resize(1600, 960)
        _style = FULL_STYLESHEET or V8_STYLESHEET or STYLESHEET
        if _style:
            self.setStyleSheet(_style)
        self.setMinimumSize(1180, 760)
        self.setLayoutDirection(Qt.RightToLeft)
        self._build_menu()
        self._build_toolbar()
        if _V8_TEAM_LOADED:
            build_toolbar_v8(self)
        # Suppress recalculation while widgets are created/populated, otherwise every
        # initial setValue/setCurrentText fires a full simulation (dozens of times).
        self._building = True
        try:
            self._build_ui()
        finally:
            self._building = False
        self.recalculate()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("קובץ")
        actions = [
            ("חדש", self.new_project, "Ctrl+N"),
            ("פתח...", self.open_project, "Ctrl+O"),
            ("שמור", self.save_project, "Ctrl+S"),
            ("שמור בשם...", self.save_project_as, "Ctrl+Shift+S"),
            ("ייבא קטלוג גופים...", self.import_catalogue, ""),
            ("ייצא קטלוג גופים...", self.export_catalogue, ""),
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
        self.main_toolbar = tb
        self.addToolBar(tb)
        # V8: Undo/Redo actions with keyboard shortcuts
        undo_act = QAction("↩ בטל", self)
        undo_act.setShortcut("Ctrl+Z")
        undo_act.triggered.connect(self._undo)
        tb.addAction(undo_act)
        redo_act = QAction("↪ בצע שוב", self)
        redo_act.setShortcut("Ctrl+Y")
        redo_act.triggered.connect(self._redo)
        tb.addAction(redo_act)
        tb.addSeparator()
        ies_act = QAction("📡 ייבא IES", self)
        ies_act.triggered.connect(self.import_ies)
        tb.addAction(ies_act)
        ai_act2 = QAction("🤖 סקירת AI", self)
        ai_act2.triggered.connect(self.run_ai_review)
        tb.addAction(ai_act2)
        html_act = QAction("🌐 שתף ללקוח", self)
        html_act.triggered.connect(self.export_client_html)
        tb.addAction(html_act)
        tb.addSeparator()
        for text, slot in [("חדש", self.new_project), ("פתח", self.open_project), ("שמור", self.save_project), ("קטלוג", self.import_catalogue), ("תכנית", self.import_floor_plan), ("DXF", self.export_dxf), ("📄 PDF", self.export_quote), ("חשמל", self.export_energy_report)]:
            a = QAction(text, self)
            a.triggered.connect(slot)
            tb.addAction(a)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.language_label = QLabel("שפה")
        self.language_label.setStyleSheet(f"color:{P['muted']}; background:transparent; border:none; padding:0 8px;")
        self.language_combo = QComboBox()
        self.language_combo.addItem("עברית", "he")
        self.language_combo.addItem("English", "en")
        self.language_combo.setCurrentIndex(0)
        self.language_combo.currentIndexChanged.connect(self._language_changed)
        tb.addWidget(self.language_label)
        tb.addWidget(self.language_combo)

    def _language_changed(self) -> None:
        if not hasattr(self, "language_combo"):
            return
        self.room.ui_language = self.language_combo.currentData() or "he"
        self._apply_language()

    def _apply_language(self) -> None:
        lang = getattr(self.room, "ui_language", "he")
        labels = UI_LABELS.get(lang, UI_LABELS["he"])
        is_he = lang == "he"
        self.setLayoutDirection(Qt.RightToLeft if is_he else Qt.LeftToRight)
        if hasattr(self, "language_label"):
            self.language_label.setText(labels["language"])
        if hasattr(self, "main_toolbar"):
            self.main_toolbar.setWindowTitle(labels["toolbar"])
        if hasattr(self, "left_tabs"):
            for i, text in enumerate(labels["tabs_left"]):
                if i < self.left_tabs.count():
                    self.left_tabs.setTabText(i, text)
        if hasattr(self, "results"):
            for i, text in enumerate(labels["tabs_results"]):
                if i < self.results.count():
                    self.results.setTabText(i, text)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        left = QTabWidget()
        self.left_tabs = left
        left.setFixedWidth(430)
        _tab_labels = LEFT_TABS if _V8_TEAM_LOADED and LEFT_TABS else [
            ("✦ פתיחה","wizard"),("📐 חדר","basic"),("📋 Brief","brief"),
            ("💡 שכבות","layers"),("⚡ חשמל","energy"),("🏆 מקצועי","pro"),("⚙️ פרויקט","project")]
        _tab_builders = [
            self._build_wizard_tab, self._build_basic_tab,
            self._build_brief_tab, self._build_layers_tab,
            self._build_energy_tab, self._build_professional_tab,
            self._build_project_tab]
        for (label, _), builder in zip(_tab_labels, _tab_builders):
            left.addTab(self._scroll(builder()), label)

        right = QSplitter(Qt.Vertical)
        # V8: KPI row + view toggle above renderer
        renderer_container = QWidget()
        rc_lay = QVBoxLayout(renderer_container)
        rc_lay.setContentsMargins(0,0,0,0); rc_lay.setSpacing(4)
        # top bar: view toggle + status
        top_bar = QHBoxLayout()
        self.view_toggle = ViewToggleBar()
        self.view_toggle.viewChanged.connect(self._on_view_changed)
        self.kpi_row = KPIStrip() if _V8_TEAM_LOADED else KPIRow()
        top_bar.addWidget(self.view_toggle)
        top_bar.addWidget(self.kpi_row, 1)
        rc_lay.addLayout(top_bar)
        self.renderer = RoomRenderer()
        self.renderer.spotMoved.connect(self._spots_moved)
        if _V8_TEAM_LOADED:
            self._renderer_stack = QStackedWidget()
            self._empty_state = EmptyStateRenderer()
            self._empty_state.newRequested.connect(self.new_project)
            self._empty_state.openRequested.connect(self.open_project)
            self._renderer_stack.addWidget(self._empty_state)   # index 0
            self._renderer_stack.addWidget(self.renderer)       # index 1
            self._renderer_stack.setCurrentIndex(0)
            rc_lay.addWidget(self._renderer_stack, 1)
        else:
            rc_lay.addWidget(self.renderer, 1)
        right.addWidget(renderer_container)
        self.results = QTabWidget()
        self.workflow_text = QTextEdit(readOnly=True)
        self.summary_text = QTextEdit(readOnly=True)
        self.lightmap_text = QTextEdit(readOnly=True)
        self.point_text = QTextEdit(readOnly=True)
        self.compliance_text = QTextEdit(readOnly=True)
        self.catalogue_text = QTextEdit(readOnly=True)
        self.energy_text = QTextEdit(readOnly=True)
        self.zones_text = QTextEdit(readOnly=True)
        self.arch_ai_text = QTextEdit(readOnly=True)
        self.validation_text = QTextEdit(readOnly=True)
        self.design_review_text = QTextEdit(readOnly=True)
        self.alternatives_text = QTextEdit(readOnly=True)
        self.before_after_text = QTextEdit(readOnly=True)
        self.pricing_text = QTextEdit(readOnly=True)
        self.preview3d_text = QTextEdit(readOnly=True)
        for w, name in [
            (self.workflow_text, "תהליך עבודה"),
            (self.summary_text, "סיכום"),
            (self.lightmap_text, "מפת אור"),
            (self.point_text, "חישוב נקודתי"),
            (self.compliance_text, "תקנים"),
            (self.zones_text, "אזורים"),
            (self.arch_ai_text, "AI אדריכלי"),
            (self.validation_text, "בדיקות"),
            (self.catalogue_text, "קטלוג"),
            (self.energy_text, "צריכת חשמל"),
            (self.design_review_text, "סקירת תכנון"),
            (self.alternatives_text, "חלופות A/B/C"),
            (self.before_after_text, "לפני/אחרי"),
            (self.pricing_text, "תמחור"),
            (self.preview3d_text, "תלת ממד"),
        ]:
            self.results.addTab(w, name)
        # V8 tabs: scenes, materials, notes, snapshots
        self._scene_timeline = SceneTimelineWidget()
        self.results.addTab(self._scene_timeline, "⏰ סצינות")
        self._surface_mats = SurfaceMaterialsWidget()
        self.results.addTab(self._surface_mats, "🏠 חומרים")
        self._sticky_notes = StickyNotesPanel()
        self.results.addTab(self._sticky_notes, "📌 הערות")
        self._snapshots = SnapshotsPanel()
        self._snapshots.set_room_provider(self._snapshot_room_dict)
        self._snapshots.restoreRequested.connect(self._restore_snapshot)
        self.results.addTab(self._snapshots, "📷 גרסאות")

        right.addWidget(self.results)
        right.setSizes([590, 280])

        root.addWidget(left)
        root.addWidget(right, 1)
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self._apply_language()

    def _scroll(self, widget: QWidget) -> QScrollArea:
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(widget)
        return sc

    def _track_fixture_options(self, width_cm: float) -> List[str]:
        options = []
        for name, data in self.room.fixture_catalogue.items():
            widths = data.get("track_widths")
            if widths and any(abs(float(w) - width_cm) < 0.05 for w in widths):
                options.append(name)
        if options:
            return options
        if width_cm <= 0.85:
            return [n for n in self.room.fixture_catalogue if "0.8" in n or "Slim" in n] or list(self.room.fixture_catalogue.keys())
        if width_cm <= 1.35:
            return [n for n in self.room.fixture_catalogue if "1.3" in n or "Micro" in n] or list(self.room.fixture_catalogue.keys())
        return [n for n in self.room.fixture_catalogue if "2.5" in n or "Magnetic" in n or "מסלול" in n] or list(self.room.fixture_catalogue.keys())

    def _sync_track_fixture_options(self, *_args) -> None:
        if not hasattr(self, "track_width") or not hasattr(self, "track_fix"):
            return
        current = self.track_fix.currentText()
        width = float(self.track_width.currentText().split()[0])
        options = self._track_fixture_options(width)
        self.track_fix.blockSignals(True)
        if hasattr(self, "track_fix"): self.track_fix.clear()
        self.track_fix.addItems(options)
        self.track_fix.setCurrentText(current if current in options else options[0])
        self.track_fix.blockSignals(False)

    def _build_wizard_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8); layout.setSpacing(10)

        # V8: Natural Language input
        self._nl_wizard = NLWizardWidget()
        self._nl_wizard.accepted.connect(self._apply_nl_parsed)
        layout.addWidget(self._nl_wizard)

        # V8: Design packages
        self._design_pkgs = DesignPackagesWidget()
        self._design_pkgs.packageSelected.connect(self._apply_design_package)
        layout.addWidget(self._design_pkgs)

        card, cl = make_card("Project Wizard - פתיחת פרויקט", P["gold"])
        note = QLabel("תהליך עבודה קצר: פרטי לקוח -> תבנית חלל -> השראה עיצובית -> מצב תצוגה -> חישוב וסקירה.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{P['muted']}; background:transparent; border:none;")
        cl.addWidget(note)
        form = QFormLayout()
        self.wizard_template = QComboBox()
        self.wizard_template.addItems(SPACE_TEMPLATES.keys())
        self.wizard_template.setCurrentText(self.room.space_template)
        self.wizard_preset = QComboBox()
        self.wizard_preset.addItems(DESIGN_PRESETS.keys())
        self.wizard_preset.setCurrentText(self.room.design_preset)
        self.view_mode = QComboBox()
        self.view_mode.addItems(PROJECT_VIEW_MODES)
        self.view_mode.setCurrentText(self.room.view_mode)
        self.existing_lighting_state = QTextEdit()
        self.existing_lighting_state.setFixedHeight(70)
        self.existing_lighting_state.setPlainText(self.room.existing_lighting_state)
        self.apply_template_btn = QPushButton("Apply template")
        self.apply_template_btn.setObjectName("amber")
        self.apply_template_btn.clicked.connect(self._apply_wizard_template)
        form.addRow("תבנית חלל:", self.wizard_template)
        form.addRow("ספריית השראה:", self.wizard_preset)
        form.addRow("מצב עבודה:", self.view_mode)
        form.addRow("Before - תאורה קיימת:", self.existing_lighting_state)
        form.addRow("", self.apply_template_btn)
        cl.addLayout(form)
        layout.addWidget(card)
        for obj in [self.wizard_template, self.wizard_preset, self.view_mode, self.existing_lighting_state]:
            self._connect_change(obj)
        layout.addStretch()
        return w

    def _apply_wizard_template(self) -> None:
        self._read_inputs()
        tpl = SPACE_TEMPLATES.get(self.room.space_template, {})
        preset = DESIGN_PRESETS.get(self.room.design_preset, {})
        if tpl.get("room_type") in ROOM_TYPES:
            self.room.room_type = tpl["room_type"]
            self.room_type.setCurrentText(self.room.room_type)
        self.room.lux_override = int(tpl.get("lux", self.room.lux_target))
        self.lux_in.setValue(self.room.lux_override)
        cct_name = preset.get("cct")
        if cct_name in CCT_PRESETS:
            self.room.cct_preset = cct_name
            self.cct.setCurrentText(cct_name)
        self.room.client_brief.desired_feeling = preset.get("feeling", self.room.client_brief.desired_feeling)
        self.room.client_brief.lighting_language = preset.get("language", self.room.client_brief.lighting_language)
        self.room.client_brief.wanted_scenes = tpl.get("scenes", self.room.client_brief.wanted_scenes)
        self._refresh_brief_controls()
        self.recalculate()

    def _build_basic_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        title_card, tl = make_card(APP_NAME, P["blue"])
        sub = QLabel("V7.8: Guided workflow | Premium pixel light map | Lux simulation | Beam analysis | Professional reporting")
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

    def _brief_line(self, value: str = "") -> QLineEdit:
        edit = QLineEdit()
        edit.setText(value)
        return edit

    def _brief_text(self, value: str = "", height: int = 58) -> QTextEdit:
        edit = QTextEdit()
        edit.setPlainText(value)
        edit.setFixedHeight(height)
        return edit

    def _brief_combo(self, values: List[str], value: str) -> QComboBox:
        combo = QComboBox()
        combo.addItems(values)
        combo.setCurrentText(value)
        return combo

    def _brief_check(self, label: str, value: bool) -> QCheckBox:
        chk = QCheckBox(label)
        chk.setChecked(value)
        return chk

    def _build_brief_tab(self) -> QWidget:
        brief = self.room.client_brief
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)

        intro_card, intro = make_card("Client brief - שאלון חוויה ותכנון", P["purple"])
        self.brief_auto_summary = QLabel("")
        self.brief_auto_summary.setWordWrap(True)
        self.brief_auto_summary.setStyleSheet(f"color:{P['muted']}; background:transparent; border:none; line-height:1.45;")
        intro.addWidget(self.brief_auto_summary)
        layout.addWidget(intro_card)

        user_card, user_l = make_card("שלב 1 - היכרות עם המשתמש", P["blue"])
        user_f = QFormLayout()
        self.brief_primary_user = self._brief_line(brief.primary_user)
        self.brief_age = self._brief_combo(["Children", "Adults", "Seniors", "Mixed"], brief.user_age_group)
        self.brief_daily = self._brief_text(brief.daily_routine)
        self.brief_hours = self._brief_combo(["Less than 1 hour", "1-2 hours", "2-4 hours", "4-8 hours", "Most of the day"], brief.hours_in_space)
        self.brief_use_hours = self._brief_combo(["Morning", "Noon", "Afternoon", "Evening", "Night", "All day"], brief.main_use_hours)
        self.brief_special_needs = self._brief_text(brief.special_needs)
        user_f.addRow("מי המשתמש העיקרי:", self.brief_primary_user)
        user_f.addRow("גיל המשתמשים:", self.brief_age)
        user_f.addRow("איך נראה היום יום:", self.brief_daily)
        user_f.addRow("משך שהייה ביום:", self.brief_hours)
        user_f.addRow("שעות שימוש עיקריות:", self.brief_use_hours)
        user_f.addRow("צרכים מיוחדים:", self.brief_special_needs)
        user_l.addLayout(user_f)
        layout.addWidget(user_card)

        feeling_card, feeling_l = make_card("שלב 2 - חוויה, תחושה ופעילות", P["gold"])
        feeling_f = QFormLayout()
        self.brief_feeling = self._brief_combo(["Calm", "Intimate", "Dramatic", "Energetic", "Luxury", "Warm", "Focused", "Calm, warm, elegant"], brief.desired_feeling)
        self.brief_current_feedback = self._brief_text(brief.current_lighting_feedback)
        self.brief_priority = self._brief_text(brief.lighting_priority)
        self.brief_success_feeling = self._brief_text(brief.success_feeling)
        self.brief_activities = self._brief_text(brief.activities)
        self.brief_multi_use = self._brief_check("החלל משמש למספר שימושים", brief.multi_use)
        feeling_f.addRow("איך רוצים להרגיש:", self.brief_feeling)
        feeling_f.addRow("מה לא טוב בתאורה הקיימת:", self.brief_current_feedback)
        feeling_f.addRow("הדבר הכי חשוב שהתאורה תעשה:", self.brief_priority)
        feeling_f.addRow("מה ירגיש מושלם:", self.brief_success_feeling)
        feeling_f.addRow("פעילויות בחלל:", self.brief_activities)
        feeling_f.addRow(self.brief_multi_use)
        feeling_l.addLayout(feeling_f)
        layout.addWidget(feeling_card)

        scenes_card, scenes_l = make_card("שלב 3 - תרחישי תאורה", P["green"])
        scenes_f = QFormLayout()
        self.brief_needs_scenes = self._brief_check("נדרשים מצבי תאורה שונים", brief.needs_scenes)
        self.brief_wanted_scenes = self._brief_text(brief.wanted_scenes)
        self.brief_one_click = self._brief_check("מעבר בין מצבים בלחיצה", brief.one_click_scenes)
        self.brief_time_based = self._brief_check("התאורה משתנה לפי שעות היום", brief.time_based_lighting)
        self.brief_night = self._brief_check("נדרשת תאורת לילה / התמצאות", brief.night_guidance)
        scenes_f.addRow(self.brief_needs_scenes)
        scenes_f.addRow("מצבים נדרשים:", self.brief_wanted_scenes)
        scenes_f.addRow(self.brief_one_click)
        scenes_f.addRow(self.brief_time_based)
        scenes_f.addRow(self.brief_night)
        scenes_l.addLayout(scenes_f)
        layout.addWidget(scenes_card)

        space_card, space_l = make_card("שלב 4 - הבנת החלל, אור טבעי וחומרים", P["cyan"])
        space_f = QFormLayout()
        self.brief_special_areas = self._brief_text(brief.special_areas)
        self.brief_daylight_notes = self._brief_text(brief.daylight_notes)
        self.brief_daylight_blend = self._brief_check("לשלב אור מלאכותי עם אור טבעי בעדינות", brief.gentle_daylight_blend)
        self.brief_style = self._brief_combo(["Modern", "Minimal", "Rustic", "Industrial", "Classic", "Luxury", "Modern luxury"], brief.design_style)
        self.brief_materials = self._brief_text(brief.materials)
        self.brief_reflective = self._brief_combo(["Reflective", "Absorbing", "Mixed"], brief.reflective_behavior)
        self.brief_textures = self._brief_text(brief.highlight_textures)
        self.brief_language = self._brief_combo(["Hidden and delicate", "Dominant decorative", "Functional only", "Architectural and minimal"], brief.lighting_language)
        self.brief_references = self._brief_text(brief.references)
        space_f.addRow("אזורים מיוחדים:", self.brief_special_areas)
        space_f.addRow("אור טבעי / סנוור:", self.brief_daylight_notes)
        space_f.addRow(self.brief_daylight_blend)
        space_f.addRow("סגנון עיצובי:", self.brief_style)
        space_f.addRow("חומרים קיימים:", self.brief_materials)
        space_f.addRow("החזר/בליעת אור:", self.brief_reflective)
        space_f.addRow("חיפויים/טקסטורות להדגשה:", self.brief_textures)
        space_f.addRow("שפת התאורה:", self.brief_language)
        space_f.addRow("השראות:", self.brief_references)
        space_l.addLayout(space_f)
        layout.addWidget(space_card)

        hierarchy_card, hierarchy_l = make_card("שלב 5 - היררכיה, מוקדים וטכניקה", P["amber"])
        hierarchy_f = QFormLayout()
        self.brief_focal = self._brief_text(brief.focal_point)
        self.brief_highlights = self._brief_text(brief.highlight_areas)
        self.brief_art = self._brief_text(brief.art_or_special_elements)
        self.brief_soft = self._brief_text(brief.soft_areas)
        self.brief_depth = self._brief_check("ליצור עומק ודרמה באמצעות אור וצל", brief.depth_and_shadow)
        self.brief_dimming = self._brief_check("נדרשת שליטה בעוצמות / דימרים", brief.dimming_required)
        self.brief_smart = self._brief_check("תאורה חכמה", brief.smart_lighting)
        self.brief_phone = self._brief_check("שליטה מהטלפון", brief.phone_control)
        self.brief_auto_scenes = self._brief_check("תרחישים אוטומטיים", brief.automatic_scenes)
        self.brief_motion = self._brief_check("חיישני תנועה", brief.motion_sensors)
        self.brief_smart_home = self._brief_check("אינטגרציה לבית חכם", brief.smart_home_integration)
        self.brief_control_system = self._brief_line(brief.control_system)
        self.brief_fixture_style = self._brief_combo(["Minimal", "Decorative", "Architectural", "No preference"], brief.preferred_fixture_style)
        hierarchy_f.addRow("מוקד החלל:", self.brief_focal)
        hierarchy_f.addRow("אזורים להדגשה:", self.brief_highlights)
        hierarchy_f.addRow("אמנות / אלמנטים מיוחדים:", self.brief_art)
        hierarchy_f.addRow("אזורים רכים ושקטים:", self.brief_soft)
        hierarchy_f.addRow(self.brief_depth)
        hierarchy_f.addRow(self.brief_dimming)
        hierarchy_f.addRow(self.brief_smart)
        hierarchy_f.addRow(self.brief_phone)
        hierarchy_f.addRow(self.brief_auto_scenes)
        hierarchy_f.addRow(self.brief_motion)
        hierarchy_f.addRow(self.brief_smart_home)
        hierarchy_f.addRow("מערכת שליטה:", self.brief_control_system)
        hierarchy_f.addRow("סגנון גופים:", self.brief_fixture_style)
        hierarchy_l.addLayout(hierarchy_f)
        layout.addWidget(hierarchy_card)

        execution_card, execution_l = make_card("שלב 6 - ביצוע, תקציב וסיכום", P["purple"])
        execution_f = QFormLayout()
        self.brief_project_type = self._brief_combo(["New build", "Renovation", "Existing space"], brief.project_type)
        self.brief_stage = self._brief_combo(["Concept", "Architectural plan", "Execution plan", "On site", "After handover"], brief.project_stage)
        self.brief_has_plans = self._brief_check("קיימות תוכניות אדריכליות", brief.has_arch_plans)
        self.brief_install_limits = self._brief_text(brief.installation_limits)
        self.brief_fixed_points = self._brief_text(brief.fixed_electrical_points)
        self.brief_budget = self._brief_line(brief.budget_range)
        self.brief_investment = self._brief_text(brief.investment_priorities)
        self.brief_problems = self._brief_text(brief.lighting_problems)
        self.brief_notes = self._brief_text(brief.additional_notes)
        self.brief_success = self._brief_text(brief.success_criteria)
        execution_f.addRow("בנייה חדשה / שיפוץ:", self.brief_project_type)
        execution_f.addRow("שלב הפרויקט:", self.brief_stage)
        execution_f.addRow(self.brief_has_plans)
        execution_f.addRow("מגבלות התקנה:", self.brief_install_limits)
        execution_f.addRow("נקודות חשמל לשימור:", self.brief_fixed_points)
        execution_f.addRow("תקציב בערך:", self.brief_budget)
        execution_f.addRow("איפה חשוב להשקיע:", self.brief_investment)
        execution_f.addRow("בעיות תאורה לטיפול:", self.brief_problems)
        execution_f.addRow("מידע נוסף:", self.brief_notes)
        execution_f.addRow("קריטריונים להצלחה:", self.brief_success)
        execution_l.addLayout(execution_f)
        layout.addWidget(execution_card)

        for obj in [
            self.brief_primary_user, self.brief_age, self.brief_daily, self.brief_hours, self.brief_use_hours, self.brief_special_needs,
            self.brief_feeling, self.brief_current_feedback, self.brief_priority, self.brief_success_feeling, self.brief_activities,
            self.brief_multi_use, self.brief_needs_scenes, self.brief_wanted_scenes, self.brief_one_click, self.brief_time_based,
            self.brief_night, self.brief_special_areas, self.brief_daylight_notes, self.brief_daylight_blend, self.brief_style,
            self.brief_materials, self.brief_reflective, self.brief_textures, self.brief_language, self.brief_references,
            self.brief_focal, self.brief_highlights, self.brief_art, self.brief_soft, self.brief_depth, self.brief_dimming,
            self.brief_smart, self.brief_phone, self.brief_auto_scenes, self.brief_motion, self.brief_smart_home,
            self.brief_control_system, self.brief_fixture_style, self.brief_project_type, self.brief_stage, self.brief_has_plans,
            self.brief_install_limits, self.brief_fixed_points, self.brief_budget, self.brief_investment, self.brief_problems,
            self.brief_notes, self.brief_success,
        ]:
            self._connect_change(obj)
        self._update_brief_auto_summary()
        layout.addStretch()
        return w

    def _build_layers_tab(self) -> QWidget:
        w = QWidget()
        self.layers_layout = QVBoxLayout(w)
        self.layers_layout.setContentsMargins(8, 8, 8, 8)
        self._rebuild_layers_tab()
        return w

    def _rebuild_layers_tab(self) -> None:
        # V8: use new LayersTabWidget if controls patch is loaded
        if LayersTabWidget is not None:
            self._rebuild_layers_tab_v8()
            return
        # ── legacy fallback ──────────────────────────────────────────────
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
        self.profile_side_b = QDoubleSpinBox()
        self.profile_side_b.setRange(0.1, 100)
        self.profile_side_b.setValue(self.room.profiles[0].side_b_m)
        self.profile_side_b.setSuffix(" m")
        self.profile_side_c = QDoubleSpinBox()
        self.profile_side_c.setRange(0.1, 100)
        self.profile_side_c.setValue(self.room.profiles[0].side_c_m)
        self.profile_side_c.setSuffix(" m")
        self.profile_qty = QSpinBox()
        self.profile_qty.setRange(1, 20)
        self.profile_qty.setValue(self.room.profiles[0].quantity)
        self.profile_spacing = QDoubleSpinBox()
        self.profile_spacing.setRange(0.05, 10)
        self.profile_spacing.setValue(self.room.profiles[0].spacing_m)
        self.profile_spacing.setSuffix(" m")
        self.profile_x = QDoubleSpinBox()
        self.profile_x.setRange(0, 1)
        self.profile_x.setSingleStep(0.05)
        self.profile_x.setValue(self.room.profiles[0].x)
        self.profile_y = QDoubleSpinBox()
        self.profile_y.setRange(0, 1)
        self.profile_y.setSingleStep(0.05)
        self.profile_y.setValue(self.room.profiles[0].y)
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
        pf.addRow("L / ח side A:", self.profile_side_b)
        pf.addRow("ח side B:", self.profile_side_c)
        pf.addRow("Profile copies:", self.profile_qty)
        pf.addRow("Copy spacing:", self.profile_spacing)
        pf.addRow("lm/m:", self.profile_lmm)
        pf.addRow("זווית:", self.profile_angle)
        pf.addRow("X position:", self.profile_x)
        pf.addRow("Y position:", self.profile_y)
        pf.addRow("", self.profile_fit_lmm)
        pf.addRow("", self.profile_lmm_hint)
        pl.addLayout(pf)
        self.layers_layout.addWidget(profile_card)
        self.layers_layout.removeWidget(profile_card)
        self.layers_layout.insertWidget(1, profile_card)
        for obj in [self.profile_enabled, self.profile_shape, self.profile_len, self.profile_width, self.profile_side_b, self.profile_side_c, self.profile_qty, self.profile_spacing, self.profile_lmm, self.profile_angle, self.profile_x, self.profile_y]:
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
        self.track_width.addItems(["0.8 cm", "1.3 cm", "2.5 cm"])
        current_track_width = self.room.tracks[0].width_cm if self.room.tracks else 2.5
        if abs(current_track_width - 2.3) < 0.05:
            current_track_width = 2.5
        self.track_width.setCurrentText(f"{current_track_width:g} cm")
        self.track_fix = QComboBox()
        self.track_fix.addItems(self._track_fixture_options(float(self.track_width.currentText().split()[0])))
        self.track_fix.setCurrentText("ספוט מסלול 24deg")
        self.track_qty = QSpinBox()
        self.track_qty.setRange(0, 50)
        self.track_qty.setValue(len(self.room.tracks[0].fixtures) if self.room.tracks else 0)
        self.track_angle = QDoubleSpinBox()
        self.track_angle.setRange(-180, 180)
        self.track_angle.setValue(self.room.tracks[0].angle_deg if self.room.tracks else 0)
        self.track_x = QDoubleSpinBox()
        self.track_x.setRange(0, 1)
        self.track_x.setSingleStep(0.05)
        self.track_x.setValue(self.room.tracks[0].x if self.room.tracks else 0.5)
        self.track_y = QDoubleSpinBox()
        self.track_y.setRange(0, 1)
        self.track_y.setSingleStep(0.05)
        self.track_y.setValue(self.room.tracks[0].y if self.room.tracks else 0.4)
        tf = QFormLayout()
        tf.addRow("פעיל:", self.track_enabled)
        tf.addRow("צורה:", self.track_shape)
        tf.addRow("אורך:", self.track_len)
        tf.addRow("רוחב מערכת:", self.track_width)
        tf.addRow("גוף:", self.track_fix)
        tf.addRow("כמות גופים:", self.track_qty)
        tf.addRow("Angle:", self.track_angle)
        tf.addRow("X position:", self.track_x)
        tf.addRow("Y position:", self.track_y)
        trl.addLayout(tf)
        self.layers_layout.addWidget(track_card)
        self.track_width.currentTextChanged.connect(self._sync_track_fixture_options)
        for obj in [self.track_enabled, self.track_shape, self.track_len, self.track_width, self.track_fix, self.track_qty, self.track_angle, self.track_x, self.track_y]:
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
        self.labour_rate_in.setPrefix("₪ ")
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
        legacy_widgets = []
        for name in ["spot_fixture","beam","offset","spot_qty","fit_spots_btn","reset_spots_btn"]:
            if hasattr(self, name): legacy_widgets.append(getattr(self, name))
        for widget in legacy_widgets:
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

    def _brief_plain(self, widget: QTextEdit) -> str:
        return widget.toPlainText().strip()

    def _read_client_brief_inputs(self) -> None:
        if not hasattr(self, "brief_primary_user"):
            return
        brief = self.room.client_brief
        brief.primary_user = self.brief_primary_user.text().strip()
        brief.user_age_group = self.brief_age.currentText()
        brief.daily_routine = self._brief_plain(self.brief_daily)
        brief.hours_in_space = self.brief_hours.currentText()
        brief.main_use_hours = self.brief_use_hours.currentText()
        brief.special_needs = self._brief_plain(self.brief_special_needs)
        brief.desired_feeling = self.brief_feeling.currentText()
        brief.current_lighting_feedback = self._brief_plain(self.brief_current_feedback)
        brief.lighting_priority = self._brief_plain(self.brief_priority)
        brief.success_feeling = self._brief_plain(self.brief_success_feeling)
        brief.activities = self._brief_plain(self.brief_activities)
        brief.multi_use = self.brief_multi_use.isChecked()
        brief.needs_scenes = self.brief_needs_scenes.isChecked()
        brief.wanted_scenes = self._brief_plain(self.brief_wanted_scenes)
        brief.one_click_scenes = self.brief_one_click.isChecked()
        brief.time_based_lighting = self.brief_time_based.isChecked()
        brief.night_guidance = self.brief_night.isChecked()
        brief.special_areas = self._brief_plain(self.brief_special_areas)
        brief.daylight_notes = self._brief_plain(self.brief_daylight_notes)
        brief.gentle_daylight_blend = self.brief_daylight_blend.isChecked()
        brief.design_style = self.brief_style.currentText()
        brief.materials = self._brief_plain(self.brief_materials)
        brief.reflective_behavior = self.brief_reflective.currentText()
        brief.highlight_textures = self._brief_plain(self.brief_textures)
        brief.lighting_language = self.brief_language.currentText()
        brief.references = self._brief_plain(self.brief_references)
        brief.focal_point = self._brief_plain(self.brief_focal)
        brief.highlight_areas = self._brief_plain(self.brief_highlights)
        brief.art_or_special_elements = self._brief_plain(self.brief_art)
        brief.soft_areas = self._brief_plain(self.brief_soft)
        brief.depth_and_shadow = self.brief_depth.isChecked()
        brief.dimming_required = self.brief_dimming.isChecked()
        brief.smart_lighting = self.brief_smart.isChecked()
        brief.phone_control = self.brief_phone.isChecked()
        brief.automatic_scenes = self.brief_auto_scenes.isChecked()
        brief.motion_sensors = self.brief_motion.isChecked()
        brief.smart_home_integration = self.brief_smart_home.isChecked()
        brief.control_system = self.brief_control_system.text().strip()
        brief.preferred_fixture_style = self.brief_fixture_style.currentText()
        brief.project_type = self.brief_project_type.currentText()
        brief.project_stage = self.brief_stage.currentText()
        brief.has_arch_plans = self.brief_has_plans.isChecked()
        brief.installation_limits = self._brief_plain(self.brief_install_limits)
        brief.fixed_electrical_points = self._brief_plain(self.brief_fixed_points)
        brief.budget_range = self.brief_budget.text().strip()
        brief.investment_priorities = self._brief_plain(self.brief_investment)
        brief.lighting_problems = self._brief_plain(self.brief_problems)
        brief.additional_notes = self._brief_plain(self.brief_notes)
        brief.success_criteria = self._brief_plain(self.brief_success)
        self._update_brief_auto_summary()

    def _update_brief_auto_summary(self) -> None:
        if not hasattr(self, "brief_auto_summary"):
            return
        brief = self.room.client_brief
        zones = ", ".join(z.name for z in self.room.zones[:4]) or "No zones yet"
        daylight = f"{self.room.daylight.orientation}, {self.room.daylight.window_width_m:.1f} x {self.room.daylight.window_height_m:.1f} m"
        finish = f"{self.room.envelope.cladding_tone}, {self.room.envelope.tambour_ral}"
        self.brief_auto_summary.setText(
            f"Auto answers already known from project data: room {self.room.room_type}, "
            f"{self.room.width:.1f} x {self.room.length:.1f} m, ceiling {self.room.ceiling_height:.2f} m, "
            f"CCT {self.room.cct_kelvin}K, target {self.room.lux_target} lx, ceiling drop {self.room.envelope.gypsum_drop_m * 100:.0f} cm, "
            f"wall finish {finish}, daylight {daylight}, zones {zones}. "
            f"Direct brief: user {brief.primary_user or 'not set'}, feeling {brief.desired_feeling}, scenes {brief.wanted_scenes}."
        )

    def _refresh_brief_controls(self) -> None:
        if not hasattr(self, "brief_primary_user"):
            return
        brief = self.room.client_brief
        self.brief_primary_user.setText(brief.primary_user)
        self.brief_age.setCurrentText(brief.user_age_group)
        self.brief_daily.setPlainText(brief.daily_routine)
        self.brief_hours.setCurrentText(brief.hours_in_space)
        self.brief_use_hours.setCurrentText(brief.main_use_hours)
        self.brief_special_needs.setPlainText(brief.special_needs)
        self.brief_feeling.setCurrentText(brief.desired_feeling)
        self.brief_current_feedback.setPlainText(brief.current_lighting_feedback)
        self.brief_priority.setPlainText(brief.lighting_priority)
        self.brief_success_feeling.setPlainText(brief.success_feeling)
        self.brief_activities.setPlainText(brief.activities)
        self.brief_multi_use.setChecked(brief.multi_use)
        self.brief_needs_scenes.setChecked(brief.needs_scenes)
        self.brief_wanted_scenes.setPlainText(brief.wanted_scenes)
        self.brief_one_click.setChecked(brief.one_click_scenes)
        self.brief_time_based.setChecked(brief.time_based_lighting)
        self.brief_night.setChecked(brief.night_guidance)
        self.brief_special_areas.setPlainText(brief.special_areas)
        self.brief_daylight_notes.setPlainText(brief.daylight_notes)
        self.brief_daylight_blend.setChecked(brief.gentle_daylight_blend)
        self.brief_style.setCurrentText(brief.design_style)
        self.brief_materials.setPlainText(brief.materials)
        self.brief_reflective.setCurrentText(brief.reflective_behavior)
        self.brief_textures.setPlainText(brief.highlight_textures)
        self.brief_language.setCurrentText(brief.lighting_language)
        self.brief_references.setPlainText(brief.references)
        self.brief_focal.setPlainText(brief.focal_point)
        self.brief_highlights.setPlainText(brief.highlight_areas)
        self.brief_art.setPlainText(brief.art_or_special_elements)
        self.brief_soft.setPlainText(brief.soft_areas)
        self.brief_depth.setChecked(brief.depth_and_shadow)
        self.brief_dimming.setChecked(brief.dimming_required)
        self.brief_smart.setChecked(brief.smart_lighting)
        self.brief_phone.setChecked(brief.phone_control)
        self.brief_auto_scenes.setChecked(brief.automatic_scenes)
        self.brief_motion.setChecked(brief.motion_sensors)
        self.brief_smart_home.setChecked(brief.smart_home_integration)
        self.brief_control_system.setText(brief.control_system)
        self.brief_fixture_style.setCurrentText(brief.preferred_fixture_style)
        self.brief_project_type.setCurrentText(brief.project_type)
        self.brief_stage.setCurrentText(brief.project_stage)
        self.brief_has_plans.setChecked(brief.has_arch_plans)
        self.brief_install_limits.setPlainText(brief.installation_limits)
        self.brief_fixed_points.setPlainText(brief.fixed_electrical_points)
        self.brief_budget.setText(brief.budget_range)
        self.brief_investment.setPlainText(brief.investment_priorities)
        self.brief_problems.setPlainText(brief.lighting_problems)
        self.brief_notes.setPlainText(brief.additional_notes)
        self.brief_success.setPlainText(brief.success_criteria)
        self._update_brief_auto_summary()

    def _read_inputs(self) -> None:
        # V8: flush LayersTabWidget state into room before reading
        if self._layers_tab_widget is not None:
            try:
                self._layers_tab_widget.apply_to_room(self.room)
            except Exception:
                pass
        self.room.room_type = self.room_type.currentText()
        self.room.width = self.width_in.value()
        self.room.length = self.length_in.value()
        self.room.ceiling_height = self.height_in.value()
        self.room.envelope.gypsum_drop_m = self.gypsum_drop_in.value()
        self.room.envelope.wall_cladding = self.wall_cladding_chk.isChecked()
        self.room.envelope.cladding_tone = self.cladding_tone.currentText()
        self.room.envelope.tambour_ral = self.tambour_ral.currentText()
        self._read_client_brief_inputs()
        if hasattr(self, "wizard_template"):
            self.room.space_template = self.wizard_template.currentText()
            self.room.design_preset = self.wizard_preset.currentText()
            self.room.view_mode = self.view_mode.currentText()
            self.room.existing_lighting_state = self.existing_lighting_state.toPlainText().strip()
        if hasattr(self, "language_combo"):
            self.room.ui_language = self.language_combo.currentData() or "he"
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
        # V8: legacy layer controls — only read if old UI is active (no LayersTabWidget)
        if not self._layers_tab_widget:
            if hasattr(self, "spot_fixture"):
                self.room.default_spot_fixture = self.spot_fixture.currentText()
            if hasattr(self, "beam"):
                self.room.beam_angle = int(self.beam.currentText().split()[0])
            if hasattr(self, "offset"):
                self.room.wall_offset = min(self.offset.value(), min(self.room.width, self.room.length) / 2 - 0.01)
            if hasattr(self, "spot_qty"):
                self.room.spot_quantity_override = self.spot_qty.value() or None
            if hasattr(self, "heatmap_chk"):
                self.room.show_heatmap = self.heatmap_chk.isChecked()
            if hasattr(self, "point_chk"):
                self.room.show_point_values = self.point_chk.isChecked()
            if self.room.profiles and hasattr(self, "profile_enabled"):
                self.room.profiles[0].enabled  = self.profile_enabled.isChecked()
                self.room.profiles[0].shape     = self.profile_shape.currentText()
                self.room.profiles[0].length_m  = self.profile_len.value()
                self.room.profiles[0].width_m   = (self.profile_width.value() if hasattr(self, 'profile_width') else 0.5)
                self.room.profiles[0].side_b_m  = self.profile_side_b.value()
                self.room.profiles[0].side_c_m  = self.profile_side_c.value()
                self.room.profiles[0].quantity  = self.profile_qty.value()
                self.room.profiles[0].spacing_m = self.profile_spacing.value()
                self.room.profiles[0].lm_per_m  = self.profile_lmm.value()
                self.room.profiles[0].angle_deg = self.profile_angle.value()
                self.room.profiles[0].x         = self.profile_x.value()
                self.room.profiles[0].y         = self.profile_y.value()
            if hasattr(self, "ambient_enabled"):
                self.room.ambient.enabled   = self.ambient_enabled.isChecked()
                self.room.ambient.shape     = self.ambient_shape.currentText()
                self.room.ambient.length_m  = self.ambient_len.value()
                self.room.ambient.lm_per_m  = self.ambient_lmm.value()
                self.room.ambient.angle_deg = self.ambient_angle.value()
            if hasattr(self, "track_enabled") and self.room.tracks:
                t = self.room.tracks[0]
                if self.track_enabled.isChecked() or self.track_qty.value() > 0:
                    t.enabled   = self.track_enabled.isChecked()
                    t.shape     = self.track_shape.currentText()
                    t.length_m  = self.track_len.value()
                    t.width_cm  = float(self.track_width.currentText().split()[0])
                    t.angle_deg = self.track_angle.value()
                    t.x         = self.track_x.value()
                    t.y         = self.track_y.value()
                    qty = self.track_qty.value()
                    t.fixtures  = [TrackFixture(self.track_fix.currentText(),
                                    (i+1)/(qty+1)) for i in range(qty)]
                else:
                    t.enabled = False; t.fixtures = []
            if self.room.pendants and hasattr(self, "pendant_enabled"):
                p0 = self.room.pendants[0]
                p0.enabled      = self.pendant_enabled.isChecked()
                p0.pendant_type = self.pendant_type.currentText()
                p0.fixture_type = self.pendant_fixture.currentText()
                p0.quantity     = self.pendant_qty.value()
                p0.drop_m       = self.pendant_drop.value()
                p0.spacing_m    = self.pendant_spacing.value()
                p0.angle_deg    = self.pendant_angle.value()
                p0.x            = self.pendant_x.value()
                p0.y            = self.pendant_y.value()
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
            self._render_workflow(snap)
            self._render_summary(snap.avg_lux, snap.watts, snap.cri, snap.ugr, snap.planner, snap.lux)
            self._render_lightmap_pixels(snap.heatmap)
            self._render_point_values(snap.heatmap)
            self._render_compliance(snap.lux)
            self._render_zones(snap.lux)
            self._render_architectural_ai()
            self._render_validation(snap.lux)
            self._render_design_review(snap)
            self._render_alternatives(snap)
            self._render_before_after(snap)
            self._render_catalogue()
            self._render_energy()
            self._render_pricing()
            self._render_3d_preview(snap.lux)
            self.state.mark_dirty()
            if not self._suppress_undo_push:
                self._schedule_undo_push()
            if _V8_TEAM_LOADED and hasattr(self, "_renderer_stack"):
                self._renderer_stack.setCurrentIndex(1)
            if hasattr(self, "kpi_row") and self._last_snapshot:
                try: self.kpi_row.update_from_snap(self._last_snapshot, self.room)
                except Exception: pass
            spot_status = f"{len(snap.spots)} ספוטים" if self.room.layer(1).enabled else "ספוטים כבויים"
            perf = f"{snap.elapsed_ms:.0f} ms"
            self.status.showMessage(f"{self.room.width:.1f}x{self.room.length:.1f}m | יעד {self.room.lux_target} lx | ממוצע {snap.avg_lux:.0f} lx | {spot_status} | UGR {snap.ugr} | CRI {snap.cri:.0f} | Sim {perf}")
        except Exception as exc:
            self.state.report_error(f"Recalculate failed: {exc}")
            QMessageBox.warning(self, "Simulation", f"Calculation failed safely:\n{exc}")

    def _render_workflow(self, snap: SimulationSnapshot) -> None:
        active_layers = sum(1 for layer in self.room.layers if layer.enabled)
        import_ready = bool(self.room.floor_plan.path or self.room.floor_plan.source_path)
        zones_ready = bool(self.room.zones)
        profile_count = sum(1 for item in self.room.profiles if item.enabled)
        track_count = sum(1 for item in self.room.tracks if item.enabled)
        pendant_count = sum(1 for item in self.room.pendants if item.enabled)
        furniture_count = sum(1 for item in self.room.furniture if item.enabled)
        fixture_count = len(snap.spots) + profile_count + track_count + pendant_count
        if self.room.ambient.enabled:
            fixture_count += 1
        if self.room.curtain_lighting.enabled:
            fixture_count += 1
        avg_ratio = snap.avg_lux / max(self.room.lux_target, 1)
        uniformity = snap.min_lux / snap.avg_lux if snap.avg_lux > 0 else 0
        issues = ValidationEngine(self.room, snap.lux).issues()
        steps = [
            ("01", "הרשמה ומילוי פרטים", True, f"{self.room.room_type}, {self.room.width:.1f} x {self.room.length:.1f} m, תקרה {self.room.ceiling_height:.2f} m"),
            ("02", "אפיון לקוח וחוויה", bool(self.room.client_brief.primary_user or self.room.client_brief.desired_feeling), f"{self.room.client_brief.desired_feeling} | {self.room.client_brief.wanted_scenes}"),
            ("03", "ייבוא תכנית / נתוני בסיס", import_ready, "תכנית רשומה כ-underlay" if import_ready else "אפשר לייבא PDF / DXF / SVG / תמונה"),
            ("04", "תקרה, חיפויים וחומרים", True, f"הנמכה {self.room.envelope.gypsum_drop_m * 100:.0f} ס״מ, חיפוי {self.room.envelope.cladding_tone}"),
            ("05", "אזורים וריהוט", zones_ready or furniture_count > 0, f"{len(self.room.zones)} אזורים, {furniture_count} פריטי ריהוט"),
            ("06", "פריסת שכבות וגופים", active_layers > 0, f"{active_layers}/3 שכבות פעילות, {fixture_count} מערכות תאורה"),
            ("07", "חישוב לוקס נקודתי", snap.avg_lux > 0, f"ממוצע {snap.avg_lux:.0f} lx, מינימום {snap.min_lux:.0f} lx, מקסימום {snap.max_lux:.0f} lx"),
            ("08", "מפת אור פיקסלית", bool(snap.heatmap), f"{len(snap.heatmap)} x {len(snap.heatmap[0]) if snap.heatmap else 0} נקודות חישוב"),
            ("09", "בדיקה, חשמל ודוחות", not issues and uniformity >= uniformity_target(self.room.room_type), f"U0 {uniformity:.2f}, {len(issues)} המלצות פתוחות, {snap.watts:.0f} W"),
        ]
        cards = []
        for num, title, ok, detail in steps:
            color = P["green"] if ok else P["amber"]
            state = "מוכן" if ok else "להמשך"
            cards.append(
                f"<tr>"
                f"<td class='dot' style='background:{color}'>{num}</td>"
                f"<td><b>{title}</b><br><span>{detail}</span></td>"
                f"<td style='color:{color};font-weight:900'>{state}</td>"
                f"</tr>"
            )
        target_color = P["green"] if 0.9 <= avg_ratio <= 1.25 else P["amber"] if 0.75 <= avg_ratio <= 1.5 else P["red"]
        self.workflow_text.setHtml(f"""
<style>
body{{direction:rtl;color:{P['text']};font-family:Segoe UI;line-height:1.6}}
h3{{color:{P['blue']};margin-bottom:6px;font-size:20px}}
.hero{{background:{P['panel2']};border:1px solid {P['border2']};border-radius:10px;padding:12px;margin-bottom:10px}}
.kpi{{display:inline-block;margin-left:18px;color:{P['muted']}}}
.kpi b{{color:{P['text']};font-size:19px}}
table{{width:100%;border-collapse:separate;border-spacing:0 6px}}
td{{background:{P['panel3']};border-top:1px solid {P['border']};border-bottom:1px solid {P['border']};padding:9px;vertical-align:middle}}
.dot{{width:44px;color:#061018;font-weight:900;text-align:center;border-radius:18px}}
span{{color:{P['muted']}}}
</style>
<h3>תהליך תכנון תאורה מקצועי</h3>
<div class="hero">
  <span class="kpi">יעד<br><b>{self.room.lux_target:.0f} lx</b></span>
  <span class="kpi">בפועל<br><b style="color:{target_color}">{snap.avg_lux:.0f} lx</b></span>
  <span class="kpi">אחידות<br><b>{uniformity:.2f}</b></span>
  <span class="kpi">חישוב<br><b>{snap.elapsed_ms:.0f} ms</b></span>
</div>
<table>{''.join(cards)}</table>
""")

    def _lux_color_hex(self, ratio: float) -> str:
        ratio = clamp(ratio, 0, 1)
        if ratio < 0.5:
            k = ratio / 0.5
            r = int(28 + 45 * k)
            g = int(96 + 118 * k)
            b = int(218 - 110 * k)
        else:
            k = (ratio - 0.5) / 0.5
            r = int(225 + 30 * k)
            g = int(188 - 116 * k)
            b = int(58 - 20 * k)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _render_lightmap_pixels(self, heat: List[List[float]]) -> None:
        vals = [v for row in heat for v in row]
        if not vals:
            self.lightmap_text.setHtml(f"<style>body{{color:{P['text']};font-family:Segoe UI}}</style><h3>No light map yet</h3>")
            return
        hi = max(max(vals), self.room.lux_target * 1.5, 1)
        avg = sum(vals) / len(vals)
        uniformity = min(vals) / avg if avg > 0 else 0
        grid_rows = []
        for row in heat:
            cells = []
            for val in row:
                cells.append(f"<td title='{val:.0f} lx' style='background:{self._lux_color_hex(val / hi)}'>&nbsp;</td>")
            grid_rows.append(f"<tr>{''.join(cells)}</tr>")
        legend_cells = "".join(
            f"<td style='background:{self._lux_color_hex(i / 23)}'>&nbsp;</td>"
            for i in range(24)
        )
        self.lightmap_text.setHtml(f"""
<style>
body{{direction:ltr;color:{P['text']};font-family:Segoe UI;line-height:1.5}}
h3{{color:{P['cyan']};margin-bottom:4px}}
.meta{{color:{P['muted']};margin-bottom:10px}}
.map{{border-collapse:separate;border-spacing:2px;background:{P['panel2']};padding:8px;border:1px solid {P['border']}}}
.map td{{width:13px;height:13px;border-radius:3px}}
.legend{{border-collapse:collapse;margin-top:10px}}
.legend td{{width:10px;height:10px}}
.kpi{{display:inline-block;margin-right:18px;color:{P['muted']}}}
.kpi b{{color:{P['text']};font-size:17px}}
</style>
<h3>Pixel lux distribution map</h3>
<div class="meta">Floor and work-surface light distribution. Blue areas are dark, red areas are high intensity.</div>
<div>
  <span class="kpi">Average<br><b>{avg:.0f} lx</b></span>
  <span class="kpi">Min / Max<br><b>{min(vals):.0f} / {max(vals):.0f} lx</b></span>
  <span class="kpi">Uniformity<br><b>{uniformity:.2f}</b></span>
  <span class="kpi">Target<br><b>{self.room.lux_target:.0f} lx</b></span>
</div>
<br>
<table class="map">{''.join(grid_rows)}</table>
<table class="legend"><tr>{legend_cells}</tr></table>
<div class="meta">0 lx → {hi:.0f} lx</div>
""")

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
            if hasattr(self, "profile_lmm_hint"): self.profile_lmm_hint.setText(f"סהכ שורה: {self.room.profiles[0].total_lm:,.0f} lm | יעד מחושב: {self.room.target_lumens:,.0f} lm")
        if hasattr(self, "ambient_lmm_hint"):
            self.ambient_lmm_hint.setText(f"תאורת אווירה: {self.room.ambient.total_lm:,.0f} lm | {self.room.ambient.watts:.1f} W")
        self.summary_text.setHtml(f"""
<style>body{{direction:rtl;color:{P['text']};font-family:Segoe UI;line-height:1.7}} .v{{color:{P['green']};font-weight:700}} .w{{color:{P['amber']}}}</style>
<h3 style="color:{P['blue']}">סיכום חישובי תאורה {APP_VERSION}</h3>
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
        note = ""
        if self.room.room_type in NON_WORKPLACE_ROOM_TYPES:
            note = (f"<p style='color:{P['amber']}'>שים לב: EN 12464-1 הוא תקן לתאורת "
                    f"מקומות עבודה ואינו חל ישירות על חללי מגורים. הערכים עבור "
                    f"\"{self.room.room_type}\" הם יעדי תכנון מומלצים בלבד.</p>")
        ambient_zone = LUX_AMBIENT_ZONES.get(self.room.room_type)
        if ambient_zone:
            note += (f"<p style='color:{P['muted']}'>יעד משימה {self.room.lux_target} lx; "
                     f"מומלץ אזור היקפי/סובב של כ-{ambient_zone} lx.</p>")
        self.compliance_text.setHtml(f"""
<style>body{{direction:rtl;color:{P['text']};font-family:Segoe UI}} table{{width:100%;border-collapse:collapse}} td{{border-bottom:1px solid {P['border']};padding:6px}}</style>
<h3 style="color:{P['green']}">תאימות לתקן EN 12464-1</h3>
<table>{rows}</table>
{note}
<p>אומדן קרדיט יעילות בסגנון LEED: <b style="color:{P['green']}">{comp.leed_score()} / 6</b></p>
<p style="color:{P['muted']};font-size:11px">חישוב לוקס: מודל ישיר (קנדלה/אלומה) + עקיף מפושט (split-flux). UGR לפי CIE 117 מפושט. לאימות סופי השתמש ב-DIALux/Relux.</p>
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

        def _eff(d):
            return d.get("efficacy") or round(float(d.get("lm", 0)) / max(float(d.get("w", 1)), 0.1), 1)
        rows = "".join(
            f"<tr><td>{'★ ' if d.get('favorite') else ''}{name}</td>"
            f"<td>{d.get('category','-')}</td><td>{d.get('brand','-')}</td>"
            f"<td>{d.get('lm',0):.0f}</td><td>{d.get('w',0):.0f}</td>"
            f"<td>{_eff(d):.0f}</td><td>{d.get('cri',0):.0f}</td>"
            f"<td>{d.get('cct',0)}K</td><td>{d.get('beam',0):.0f}°</td>"
            f"<td>{d.get('ip','-')}</td><td>₪{float(d.get('price',0)):,.0f}</td></tr>"
            for name, d in fixtures.items()
        )
        # Category / CCT / CRI / IP summary so the user can scan the library quickly.
        cats = sorted({str(d.get("category", "-")) for d in fixtures.values()})
        ccts = sorted({int(d.get("cct", 0)) for d in fixtures.values()})
        ips = sorted({str(d.get("ip", "-")) for d in fixtures.values()})
        cris = sorted({int(d.get("cri", 0)) for d in fixtures.values()})
        summary = (f"קטגוריות: {', '.join(cats)} | CCT: {', '.join(str(c) + 'K' for c in ccts)} | "
                   f"CRI: {', '.join(str(c) for c in cris)} | IP: {', '.join(ips)}")
        self.catalogue_text.setHtml(f"""
<style>body{{direction:rtl;color:{P['text']};font-family:Segoe UI}} table{{width:100%;border-collapse:collapse}} td{{border-bottom:1px solid {P['border']};padding:5px}} .sum{{color:{P['muted']};font-size:11px;margin-bottom:8px}}</style>
<h3 style="color:{P['blue']}">קטלוג גופי תאורה ({len(fixtures)})</h3>
<div class="sum">{summary}</div>
<table><tr><td>שם</td><td>קטגוריה</td><td>מותג</td><td>lm</td><td>W</td><td>lm/W</td><td>CRI</td><td>CCT</td><td>Beam</td><td>IP</td><td>מחיר</td></tr>{rows}</table>
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
        rows = "".join(f"<tr><td>{name}</td><td>{qty}</td><td>₪{unit:,.2f}</td><td>₪{total:,.2f}</td></tr>" for name, qty, unit, total in price.line_items())
        self.pricing_text.setHtml(f"""
<style>body{{direction:rtl;color:{P['text']};font-family:Segoe UI}} table{{width:100%;border-collapse:collapse}} td{{border-bottom:1px solid {P['border']};padding:6px}}</style>
<h3 style="color:{P['green']}">תמחור אוטומטי ועומס חשמלי (₪ ILS)</h3>
<table><tr><td>פריט</td><td>כמות</td><td>יחידה</td><td>סה"כ</td></tr>{rows}</table>
<p>חומרים: <b>₪{totals['material']:,.2f}</b> | תוספת רווח: <b>₪{totals['markup']:,.2f}</b> | עבודה: <b>₪{totals['labour']:,.2f}</b> | אומדן כולל: <b style="color:{P['green']}">₪{totals['total']:,.2f}</b></p>
<p>Electrical load: <b>{electrical['watts']:.0f} W</b> | <b>{electrical['amps']:.2f} A</b> @ {electrical['voltage']:.0f}V | Recommended circuits: <b>{electrical['circuits']:.0f}</b></p>
<p>Energy score: <b style="color:{P['green']}">{electrical['efficiency_score']:.0f}/100</b> | Monthly estimate: <b>{electrical['monthly_kwh']:.1f} kWh</b> | CO2 estimate: <b>{electrical['co2_kg']:.1f} kg/month</b></p>
""")

    def _quality_score(self, snap: SimulationSnapshot) -> Tuple[int, Dict[str, float]]:
        target_ratio = snap.avg_lux / max(self.room.lux_target, 1)
        target_score = max(0, 100 - abs(target_ratio - 1) * 90)
        uniformity = snap.min_lux / snap.avg_lux if snap.avg_lux > 0 else 0
        uniformity_score = clamp(uniformity / 0.45 * 100, 0, 100)
        lpd = snap.watts / max(self.room.area, 0.01)
        lpd_limit = LPD_LIMITS_W_M2.get(self.room.room_type, 12)
        energy_score = clamp(100 - max(0, lpd - lpd_limit) * 8, 0, 100)
        layer_score = 100 if sum(1 for l in self.room.layers if l.enabled) >= 2 else 65
        issues = len(ValidationEngine(self.room, snap.lux).issues())
        validation_score = max(0, 100 - issues * 12)
        score = int(round(target_score * 0.28 + uniformity_score * 0.24 + energy_score * 0.18 + layer_score * 0.12 + validation_score * 0.18))
        return score, {"target": target_score, "uniformity": uniformity_score, "energy": energy_score, "layers": layer_score, "validation": validation_score}

    def _render_design_review(self, snap: SimulationSnapshot) -> None:
        score, parts = self._quality_score(snap)
        mode = self.room.view_mode
        brief = self.room.client_brief
        issues = ValidationEngine(self.room, snap.lux).issues()
        issue_rows = "".join(f"<li>{name}: {fix}</li>" for name, _why, fix in issues[:6]) or "<li>אין הערות קריטיות כרגע.</li>"
        client_copy = "Client" if mode == "Client" else "Planner"
        self.design_review_text.setHtml(f"""
<style>body{{direction:rtl;color:{P['text']};font-family:Segoe UI;line-height:1.65}} .score{{font-size:38px;color:{P['green']};font-weight:900}} .muted{{color:{P['muted']}}} table{{width:100%;border-collapse:collapse}} td{{border-bottom:1px solid {P['border']};padding:7px}}</style>
<h3 style="color:{P['gold']}">Design Review - {client_copy}</h3>
<div class="score">{score}/100</div>
<p class="muted">סגנון: {self.room.design_preset} | תבנית: {self.room.space_template} | תחושה: {brief.desired_feeling}</p>
<table>
<tr><td>יעד לוקס</td><td>{self.room.lux_target:.0f} lx</td><td>בפועל {snap.avg_lux:.0f} lx</td></tr>
<tr><td>אחידות</td><td>{snap.min_lux / snap.avg_lux if snap.avg_lux else 0:.2f}</td><td>ציון {parts['uniformity']:.0f}</td></tr>
<tr><td>צריכת חשמל</td><td>{snap.watts:.0f} W</td><td>ציון {parts['energy']:.0f}</td></tr>
<tr><td>שכבות פעילות</td><td>{sum(1 for l in self.room.layers if l.enabled)}</td><td>ציון {parts['layers']:.0f}</td></tr>
</table>
<h4>החלטות לאישור</h4>
<ul><li>CCT: {self.room.cct_kelvin}K</li><li>שפת תאורה: {brief.lighting_language}</li><li>תרחישים: {brief.wanted_scenes}</li><li>תקציב: {brief.budget_range or 'לא הוגדר'}</li></ul>
<h4>המלצות לשיפור</h4><ul>{issue_rows}</ul>
""")

    def _render_alternatives(self, snap: SimulationSnapshot) -> None:
        price = PricingEngine(self.room).totals()["total"]
        lpd = snap.watts / max(self.room.area, 0.01)
        options = [
            ("A - Budget", 0.78, 0.82, "פחות גופים, דגש על שכבה פונקציונלית ועלות נמוכה."),
            ("B - Balanced", 1.0, 1.0, "איזון בין שכבות, נוחות עבודה ועלות ריאלית."),
            ("C - Premium", 1.22, 1.35, "יותר שכבות, יותר שליטה, מראה יוקרתי ומכירה חזקה ללקוח."),
        ]
        rows = "".join(
            f"<tr><td>{name}</td><td>{snap.avg_lux * lux_factor:.0f} lx</td><td>{snap.watts * power_factor:.0f} W</td><td>{lpd * power_factor:.1f} W/m²</td><td>₪{price * power_factor:,.0f}</td><td>{desc}</td></tr>"
            for name, lux_factor, power_factor, desc in options
        )
        self.alternatives_text.setHtml(f"""
<style>body{{direction:rtl;color:{P['text']};font-family:Segoe UI}} table{{width:100%;border-collapse:collapse}} td{{border-bottom:1px solid {P['border']};padding:7px;vertical-align:top}}</style>
<h3 style="color:{P['purple']}">חלופות תכנון A / B / C</h3>
<table><tr><td>חלופה</td><td>לוקס</td><td>וואט</td><td>LPD</td><td>אומדן</td><td>מתי לבחור</td></tr>{rows}</table>
""")

    def _render_before_after(self, snap: SimulationSnapshot) -> None:
        existing = self.room.existing_lighting_state or "לא תועד מצב קיים. אפשר למלא ב-Wizard."
        improvements = [
            f"מעבר לתכנון מדיד: {snap.avg_lux:.0f} lx מול יעד {self.room.lux_target:.0f} lx.",
            f"מפת אור פיקסלית לאיתור אזורים כהים וחמים.",
            f"אפשרות להשוות חלופות תקציב / מאוזן / פרימיום.",
            f"הפרדה בין מצב לקוח למצב מתכנן."
        ]
        self.before_after_text.setHtml(f"""
<style>body{{direction:rtl;color:{P['text']};font-family:Segoe UI;line-height:1.65}} .box{{background:{P['panel2']};border:1px solid {P['border']};border-radius:8px;padding:10px;margin:8px 0}}</style>
<h3 style="color:{P['cyan']}">Before / After</h3>
<div class="box"><b>Before - מצב קיים</b><br>{existing}</div>
<div class="box"><b>After - תכנון מוצע</b><ul>{''.join(f'<li>{x}</li>' for x in improvements)}</ul></div>
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
        # Suppress the per-widget recalculate() while we push model values into the
        # controls; otherwise an early setValue triggers _read_inputs mid-refresh and
        # clobbers values that haven't been written to their widgets yet.
        was_building = self._building
        self._building = True
        try:
            self._refresh_all_controls_inner()
        finally:
            self._building = was_building
        self.recalculate()

    def _refresh_all_controls_inner(self) -> None:
        self.room_type.setCurrentText(self.room.room_type)
        self.width_in.setValue(self.room.width)
        self.length_in.setValue(self.room.length)
        self.height_in.setValue(self.room.ceiling_height)
        self.gypsum_drop_in.setValue(self.room.envelope.gypsum_drop_m)
        self.wall_cladding_chk.setChecked(self.room.envelope.wall_cladding)
        self.cladding_tone.setCurrentText(self.room.envelope.cladding_tone)
        self.tambour_ral.setCurrentText(self.room.envelope.tambour_ral)
        if hasattr(self, "wizard_template"):
            self.wizard_template.setCurrentText(self.room.space_template)
            self.wizard_preset.setCurrentText(self.room.design_preset)
            self.view_mode.setCurrentText(self.room.view_mode)
            self.existing_lighting_state.setPlainText(self.room.existing_lighting_state)
        if hasattr(self, "language_combo"):
            self.language_combo.setCurrentIndex(1 if self.room.ui_language == "en" else 0)
            self._apply_language()
        self._refresh_brief_controls()
        self.lux_in.setValue(self.room.lux_override or 0)
        self.target_unit.setCurrentText(self.room.target_unit)
        self.lux_in.setValue(self.room.lumens_override if self.room.target_unit == "lumens" and self.room.lumens_override else self.room.lux_override or 0)
        self.cct.setCurrentText(self.room.cct_preset)
        catalogue_keys = list(self.room.fixture_catalogue.keys())
        if hasattr(self, "spot_fixture"):
            self.spot_fixture.clear()
            self.spot_fixture.addItems(catalogue_keys)
            self.spot_fixture.setCurrentText(self.room.default_spot_fixture)
        if hasattr(self, "pendant_fixture"):
            self.pendant_fixture.clear()
            self.pendant_fixture.addItems(catalogue_keys)
        if hasattr(self, "track_fix"):
            self.track_fix.clear()
            track_width = self.room.tracks[0].width_cm if self.room.tracks else 2.5
            self.track_fix.addItems(self._track_fixture_options(track_width))
        # V8 path: refresh the LayersTabWidget cards in place
        if self._layers_tab_widget is not None:
            self._rebuild_layers_tab()
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
        # recalculate() is invoked by the _refresh_all_controls() wrapper.

    # ── V8: Natural Language ──────────────────────────────────────────────
    def _apply_nl_parsed(self, parsed: dict) -> None:
        if parsed.get("room_type") and parsed["room_type"] in ROOM_TYPES:
            self.room.room_type = parsed["room_type"]
        if parsed.get("width"):  self.room.width  = float(parsed["width"])
        if parsed.get("length"): self.room.length = float(parsed["length"])
        if parsed.get("ceiling"):self.room.ceiling_height = float(parsed["ceiling"])
        if parsed.get("cct"):    self.room.cct_preset = cct_preset_for_kelvin(parsed["cct"])
        if parsed.get("lux"):
            self.room.lux_override = int(parsed["lux"])
            self.room.target_unit = "lux"
        if parsed.get("feeling"):self.room.client_brief.desired_feeling = parsed["feeling"]
        # enable systems
        sys_map = parsed.get("systems", [])
        if sys_map and self.room.tracks:
            self.room.tracks[0].enabled = "track" in sys_map
        if sys_map and self.room.profiles:
            self.room.profiles[0].enabled = "profile" in sys_map
        if sys_map and self.room.pendants:
            self.room.pendants[0].enabled = "pendant" in sys_map
        self._refresh_all_controls()
        self.recalculate()
        self.status.showMessage("✅ פרמטרים הוזנו מהטקסט")

    # ── V8: Design Package ────────────────────────────────────────────────
    def _apply_design_package(self, name: str, cfg: dict) -> None:
        # CCT
        cct = cfg.get("cct", 3000)
        if isinstance(cct, str):
            self.room.cct_preset = cct if cct in CCT_PRESETS else cct_preset_for_kelvin(3000)
        else:
            self.room.cct_preset = cct_preset_for_kelvin(cct)
        # feeling
        feel = cfg.get("feel","")
        if feel: self.room.client_brief.desired_feeling = feel
        # systems
        sys = cfg.get("systems", {})
        if self.room.tracks   and "track"   in sys: self.room.tracks[0].enabled   = sys["track"]
        if self.room.profiles and "profile" in sys: self.room.profiles[0].enabled = sys["profile"]
        if self.room.pendants and "pendant" in sys: self.room.pendants[0].enabled = sys["pendant"]
        if hasattr(self.room,"ambient") and "ambient" in sys:
            self.room.ambient.enabled = sys["ambient"]
        self._refresh_all_controls()
        self.recalculate()
        self.status.showMessage(f"✅ חבילת עיצוב '{name}' הוחלה")

    # ── V8: View toggle ───────────────────────────────────────────────────

    def _schedule_undo_push(self) -> None:
        """Coalesce rapid changes (e.g. spinbox drags) into a single undo entry."""
        if not hasattr(self, "_undo_push_timer"):
            self._undo_push_timer = QTimer(self)
            self._undo_push_timer.setSingleShot(True)
            self._undo_push_timer.timeout.connect(self._commit_undo_push)
        self._undo_push_timer.start(600)

    def _commit_undo_push(self) -> None:
        if self._suppress_undo_push:
            return
        try:
            self.undo_stack.push(self.room.to_dict())
        except Exception as exc:
            self.state.report_error(f"Undo push failed: {exc}")

    def _restore_room_state(self, room_dict: dict) -> None:
        """Rebuild the model from a snapshot without polluting the undo history."""
        if hasattr(self, "_undo_push_timer"):
            self._undo_push_timer.stop()
        self._suppress_undo_push = True
        try:
            self.room = RoomModel.from_dict(room_dict)
            ModelGuard.sanitize_room(self.room)
            self._refresh_all_controls()
        finally:
            self._suppress_undo_push = False

    def _snapshot_room_dict(self) -> dict:
        """Room dict for a saved snapshot, without the (recursive) notes/snapshots."""
        d = self.room.to_dict()
        d.pop("sticky_notes", None)
        d.pop("project_snapshots", None)
        return d

    def _sync_annotations_to_room(self) -> None:
        """Pull sticky notes & saved snapshots from the side panels into the model."""
        if hasattr(self, "_sticky_notes"):
            self.room.sticky_notes = self._sticky_notes.get_notes()
        if hasattr(self, "_snapshots"):
            self.room.project_snapshots = self._snapshots.get_snaps()

    def _sync_annotations_from_room(self) -> None:
        """Push sticky notes & saved snapshots from the model into the side panels."""
        if hasattr(self, "_sticky_notes"):
            self._sticky_notes.set_notes(self.room.sticky_notes)
        if hasattr(self, "_snapshots"):
            self._snapshots.set_snaps(self.room.project_snapshots)

    def _restore_snapshot(self, room_dict: dict) -> None:
        try:
            self._restore_room_state(room_dict)
            self.status.showMessage("✅ גרסה שוחזרה")
        except Exception as e:
            QMessageBox.warning(self, "שגיאה", f"שחזור נכשל: {e}")

    def _on_view_changed(self, mode: str) -> None:
        self._view_mode = mode
        if mode == "client":
            # hide technical panels, show clean floor plan
            self.status.showMessage("👤 מצב לקוח — תצוגה נקייה")
            if hasattr(self, "results"): self.results.hide()
        else:
            self.status.showMessage("🛠 מצב מעצב")
            if hasattr(self, "results"): self.results.show()

    # ── V8: Client HTML export ────────────────────────────────────────────
    def export_client_html(self) -> None:
        self._read_inputs()
        snap = getattr(self, "_last_snapshot", None)
        scenes = []
        if hasattr(self, "_scene_timeline"):
            scenes = self._scene_timeline.get_scenes()
        path, _ = QFileDialog.getSaveFileName(
            self, "ייצא HTML ללקוח",
            f"{self.room.project_name or 'project'}_client.html",
            "HTML (*.html)")
        if not path: return
        out = ClientHTMLExporter.export(self.room, snap, scenes, path)
        QMessageBox.information(self, "ייצא בהצלחה",
            f"קובץ ללקוח נשמר:\n{out}\n\nניתן לשלוח ישירות ללקוח.")

    # ── V8: Undo / Redo ──────────────────────────────────
    def _undo(self) -> None:
        state = self.undo_stack.undo()
        if state:
            self._restore_room_state(state)
            self.status.showMessage("↩ בוטל")

    def _redo(self) -> None:
        state = self.undo_stack.redo()
        if state:
            self._restore_room_state(state)
            self.status.showMessage("↪ בוצע שוב")

    def closeEvent(self, event) -> None:
        if not getattr(self.state, "dirty", False):
            event.accept()
            return
        reply = QMessageBox.question(
            self, "שינויים לא נשמרו",
            "יש שינויים שלא נשמרו. לשמור לפני יציאה?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if reply == QMessageBox.Save:
            self.save_project()
            # If still dirty (save cancelled/failed), abort the close.
            if getattr(self.state, "dirty", False):
                event.ignore()
            else:
                event.accept()
        elif reply == QMessageBox.Discard:
            event.accept()
        else:
            event.ignore()

    # ── V8: IES/LDT Import ───────────────────────────────
    def import_ies(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "ייבא קובץ פוטומטרי", "",
            "Photometry files (*.ies *.ldt);;IES (*.ies);;LDT (*.ldt);;All Files (*)")
        if not path:
            return
        try:
            ext = os.path.splitext(path)[1].lower()
            if ext == ".ldt":
                ph = LDTParser.parse_file(path)
            else:
                ph = IESParser.parse_file(path)
            fix_dict = ph.to_fixture_dict()
            name = os.path.splitext(os.path.basename(path))[0]
            # avoid duplicates
            base_name, idx = name, 1
            while name in self.room.fixture_catalogue:
                name = f"{base_name}_{idx}"
                idx += 1
            self.room.fixture_catalogue[name] = fix_dict
            self._refresh_all_controls()
            QMessageBox.information(
                self, "IES/LDT נטען",
                f"גוף '{name}' נוסף לקטלוג.\n"
                f"לומן: {fix_dict['lm']:.0f} lm | "
                f"וואט: {fix_dict['w']:.1f} W | "
                f"זווית קרן: {fix_dict['beam']:.1f}°\n"
                f"יעילות: {ph.efficacy_lm_per_w():.1f} lm/W")
        except Exception as exc:
            QMessageBox.critical(self, "שגיאה", f"ייבוא IES נכשל:\n{exc}")

    # ── V8: AI Design Review ─────────────────────────────
    def run_ai_review(self) -> None:
        self._read_inputs()
        snap = getattr(self, "_last_snapshot", None)
        # show loading message
        self.status.showMessage("🤖 שולח לסקירת AI…")
        QApplication.processEvents()
        result = AIDesignReviewer.review(self.room, snap)
        # display in design review tab
        if hasattr(self, "design_review_text"):
            self.design_review_text.setHtml(
                f"<style>body{{direction:rtl;color:#F0F4FF;font-family:Segoe UI;line-height:1.7;padding:10px}}"
                f"h3{{color:#3D8EF0}} li{{margin-bottom:6px}}</style>"
                f"<h3>🤖 סקירת תכנון AI — Claude</h3>"
                f"<pre style='white-space:pre-wrap;color:#F0F4FF;font-family:Segoe UI;font-size:13px'>{result}</pre>")
        self.status.showMessage("✅ סקירת AI הושלמה")
        QMessageBox.information(self, "סקירת AI הושלמה", result[:600] + ("…" if len(result) > 600 else ""))


    # ── V8 LAYERS TAB ──────────────────────────────────────────────────────
    def _rebuild_layers_tab_v8(self) -> None:
        """Replace legacy layers column with LayersTabWidget."""
        while self.layers_layout.count():
            item = self.layers_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        opts = list(self.room.fixture_catalogue.keys())
        ltw = LayersTabWidget()
        ltw.build(self.room, opts)
        ltw.changed.connect(self._on_layers_changed)
        self.layers_layout.addWidget(ltw)
        self._layers_tab_widget = ltw

    def _on_layers_changed(self) -> None:
        if self._layers_tab_widget is not None:
            self._layers_tab_widget.apply_to_room(self.room)
        self._schedule_recalc()

    def _schedule_recalc(self) -> None:
        if not hasattr(self, "_recalc_timer"):
            from PySide6.QtCore import QTimer
            self._recalc_timer = QTimer(self)
            self._recalc_timer.setSingleShot(True)
            self._recalc_timer.timeout.connect(self.recalculate)
        self._recalc_timer.start(400)

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "פתח פרויקט", "", "Lighting Design Project (*.ldp);;JSON (*.json);;All Files (*)")
        if not path:
            return
        try:
            extracted: Dict[str, str] = {}
            if ProjectContainer.is_zip(path):
                room_dict, assets = ProjectContainer.load(path)
                # restore bundled assets to temp dir
                if assets:
                    cache = os.path.join(tempfile.gettempdir(), "ldp_assets")
                    os.makedirs(cache, exist_ok=True)
                    for fname, data in assets.items():
                        out_path = os.path.join(cache, fname)
                        with open(out_path, "wb") as af:
                            af.write(data)
                        extracted[fname] = out_path
            else:
                with open(path, "r", encoding="utf-8") as f:
                    room_dict = json.load(f)
                assets = {}
            # Repoint asset references (saved as relative "assets/<basename>") to the
            # freshly extracted temp files, matching by basename.
            def _resolve_asset(value: str) -> str:
                base = os.path.basename(value) if value else ""
                return extracted.get(base, value)
            fp = room_dict.get("floor_plan")
            if isinstance(fp, dict) and fp.get("path"):
                fp["path"] = _resolve_asset(fp["path"])
            br = room_dict.get("branding")
            if isinstance(br, dict) and br.get("company_logo"):
                br["company_logo"] = _resolve_asset(br["company_logo"])
            self.room = RoomModel.from_dict(room_dict)
            ModelGuard.sanitize_room(self.room)
            self.current_file = path
            self._suppress_undo_push = True
            try:
                self._refresh_all_controls()
                self._sync_annotations_from_room()
            finally:
                self._suppress_undo_push = False
            self.state.mark_saved()
            self.undo_stack.clear()
            self.undo_stack.push(self.room.to_dict())
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
            self._sync_annotations_to_room()
            # Collect asset paths (underlay + logo)
            assets = []
            if self.room.floor_plan.path and os.path.isfile(self.room.floor_plan.path):
                assets.append(self.room.floor_plan.path)
            if self.room.branding.company_logo and os.path.isfile(self.room.branding.company_logo):
                assets.append(self.room.branding.company_logo)
            ProjectContainer.save(self.room.to_dict(), path, assets or None)
            self.state.mark_saved()
            self.undo_stack.push(self.room.to_dict())
            self.status.showMessage(f"נשמר (V8 ZIP): {path}")
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
                self.room.fixture_catalogue[str(name)] = self._normalize_imported_fixture(raw)
            self._refresh_all_controls()
            QMessageBox.information(self, "הצלחה", "קטלוג הגופים נטען.")
        except Exception as exc:
            QMessageBox.critical(self, "שגיאה", f"ייבוא קטלוג נכשל:\n{exc}")

    @staticmethod
    def _normalize_imported_fixture(raw: dict) -> dict:
        """Map an imported row (JSON/CSV, old or new schema) to a fixture dict."""
        def _f(*keys, default=0.0):
            for k in keys:
                if k in raw and raw[k] not in (None, ""):
                    try:
                        return float(raw[k])
                    except (TypeError, ValueError):
                        pass
            return default

        def _parse_list(val):
            if isinstance(val, list):
                return [float(x) for x in val]
            if isinstance(val, str) and val.strip():
                out = []
                for tok in val.replace(";", ",").replace("|", ",").split(","):
                    tok = tok.strip()
                    if tok:
                        try:
                            out.append(float(tok))
                        except ValueError:
                            pass
                return out
            return []

        lm = _f("lm", "lumens", default=800)
        w = _f("w", "watts", default=8) or 8
        fx = {
            "lm": lm,
            "w": w,
            "cri": _f("cri", default=90),
            "beam": _f("beam", "beam_angle", default=36),
            "cct": int(_f("cct", default=3000)),
            "brand": raw.get("brand", raw.get("מותג", "")),
            "price": _f("price", "מחיר", default=0),
            "currency": raw.get("currency", "ILS"),
            "efficacy": round(lm / max(w, 0.1), 1),
        }
        # Optional normalized / metadata fields (only stored when present).
        for key in ("category", "sku", "mounting", "ip", "dimming", "datasheet",
                    "photometry_file"):
            if raw.get(key):
                fx[key] = raw[key]
        for key in ("cct_default",):
            if raw.get(key):
                fx[key] = int(_f(key, default=fx["cct"]))
        for key in ("sdcm", "lifetime", "ugr_rated"):
            if raw.get(key) not in (None, ""):
                fx[key] = _f(key)
        for key in ("length_m", "lm_per_m"):
            if raw.get(key) not in (None, ""):
                fx[key] = _f(key)
        cct_options = _parse_list(raw.get("cct_options"))
        if cct_options:
            fx["cct_options"] = [int(x) for x in cct_options]
        beam_variants = _parse_list(raw.get("beam_variants"))
        if beam_variants:
            fx["beam_variants"] = beam_variants
        track_widths = _parse_list(raw.get("track_widths"))
        if track_widths:
            fx["track_widths"] = track_widths
        fav = str(raw.get("favorite", raw.get("מועדף", ""))).lower()
        if fav in {"1", "true", "yes", "y"}:
            fx["favorite"] = True
        return fx

    def export_catalogue(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "ייצא קטלוג", "fixture_catalogue.json",
            "JSON (*.json);;CSV (*.csv)")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".json"
        try:
            cat = self.room.fixture_catalogue
            if path.lower().endswith(".csv"):
                # Union of all keys so the CSV is complete and round-trippable.
                fields = ["name"]
                seen = set(fields)
                for data in cat.values():
                    for k in data:
                        if k not in seen:
                            seen.add(k)
                            fields.append(k)
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fields)
                    writer.writeheader()
                    for name, data in cat.items():
                        row = {"name": name}
                        for k, v in data.items():
                            row[k] = ",".join(str(x) for x in v) if isinstance(v, list) else v
                        writer.writerow(row)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(cat, f, ensure_ascii=False, indent=2)
            self.status.showMessage(f"קטלוג יוצא: {path}")
            QMessageBox.information(self, "הצלחה", f"קטלוג הגופים יוצא:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "שגיאה", f"ייצוא קטלוג נכשל:\n{exc}")

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
        path, _ = QFileDialog.getSaveFileName(
            self, "ייצא דוח מקצועי", self.room.project_name + "_report.pdf",
            "PDF Report (*.pdf);;Text (*.txt);;All Files (*)")
        if not path:
            return
        try:
            self._read_inputs()
            snap = getattr(self, "_last_snapshot", None)
            if path.lower().endswith(".pdf"):
                msg = ProfessionalPDFExporter(self.room, snap).export(path)
            else:
                text = ProfessionalExporter(self.room).quotation_text()
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                msg = f"Text exported: {path}"
            self.status.showMessage(msg)
            QMessageBox.information(self, "ייצוא הצליח", msg)
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
    app.setStyleSheet(FULL_STYLESHEET or V8_STYLESHEET or STYLESHEET)
    app.setLayoutDirection(Qt.RightToLeft)
    splash = (PremiumSplash if _V8_TEAM_LOADED else PremiumStartupSplash)()
    splash.show()
    win = LightingApp()
    win.setWindowOpacity(0.0)
    win.show()
    QTimer.singleShot(1100, splash.close)
    QTimer.singleShot(1150, lambda: win.setWindowOpacity(1.0))
    import hashlib as _hlib
    _first_run_flag = os.path.join(os.path.expanduser("~"), ".ldp_v8_seen")
    def _maybe_onboard():
        if not os.path.exists(_first_run_flag) and _V8_TEAM_LOADED:
            ov = OnboardingOverlay(win)
            ov.setGeometry(win.rect())
            ov.show()
            ov.finished.connect(ov.hide)
            ov.finished.connect(lambda: open(_first_run_flag,"w").close())
    QTimer.singleShot(1300, _maybe_onboard)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
