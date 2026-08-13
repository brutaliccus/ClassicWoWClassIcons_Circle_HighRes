"""
Old-school WC3-style class icon builder.

Sources: sources/<class>.png
Outputs: this folder (final icons + layer* subfolders)

Run: pip install -r requirements.txt && python build_class_icons_old_school.py
"""

from __future__ import annotations

import math
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from scipy.ndimage import binary_dilation, distance_transform_edt

ROOT = Path(__file__).resolve().parent
SOURCE_DIR = ROOT / "sources"
OUT = ROOT
CLASSES = [
    "warrior", "paladin", "rogue", "mage", "hunter",
    "druid", "priest", "warlock", "shaman",
]

# Measured from 38×35 reference GIFs (bright metal lip ≈ 10–11% of half-size).
OUTER_R_RATIO = 0.97
RIM_RATIO = 0.11
INNER_R_RATIO = OUTER_R_RATIO - RIM_RATIO  # 0.86
RIM_EXTRA_PX = 3.0  # widen metallic ring inward by this many pixels

# Blue haze ring sitting on the black disc, just inside the metal inner lip.
INNER_HAZE_WIDTH_RATIO = 0.26   # thin band (~1/3 prior 0.78 width; intensity unchanged)
INNER_HAZE_POWER = 1.02         # slow falloff keeps the glow aggressive at the lip
INNER_HAZE_RGB = (2.0, 92.0, 182.0)
INNER_HAZE_GREEN_LIFT = 22.0    # peak green shift at inner lip
INNER_HAZE_BLUE_LIFT = 42.0     # peak blue shift at inner lip
INNER_HAZE_INTENSITY = 1.38     # overall opacity/saturation boost

# Clockwise arc gap: haze fades inside the gap, full intensity outside (see haze_clock_arc_multiplier).
INNER_HAZE_ARC: dict[str, dict[str, float]] = {
    "mage": {
        "start_clock_deg": 73.0,   # ~2:26 — TR fade toward crystal
        "end_clock_deg": 16.0,     # ~12:32 — top taper starts here
        "edge_feather_deg": 12.0,  # same soft taper profile at both fade edges
    },
    "warrior": {
        "start_clock_deg": 270.0,  # 9 o'clock — gap ends here
        "end_clock_deg": 150.0,    # ~5 o'clock — gap starts here
        "edge_feather_deg": 50.0,
    },
}

# Silver ring cast shadow: CW arc on the clock face (default 1 → 9 o'clock).
RING_SHADOW: dict[str, float] = {
    "start_clock_deg": 30.0,   # 1 o'clock
    "end_clock_deg": 270.0,    # 9 o'clock
    "peak_clock_deg": 180.0,   # darkest at 6 o'clock
    "edge_feather_deg": 16.0,
    "depth_power": 1.12,
    "max_tone_drop": 72.0,
    "min_tone": 36.0,          # never clip to full black in the shadow arc
    "lip_extra_mult": 0.42,    # extra lip crush in deep shadow (still floored)
}

# Applied to every class on top of per-class ART_SCALE / inner-ring touch scaling.
GLOBAL_ART_SCALE = 1.05

# Per-class art scale applied before compositing (1.0 = full source size).
ART_SCALE: dict[str, float] = {
    "druid": 0.816,
    "hunter": 0.7875,
    "mage": 0.70875,
    "paladin": 0.95,
    "priest": 1.0,
    "rogue": 0.856844,
    "shaman": 0.887807,
    "warlock": 0.95,
    "warrior": 0.8,
}

# Extra pixels added to scaled art width/height (after ART_SCALE).
ART_SIZE_PAD: dict[str, tuple[int, int]] = {
    "warlock": (4, 4),
}

# Per-class canvas paste nudge in pixels (x right, y down).
ART_PASTE_OFFSET: dict[str, tuple[float, float]] = {
    "druid": (6.0, 0.0),
    "hunter": (6.0, 12.0),
    "mage": (-8.0, 7.0),
    "paladin": (5.0, -5.0),
    "rogue": (-3.0, 1.0),
    "shaman": (8.0, 5.0),
    "warrior": (-2.0, -1.0),
}

# Scale art so square edges touch the inner ring (no overflow past the ring).
ART_TOUCH_INNER_RING: set[str] = {"paladin", "rogue", "warlock"}

# Scale art so top + right silhouette edges touch the inner ring (mage fire body).
ART_TR_TOUCH_INNER_RING: set[str] = {"mage"}


def resolve_source_path(cls: str) -> Path | None:
    path = SOURCE_DIR / f"{cls}.png"
    return path if path.exists() else None

# Base art clipped to inner disc edge (sits behind ring); overhangs still pop above.
BASE_INNER_CLIP: set[str] = {
    "druid", "mage", "paladin", "priest", "rogue", "shaman", "warlock", "warrior",
}

# Base art clipped hard to outer badge edge (prevents soft bleed past the silver ring).
BASE_OUTER_HARD_CLIP: set[str] = {"hunter"}

# Inner-disc arc clip on base art (e.g. bow handle tucked behind ring after art shift).
BASE_ARC_INNER_CLIP: dict[str, list[dict[str, float]]] = {
    "hunter": [
        {
            "clock_start": 190.0,
            "clock_end": 245.0,
            "x_max": 110.0,
            "lip_inset_px": 0.0,
            "feather_px": 0.65,
        },
    ],
}

# Keep base art visible in the ring band (behind the silver ring) within these arcs.
BASE_BEHIND_RING: dict[str, list[dict[str, float]]] = {
    "rogue": [
        {
            "x_max": 95.0,
            "clock_start": 205.0,  # left blade/crossguard tuck behind ring in band
            "clock_end": 318.0,
        },
    ],
}

# Strip stray opaque pixels from cutout corners before compositing (source art artifacts).
ART_STRIP_CORNERS: dict[str, dict[str, int | dict[str, tuple[int, int]]]] = {
    "hunter": {"x_px": 4, "y_px": 5},  # top-right + bottom-right corner specks
    "paladin": {
        "corners": {
            "tl": (8, 5),
            "tr": (8, 5),
            "bl": (12, 8),
        },
    },
}

# Re-opacify near-black pixels stripped by edge flood-fill (interior dark fringe holes).
ART_RESTORE_DARK_FRINGE: dict[str, dict[str, float]] = {
    "druid": {
        "lum_max": 45.0,
        "chroma_max": 25.0,
        "dilate_px": 7.0,
        "x_min_frac": 0.76,  # right-edge collar shadow only
        "y_min_frac": 0.36,
        "y_max_frac": 0.70,
        "exempt_inner_clip": 1.0,  # keep collar opaque at inner ring lip
    },
}

# Split baked-in colored aura from subject art (separate export; same composite strata for now).
ART_AURA_SPLIT: dict[str, dict[str, float]] = {
    "warrior": {
        "b_min": 70.0,
        "b_minus_r_min": 8.0,
        "g_min": 30.0,
        "g_max": 140.0,
        "lum_max_exclude": 35.0,
        "chroma_max_exclude": 25.0,
        "dilate_px": 2.0,
        "fringe_expand_px": 8.0,
        "fringe_soft_pass_px": 3.0,
        "outer_rim_px": 12.0,  # last ~10–12 px at aura silhouette edge
    },
}

# Blurred black feather along TR sector borders (front layer only).
TR_SECTOR_BLACK_EDGE: dict[str, dict[str, float]] = {
    "mage": {
        "width_px": 8.0,
        "tip_width_px": 14.0,
        "width_taper_power": 2.0,
        "power": 2.0,
        "tip_angle_deg": -36.0,
    },
}

# Tapered edge feather on base art: TR corner along top + right until inner ring lip.
TR_ART_RING_EDGE: dict[str, dict[str, float]] = {
    "mage": {
        "corner_width_px": 6.0,
        "width_taper_power": 1.0,
        "feather_power": 2.25,
        "alpha_feather": 1.0,
        "ring_end_inset_px": 18.0,  # stop feather 18px before inner ring on top + right
    },
}

CORNER_FADE: dict[str, tuple[str, ...]] = {}
CORNER_FADE_CFG: dict[str, dict[str, float]] = {}

# Inward alpha fade on all sides of subject art (pixels from edge).
ART_EDGE_FADE: dict[str, dict[str, float]] = {}
OVERHANG_SECTORS: dict[str, list[tuple[float, float]]] = {
    "paladin": [(-180, -45), (-90, 0), (90, 180)],  # TL + top-middle, TR, BL overhang
    "rogue": [(-90, -5)],              # top-right overhang only
    "mage": [(-64, 4)],              # crystal tip (shifted north)
    "hunter": [(-90, 52)],             # bow tip/nock overhang (TR, includes top edge)
    "druid": [(105, 175)],              # claws overlapping frame bottom-left
    "priest": [(-90, 50)],             # top-right + far right overhang
    "warlock": [(-180, -90)],           # top-left fingers overhang; TR behind ring
    "shaman": [(-180, -90), (-90, -75), (-75, 75)],  # TL + top-middle (12–12:30) + right
}

# How far inside the inner ring overhang art may start (px)
OVERHANG_INNER_PAD: dict[str, float] = {
    "druid": 8.0,
    "paladin": 6.0,
    "mage": 5.0,
}

# Entire art in overhang sectors goes above ring (no inner/outer radius clip on front).
FULL_QUADRANT_OVERHANG: set[str] = {
    "hunter", "mage", "paladin", "priest", "shaman",
}

# On the left side, front overhang only above (cy - tuck); lower left stays behind ring.
LEFT_FRONT_TUCK: dict[str, float] = {}

# Top-right from vertical center axis eastward, above midline (xx >= cx, yy < cy).
TOP_R_OVERHANG: set[str] = {"mage"}

# Bottom-right corner of the top-right quadrant stays behind the ring.
TR_QUAD_LOWER_TUCK: dict[str, tuple[float, float]] = {}

# Front overhang on the right side clipped to outer disc (within FULL_QUADRANT_OVERHANG).
FRONT_OUTER_CLIP_RIGHT: set[str] = set()

# Front overhang not clipped at outer disc (crystal may extend past border).
FRONT_NO_OUTER_CLIP: set[str] = {"mage", "rogue", "warlock"}

# Inside the inner ring, promote art to mid layer (above haze, behind silver ring).
MID_INNER_PROMOTE: dict[str, list[dict[str, float]]] = {
    "warrior": [
        {
            "x_max": 112.0,
            "y_min": 100.0,
            "y_max": 205.0,
            # Left handle + crossguard — spatial mask only (shift-safe, all colors).
        },
        {
            "x_min": 112.0,
            "y_min": 150.0,
            "y_max": 200.0,
            "clock_start": 130.0,
            "clock_end": 220.0,
            "r_min": 170.0,          # bottom-right crossguard tip above haze
            "g_min": 130.0,
            "b_max": 140.0,
            "r_minus_b_min": 50.0,
        },
        {
            "x_min": 112.0,
            "y_min": 188.0,
            "y_max": 200.0,
            "clock_start": 145.0,
            "clock_end": 175.0,
            "r_min": 50.0,           # shadow pixels on crossguard bottom edge
            "g_min": 35.0,
            "b_max": 110.0,
            "r_minus_b_min": 5.0,
        },
    ],
}

# Mid-layer art in these zones skips outer/arc alpha clip (interior handle only).
MID_NO_OUTER_CLIP: dict[str, list[dict[str, float]]] = {
    "warrior": [
        {
            "x_max": 112.0,
            "y_min": 100.0,
            "y_max": 175.0,
        },
    ],
}

# Hard outer-ring clip on mid-layer art within these arcs (no lip past silver ring).
MID_ARC_OUTER_CLIP: dict[str, list[dict[str, float]]] = {
    "warrior": [
        {
            "x_max": 112.0,
            "y_min": 175.0,
            "y_max": 205.0,
            "clock_start": 195.0,
            "clock_end": 250.0,
            "lip_inset_px": 0.0,
            "feather_px": 1.0,
        },
        {
            "x_min": 112.0,
            "y_min": 150.0,
            "y_max": 200.0,
            "clock_start": 130.0,
            "clock_end": 220.0,
            "lip_inset_px": 0.0,
            "feather_px": 1.0,
        },
    ],
}

# Inside the inner ring, promote art in these zones to the front layer (above haze + ring).
FRONT_INNER_PROMOTE: dict[str, list[dict[str, float]]] = {
    "warrior": [],
    "rogue": [
        {
            "x_min": 100.0,
            "y_max": 80.0,
            "clock_start": 325.0,  # TR handle above haze + ring (interior + overhang)
            "clock_end": 55.0,
        },
    ],
}

# Subtract from FRONT_INNER_PROMOTE so art tucks behind the ring instead.
FRONT_INNER_DEMOTE: dict[str, list[dict[str, float]]] = {}

# Warlock: clip art to the silver ring along the left arc; TL overhang only above tuck_end.
OVERHANG_CLOCK_TUCK: dict[str, dict[str, float]] = {
    "warlock": {
        "tuck_start_clock_deg": 270.0,  # 9 o'clock — begin ring clip
        "tuck_end_clock_deg": 288.0,    # user ~280°; 288° clears 9:30 sliver
        "edge_feather_deg": 8.0,
        "lip_outset_px": 2.0,           # extend base lip outward to cover haze
    },
}

# Blue haze composited above base art for these classes (still under ring + front).
HAZE_OVER_BASE: set[str] = {"mage", "rogue", "warrior"}

# Haze composited last — above ring, shadows, and all art (incl. front overhang).
HAZE_OVER_ALL: set[str] = {"mage"}

# Fade haze behind art in a corner zone (e.g. mage crystal overhang at TR).
HAZE_TUCK_BEHIND_ART: dict[str, dict[str, float]] = {
    "mage": {
        "strength": 1.0,
        "corner_start": 0.15,
        "corner_span": 0.70,
        "power": 1.2,
        "tuck_inner_only": 1.0,  # keep ring-band haze; angular arc controls TR lip fade
    },
}

# Dark pixelated inner fill + subject drop shadow (all classes; hunter values as default).
DEFAULT_SECONDARY_DARK_CORE: dict[str, float] = {
    "rgb": (0.0, 13.0, 99.0),
    "spatial_from_art": True,
    "art_alpha_threshold": 0.10,
    "void_tone_power": 0.50,
    "pixel_size": 1.0,
    "pixel_levels": 32,
    "radius_ratio": 0.82,
    "cx_offset_ratio": 0.08,
    "cy_offset_ratio": 0.03,
    "edge_softness": 12.0,
}
DEFAULT_ART_DROP_SHADOW: dict[str, float] = {
    "blur": 7.0,
    "offset_x": 5.0,
    "offset_y": 6.0,
    "alpha": 0.78,
    "rgb": (0.0, 0.0, 0.0),
}
# Optional per-class overrides merged onto defaults above.
SECONDARY_DARK_CORE: dict[str, dict[str, float]] = {
    "warrior": {
        "art_alpha_threshold": 0.35,  # feathery edges count as void for blue core fill
        "void_tone_power": 0.38,      # slightly brighter core in enclosed hollows
    },
}
ART_DROP_SHADOW: dict[str, dict[str, float]] = {
    "warrior": {
        "void_shadow_fade_span": 6.0,  # fade shadow in art hollows (keep drop shadow outside)
        "void_solid_threshold": 0.35,
    },
}

# Drop shadow for front overhang only (drawn above ring, under front art).
FRONT_DROP_SHADOW: dict[str, dict[str, float]] = {
    "druid": {
        "blur": 5.0,
        "offset_x": 8.0,
        "offset_y": 2.0,
        "alpha": 0.95,
        "rgb": (0.0, 0.0, 0.0),
        "right_edge_boost": 1.35,
    },
}


def make_metallic_ring(
    size: int, outer_r: float, inner_r: float, light_angle_deg: float = -135.0
) -> Image.Image:
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = cy = (size - 1) / 2.0
    dx, dy = xx - cx, yy - cy
    rr = np.sqrt(dx * dx + dy * dy)
    shade = np.cos(np.arctan2(dy, dx) - math.radians(light_angle_deg))
    t = np.clip((outer_r - rr) / max(outer_r - inner_r, 1e-6), 0.0, 1.0)

    # Stronger bevel: bright top-left, deep shadow bottom-right (old icon look).
    base = 98 + shade * 138
    ridge = np.exp(-((t - 0.30) ** 2) / (2 * 0.055**2))
    groove = np.exp(-((t - 0.72) ** 2) / (2 * 0.055**2))
    tone = np.clip(base + ridge * 72 - groove * 42, 0, 255)
    tone = tone - np.exp(-(t**2) / (2 * 0.03**2)) * 118
    tone = tone - np.exp(-((1 - t) ** 2) / (2 * 0.035**2)) * 92

    in_ring = (rr <= outer_r) & (rr >= inner_r)
    ang_deg = np.degrees(np.arctan2(dy, dx))
    ring_shadow = ring_clock_shadow_strength(ang_deg, RING_SHADOW)
    ring_shadow = np.where(in_ring, ring_shadow, 0.0)

    dark_lobe = np.clip(-shade, 0.0, 1.0) ** 1.15
    # Directional lobe only on the lit top arc; angular shadow handles 1→9 o'clock.
    tone = tone - dark_lobe * 62.0 * (1.0 - 0.9 * ring_shadow)

    max_drop = RING_SHADOW.get("max_tone_drop", 72.0)
    min_tone = RING_SHADOW.get("min_tone", 36.0)
    tone = np.maximum(tone - ring_shadow * max_drop, min_tone)

    highlight = in_ring & (xx <= cx) & (yy <= cy)
    tone = np.where(highlight, np.clip(tone * 1.10 + 16.0, 0, 255), tone)

    lip_band = in_ring & (
        ((rr >= outer_r - 2.4) & (rr <= outer_r))
        | ((rr >= inner_r) & (rr <= inner_r + 2.4))
    )
    lip_extra = RING_SHADOW.get("lip_extra_mult", 0.42)
    tone = np.where(
        lip_band,
        np.maximum(tone * (1.0 - ring_shadow * lip_extra), min_tone * 0.55),
        tone,
    )

    tone = np.clip(tone, 0, 255)
    r = np.clip(tone + shade * 12 + 6, 0, 255)
    g = np.clip(tone + shade * 6 + 4, 0, 255)
    b = np.clip(tone - shade * 4 + 10, 0, 255)
    aa = 0.85
    alpha = (
        np.clip((outer_r + aa - rr) / (2 * aa), 0, 1)
        * np.clip((rr - (inner_r - aa)) / (2 * aa), 0, 1)
        * 255
    )
    stroke = 1.1
    lip = ((rr <= outer_r) & (rr >= outer_r - stroke)) | (
        (rr >= inner_r) & (rr <= inner_r + stroke)
    )
    r = np.where(lip, r * 0.10, r)
    g = np.where(lip, g * 0.10, g)
    b = np.where(lip, b * 0.10, b)
    return Image.fromarray(np.dstack([r, g, b, alpha]).astype(np.uint8), "RGBA")


def radial_alpha_mask(size: int, radius: float, softness: float = 0.85) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = cy = (size - 1) / 2.0
    rr = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    return np.clip((radius + softness - rr) / (2 * softness), 0.0, 1.0)


def clip_rgba_to_circle(layer: Image.Image, radius: float, softness: float = 0.85) -> Image.Image:
    """Crop layer alpha so nothing extends past the circular badge edge."""
    arr = np.array(layer, dtype=np.float32)
    clip = radial_alpha_mask(arr.shape[0], radius, softness)[..., np.newaxis]
    arr *= clip
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")


def circular_mask(size: int, radius: float, softness: float = 0.85) -> Image.Image:
    return Image.fromarray((radial_alpha_mask(size, radius, softness) * 255).astype(np.uint8), "L")


def make_inner_disc(size: int, inner_r: float) -> Image.Image:
    disc = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    fill = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    disc.paste(fill, mask=circular_mask(size, inner_r + 0.5))
    return disc


def atan2_to_clock_deg(ang_deg: np.ndarray) -> np.ndarray:
    """Convert atan2 degrees (0=east, CCW+) to clock degrees CW from 12 o'clock."""
    return (ang_deg + 90.0) % 360.0


def haze_clock_arc_multiplier(
    ang_deg: np.ndarray,
    cfg: dict[str, float],
) -> np.ndarray:
    """
    0–1 mask for haze on a clockwise arc between clock start/end (CW from north).
    Mage: 3 o'clock around through north/west/south to ~1 o'clock, feathered at TR.
    """
    start = cfg.get("start_clock_deg", 90.0) % 360.0
    end = cfg.get("end_clock_deg", 0.0) % 360.0
    feather = max(cfg.get("edge_feather_deg", 0.0), 0.0)
    clock = atan2_to_clock_deg(ang_deg)

    if abs(start - end) < 1e-6:
        return np.ones_like(clock, dtype=np.float32)

    if start > end:
        in_gap = (clock > end) & (clock < start)
        if feather <= 0.0:
            return (~in_gap).astype(np.float32)
        dist_to_end = np.where(in_gap, clock - end, feather)
        dist_to_start = np.where(in_gap, start - clock, feather)
        nearest_gap_edge = np.minimum(dist_to_end, dist_to_start)
        gap_mult = np.clip((feather - nearest_gap_edge) / feather, 0.0, 1.0)
        return np.where(in_gap, gap_mult, 1.0).astype(np.float32)

    if end > start:
        # Gap wraps through north/TR (e.g. mage 11 o'clock → 3 o'clock).
        in_gap = (clock >= end) | (clock <= start)
        if feather <= 0.0:
            return (~in_gap).astype(np.float32)
        dist_to_end = np.where(
            clock >= end,
            clock - end,
            np.where(clock <= start, (360.0 - end) + clock, 360.0),
        )
        dist_to_start = np.where(
            clock <= start,
            start - clock,
            np.where(clock >= end, (360.0 - clock) + start, 360.0),
        )
        nearest_gap_edge = np.minimum(dist_to_start, dist_to_end)
        gap_mult = np.clip((feather - nearest_gap_edge) / feather, 0.0, 1.0)
        return np.where(in_gap, gap_mult, 1.0).astype(np.float32)

    on_arc = (clock >= start) & (clock <= end)
    if feather <= 0.0:
        return on_arc.astype(np.float32)
    edge_dist = np.where(on_arc, np.minimum(clock - start, end - clock), feather)
    return np.clip(edge_dist / feather, 0.0, 1.0)


def ring_clock_shadow_strength(
    ang_deg: np.ndarray,
    cfg: dict[str, float] | None = None,
) -> np.ndarray:
    """
    0–1 shadow strength on a clockwise clock arc (default 1 o'clock → 9 o'clock).
    Peaks at peak_clock_deg; feathered to zero at arc endpoints.
    """
    cfg = cfg or RING_SHADOW
    start = cfg.get("start_clock_deg", 30.0) % 360.0
    end = cfg.get("end_clock_deg", 270.0) % 360.0
    peak = cfg.get("peak_clock_deg", 180.0) % 360.0
    feather = max(cfg.get("edge_feather_deg", 16.0), 0.0)
    power = max(cfg.get("depth_power", 1.12), 0.01)
    clock = atan2_to_clock_deg(ang_deg)

    if start <= end:
        on_arc = (clock >= start) & (clock <= end)
        dist_to_start = clock - start
        dist_to_end = end - clock
        half_span = max((end - start) / 2.0, 1.0)
    else:
        on_arc = (clock >= start) | (clock <= end)
        dist_to_start = np.where(clock >= start, clock - start, clock + (360.0 - start))
        dist_to_end = np.where(clock <= end, end - clock, (360.0 - clock) + end)
        half_span = max((360.0 - start + end) / 2.0, 1.0)

    if feather > 0.0:
        edge1 = np.clip(dist_to_start / feather, 0.0, 1.0)
        edge2 = np.clip(dist_to_end / feather, 0.0, 1.0)
        arc_mask = on_arc.astype(np.float32) * edge1 * edge2
    else:
        arc_mask = on_arc.astype(np.float32)

    depth = np.clip(1.0 - np.abs(clock - peak) / half_span, 0.0, 1.0) ** power
    return arc_mask * depth


def make_inner_haze_ring(
    size: int,
    inner_r: float,
    arc_cfg: dict[str, float] | None = None,
) -> Image.Image:
    """Annular blue glow on the black disc; transparent toward the center."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = cy = (size - 1) / 2.0
    rr = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    ang = np.degrees(np.arctan2(yy - cy, xx - cx))

    inward = np.clip(inner_r - rr, 0.0, inner_r)
    band_width = max(inner_r * INNER_HAZE_WIDTH_RATIO, 1.0)
    t = np.clip(1.0 - inward / band_width, 0.0, 1.0) ** INNER_HAZE_POWER
    inside = (rr <= inner_r).astype(np.float32)
    intensity = np.clip(t * inside * INNER_HAZE_INTENSITY, 0.0, 1.35)

    if arc_cfg is not None:
        arc_mult = haze_clock_arc_multiplier(ang, arc_cfg)
        intensity *= arc_mult

    hr, hg, hb = INNER_HAZE_RGB
    rgb = np.stack(
        [
            intensity * hr,
            intensity * (hg + t * INNER_HAZE_GREEN_LIFT),
            intensity * (hb + t * INNER_HAZE_BLUE_LIFT),
        ],
        axis=-1,
    )
    alpha = np.clip(intensity * 255.0, 0.0, 255.0)
    return Image.fromarray(
        np.dstack([np.clip(rgb, 0, 255), alpha[..., np.newaxis]]).astype(np.uint8), "RGBA"
    )


def art_paste_offset(
    cutout: Image.Image,
    size: int,
    inner_r: float,
    cls: str,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> tuple[int, int]:
    paste_x, paste_y = ART_PASTE_OFFSET.get(cls, (0.0, 0.0))
    offset_x += paste_x
    offset_y += paste_y
    if cls in ART_TR_TOUCH_INNER_RING:
        cx = cy = (size - 1) / 2.0
        arr = np.array(cutout)
        solid = arr[:, :, 3] > 8.0
        ys, xs = np.where(solid)
        if len(xs) > 0:
            ox = int(round(cx + inner_r - int(xs.max()) + offset_x))
            oy = int(round(cy - inner_r - int(ys.min()) + offset_y))
            return ox, oy
    cw, ch = cutout.size
    return (size - cw) // 2 + int(round(offset_x)), (size - ch) // 2 + int(round(offset_y))


def art_alpha_on_canvas(
    cutout: Image.Image,
    size: int,
    inner_r: float = 0.0,
    cls: str = "",
) -> np.ndarray:
    cw, ch = cutout.size
    ox, oy = art_paste_offset(cutout, size, inner_r, cls)
    alpha = np.zeros((size, size), dtype=np.float32)
    src_x0 = max(0, -ox)
    src_y0 = max(0, -oy)
    src_x1 = min(cw, size - ox)
    src_y1 = min(ch, size - oy)
    dst_x0 = max(0, ox)
    dst_y0 = max(0, oy)
    if src_x1 <= src_x0 or src_y1 <= src_y0:
        return alpha
    alpha[dst_y0 : dst_y0 + (src_y1 - src_y0), dst_x0 : dst_x0 + (src_x1 - src_x0)] = (
        np.array(cutout.split()[3], dtype=np.float32)[src_y0:src_y1, src_x0:src_x1]
        / 255.0
    )
    return alpha


def pool_blocks(arr: np.ndarray, block: int, mode: str = "min") -> np.ndarray:
    h, w = arr.shape
    out = np.zeros_like(arr)
    for y in range(0, h, block):
        y2 = min(y + block, h)
        for x in range(0, w, block):
            x2 = min(x + block, w)
            patch = arr[y:y2, x:x2]
            value = patch.min() if mode == "min" else patch.mean()
            out[y:y2, x:x2] = value
    return out


def make_secondary_dark_core(
    size: int,
    inner_r: float,
    cfg: dict[str, float],
    cutout: Image.Image | None = None,
    cls: str = "",
) -> Image.Image:
    """Dark block-noised fill; optionally shaped by void space around subject art."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = (size - 1) / 2.0 + inner_r * cfg.get("cx_offset_ratio", 0.0)
    cy = (size - 1) / 2.0 + inner_r * cfg.get("cy_offset_ratio", 0.0)
    rr = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    radius = inner_r * cfg.get("radius_ratio", 0.75)
    softness = cfg.get("edge_softness", 8.0)
    mask = np.clip((radius + softness - rr) / (2.0 * softness), 0.0, 1.0)

    base_r, base_g, base_b = cfg.get("rgb", (1.0, 6.0, 18.0))
    pixel = max(int(cfg.get("pixel_size", 3.0)), 1)
    levels = max(int(cfg.get("pixel_levels", 4)), 2)

    if cfg.get("spatial_from_art") and cutout is not None:
        art_a = art_alpha_on_canvas(cutout, size, inner_r, cls)
        solid = art_a >= cfg.get("art_alpha_threshold", 0.10)
        void = ~solid
        dist = distance_transform_edt(void).astype(np.float32)
        void_inside = void & (mask > 0.05)
        tone = np.zeros((size, size), dtype=np.float32)
        if np.any(void_inside):
            ref = np.percentile(dist[void_inside], 92)
            ref = max(ref, 1.0)
            tone[void_inside] = np.clip(dist[void_inside] / ref, 0.0, 1.0)
            tone[void_inside] **= cfg.get("void_tone_power", 0.55)
        tone *= mask
        tone = pool_blocks(tone, pixel, mode="min")
        quant = np.floor(tone * levels) / float(levels - 1)
        r = base_r * quant
        g = base_g * quant
        b = base_b * quant
    else:
        bx = (xx // pixel).astype(np.int32)
        by = (yy // pixel).astype(np.int32)
        noise = np.sin(bx * 12.9898 + by * 78.233) * 43758.5453
        noise = noise - np.floor(noise)
        variation = cfg.get("rgb_variation", 12.0)
        black_threshold = cfg.get("black_threshold", 0.0)
        if black_threshold > 0.0:
            accent = np.clip(
                (noise - black_threshold) / max(1.0 - black_threshold, 1e-6),
                0.0,
                1.0,
            )
            r = base_r * accent
            g = base_g * accent + accent * variation * 0.15
            b = base_b * accent + accent * variation * 0.25
        else:
            r = np.clip(base_r + noise * variation * 0.35, 0, 255)
            g = np.clip(base_g + noise * variation * 0.55, 0, 255)
            b = np.clip(base_b + noise * variation, 0, 255)

    r = np.clip(r, 0, 255)
    g = np.clip(g, 0, 255)
    b = np.clip(b, 0, 255)
    disc_clip = radial_alpha_mask(size, inner_r, 0.85)
    alpha = mask * disc_clip * 255.0
    return Image.fromarray(
        np.dstack([r, g, b, alpha[..., np.newaxis]]).astype(np.uint8), "RGBA"
    )


def make_art_drop_shadow(
    cutout: Image.Image,
    size: int,
    cfg: dict[str, float],
    clip_r: float | None = None,
    inner_r: float = 0.0,
    cls: str = "",
) -> Image.Image:
    """Blurred dark copy of subject alpha, offset behind the art."""
    cw, ch = cutout.size
    ox, oy = art_paste_offset(
        cutout,
        size,
        inner_r,
        cls,
        cfg.get("offset_x", 3.0),
        cfg.get("offset_y", 4.0),
    )
    alpha = np.array(cutout.split()[3], dtype=np.float32)
    strength = cfg.get("alpha", 0.65)
    sr, sg, sb = cfg.get("rgb", (0.0, 0.0, 0.0))
    shadow = np.zeros((ch, cw, 4), dtype=np.float32)
    shadow[:, :, 0] = sr
    shadow[:, :, 1] = sg
    shadow[:, :, 2] = sb
    shadow[:, :, 3] = alpha * strength
    shadow_img = Image.fromarray(shadow.astype(np.uint8), "RGBA")
    blur = max(cfg.get("blur", 5.0), 0.0)
    if blur > 0:
        shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(radius=blur))
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(shadow_img, (ox, oy), shadow_img)
    fade_span = cfg.get("void_shadow_fade_span", 0.0)
    if fade_span > 0.0 and inner_r > 0.0:
        arr = np.array(canvas, dtype=np.float32)
        art_a = art_alpha_on_canvas(cutout, size, inner_r, cls)
        solid_thresh = cfg.get("void_solid_threshold", 0.10)
        solid = art_a >= solid_thresh
        dist_void = distance_transform_edt(~solid).astype(np.float32)
        fade = np.clip((dist_void - 0.5) / fade_span, 0.0, 1.0)
        arr[:, :, 3] *= np.where(solid, 1.0, fade)
        canvas = Image.fromarray(arr.astype(np.uint8), "RGBA")
    if clip_r is not None:
        canvas = clip_rgba_to_circle(canvas, clip_r)
    return canvas


def make_layer_drop_shadow(
    layer: Image.Image,
    cfg: dict[str, float],
    clip_r: float | None = None,
) -> Image.Image:
    """Blurred shadow from a full-canvas layer (e.g. front overhang), offset behind it."""
    arr = np.array(layer, dtype=np.float32)
    h, w = arr.shape[:2]
    alpha = arr[:, :, 3].copy()
    ox = int(round(cfg.get("offset_x", 4.0)))
    oy = int(round(cfg.get("offset_y", 2.0)))
    strength = cfg.get("alpha", 0.85)
    sr, sg, sb = cfg.get("rgb", (0.0, 0.0, 0.0))

    edge_boost = cfg.get("right_edge_boost", 0.0)
    if edge_boost > 0.0 and np.any(alpha > 8.0):
        yy, xx = np.mgrid[0:h, 0:w]
        art = alpha > 8.0
        east = xx >= np.percentile(xx[art], 58)
        west_weight = np.clip((np.percentile(xx[art], 72) - xx) / 12.0, 0.0, 1.0)
        alpha = alpha * (1.0 + (edge_boost - 1.0) * east.astype(np.float32) * west_weight)

    shadow = np.zeros((h, w, 4), dtype=np.float32)
    shadow[:, :, 0] = sr
    shadow[:, :, 1] = sg
    shadow[:, :, 2] = sb
    shadow[:, :, 3] = np.clip(alpha * strength, 0.0, 255.0)
    shadow_img = Image.fromarray(shadow.astype(np.uint8), "RGBA")
    blur = max(cfg.get("blur", 4.0), 0.0)
    if blur > 0:
        shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(radius=blur))
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(shadow_img, (ox, oy), shadow_img)
    if clip_r is not None:
        canvas = clip_rgba_to_circle(canvas, clip_r)
    return canvas


def make_border_badge(size: int) -> tuple[Image.Image, float, float]:
    half = (size - 1) / 2.0
    outer_r = half * OUTER_R_RATIO
    inner_r = half * INNER_R_RATIO - RIM_EXTRA_PX
    disc = make_inner_disc(size, inner_r)
    ring = make_metallic_ring(size, outer_r, inner_r)
    return Image.alpha_composite(disc, ring), inner_r, outer_r


def remove_edge_background(
    img: Image.Image,
    lum_threshold: float = 20.0,
    max_chroma: float = 20.0,
    soft_edge: float = 8.0,
) -> Image.Image:
    """
    Remove ONLY edge-connected near-black pixels.
    Preserves colored glows, smoke, orange flames, purple mist, teal haze.
    """
    rgba = np.array(img.convert("RGBA"), dtype=np.float32)
    rgb = rgba[:, :, :3]
    h, w = rgb.shape[:2]
    lum = rgb.mean(axis=2)
    chroma = rgb.std(axis=2)
    candidate = (lum <= lum_threshold) & (chroma <= max_chroma)

    border = np.zeros((h, w), dtype=bool)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True

    bg = np.zeros((h, w), dtype=bool)
    q: deque[tuple[int, int]] = deque()
    for y, x in zip(*np.where(border & candidate)):
        bg[y, x] = True
        q.append((y, x))

    neighbors = (
        (-1, -1), (-1, 0), (-1, 1),
        (0, -1),           (0, 1),
        (1, -1),  (1, 0),  (1, 1),
    )
    while q:
        y, x = q.popleft()
        for dy, dx in neighbors:
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and not bg[ny, nx] and candidate[ny, nx]:
                bg[ny, nx] = True
                q.append((ny, nx))

    alpha = np.where(bg, 0.0, 255.0).astype(np.float32)
    out = rgba.copy()
    out[:, :, 3] = alpha
    out[alpha < 1, :3] = 0
    return Image.fromarray(out.astype(np.uint8), "RGBA")


def dilate_bool_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    """8-connected binary dilation by ``radius`` pixels."""
    if radius <= 0:
        return mask
    out = mask.copy()
    for _ in range(radius):
        padded = np.pad(out, 1, mode="constant", constant_values=False)
        merged = np.zeros_like(out)
        h, w = out.shape
        for dy in range(3):
            for dx in range(3):
                merged |= padded[dy : dy + h, dx : dx + w]
        out = merged
    return out


def fractional_zone_mask(
    height: int,
    width: int,
    cfg: dict[str, float],
) -> np.ndarray:
    """Boolean mask for optional x/y fractional bounds in ``cfg``."""
    yy, xx = np.mgrid[0:height, 0:width]
    zone = np.ones((height, width), dtype=bool)
    if "x_min_frac" in cfg:
        zone &= xx >= int(float(cfg["x_min_frac"]) * width)
    if "x_max_frac" in cfg:
        zone &= xx <= int(float(cfg["x_max_frac"]) * width)
    if "y_min_frac" in cfg:
        zone &= yy >= int(float(cfg["y_min_frac"]) * height)
    if "y_max_frac" in cfg:
        zone &= yy <= int(float(cfg["y_max_frac"]) * height)
    return zone


def restore_dark_fringe(src: Image.Image, cutout: Image.Image, cls: str) -> Image.Image:
    """
    Restore dark opaque source pixels removed by remove_edge_background.

    Limited to an optional fractional zone so exterior background stays removed.
    """
    cfg = ART_RESTORE_DARK_FRINGE.get(cls)
    if cfg is None:
        return cutout

    raw = np.array(src.convert("RGBA"), dtype=np.uint8)
    cut = np.array(cutout.convert("RGBA"), dtype=np.uint8)
    h, w = cut.shape[:2]
    if raw.shape[:2] != (h, w):
        return cutout

    kept = cut[:, :, 3] > 128
    dilate_px = int(cfg.get("dilate_px", 6.0))
    near_art = dilate_bool_mask(kept, dilate_px)

    lum = raw[:, :, :3].mean(axis=2)
    chroma = raw[:, :, :3].std(axis=2)
    restore = (
        near_art
        & (cut[:, :, 3] < 30)
        & (raw[:, :, 3] > 200)
        & (lum <= cfg.get("lum_max", 45.0))
        & (chroma <= cfg.get("chroma_max", 25.0))
    )

    x_min_frac = cfg.get("x_min_frac")
    y_min_frac = cfg.get("y_min_frac")
    y_max_frac = cfg.get("y_max_frac")
    if x_min_frac is not None or y_min_frac is not None or y_max_frac is not None:
        restore &= fractional_zone_mask(h, w, cfg)

    if not restore.any():
        return cutout

    cut[restore] = raw[restore]
    return Image.fromarray(cut, "RGBA")


def art_aura_strict_subject_mask(
    has_art: np.ndarray,
    rgb: np.ndarray,
) -> np.ndarray:
    """Hard subject colors only — used for spatial aura envelope expansion."""
    r = rgb[:, :, 0]
    g = rgb[:, :, 1]
    b = rgb[:, :, 2]
    gold = (
        has_art
        & (r >= 170.0)
        & (g >= 130.0)
        & (b <= 140.0)
        & ((r - b) >= 50.0)
    )
    silver = (
        has_art
        & (r >= 100.0)
        & (g >= 100.0)
        & (b >= 100.0)
        & (np.abs(r - g) < 30.0)
        & (np.abs(r - b) < 40.0)
        & ~gold
    )
    brown = (
        has_art
        & (r >= 55.0)
        & (g >= 35.0)
        & (b <= 85.0)
        & (r > b + 15.0)
        & (g > b)
    )
    red = has_art & (r >= 140.0) & (g < 80.0) & (b < 80.0) & (r > g + 40.0)
    return gold | silver | brown | red


def art_aura_subject_exclusion_mask(
    has_art: np.ndarray,
    rgb: np.ndarray,
    cfg: dict[str, float],
) -> np.ndarray:
    """Pixels that must stay on the subject layer during aura split."""
    r = rgb[:, :, 0]
    g = rgb[:, :, 1]
    b = rgb[:, :, 2]
    gold = (
        has_art
        & (r >= 170.0)
        & (g >= 130.0)
        & (b <= 140.0)
        & ((r - b) >= 50.0)
    )
    silver = (
        has_art
        & (r >= 100.0)
        & (g >= 100.0)
        & (b >= 100.0)
        & (np.abs(r - g) < 30.0)
        & (np.abs(r - b) < 40.0)
        & ~gold
    )
    brown = (
        has_art
        & (r >= 55.0)
        & (g >= 35.0)
        & (b <= 85.0)
        & (r > b + 15.0)
        & (g > b)
    )
    red = has_art & (r >= 140.0) & (g < 80.0) & (b < 80.0) & (r > g + 40.0)
    lum = rgb.mean(axis=2)
    chroma = rgb.std(axis=2)
    dark = (
        has_art
        & (lum <= cfg.get("lum_max_exclude", 35.0))
        & (chroma <= cfg.get("chroma_max_exclude", 25.0))
    )
    return gold | silver | brown | red | dark


def art_aura_glow_mask(
    rgb: np.ndarray,
    alpha: np.ndarray,
    cfg: dict[str, float],
) -> np.ndarray:
    """Boolean mask for baked-in teal/cyan aura pixels in subject cutouts."""
    has_art = alpha > 8.0
    r = rgb[:, :, 0]
    g = rgb[:, :, 1]
    b = rgb[:, :, 2]
    subject = art_aura_subject_exclusion_mask(has_art, rgb, cfg)

    glow = has_art.copy()
    if "b_min" in cfg:
        glow &= b >= cfg["b_min"]
    if "b_minus_r_min" in cfg:
        glow &= b > r + cfg["b_minus_r_min"]
    if "g_min" in cfg:
        glow &= g >= cfg["g_min"]
    if "g_max" in cfg:
        glow &= g <= cfg["g_max"]
    glow &= ~subject

    dilate_px = int(cfg.get("dilate_px", 0.0))
    if dilate_px > 0:
        expanded = glow.copy()
        for _ in range(dilate_px):
            expanded = binary_dilation(expanded)
        glow = expanded & has_art & ~subject

    fringe_expand_px = int(cfg.get("fringe_expand_px", 0.0))
    if fringe_expand_px > 0:
        dist = distance_transform_edt(~glow)
        inner_fringe = (dist > 0.0) & (dist <= 2.0) & has_art & ~subject
        outer_fringe = (dist > 2.0) & (dist <= float(fringe_expand_px)) & has_art & ~subject
        teal_soft = (
            (b >= r - 15.0)
            & (b >= g - 20.0)
            & (b >= 25.0)
            & (g >= 18.0)
            & ((b - r) >= -10.0)
        )
        glow |= inner_fringe | (outer_fringe & teal_soft)

    soft_pass_px = int(cfg.get("fringe_soft_pass_px", 0.0))
    if soft_pass_px > 0:
        dist = distance_transform_edt(~glow)
        near = (dist > 0.0) & (dist <= float(soft_pass_px)) & has_art & ~subject
        softer = (
            (b >= r - 8.0)
            & (b >= 22.0)
            & (g >= 14.0)
            & ((b - r) >= -6.0)
        )
        glow |= near & softer

    outer_rim_px = int(cfg.get("outer_rim_px", 0.0))
    if outer_rim_px > 0:
        strict = art_aura_strict_subject_mask(has_art, rgb)
        dist_out = distance_transform_edt(~glow)
        envelope = (
            (dist_out > 0.0)
            & (dist_out <= float(outer_rim_px))
            & has_art
            & ~strict
        )
        glow |= envelope

    return glow & has_art


def split_art_aura(cutout: Image.Image, cls: str) -> tuple[Image.Image, Image.Image | None]:
    """Split subject art and baked-in aura into separate RGBA cutouts."""
    cfg = ART_AURA_SPLIT.get(cls)
    if cfg is None:
        return cutout, None
    arr = np.array(cutout.convert("RGBA"), dtype=np.uint8)
    glow = art_aura_glow_mask(arr[:, :, :3].astype(np.float32), arr[:, :, 3].astype(np.float32), cfg)
    if not glow.any():
        return cutout, None
    subject = arr.copy()
    aura = np.zeros_like(arr)
    aura[glow] = arr[glow]
    subject[glow, :3] = 0
    subject[glow, 3] = 0
    return Image.fromarray(subject, "RGBA"), Image.fromarray(aura, "RGBA")


def strip_cutout_corners(
    art: Image.Image,
    cfg: dict[str, int | dict[str, tuple[int, int]]],
) -> Image.Image:
    """Clear stray opaque pixels from cutout corner zones."""
    corners_cfg = cfg.get("corners")
    if corners_cfg is None:
        x_px = int(cfg["x_px"])
        y_px = int(cfg["y_px"])
        corners_cfg = {"tr": (x_px, y_px), "br": (x_px, y_px)}

    arr = np.array(art, dtype=np.uint8)
    h, w = arr.shape[:2]
    for corner, sizes in corners_cfg.items():
        x_px, y_px = sizes
        if x_px <= 0 or y_px <= 0:
            continue
        if corner == "tl":
            arr[:y_px, :x_px, 3] = 0
            arr[:y_px, :x_px, :3] = 0
        elif corner == "tr":
            x0 = max(0, w - x_px)
            arr[:y_px, x0:, 3] = 0
            arr[:y_px, x0:, :3] = 0
        elif corner == "bl":
            y0 = max(0, h - y_px)
            arr[y0:, :x_px, 3] = 0
            arr[y0:, :x_px, :3] = 0
        elif corner == "br":
            x0 = max(0, w - x_px)
            y0 = max(0, h - y_px)
            arr[y0:, x0:, 3] = 0
            arr[y0:, x0:, :3] = 0
    return Image.fromarray(arr, "RGBA")


def angle_in_sectors(angle_deg: np.ndarray, sectors: list[tuple[float, float]]) -> np.ndarray:
    result = np.zeros(angle_deg.shape, dtype=bool)
    for start, end in sectors:
        if start <= end:
            result |= (angle_deg >= start) & (angle_deg < end)
        else:
            result |= (angle_deg >= start) | (angle_deg < end)
    return result


def inner_promote_mask(
    size: int,
    yy: np.ndarray,
    xx: np.ndarray,
    ang: np.ndarray,
    alpha: np.ndarray,
    zones: list[dict[str, float]] | None,
    rgb: np.ndarray | None = None,
) -> np.ndarray:
    """Boolean mask for spatial/color promote zones."""
    if not zones:
        return np.zeros((size, size), dtype=bool)
    has_art = alpha > 8.0
    result = np.zeros((size, size), dtype=bool)
    for zone in zones:
        mask = has_art.copy()
        if "y_max" in zone:
            mask &= yy <= zone["y_max"]
        if "y_min" in zone:
            mask &= yy >= zone["y_min"]
        if "x_max" in zone:
            mask &= xx <= zone["x_max"]
        if "x_min" in zone:
            mask &= xx >= zone["x_min"]
        if "angle_start" in zone:
            mask &= ang >= zone["angle_start"]
        if "angle_end" in zone:
            mask &= ang < zone["angle_end"]
        if "clock_start" in zone:
            clock = atan2_to_clock_deg(ang)
            cs = zone["clock_start"] % 360.0
            ce = zone.get("clock_end", 360.0) % 360.0
            if cs <= ce:
                mask &= (clock >= cs) & (clock < ce)
            else:
                mask &= (clock >= cs) | (clock < ce)
        if rgb is not None:
            if "r_min" in zone:
                mask &= rgb[:, :, 0] >= zone["r_min"]
            if "g_min" in zone:
                mask &= rgb[:, :, 1] >= zone["g_min"]
            if "b_min" in zone:
                mask &= rgb[:, :, 2] >= zone["b_min"]
            if "r_max" in zone:
                mask &= rgb[:, :, 0] <= zone["r_max"]
            if "g_max" in zone:
                mask &= rgb[:, :, 1] <= zone["g_max"]
            if "b_max" in zone:
                mask &= rgb[:, :, 2] <= zone["b_max"]
            if "r_minus_b_min" in zone:
                mask &= (rgb[:, :, 0] - rgb[:, :, 2]) >= zone["r_minus_b_min"]
        result |= mask
    return result


def front_inner_promote_mask(
    size: int,
    yy: np.ndarray,
    xx: np.ndarray,
    ang: np.ndarray,
    alpha: np.ndarray,
    cls: str,
    rgb: np.ndarray | None = None,
) -> np.ndarray:
    """Art inside the inner ring that should render above haze and ring."""
    return inner_promote_mask(size, yy, xx, ang, alpha, FRONT_INNER_PROMOTE.get(cls), rgb)


def mid_inner_promote_mask(
    size: int,
    yy: np.ndarray,
    xx: np.ndarray,
    ang: np.ndarray,
    alpha: np.ndarray,
    cls: str,
    rgb: np.ndarray | None = None,
) -> np.ndarray:
    """Art above haze but tucked behind the silver ring."""
    return inner_promote_mask(size, yy, xx, ang, alpha, MID_INNER_PROMOTE.get(cls), rgb)


def front_inner_demote_mask(
    size: int,
    yy: np.ndarray,
    xx: np.ndarray,
    ang: np.ndarray,
    alpha: np.ndarray,
    cls: str,
) -> np.ndarray:
    """Pixels that must stay off the front layer even if promoted elsewhere."""
    zones = FRONT_INNER_DEMOTE.get(cls)
    if not zones:
        return np.zeros((size, size), dtype=bool)
    has_art = alpha > 8.0
    result = np.zeros((size, size), dtype=bool)
    for zone in zones:
        mask = has_art.copy()
        if "y_max" in zone:
            mask &= yy <= zone["y_max"]
        if "y_min" in zone:
            mask &= yy >= zone["y_min"]
        if "x_max" in zone:
            mask &= xx <= zone["x_max"]
        if "x_min" in zone:
            mask &= xx >= zone["x_min"]
        if "angle_start" in zone:
            mask &= ang >= zone["angle_start"]
        if "angle_end" in zone:
            mask &= ang < zone["angle_end"]
        if "clock_start" in zone:
            clock = atan2_to_clock_deg(ang)
            cs = zone["clock_start"] % 360.0
            ce = zone.get("clock_end", 360.0) % 360.0
            if cs <= ce:
                mask &= (clock >= cs) & (clock < ce)
            else:
                mask &= (clock >= cs) | (clock < ce)
        result |= mask
    return result


def arc_zone_mask(
    yy: np.ndarray,
    xx: np.ndarray,
    ang: np.ndarray,
    zones: list[dict[str, float]],
) -> np.ndarray:
    """Clock-arc (and optional rectangular) mask for per-zone base clipping."""
    result = np.zeros(yy.shape, dtype=bool)
    clock = atan2_to_clock_deg(ang)
    for zone in zones:
        mask = np.ones(yy.shape, dtype=bool)
        if "x_max" in zone:
            mask &= xx <= zone["x_max"]
        if "x_min" in zone:
            mask &= xx >= zone["x_min"]
        if "y_max" in zone:
            mask &= yy <= zone["y_max"]
        if "y_min" in zone:
            mask &= yy >= zone["y_min"]
        if "clock_start" in zone:
            cs = zone["clock_start"] % 360.0
            ce = zone.get("clock_end", 360.0) % 360.0
            if cs <= ce:
                mask &= (clock >= cs) & (clock < ce)
            else:
                mask &= (clock >= cs) | (clock < ce)
        result |= mask
    return result


def overhang_clock_tuck_multiplier(
    clock_deg: np.ndarray,
    cfg: dict[str, float],
) -> np.ndarray:
    """
    0 = tuck behind ring (standard inner/outer clip); 1 = allow front overhang.
    Clock degrees CW from 12 o'clock.
    """
    start = cfg.get("tuck_start_clock_deg", 270.0) % 360.0
    end = cfg.get("tuck_end_clock_deg", 280.0) % 360.0
    feather = max(cfg.get("edge_feather_deg", 0.0), 0.0)
    mult = np.ones_like(clock_deg, dtype=np.float32)

    if start <= end:
        in_tuck = (clock_deg >= start) & (clock_deg <= end)
        if feather > 0.0:
            ramp_end = min(end + feather, 360.0)
            in_ramp = (clock_deg > end) & (clock_deg <= ramp_end)
            mult = np.where(in_ramp, (clock_deg - end) / feather, mult)
        mult = np.where(in_tuck, 0.0, mult)
    else:
        in_tuck = (clock_deg >= start) | (clock_deg <= end)
        mult = np.where(in_tuck, 0.0, mult)

    return np.clip(mult, 0.0, 1.0)


def find_front_overhang_tip(
    front_a: np.ndarray,
    xx: np.ndarray,
    yy: np.ndarray,
    cx: float,
    cy: float,
    front_sector: np.ndarray,
    outer_r: float,
    tip_deg: float,
) -> tuple[float, float]:
    """Outermost top-right pixel of the front overhang layer."""
    mask = front_sector & (front_a > 8.0)
    if not np.any(mask):
        tip_a = math.radians(tip_deg)
        return cx + outer_r * math.cos(tip_a), cy + outer_r * math.sin(tip_a)

    tr_score = (xx - cx) + (cy - yy)
    tr_score = np.where(mask, tr_score, -np.inf)
    flat = int(np.argmax(tr_score))
    tip_y, tip_x = divmod(flat, tr_score.shape[1])
    return float(tip_x), float(tip_y)


def tr_tip_edge_strips(
    size: int,
    cx: float,
    cy: float,
    outer_r: float,
    front_sector: np.ndarray,
    front_a: np.ndarray,
    cfg: dict[str, float],
) -> np.ndarray:
    """Strip envelope from crystal tip along top + right outside edges."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    width = max(cfg.get("width_px", 5.5), 1.0)
    tip_width = max(cfg.get("tip_width_px", width * 1.25), width)
    width_taper = max(cfg.get("width_taper_power", 1.0), 0.1)
    power = cfg.get("power", 2.0)
    tip_deg = cfg.get("tip_angle_deg", -36.0)

    tip_x, tip_y = find_front_overhang_tip(
        front_a, xx, yy, cx, cy, front_sector, outer_r, tip_deg
    )
    tl_jx, tl_jy = cx, 0.0
    br_jx, br_jy = cx + outer_r, cy

    def edge_strip(jx: float, jy: float) -> np.ndarray:
        dx = jx - tip_x
        dy = jy - tip_y
        length = max(math.hypot(dx, dy), 1e-6)
        ux, uy = dx / length, dy / length
        pxp, pyp = -uy, ux
        rx = xx - tip_x
        ry = yy - tip_y
        along = rx * ux + ry * uy
        perp = np.abs(rx * pxp + ry * pyp)
        along_norm = np.clip(along / length, 0.0, 1.0)
        local_w = tip_width + (width - tip_width) * (along_norm**width_taper)
        perp_f = np.clip(1.0 - perp / local_w, 0.0, 1.0) ** power
        along_f = np.clip(1.0 - along_norm, 0.0, 1.0) ** power
        on_strip = (along >= 0.0) & (along <= length) & (perp <= local_w)
        return perp_f * along_f * on_strip.astype(np.float32)

    in_tr = front_sector.astype(np.float32)
    return np.maximum(edge_strip(tl_jx, tl_jy), edge_strip(br_jx, br_jy)) * in_tr


def find_art_tr_corner(
    art_a: np.ndarray,
    xx: np.ndarray,
    yy: np.ndarray,
    cx: float,
    cy: float,
    inner_r: float,
) -> tuple[float, float]:
    """Outermost opaque pixel in the top-right (the art TR anchor point)."""
    solid = art_a > 8.0
    tr = (xx + 0.5 >= cx) & (yy + 0.5 <= cy)
    mask = solid & tr
    if not np.any(mask):
        return cx + inner_r, cy - inner_r

    tr_score = np.where(mask, (xx - cx) + (cy - yy), -np.inf)
    flat = int(np.argmax(tr_score))
    corner_y, corner_x = divmod(flat, tr_score.shape[1])
    return float(corner_x), float(corner_y)


def ring_junction_along_top(
    corner_x: float,
    corner_y: float,
    cx: float,
    cy: float,
    inner_r: float,
) -> tuple[float, float]:
    """Leftward along the art top until the inner silver ring."""
    dy = corner_y - cy
    radic = inner_r * inner_r - dy * dy
    if radic > 0.0:
        x_ring = cx - math.sqrt(radic)
        if x_ring < corner_x - 0.5:
            return x_ring, corner_y
    return cx, cy - inner_r


def ring_junction_along_right(
    corner_x: float,
    corner_y: float,
    cx: float,
    cy: float,
    inner_r: float,
) -> tuple[float, float]:
    """Downward along the art right side until the inner silver ring."""
    dx = corner_x - cx
    radic = inner_r * inner_r - dx * dx
    if radic > 0.0:
        y_ring = cy + math.sqrt(radic)
        if y_ring > corner_y + 0.5:
            return corner_x, y_ring
    return cx + inner_r, cy


def tr_art_ring_edge_strips(
    size: int,
    cx: float,
    cy: float,
    inner_r: float,
    art_a: np.ndarray,
    cfg: dict[str, float],
) -> np.ndarray:
    """
    Feather band on the art top + right silhouette in TR (corner → inner ring).
    Returns 0–1 fade strength (1 = outer art edge, 0 = inner band end).
    Width tapers from corner_width_px to zero at the ring.
    """
    corner_w = max(cfg.get("corner_width_px", 6.0), 0.0)
    width_taper = max(cfg.get("width_taper_power", 1.0), 0.1)
    feather_power = max(cfg.get("feather_power", 2.25), 0.1)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    solid = art_a > 8.0
    tr_zone = (xx + 0.5 >= cx) & (yy + 0.5 <= cy)
    solid_tr = solid & tr_zone
    if not np.any(solid_tr) or corner_w < 0.5:
        return np.zeros((size, size), dtype=np.float32)

    corner_x, corner_y = find_art_tr_corner(art_a, xx, yy, cx, cy, inner_r)
    jx_top, _ = ring_junction_along_top(corner_x, corner_y, cx, cy, inner_r)
    _, jy_right = ring_junction_along_right(corner_x, corner_y, cx, cy, inner_r)
    ring_inset = max(cfg.get("ring_end_inset_px", 0.0), 0.0)
    x_ring = min(jx_top, corner_x) + ring_inset
    y_ring = max(jy_right, corner_y) - ring_inset

    y_top = np.full(size, np.inf, dtype=np.float32)
    has_col = solid_tr.any(axis=0)
    y_top[has_col] = np.where(solid_tr, yy, np.inf).min(axis=0)[has_col]

    x_right = np.full(size, -np.inf, dtype=np.float32)
    has_row = solid_tr.any(axis=1)
    x_right[has_row] = np.where(solid_tr, xx, -np.inf).max(axis=1)[has_row]

    top_span = max(corner_x - x_ring, 1.0)
    along_top = np.clip((corner_x - xx) / top_span, 0.0, 1.0)
    local_w_top = corner_w * (1.0 - along_top) ** width_taper

    y_top_2d = y_top[np.newaxis, :]
    depth_top = yy - y_top_2d
    safe_w_top = np.maximum(local_w_top, 1e-6)
    top_mask = (
        solid_tr
        & (xx + 0.5 >= x_ring - 0.5)
        & (xx + 0.5 <= corner_x + 0.5)
        & np.isfinite(depth_top)
        & (depth_top >= 0.0)
        & (local_w_top > 0.05)
        & (depth_top <= local_w_top)
    )
    t_top = np.clip(depth_top / safe_w_top, 0.0, 1.0)
    top_edge = np.clip(1.0 - t_top, 0.0, 1.0) ** feather_power

    right_span = max(y_ring - corner_y, 1.0)
    along_right = np.clip((yy - corner_y) / right_span, 0.0, 1.0)
    local_w_right = corner_w * (1.0 - along_right) ** width_taper

    x_right_2d = x_right[:, np.newaxis]
    depth_right = x_right_2d - xx
    safe_w_right = np.maximum(local_w_right, 1e-6)
    right_mask = (
        solid_tr
        & (yy + 0.5 >= corner_y - 0.5)
        & (yy + 0.5 <= y_ring + 0.5)
        & np.isfinite(depth_right)
        & (depth_right >= 0.0)
        & (local_w_right > 0.05)
        & (depth_right <= local_w_right)
    )
    t_right = np.clip(depth_right / safe_w_right, 0.0, 1.0)
    right_edge = np.clip(1.0 - t_right, 0.0, 1.0) ** feather_power

    only_top = top_mask & ~right_mask
    only_right = right_mask & ~top_mask
    overlap = top_mask & right_mask
    prefer_top = depth_top / safe_w_top <= depth_right / safe_w_right
    edge = np.zeros_like(top_edge, dtype=np.float32)
    edge = np.where(only_top, top_edge, edge)
    edge = np.where(only_right, right_edge, edge)
    edge = np.where(overlap & prefer_top, top_edge, edge)
    edge = np.where(overlap & ~prefer_top, right_edge, edge)
    return edge


def tr_sector_black_edge(
    size: int,
    cx: float,
    cy: float,
    outer_r: float,
    front_sector: np.ndarray,
    front_a: np.ndarray,
    cfg: dict[str, float],
) -> np.ndarray:
    """
    Thin black feather from the actual overhang art tip along outside edges,
    tapering to zero at the TL and BR sector junctions.
    """
    cx_f = cy_f = (size - 1) / 2.0
    edge = tr_tip_edge_strips(size, cx_f, cy_f, outer_r, front_sector, front_a, cfg)
    return edge


def art_edge_fade_multiplier(alpha: np.ndarray, cfg: dict[str, float]) -> np.ndarray:
    """Fade art alpha to transparent within cfg width_px of the silhouette edge."""
    width = max(cfg.get("width_px", 8.0), 1.0)
    power = cfg.get("power", 1.0)
    solid = alpha > 8.0
    if not np.any(solid):
        return np.ones_like(alpha, dtype=np.float32)
    dist = distance_transform_edt(solid.astype(np.uint8)).astype(np.float32)
    return np.clip(dist / width, 0.0, 1.0) ** power


def corner_fade_multiplier(size: int, cls: str) -> np.ndarray:
    corners = CORNER_FADE.get(cls)
    if not corners:
        return np.ones((size, size), dtype=np.float32)

    cfg = CORNER_FADE_CFG.get(cls, {})
    reach = cfg.get("reach", 0.65)
    power = cfg.get("power", 2.5)
    edge_frac = cfg.get("edge_frac", 0.0)
    invert = bool(cfg.get("invert", 0.0))
    stretch = cfg.get("stretch", 1.0)
    edge_px = size * edge_frac

    def radial_curve(nx: np.ndarray, ny: np.ndarray) -> np.ndarray:
        t = np.clip(
            np.sqrt((nx / stretch) ** 2 + (ny / stretch) ** 2) / reach, 0, 1
        )
        if invert:
            return 1.0 - (1.0 - t) ** power
        return t ** power

    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    denom = max(size - 1, 1)
    mult = np.ones((size, size), dtype=np.float32)

    for corner in corners:
        if corner == "tl":
            nx, ny = xx / denom, yy / denom
            radial = radial_curve(nx, ny)
            if edge_frac > 0:
                from_top = np.clip(yy / edge_px, 0, 1) ** power
                from_left = np.clip(xx / edge_px, 0, 1) ** power
                corner_mult = np.minimum(radial, np.minimum(from_top, from_left))
            else:
                corner_mult = radial
        elif corner == "tr":
            nx, ny = (denom - xx) / denom, yy / denom
            radial = radial_curve(nx, ny)
            if edge_frac > 0:
                from_top = np.clip(yy / edge_px, 0, 1) ** power
                from_right = np.clip((denom - xx) / edge_px, 0, 1) ** power
                corner_mult = np.minimum(radial, np.minimum(from_top, from_right))
            else:
                corner_mult = radial
        elif corner == "bl":
            nx, ny = xx / denom, (denom - yy) / denom
            radial = radial_curve(nx, ny)
            if edge_frac > 0:
                from_bottom = np.clip((denom - yy) / edge_px, 0, 1) ** power
                from_left = np.clip(xx / edge_px, 0, 1) ** power
                corner_mult = np.minimum(radial, np.minimum(from_bottom, from_left))
            else:
                corner_mult = radial
        elif corner == "br":
            nx, ny = (denom - xx) / denom, (denom - yy) / denom
            radial = radial_curve(nx, ny)
            if edge_frac > 0:
                from_bottom = np.clip((denom - yy) / edge_px, 0, 1) ** power
                from_right = np.clip((denom - xx) / edge_px, 0, 1) ** power
                corner_mult = np.minimum(radial, np.minimum(from_bottom, from_right))
            else:
                corner_mult = radial
        else:
            continue
        mult = np.minimum(mult, corner_mult)

    return mult


def prepare_art_layers_v2(
    cutout: Image.Image,
    size: int,
    inner_r: float,
    outer_r: float,
    cls: str,
) -> tuple[Image.Image, Image.Image, Image.Image]:
    """
    base  — art clipped to inner disc (drawn under ring / haze)
    mid   — above haze, behind silver ring (arc-clipped)
    front — overhang outside inner ring (drawn above ring)
    """
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if cls in ART_TR_TOUCH_INNER_RING:
        paste_tr_touch_inner_ring(canvas, cutout, inner_r)
    else:
        ox, oy = art_paste_offset(cutout, size, inner_r, cls)
        canvas.paste(cutout, (ox, oy), cutout)
    arr = np.array(canvas, dtype=np.float32)
    alpha = arr[:, :, 3]

    fade = corner_fade_multiplier(size, cls)
    alpha = alpha * fade
    arr[:, :, :3] *= fade[..., np.newaxis]
    edge_cfg = ART_EDGE_FADE.get(cls)
    if edge_cfg is not None:
        edge_fade = art_edge_fade_multiplier(alpha, edge_cfg)
        alpha = alpha * edge_fade
        arr[:, :, :3] *= edge_fade[..., np.newaxis]
    arr[:, :, 3] = alpha

    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = cy = (size - 1) / 2.0
    rr = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    ang = np.degrees(np.arctan2(yy - cy, xx - cx))

    outer_clip = np.clip((outer_r + 1.2 - rr) / 1.2, 0, 1)
    if cls in BASE_INNER_CLIP:
        behind_clip = np.clip((inner_r + 1.0 - rr) / 1.5, 0, 1)
        fringe_cfg = ART_RESTORE_DARK_FRINGE.get(cls)
        if fringe_cfg is not None and fringe_cfg.get("exempt_inner_clip", 0.0) > 0.0:
            behind_clip = np.where(
                fractional_zone_mask(size, size, fringe_cfg),
                1.0,
                behind_clip,
            )
    elif cls in BASE_OUTER_HARD_CLIP:
        behind_clip = np.clip((outer_r + 0.35 - rr) / 0.35, 0, 1)
    else:
        behind_clip = outer_clip
    arc_cfgs = BASE_ARC_INNER_CLIP.get(cls)
    if arc_cfgs:
        arc_mask = arc_zone_mask(yy, xx, ang, arc_cfgs)
        if arc_mask.any():
            lip_in = float(arc_cfgs[0].get("lip_inset_px", 1.0))
            feather = max(float(arc_cfgs[0].get("feather_px", 1.5)), 1e-6)
            inner_arc_clip = np.clip((inner_r + lip_in - rr) / feather, 0, 1)
            behind_clip = np.where(arc_mask, inner_arc_clip, behind_clip)
    ring_cfgs = BASE_BEHIND_RING.get(cls)
    if ring_cfgs and cls in BASE_INNER_CLIP:
        ring_mask = arc_zone_mask(yy, xx, ang, ring_cfgs)
        band_clip = np.clip((outer_r + 1.2 - rr) / 1.2, 0, 1)
        in_band = (rr >= inner_r) & (rr <= outer_r)
        behind_clip = np.where(ring_mask & in_band, band_clip, behind_clip)
    front_sector = angle_in_sectors(ang, OVERHANG_SECTORS.get(cls, []))
    if cls in TOP_R_OVERHANG:
        # Pixel-center halves so the column/row through icon center is included.
        front_sector = front_sector | ((xx + 0.5 >= cx) & (yy + 0.5 <= cy))
    if cls in LEFT_FRONT_TUCK:
        tuck = LEFT_FRONT_TUCK[cls]
        front_sector = front_sector & ~((xx < cx) & (yy >= cy - tuck))
    if cls in TR_QUAD_LOWER_TUCK:
        y_tuck, x_frac = TR_QUAD_LOWER_TUCK[cls]
        x_min = cx + inner_r * x_frac
        front_sector = front_sector & ~(
            (xx > x_min) & (yy < cy) & (yy >= cy - y_tuck)
        )
    inner_promote = front_inner_promote_mask(size, yy, xx, ang, alpha, cls, arr[:, :, :3])
    inner_mid = mid_inner_promote_mask(size, yy, xx, ang, alpha, cls, arr[:, :, :3])
    inner_demote = front_inner_demote_mask(size, yy, xx, ang, alpha, cls)
    inner_front = inner_promote & ~inner_demote
    inner_mid = inner_mid & ~inner_demote
    tuck_cfg = OVERHANG_CLOCK_TUCK.get(cls)
    tuck_mult = np.ones_like(alpha, dtype=np.float32)
    if tuck_cfg is not None:
        clock = atan2_to_clock_deg(ang)
        tuck_mult = overhang_clock_tuck_multiplier(clock, tuck_cfg)
        front_sector = front_sector & (tuck_mult >= 0.98)
    front_sector = front_sector | inner_front
    inner_pad = OVERHANG_INNER_PAD.get(cls, 0.5)
    mid_a = np.zeros_like(alpha, dtype=np.float32)

    if cls in FULL_QUADRANT_OVERHANG:
        base_a = np.where(front_sector | inner_mid, 0.0, alpha * behind_clip)
        if cls in FRONT_OUTER_CLIP_RIGHT:
            outer_clip_front = (xx > cx) & front_sector
        else:
            outer_clip_front = np.zeros_like(front_sector, dtype=bool)
        front_mult = np.where(outer_clip_front, outer_clip, 1.0)
        front_a = np.where(front_sector, alpha * front_mult, 0.0)
        mid_clip = outer_clip
        mid_arc_cfgs = MID_ARC_OUTER_CLIP.get(cls)
        if mid_arc_cfgs:
            mid_arc = arc_zone_mask(yy, xx, ang, mid_arc_cfgs)
            lip_in = float(mid_arc_cfgs[0].get("lip_inset_px", 0.0))
            feather = max(float(mid_arc_cfgs[0].get("feather_px", 1.0)), 1e-6)
            hard_outer = np.clip((outer_r + lip_in - rr) / feather, 0, 1)
            mid_clip = np.where(mid_arc, hard_outer, mid_clip)
        no_clip_cfgs = MID_NO_OUTER_CLIP.get(cls)
        if no_clip_cfgs:
            no_clip = arc_zone_mask(yy, xx, ang, no_clip_cfgs)
            mid_clip = np.where(no_clip, 1.0, mid_clip)
        mid_a = np.where(inner_mid, alpha * mid_clip, 0.0)
    else:
        # Radius gate applies to sector overhang only; inner promote may sit inside inner ring.
        overhang_front = (rr > inner_r - inner_pad) & (front_sector & ~inner_front)
        is_front = overhang_front | inner_front
        is_mid = inner_mid
        base_a = np.where(is_front | is_mid, 0.0, alpha * behind_clip)
        mid_clip = outer_clip
        mid_arc_cfgs = MID_ARC_OUTER_CLIP.get(cls)
        if mid_arc_cfgs:
            mid_arc = arc_zone_mask(yy, xx, ang, mid_arc_cfgs)
            lip_in = float(mid_arc_cfgs[0].get("lip_inset_px", 0.0))
            feather = max(float(mid_arc_cfgs[0].get("feather_px", 1.0)), 1e-6)
            hard_outer = np.clip((outer_r + lip_in - rr) / feather, 0, 1)
            mid_clip = np.where(mid_arc, hard_outer, mid_clip)
        no_clip_cfgs = MID_NO_OUTER_CLIP.get(cls)
        if no_clip_cfgs:
            no_clip = arc_zone_mask(yy, xx, ang, no_clip_cfgs)
            mid_clip = np.where(no_clip, 1.0, mid_clip)
        mid_a = np.where(is_mid, alpha * mid_clip, 0.0)
        if cls in FRONT_NO_OUTER_CLIP:
            front_a = np.where(is_front, alpha, 0.0)
        else:
            front_a = np.where(is_front, alpha * outer_clip, 0.0)

    if tuck_cfg is not None:
        lip_out = tuck_cfg.get("lip_outset_px", 2.0)
        tucked = tuck_mult < 0.98
        inner_fill = np.clip((inner_r + lip_out + 0.5 - rr) / 1.0, 0, 1)
        band_clip = np.clip((outer_r + 1.2 - rr) / 1.2, 0, 1)
        tuck_base_clip = np.where(rr <= inner_r + lip_out, inner_fill, band_clip)
        tuck_base_a = alpha * tuck_base_clip
        front_a = np.where(tucked, 0.0, front_a)
        base_a = np.where(tucked, np.maximum(base_a, tuck_base_a), base_a)

    base = arr.copy()
    base[:, :, 3] = np.clip(base_a, 0, 255)
    base[base[:, :, 3] < 1, :3] = 0

    mid = arr.copy()
    mid[:, :, 3] = np.clip(mid_a, 0, 255)
    mid[mid[:, :, 3] < 1, :3] = 0

    front = arr.copy()
    front[:, :, 3] = np.clip(front_a, 0, 255)

    ring_edge_cfg = TR_ART_RING_EDGE.get(cls)
    if ring_edge_cfg is not None:
        edge = tr_art_ring_edge_strips(size, cx, cy, inner_r, alpha, ring_edge_cfg)
        alpha_feather = ring_edge_cfg.get("alpha_feather", 1.0)
        front[:, :, 3] = np.clip(
            front[:, :, 3] * (1.0 - edge * alpha_feather), 0, 255
        )

    if cls in TR_SECTOR_BLACK_EDGE:
        cx_f = cy_f = (size - 1) / 2.0
        edge = tr_sector_black_edge(
            size,
            cx_f,
            cy_f,
            outer_r,
            front_sector,
            front_a,
            TR_SECTOR_BLACK_EDGE[cls],
        )
        keep = 1.0 - edge[..., np.newaxis]
        front[:, :, :3] = front[:, :, :3] * keep
    front[front[:, :, 3] < 1, :3] = 0

    return (
        Image.fromarray(base.astype(np.uint8), "RGBA"),
        Image.fromarray(mid.astype(np.uint8), "RGBA"),
        Image.fromarray(front.astype(np.uint8), "RGBA"),
    )


def prepare_art_aura_layer(
    cutout: Image.Image,
    size: int,
    inner_r: float,
    outer_r: float,
    cls: str,
) -> Image.Image:
    """Paste split aura cutout; outer-badge clip only (keep full teal silhouette)."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if cls in ART_TR_TOUCH_INNER_RING:
        paste_tr_touch_inner_ring(canvas, cutout, inner_r)
    else:
        ox, oy = art_paste_offset(cutout, size, inner_r, cls)
        canvas.paste(cutout, (ox, oy), cutout)
    arr = np.array(canvas, dtype=np.float32)
    alpha = arr[:, :, 3]

    fade = corner_fade_multiplier(size, cls)
    alpha = alpha * fade
    arr[:, :, :3] *= fade[..., np.newaxis]
    edge_cfg = ART_EDGE_FADE.get(cls)
    if edge_cfg is not None:
        edge_fade = art_edge_fade_multiplier(alpha, edge_cfg)
        alpha = alpha * edge_fade
        arr[:, :, :3] *= edge_fade[..., np.newaxis]

    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = cy = (size - 1) / 2.0
    rr = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    outer_clip = np.clip((outer_r + 1.2 - rr) / 1.2, 0, 1)
    arr[:, :, 3] = np.clip(alpha * outer_clip, 0, 255)
    arr[arr[:, :, 3] < 1, :3] = 0
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def paste_tr_touch_inner_ring(
    canvas: Image.Image,
    cutout: Image.Image,
    inner_r: float,
) -> None:
    """Place art so opaque top + right extents meet the inner silver ring."""
    ox, oy = art_paste_offset(cutout, canvas.size[0], inner_r, "mage")
    canvas.paste(cutout, (ox, oy), cutout)


def resolve_art_scale(
    cls: str,
    inner_r: float,
    source_size: int,
    cutout: Image.Image | None = None,
) -> float:
    extra = ART_SCALE.get(cls, 1.0) * GLOBAL_ART_SCALE
    if cls in ART_TOUCH_INNER_RING:
        return (2.0 * inner_r) / source_size * extra
    if cls in ART_TR_TOUCH_INNER_RING and cutout is not None:
        return extra
    return extra


def scale_art(art: Image.Image, scale: float) -> Image.Image:
    if scale == 1.0:
        return art
    w, h = art.size
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    return art.resize((nw, nh), Image.Resampling.LANCZOS)


def pad_art_size(art: Image.Image, dw: int, dh: int) -> Image.Image:
    if dw == 0 and dh == 0:
        return art
    w, h = art.size
    return art.resize((w + dw, h + dh), Image.Resampling.LANCZOS)


def paste_centered(canvas: Image.Image, art: Image.Image) -> Image.Image:
    out = canvas.copy()
    cw, ch = art.size
    size = canvas.size[0]
    out.paste(art, ((size - cw) // 2, (size - ch) // 2), art)
    return out


def composite_art_on_top(badge: Image.Image, cutout: Image.Image) -> Image.Image:
    """Border underneath, full background-removed art on top."""
    return Image.alpha_composite(badge, paste_centered(badge, cutout))


def tuck_haze_behind_art(
    haze: Image.Image,
    base: Image.Image,
    front: Image.Image,
    inner_r: float,
    cls: str,
) -> Image.Image:
    """Reduce haze opacity where art sits in a top-right tuck zone."""
    cfg = HAZE_TUCK_BEHIND_ART.get(cls)
    if cfg is None:
        return haze

    h = np.array(haze, dtype=np.float32)
    art_a = np.maximum(
        np.array(base, dtype=np.float32)[:, :, 3],
        np.array(front, dtype=np.float32)[:, :, 3],
    ) / 255.0

    size = h.shape[0]
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = cy = (size - 1) / 2.0
    corner_start = cfg.get("corner_start", 0.15)
    corner_span = max(cfg.get("corner_span", 0.70), 1e-6)
    strength = cfg.get("strength", 1.0)
    power = cfg.get("power", 1.0)

    tr_norm = np.clip(
        ((xx - cx) / inner_r + (cy - yy) / inner_r - corner_start) / corner_span,
        0.0,
        1.0,
    ) ** power
    keep = np.clip(1.0 - tr_norm * art_a * strength, 0.0, 1.0)
    if cfg.get("tuck_inner_only", 0.0) > 0.0:
        rr = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        band_width = max(inner_r * INNER_HAZE_WIDTH_RATIO, 1.0)
        keep = np.where(rr >= inner_r - band_width, 1.0, keep)
    h *= keep[..., np.newaxis]
    return Image.fromarray(h.astype(np.uint8), "RGBA")


def resolve_effect_cfg(default: dict[str, float], overrides: dict[str, dict[str, float]], cls: str) -> dict[str, float]:
    cfg = dict(default)
    cfg.update(overrides.get(cls, {}))
    return cfg


def composite_icon(cls: str, src: Image.Image, size: int = 130) -> tuple[Image.Image, ...]:
    badge, inner_r, outer_r = make_border_badge(size)
    raw_cutout = remove_edge_background(src)
    raw_cutout = restore_dark_fringe(src, raw_cutout, cls)
    scale = resolve_art_scale(cls, inner_r, max(raw_cutout.size), raw_cutout)
    cutout = scale_art(raw_cutout, scale)
    pad_w, pad_h = ART_SIZE_PAD.get(cls, (0, 0))
    cutout = pad_art_size(cutout, pad_w, pad_h)
    strip_cfg = ART_STRIP_CORNERS.get(cls)
    if strip_cfg is not None:
        cutout = strip_cutout_corners(cutout, strip_cfg)
    subject_cutout, aura_cutout = split_art_aura(cutout, cls)
    base, mid, front = prepare_art_layers_v2(subject_cutout, size, inner_r, outer_r, cls)
    art_aura: Image.Image | None = None
    if aura_cutout is not None:
        art_aura = prepare_art_aura_layer(aura_cutout, size, inner_r, outer_r, cls)

    disc = make_inner_disc(size, inner_r)
    haze = make_inner_haze_ring(size, inner_r, INNER_HAZE_ARC.get(cls))
    ring = make_metallic_ring(size, outer_r, inner_r)

    out = disc
    core_cfg = resolve_effect_cfg(DEFAULT_SECONDARY_DARK_CORE, SECONDARY_DARK_CORE, cls)
    out = Image.alpha_composite(
        out, make_secondary_dark_core(size, inner_r, core_cfg, cutout, cls)
    )
    shadow_cfg = resolve_effect_cfg(DEFAULT_ART_DROP_SHADOW, ART_DROP_SHADOW, cls)
    shadow = make_art_drop_shadow(cutout, size, shadow_cfg, clip_r=inner_r, inner_r=inner_r, cls=cls)
    haze_on_top = cls in HAZE_OVER_ALL
    if cls in HAZE_TUCK_BEHIND_ART and not haze_on_top:
        haze = tuck_haze_behind_art(haze, base, front, inner_r, cls)
    if cls in HAZE_OVER_BASE:
        out = Image.alpha_composite(out, shadow)
        if art_aura is not None:
            out = Image.alpha_composite(out, art_aura)
        out = Image.alpha_composite(out, base)
        if not haze_on_top:
            out = Image.alpha_composite(out, haze)
        out = Image.alpha_composite(out, mid)
    else:
        if not haze_on_top:
            out = Image.alpha_composite(out, haze)
        out = Image.alpha_composite(out, shadow)
        if art_aura is not None:
            out = Image.alpha_composite(out, art_aura)
        out = Image.alpha_composite(out, base)
        out = Image.alpha_composite(out, mid)
    out = Image.alpha_composite(out, ring)
    front_shadow_cfg = FRONT_DROP_SHADOW.get(cls)
    if front_shadow_cfg is not None:
        out = Image.alpha_composite(
            out, make_layer_drop_shadow(front, front_shadow_cfg, clip_r=outer_r)
        )
    out = Image.alpha_composite(out, front)
    if haze_on_top:
        out = Image.alpha_composite(out, haze)
    return out, badge, haze, raw_cutout, base, mid, front, art_aura


def checkerboard(size: tuple[int, int], cell: int = 8) -> Image.Image:
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    dark = ((yy // cell) + (xx // cell)) % 2 == 0
    board = np.full((h, w, 3), 200, dtype=np.uint8)
    board[dark] = 150
    return Image.fromarray(board, "RGB")


def export_art_only() -> None:
    """Single-layer cutouts: edge-black removal only, no border compositing."""
    art_dir = OUT / "art_only"
    preview_dir = OUT / "art_only" / "preview"
    art_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(exist_ok=True)

    for cls in CLASSES:
        src_path = resolve_source_path(cls)
        if src_path is None:
            continue
        src_img = Image.open(src_path)
        cutout = restore_dark_fringe(src_img, remove_edge_background(src_img), cls)
        cutout.save(art_dir / f"{cls}.png")
        preview = Image.alpha_composite(checkerboard(cutout.size).convert("RGBA"), cutout)
        preview.save(preview_dir / f"{cls}.png")
        a = np.array(cutout)[:, :, 3]
        print(f"  art_only/{cls}.png ({a.mean()/255:.0%} opaque) <- {src_path.name}")


def build_icon_set(
    *,
    title: str,
    resolve_path,
    out_dir: Path,
    write_layers: bool = True,
) -> None:
    layer_names = (
        "layer1_border",
        "layer2_haze",
        "layer3_cutout",
        "layer4_base",
        "layer4_art_aura",
        "layer4_mid",
        "layer5_front",
    )
    if write_layers:
        for sub in layer_names:
            (out_dir / sub).mkdir(parents=True, exist_ok=True)

    print(f"\n=== {title} ===")
    for cls in CLASSES:
        src_path = resolve_path(cls)
        if src_path is None:
            print(f"SKIP {cls}: missing source")
            continue
        src = Image.open(src_path)
        size = max(src.size)
        final, border, haze, cutout, base, mid, front, art_aura = composite_icon(cls, src, size=size)

        final.save(out_dir / f"{cls}.png")
        if write_layers:
            border.save(out_dir / "layer1_border" / f"{cls}.png")
            haze.save(out_dir / "layer2_haze" / f"{cls}.png")
            cutout.save(out_dir / "layer3_cutout" / f"{cls}.png")
            base.save(out_dir / "layer4_base" / f"{cls}.png")
            if art_aura is not None:
                art_aura.save(out_dir / "layer4_art_aura" / f"{cls}.png")
            mid.save(out_dir / "layer4_mid" / f"{cls}.png")
            front.save(out_dir / "layer5_front" / f"{cls}.png")

        cut_a = np.array(cutout)[:, :, 3]
        rel = out_dir.relative_to(ROOT) if out_dir.is_relative_to(ROOT) else out_dir
        print(
            f"{cls}: {src_path.name} cutout {cut_a.mean()/255:.0%} opaque "
            f"-> {rel / f'{cls}.png'}"
        )

    print(f"Done. {out_dir}")


def main() -> None:
    for sub in (
        "layer1_border",
        "layer2_haze",
        "layer3_cutout",
        "layer4_base",
        "layer4_art_aura",
        "layer4_mid",
        "layer5_front",
    ):
        (OUT / sub).mkdir(parents=True, exist_ok=True)

    build_icon_set(
        title="Full composite (old school)",
        resolve_path=resolve_source_path,
        out_dir=OUT,
    )


if __name__ == "__main__":
    main()
