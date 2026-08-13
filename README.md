# Old School Class Icons

Warcraft III–style class icons with metallic ring, blue inner haze, and per-class art layering.

## Quick start

```bash
pip install -r requirements.txt
python build_class_icons_old_school.py
```

Requires Python 3.10+.

## Folder layout

| Path | Description |
|------|-------------|
| `sources/` | Input class art PNGs (one per class) |
| `*.png` (root) | Final composited icons |
| `layer1_border/` | Metallic ring + disc |
| `layer2_haze/` | Blue inner haze ring |
| `layer3_cutout/` | Scaled subject cutout |
| `layer4_base/` | Base art layer (behind haze) |
| `layer4_art_aura/` | Warrior teal aura split (warrior only) |
| `layer4_mid/` | Mid layer (above haze, behind ring) |
| `layer5_front/` | Front overhang (above ring) |
| `build_class_icons_old_school.py` | Builder script and all tuning constants |
| `comparison/github-dark/` | README images for GitHub dark theme |
| `comparison/github-light/` | README images for GitHub light theme |
| `comparison/transparent/` | True alpha PNGs (for editors; not for README) |
| `make_comparisons.py` | Regenerate all comparison variants |

## Classes

warrior, paladin, rogue, mage, hunter, druid, priest, warlock, shaman

## Tuning

Edit constants at the top of `build_class_icons_old_school.py` — art scale, paste offsets, haze arcs, and per-class promote/clip zones.

## Comparison vs original WC3 minis

The **final icons in this folder** are a modern rebuild of the classic Warcraft III class minis. Originals: `C:\Users\jeb32\OneDrive\Documents\class icons\old minis` (not bundled here).

### All classes (overview)

Left = original 38×35 mini · Right = new build.

The PNGs in `comparison/transparent/` have real alpha, but **GitHub and most Markdown previews do not show that as see-through** — they either draw a grey/white checkerboard (Cursor, VS Code) or flatten transparency unpredictably. The README therefore uses theme-matched images via `<picture>`:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="comparison/github-dark/overview.png">
  <source media="(prefers-color-scheme: light)" srcset="comparison/github-light/overview.png">
  <img src="comparison/github-light/overview.png" alt="Old vs new — all classes">
</picture>

### Per class

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="comparison/github-dark/warrior.png">
  <source media="(prefers-color-scheme: light)" srcset="comparison/github-light/warrior.png">
  <img src="comparison/github-light/warrior.png" alt="Warrior — old vs new">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="comparison/github-dark/paladin.png">
  <source media="(prefers-color-scheme: light)" srcset="comparison/github-light/paladin.png">
  <img src="comparison/github-light/paladin.png" alt="Paladin — old vs new">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="comparison/github-dark/rogue.png">
  <source media="(prefers-color-scheme: light)" srcset="comparison/github-light/rogue.png">
  <img src="comparison/github-light/rogue.png" alt="Rogue — old vs new">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="comparison/github-dark/mage.png">
  <source media="(prefers-color-scheme: light)" srcset="comparison/github-light/mage.png">
  <img src="comparison/github-light/mage.png" alt="Mage — old vs new">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="comparison/github-dark/hunter.png">
  <source media="(prefers-color-scheme: light)" srcset="comparison/github-light/hunter.png">
  <img src="comparison/github-light/hunter.png" alt="Hunter — old vs new">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="comparison/github-dark/druid.png">
  <source media="(prefers-color-scheme: light)" srcset="comparison/github-light/druid.png">
  <img src="comparison/github-light/druid.png" alt="Druid — old vs new">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="comparison/github-dark/priest.png">
  <source media="(prefers-color-scheme: light)" srcset="comparison/github-light/priest.png">
  <img src="comparison/github-light/priest.png" alt="Priest — old vs new">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="comparison/github-dark/warlock.png">
  <source media="(prefers-color-scheme: light)" srcset="comparison/github-light/warlock.png">
  <img src="comparison/github-light/warlock.png" alt="Warlock — old vs new">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="comparison/github-dark/shaman.png">
  <source media="(prefers-color-scheme: light)" srcset="comparison/github-light/shaman.png">
  <img src="comparison/github-light/shaman.png" alt="Shaman — old vs new">
</picture>

Regenerate after rebuilding icons:

```bash
python make_comparisons.py
```

### At a glance

| | Original minis (`old minis/`) | New build (this folder) |
|--|-------------------------------|-------------------------|
| **Resolution** | 38×35 px | 224×224 px (240×240 for shaman — follows source size) |
| **Format** | GIF (8 classes) + PNG (shaman), ~1 KB each | RGBA PNG, ~56–84 KB each |
| **Art source** | Original in-game assets | Upscaled PNGs in `sources/`, background removed |
| **Ring / frame** | Flat baked-in silver rim | Procedural beveled metallic ring (`layer1_border/`) |
| **Blue glow** | Simple flat cyan disc baked into art | Separate inner haze ring (`layer2_haze/`) with per-class arc fades |
| **Layering** | Single flat image | Multi-layer stack: base → haze → mid → ring → front |
| **Overhang** | None — art clipped to circle | Per-class sectors (blades, bows, crystals, etc. past the ring) |
| **Rebuildable** | No | Yes — `python build_class_icons_old_school.py` |

Ring proportions and haze placement were measured from the 38×35 reference GIFs (`OUTER_R_RATIO`, `RIM_RATIO`, etc. in the script).

### File mapping

| Class | Original (`old minis/`) | New output |
|-------|-------------------------|------------|
| warrior | `Warrior_Icon.gif` | `warrior.png` |
| paladin | `Paladin_Icon.gif` | `paladin.png` |
| rogue | `Rogue_Icon.gif` | `rogue.png` |
| mage | `Mage_Icon.gif` | `mage.png` |
| hunter | `Hunter_Icon.gif` | `hunter.png` |
| druid | `Druid_Icon.gif` | `druid.png` |
| priest | `Priest_Icon.gif` | `priest.png` |
| warlock | `Warlock_Icon.gif` | `warlock.png` |
| shaman | `Shaman_Icon.png` | `shaman.png` |

### What changed visually

**Overall**
- Much higher resolution and smoother edges from upscaled source art.
- Stronger blue inner haze with a soft falloff at the ring lip (not a flat fill).
- Thicker, lit metallic ring with top-left highlight and bottom-right shadow.
- Dark pixelated inner core and drop shadow behind the subject.

**Per-class highlights** (vs the flat originals)
- **Warrior** — Teal aura split to its own layer; handle/crossguard promoted above haze with spatial masks; haze arc gap (~150°–270°, 50° feather) fades around the grip.
- **Mage** — Crystal/fire overhang above the ring; haze arc gap at top-right (~12°–73°).
- **Rogue** — Daggers and TR handle overhang; blade tucks behind the ring on the left arc.
- **Hunter** — Bow overhang with arc clip on the grip.
- **Others** — Individual art scale, paste offset, and overhang sectors tuned to match each original silhouette.

**Trade-off**
- New files are larger (PNG vs 1 KB GIFs) and require Python to regenerate — in exchange you get editable layers and per-class compositing control.

