# Cambot minimum PETG test-print kit

Release: 2026-11-04. Seven printed pieces, providing one complete camera mount with interchangeable arm lengths. No front cover, clamp, old retainer, or embedded-magnet puck is included.

## Print

Open the three files in `plates/`, select your PETG profile, and configure supports. Each layout fits within 180 × 180 mm before adding brim/support margins. Alternatively use individual files in `3mf/`. Files contain millimeter geometry and orientation, not printer settings or G-code.

|Plate|Parts|Quantity|
|---|---|---|
|01_case_and_short_retainer|housing_v3; retainer_v3_short|1 each|
|02_arms_25_and_50|rod_male_25; rod_female_25_6-32; rod_male_50; rod_female_50_6-32|1 each|
|03_dual_magnet_foot|dual_magnet_foot|1|

Only five pieces are used in a single assembly: case, retainer, one male rod, one female rod, and foot. The other two rods are alternative lengths. For the fewest prints, choose male 25 + female 50 and omit the other two rods; this gives approximately 117–121 mm (4.6–4.8 in) between pivots.

|Male + female shaft names|Approximate pivot-to-pivot distance|
|---|---|
|25 + 25|92–96 mm / 3.6–3.8 in|
|25 + 50 (or 50 + 25)|117–121 mm / 4.6–4.8 in|
|50 + 50|142–146 mm / 5.6–5.7 in|

These are pivot distances, not steel-to-lens reach. Camera position also depends on joint angles and magnet thickness.

## Current revisions

- Case: v3 drop-in body, two center posts, header clearance, no cover. This is the housing reported to fit successfully.
- Retainer: 4.5 mm thick, latest shortened bridge (7.3 mm removed). Bridge inner edge is 0.5 mm past the case end. The 24 mm antenna opening accommodates the reported 17.7 mm width; longitudinal clearance still needs a test fit.
- Arms: existing revision A geometry, 25 and 50 mm shafts. 12 mm spigot, 12.4 mm bore, minimum 20 mm engagement. No change to the sliding fit or direct-thread locking pilot in this bundle.
- Foot: latest dual external-magnet base with peel lip, 56 mm magnet spacing, 32 mm width, 6 mm plate, reinforced joint roots. Magnets mount UNDER the flat base, directly against steel. Top recesses are for fasteners and washers.

## Bill of materials (one assembled unit)

Printed parts (from this kit) plus fasteners and magnets:

| Qty | Part | Purpose |
|---:|---|---|
| 1 | `housing_v3` | Camera case |
| 1 | `retainer_v3_short` | Holds the PCB on the case posts |
| 1 | Male rod (`rod_male_25` or `rod_male_50`) | Arm half toward the foot |
| 1 | Female rod (`rod_female_25_6-32` or `rod_female_50_6-32`) | Arm half toward the case |
| 1 | `dual_magnet_foot` | Steel-surface base |
| 2 | M5 × 25 mm bolts or thumbscrews | End pivots (foot↔male rod, female rod↔case) |
| 2 | M5 nuts | Pair with the M5 pivots; verify bolt reach |
| 1 | 6-32 machine screw, ~3/8 in (≈10 mm) | Locks arm length in the female-rod pilot |
| 2 | Rubber-coated mounting magnets, Ø ~22 mm, M4 | Mount **under** the foot; magnetic faces to steel |
| 2 | M4 washers, ~12 mm OD × ~1 mm thick | Sit in the top recesses over each magnet stud |
| 2 | M4 nuts or screws | Match magnet stud/thread gender |

**Magnets.** Nominal 22 mm diameter with an M4 mounting point. 25–30 mm
alternatives also fit the flat underside; 32 mm is the maximum without side
overhang. The mounting back must be flat. Reference part: [Radial Magnet
22 mm rubber-coated outdoor assembly, SKU 9763](https://radialmagnet.com/our-magnets/22mm-rubber-coated-outdoor-magnetic-assembly-base-holds-up-to-8-lbs/)
(manufacturer lists 22 mm, M4, 8.1 lb pull). Exact thread gender, depth, and
stud projection were not established for every vendor. Pull ratings do not
establish the assembled arm’s working load.

**Arm lock screw.** Start the 6-32 by hand in the 2.8 mm PETG pilot. Do not
force it. No printed clamp screw or nut is used.

**Magnet fasteners.** There is 4 mm of PETG below each washer recess. Confirm
stud length or thread depth before choosing hardware; do not bottom out a
screw or crush the PETG.

## PETG printing

Starting point: 0.4 mm nozzle, 0.2 mm layers, 5 walls, 6 top/bottom layers, 40–50% infill. Use your proven filament temperatures. Finer layers alone will not correct an interfering fit.

- Case: supplied on its side. Support tray and mounting overhangs; clean the latch windows.
- Retainer: supplied flat. Preserve the small latch clearances; avoid support in those gaps.
- Arms: supplied horizontally with finger faces parallel to the bed. Support round undersides and projecting fingers; inspect the socket bore and remove residue before assembly. Avoid inaccessible support trapped in the bore.
- Foot: supplied on its long side edge, finger faces parallel to the bed. Add a brim and support projecting fingers. Its STEP file is in assembly orientation; use 3MF/STL for the intended print orientation.

Inspect all plates in the slicer, especially brim/support margins on a 180 mm bed. PETG settings are guidance, not a validated strength specification.

## Assembly

1. Clean and test-fit the empty case and retainer. Drop the PCB onto the two center posts, then seat the shortened retainer. Check antenna, header, camera ribbon, and USB clearance. Release latches outward, not inward.
2. Attach the magnets under the foot with metal fasteners and washers. Magnetic faces must remain exposed to steel.
3. Join the foot's three fingers to the male rod's two fingers with an M5 bolt/nut.
4. With the socket empty, carefully form the locking screw thread by hand. Back the screw out of the bore, then slide in the male spigot. The minimum-engagement groove must be completely hidden (20–24 mm insertion).
5. Set roll angle and tighten the 6-32 screw lightly. It bears on the spigot; there is no positive pullout catch. The direct plastic thread is intended for occasional repositioning.
6. Join the female rod's three fingers to the case's two fingers with the other M5 bolt/nut.
7. On a vertical steel surface, place the magnets vertically to resist the outward arm's gravity-induced tipping moment. Remove by peeling at the foot lip, not pulling on the rods or camera.

Test the complete unit at full extension over a padded surface, including cable pull. Check slipping, sag, latch seating and finger roots. The adjusted retainer, PETG arm tolerance, fasteners, long-term creep, and magnet holding strength require physical validation. Do not force binding joints.

## Repository contents and reproduction

`3mf/`, `stl/`, `step/`: only the seven selected parts. `plates/`: three grouped print layouts. `source/`: original editable generators for housing, arms and foot, plus dependency pins. `checks/`: provenance reports and fresh package validation. `manifest.json`: artifact provenance and SHA-256 checksums.

Install `source/requirements.txt` in a Python virtual environment. Run each generator independently:

    python source/housing/source/build.py
    python source/arms/source/build.py
    python source/magnet-foot/source/build.py

They write outputs beside their respective source folders. Housing and arms read their local parameters.json. The foot currently defines dimensions directly in build.py and writes parameters.json as a record; edit its geometry there. Arm regeneration also produces optional fit coupons, but those are excluded from the minimum print bundle. Copy desired regenerated parts to the top-level export folders and recreate plates if you change dimensions; the top-level exports are a frozen release.

Run `python source/verify_release.py` to check this release's printable files. No CAD regeneration or physical tests are implied by these checks.

See LICENSE.md for the inherited design license and provenance.
