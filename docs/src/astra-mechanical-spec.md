---
title: "Gizmonic Cambot: mechanical specification"
subtitle: "PETG housing, retainer, modular arms and dual-magnet foot · W-Mount contract"
author: "Gizmonic Cambot project · open hardware (CERN-OHL-P-2.0)"
date: "Spec rev B · November 2026"
---

## 1. Purpose and deliverables

Document the 3D-printable enclosure and modular mount for the Cambot camera on
the FORIOT ESP32-S3-CAM board, and keep the **W-Mount** interface stable so
community feet and arms can mate with it.

**Source of truth for printable files:** the frozen release under
`hardware/cambot-minimum-petg-kit/` (release **2026-11-04**). That kit ships
STL, STEP and 3MF exports checked by `manifest.json`, CadQuery generators under
`source/`, and a kit README with PETG settings, hardware and assembly. Edit the
generators, regenerate, and update exports and checksums together.

| Format | Purpose |
|---|---|
| CadQuery Python under `source/` | Parametric edits |
| STEP | CAD interchange |
| STL and oriented 3MF | Printing |
| Plate 3MFs in `plates/` | Grouped print layouts |
| Kit `README.md` + `LICENSE.md` | Print, assembly, provenance |

Units are millimeters. Tolerances not stated are ±0.2 mm on the printed part.

<div class="note">
<strong>Dimensions marked TBV (to be verified)</strong> come from vendor listings
or clone boards, not from calipers. Before freezing geometry that depends on
them, measure a physical board. Every TBV value should stay a top-level
parameter in the generators.
</div>

## 2. System overview (current kit)

The minimum PETG kit is an **open housing** with modular W-Mount arms and a
dual external-magnet foot. It deliberately omits a front cover, screw clamp and
embedded-magnet puck.

| Part | Role |
|---|---|
| `housing_v3` | Drop-in body, two center posts, header clearance, camera-side `knuckle2` |
| `retainer_v3_short` | Short bridge retainer that latches over the PCB |
| `rod_male_25` / `rod_male_50` | Two-finger arm with 12 mm spigot |
| `rod_female_25_6-32` / `rod_female_50_6-32` | Three-finger arm with bore and 6-32 locking pilot |
| `dual_magnet_foot` | Three-finger base; magnets mount under the plate against steel |

Only five pieces assemble into one mount: housing, retainer, one male rod, one
female rod, and the foot. The unused rod lengths are alternatives.

| Male + female shafts | Approx. pivot-to-pivot |
|---|---|
| 25 + 25 | 92–96 mm |
| 25 + 50 (or 50 + 25) | 117–121 mm |
| 50 + 50 | 142–146 mm |

These are pivot distances, not steel-to-lens reach. Reach also depends on joint
angles and magnet thickness.

The housing and feet meet at the **W-Mount** (section 6). That interface is the
stable contract. Publish `knuckle2` / `knuckle3` as standalone modules (the kit
already does this in `w_mount.py`).

## 3. Reference board: FORIOT ESP32-S3-CAM

Orientation used throughout: hold the board component side toward you, with the
**antenna end at the top** and the **two USB-C ports on the bottom edge**.
"Front" is the component and camera side; "rear" is the side with the microSD
slot and header pins.

| Feature | Value | Status |
|---|---|---|
| PCB outline | 62.6 × 28.3 mm | Vendor figure, TBV |
| PCB thickness | 1.6 mm | TBV |
| Mounting holes | None. Kit retains on two center posts + retainer. | Confirmed in kit |
| Header rows | 2 × 20 pins, 2.54 mm pitch, rows 25.4 mm apart | Vendor figure |
| Header pins on rear (if soldered) | Up to 8.5 mm below the PCB rear face | Housing clears headers |
| Tallest front component, excluding the camera | ESP32-S3-WROOM-1 shield, ~3.2 mm | TBV |
| Antenna keep-out | Top ~6 mm of the PCB (module antenna) | Retainer opening ~24 mm for ~17.7 mm antenna width; fit still TBV |
| USB-C ports | Two, on the bottom edge. Label TTL (CH340) and OTG. | Centers TBV |
| USB-C plug overmold clearance | 12.5 × 7.0 mm per plug, minimum | Typical cable |
| Buttons | RST and BOOT near the USB edge, front side | Open housing; accessible |
| LEDs | Power, TX, WS2812 RGB (GPIO 48), user LED (GPIO 2) | Open housing |
| microSD | Push-push slot on rear; card insertion edge | Access with housing open / unmounted |
| Camera | OV3660 module on a 24-pin 0.5 mm flex, lens on the front | Module size, lens Ø and flex length TBV |

**Camera module (TBV):** typical modules are about 8.5 × 8.5 mm with an
M8-class lens barrel about 8 mm in diameter. Wide-angle variants (120° and 160°)
of the OV3660 fit the same connector. A future front cover (section 8) must
work with lens fields of view from 66° to 160°.

## 4. Design rules for all parts

| Rule | Value |
|---|---|
| Target printers | 0.4 mm nozzle, 0.2 mm layers, bed ≥ 180 × 180 mm |
| Materials | PETG (default). PLA allowed for indoor prototypes. |
| Minimum wall | 1.6 mm (four perimeters at 0.4 mm) |
| Sliding fit | Arms: 12 mm spigot into 12.4 mm bore, ≥ 20 mm engagement |
| Overhangs | Support as noted in the kit README; finger faces parallel to the bed |
| Pivots | M5 bolt through W-Mount fingers with an M5 nut |
| Arm lock | 6-32 machine screw into female-rod pilot; bears on male spigot |
| Magnets | External rubber-coated assemblies under the foot (M4), not glued discs |
| Metal near antenna | No inserts, nuts or magnets within 15 mm of the antenna end |

Starting PETG profile from the kit: 5 walls, 6 top/bottom layers, 40–50%
infill. Finer layers alone will not correct an interfering fit.

## 5. Body (current kit)

### 5.1 Housing (`housing_v3`)

- Open drop-in body. The PCB sits on **two center posts**; there is no edge
  sled and no front cover in this release.
- Header clearance on the rear side of the cavity.
- Camera-side **two-finger W-Mount** (`knuckle2`) for the arm stack.
- Print orientation: on its side in the plate 3MF; support tray and mounting
  overhangs; clean the latch windows.

### 5.2 Retainer (`retainer_v3_short`)

- 4.5 mm thick bridge with a shortened span (7.3 mm removed relative to the
  earlier long retainer). Bridge inner edge is about 0.5 mm past the case end.
- Latches outward (not inward) over the housing.
- 24 mm antenna opening. Longitudinal clearance still needs a test fit on each
  board.
- Print flat; preserve latch clearances; avoid support in those gaps.

### 5.3 Assembly of board into housing

1. Clean and test-fit the empty housing and retainer.
2. Drop the PCB onto the two center posts (antenna end toward the retainer
   opening).
3. Seat the retainer. Check antenna, header, camera ribbon and USB clearance.
4. Insert the microSD card before or after seating, as access allows.

No M2 case screws are used in the minimum kit.

## 6. W-Mount interface (fixed contract)

The W-Mount follows the de facto GoPro finger mount, so off-the-shelf GoPro
accessories work. **Check the final parameters against a genuine GoPro buckle
and adjust until both mate with a firm, wobble-free fit.**

| Parameter | Default | Notes |
|---|---|---|
| `finger_t` | 3.0 | Finger thickness |
| `finger_gap` | 3.2 | Gap between fingers |
| `finger_r` | 7.5 | Outer radius around the pivot |
| `finger_reach` | 9.0 | Pivot center to base surface |
| `bolt_d` | 5.3 | Clearance for M5 |
| Camera / male-rod side (`knuckle2`) | 2 fingers | Total width 9.2 |
| Foot / female-rod side (`knuckle3`) | 3 fingers | Total width 15.4. One outer finger has a captive M5 hex-nut pocket. |
| Bolt | M5 × 25 thumbscrew or socket-head bolt | Verify reach with your hardware |

Print fingers so the layers stack across the finger thickness (finger faces
parallel to the bed). This avoids layer splits at the pivot. Fillet the finger
roots with r = 1.5.

The kit modules live in each generator's `w_mount.py`. Keep them consistent
across housing, arms and foot.

## 7. Arms and foot

### 7.1 Modular arms

| Feature | Spec |
|---|---|
| Shaft lengths | 25 mm and 50 mm (male and female) |
| Spigot / bore | Ø12 mm spigot into Ø12.4 mm bore |
| Engagement | Minimum 20 mm; groove must be fully hidden (20–24 mm insertion) |
| Lock | 6-32 × ~3/8 in machine screw in female pilot; forms thread in PETG by hand |
| Joints | Male rod: `knuckle2`. Female rod: `knuckle3`. |

There is no positive pullout catch; the set screw bears on the spigot. Intended
for occasional repositioning, not continuous adjustment under load.

**Print orientation.** Horizontal with finger faces on the bed. Support round
undersides and projecting fingers; clear the bore before assembly.

### 7.2 Dual-magnet foot (`dual_magnet_foot`)

| Feature | Spec |
|---|---|
| Plate | ~32 mm wide, 6 mm thick, reinforced joint roots |
| Magnet spacing | 56 mm centers |
| Magnets | Two rubber-coated mounting magnets, nominal Ø22 mm with M4 stud/thread (25–30 mm also fit; 32 mm max without overhang) |
| Mounting | Magnets under the flat base, magnetic face against steel; M4 washers (~12 mm OD) in top recesses with metal fasteners |
| Peel lip | Lever the foot off steel at the lip; do not pull on the rods |
| Knuckle | `knuckle3` on the top |

Reference magnet class: rubber-coated outdoor magnetic assembly base, ~8 lb
pull rating (manufacturer figure; not a validated arm working load). Confirm
stud length / thread depth before choosing nuts vs screws; do not crush the
4 mm of PETG under each washer recess.

**Print orientation.** Long side edge, finger faces parallel to the bed; brim
and support on projecting fingers. STEP is assembly orientation; use 3MF/STL
for print orientation.

### 7.3 Hardware for one assembled unit

- 2 × M5 × 25 bolts or thumbscrews and 2 × M5 nuts (end pivots)
- 1 × 6-32 machine screw, ~3/8 in (arm lock)
- 2 × rubber-coated magnets with M4 mounting
- 2 × M4 washers (~12 mm OD, ~1 mm thick) plus matching M4 nuts or screws

## 8. Future / deferred parts

The October design brief called for a board kit (`sled`, `front_cover`), a
universal rear shell with SD door, a screw clamp foot and a glued-in magnet
puck. Those are **not** in the 2026-11-04 kit. Keep them as optional follow-ons:

| Deferred part | Intent |
|---|---|
| Front cover + lens window | Optics protection, LED baffles, button pins |
| Edge sled | Alternate retention for boards without post fit |
| Screw clamp foot | Shelf / extrusion clamp with printed or steel screw |
| Embedded-magnet puck | Slide-in Ø10 discs glued behind a thin skin |
| Foot template | Minimal `knuckle3` plate for community feet |

If revived, they must mate with the same W-Mount contract in section 6 and must
not break the current housing knuckle geometry.

## 9. Assembly (minimum kit)

1. Test-fit housing and retainer; seat the PCB and retainer (section 5.3).
2. Fasten the magnets under the foot with washers and M4 hardware.
3. Join foot `knuckle3` to male-rod `knuckle2` with an M5 bolt and nut.
4. Form the 6-32 thread by hand in the empty female bore, back the screw out,
   insert the male spigot to full engagement, set roll, tighten lightly.
5. Join female-rod `knuckle3` to housing `knuckle2` with the second M5 bolt.
6. On vertical steel, orient the magnets to resist the arm's tipping moment.
   Remove by peeling at the foot lip.

Test the complete unit at full extension over a padded surface, including cable
pull. Check slipping, sag, latch seating and finger roots. Do not force binding
joints.

## 10. Acceptance checks

| # | Check | Pass criterion |
|---|---|---|
| 1 | Board fit | PCB seats on posts; retainer latches; USB-C plugs seat; antenna clears opening |
| 2 | Ribbon / headers | Camera flex and header pins clear the housing |
| 3 | W-Mount | Mates with printed arms/foot and a genuine GoPro buckle; holds tilt hand-tight |
| 4 | Arm lock | Spigot reaches min engagement; 6-32 holds roll without stripping on first sets |
| 5 | Magnet | Holds on vertical steel at full extension; peel lip works; PETG under washers uncrushed |
| 6 | Printability | Every kit part prints from the plate 3MF orientations in PETG at 0.2 mm |
| 7 | Wi-Fi | RSSI at 5 m is no worse than 6 dB below the bare board with the foot on steel |
| 8 | Checksums | `python source/verify_release.py` passes for the frozen exports |

## 11. Open questions

1. Confirm remaining TBV board dimensions from a physical board, especially
   lens position, USB-C centers and antenna envelope vs the retainer opening.
2. Validate long-term PETG creep on the arm lock and magnet washer stack.
3. Decide whether a front cover is worth adding for dust and lens protection
   without blocking Wi-Fi or overheating.
4. If a clamp foot returns, prefer M5 W-Mount compatibility over a one-off
   interface.
