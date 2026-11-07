---
title: "Gizmonic Cambot: mechanical specification for CAD"
subtitle: "Case, board sled, W-Mount interface, screw clamp and magnet puck · for Astra"
author: "Gizmonic Cambot project · open hardware (CERN-OHL-P-2.0)"
date: "Spec rev A · October 2026"
---

## 1. Purpose and deliverables

Design a 3D-printable enclosure and modular mounting system for the Cambot
camera, a monitoring camera built on the FORIOT ESP32-S3-CAM board. Every part
must print on a hobby FDM printer without supports unless noted, and must be
fully parametric so the community can change it.

**Deliverables, per part:**

| Format | Purpose |
|---|---|
| Parametric source (OpenSCAD or CadQuery preferred; FreeCAD acceptable) | Community edits. Every parameter in this document is a named variable. |
| STEP (AP214 or AP242) | Interchange with other CAD |
| STL, plus 3MF with print orientation already set | Printing |
| PNG renders: isometric, exploded, and each part in print orientation | Docs and README |
| `PARTS.md` listing each file, its material, orientation and hardware | Assembly |

Units are millimeters. Tolerances not stated are ±0.2 mm on the printed part.

<div class="note">
<strong>Dimensions marked TBV (to be verified)</strong> come from vendor listings
or clone boards, not from calipers. Before freezing geometry, measure them on a
physical board. The Gizmonic Cambot team will supply calipered values and photos on
request. Every TBV value must be a top-level parameter.
</div>

## 2. System overview

The design has three tiers. Only the board kit changes when a different camera
board is supported.

| Tier | Parts | Changes per board? |
|---|---|---|
| Board kit | `sled`, `front_cover` | Yes |
| Universal body | `rear_shell`, `sd_door`, `button_pins` | No |
| Feet | `foot_clamp` (+ `clamp_screw`, `clamp_pad`, `clamp_nut`), `foot_magnet`, `foot_template` | No |

The board kit and rear shell meet at a fixed **shell interface** (section 5.4).
The shell and feet meet at the **W-Mount** (section 6). These two interfaces are
the stable contracts. Publish them as separate parametric modules so others can
design against them.

## 3. Reference board: FORIOT ESP32-S3-CAM

Orientation used throughout: hold the board component side toward you, with the
**antenna end at the top** and the **two USB-C ports on the bottom edge**.
"Front" is the component and camera side; "rear" is the side with the microSD
slot and header pins.

| Feature | Value | Status |
|---|---|---|
| PCB outline | 62.6 × 28.3 mm | Vendor figure, TBV |
| PCB thickness | 1.6 mm | TBV |
| Mounting holes | None. Retain by edges only. | TBV |
| Header rows | 2 × 20 pins, 2.54 mm pitch, rows 25.4 mm apart | Vendor figure |
| Header pins on rear (if soldered) | Up to 8.5 mm below the PCB rear face | Design for both soldered and unsoldered |
| Tallest front component, excluding the camera | ESP32-S3-WROOM-1 shield, ~3.2 mm | TBV |
| Antenna keep-out | Top ~6 mm of the PCB (module antenna) | TBV |
| USB-C ports | Two, on the bottom edge. Label TTL (CH340) and OTG. | Centers TBV |
| USB-C plug overmold clearance | 12.5 × 7.0 mm per plug, minimum | Typical cable |
| Buttons | RST and BOOT near the USB edge, front side | Positions TBV |
| LEDs | Power, TX, WS2812 RGB (GPIO 48), user LED (GPIO 2) | Positions TBV |
| microSD | Push-push slot on rear; card insertion edge | TBV |
| Camera | OV3660 module on a 24-pin 0.5 mm flex, lens on the front | Module size, lens Ø and flex length TBV |

**Camera module (TBV):** typical modules are about 8.5 × 8.5 mm with an
M8-class lens barrel about 8 mm in diameter. Wide-angle variants (120° and 160°)
of the OV3660 fit the same connector. The front window must work with lens
fields of view from 66° to 160° (section 5.2).

## 4. Design rules for all parts

| Rule | Value |
|---|---|
| Target printers | 0.4 mm nozzle, 0.2 mm layers, bed ≥ 180 × 180 mm |
| Materials | PETG (default) or ASA. PLA allowed for indoor prototypes. Optional TPU for pads. |
| Minimum wall | 1.6 mm (four perimeters at 0.4 mm) |
| Sliding fit | 0.20 mm clearance per side (`fit_slide`) |
| Snug/press fit | 0.10 mm per side (`fit_press`) |
| Printed thread radial clearance | 0.35 mm (`fit_thread`) |
| Overhangs | ≤ 45° without supports; bridges ≤ 15 mm |
| Screws | M2 self-tapping into Ø1.7 pilot holes, or M2 heat-set inserts (Ø3.2 pocket) as an option (`use_inserts`) |
| Edge treatment | 0.6 mm chamfer on outer edges; 1 mm fillets on hand-contact edges |
| Text | Debossed 0.4 mm, ≥ 4 mm tall: "CAMBOT", "TTL", "OTG", "SD" |
| Metal near antenna | No inserts, nuts or magnets within 15 mm of the antenna end |

## 5. Body

### 5.1 Overall envelope

The interior is sized to accept the reference board and two planned future
boards: the AI-Thinker ESP32-CAM (27 × 40.5 mm) and the Seeed XIAO ESP32S3
Sense (21 × 17.5 mm). Future sleds must fit without changing the rear shell.

| Parameter | Default | Notes |
|---|---|---|
| `cavity_w` × `cavity_h` | 32 × 67 | Inside the shell, PCB plane |
| `front_clear` | 9.0 | PCB front face to inside of front cover, covers camera stack |
| `rear_clear` | 9.5 | PCB rear face to inside of rear shell, covers header pins plus SD |
| `wall` | 2.0 | Shell and cover walls |
| Approx. outside size | 36 × 71 × 25 | Width × height × depth, before the knuckle |

`rear_clear` may be reduced to 4.5 in a "no headers" variant for users who
desolder or never fit the header pins. Expose it as a parameter and ship both
STLs.

### 5.2 Front cover (board kit)

- **Lens window.** A round aperture centered on the lens axis (`lens_x`,
  `lens_y`, TBV). The lens front sits no more than `lens_recess` = 1.0 mm behind
  the cover's outer face. Aperture diameter =
  `lens_od + 2 × lens_recess × tan(fov_half) + 0.6`, with `fov_half` = 80°
  by default so 160° lenses do not vignette. Add a 45° outward chamfer.
- **Camera retention.** A square pocket on the inside, sized to the camera module
  plus `fit_slide`, holds the module square to the window. A printed spring
  finger or a 1 mm foam pad behind the module presses it forward. The lens must
  not touch the window edge.
- **Flex routing.** A channel from the camera pocket to the board's FPC
  connector, minimum bend radius 1.5 mm, with no sharp edges along the path.
  The flex must not be creased.
- **Light pipes.** Optional 2 mm holes over the power LED and the WS2812
  (`led_pipe = true`). Keep them at least 15 mm from the lens axis and add a
  0.8 mm baffle ring so LED light cannot leak into the lens.
- **Button access.** Ø2.5 mm holes over RST and BOOT for a paperclip, or
  captive printed push pins (`button_pins` part) with a 0.4 mm flexure
  retention lip.
- **Closure.** Mates to the rear shell through the shell interface (5.4).

### 5.3 Sled (board kit)

- Holds the PCB by its long edges. Two rails with slots `pcb_t + 0.15` wide and
  1.2 mm deep. The board slides in from the USB end toward an end stop at the
  antenna end.
- A flexing detent tab (0.8 mm thick, 6 mm long) clicks over the USB edge of the
  PCB to stop it sliding back out.
- The sled keeps the PCB centered in the cavity so the USB-C ports line up with
  the rear-shell openings to ±0.3 mm. Register it to the shell with two
  locating pins (Ø2.0) at fixed positions defined by the shell interface.
- Underside cutouts leave clear access to the SD slot and to header pins.
- Thermal: no material within 1 mm of the module shield's top face; leave an
  air gap to the vents.

### 5.4 Shell interface (fixed contract)

This is the boundary between the board kit and the universal rear shell. It must
stay fixed so any future board kit fits any rear shell.

| Feature | Spec |
|---|---|
| Joint | Tongue on the front cover (1.2 wide × 1.5 tall), groove on the rear shell (`1.2 + 2 × fit_slide`) around the full perimeter |
| Fasteners | 2 × M2 × 8 screws through the front cover into rear-shell bosses, on the long sides, at least 15 mm from the antenna end |
| Sled locating | 2 × Ø2.0 pins on the rear-shell floor; sled holes Ø2.0 + `fit_press` |
| Datum | Interior cavity center, PCB plane at `rear_clear` above the rear-shell floor |

### 5.5 Rear shell (universal)

- **USB-C openings.** Two openings on the bottom face, each sized to the plug
  overmold (12.5 × 7.0 mm plus `fit_slide`), centered on the port positions.
  Deboss "TTL" and "OTG" beside them. If the two port centers are too close
  for two full overmold openings, use one combined slot and note it.
- **Cable strain relief.** A zip-tie bridge (4 × 2 mm slot pair) on the bottom
  face beside the USB openings.
- **SD door.** A sliding or snap door (`sd_door` part) over the card slot. The
  card must be insertable and ejectable (push-push) with the case closed and
  the door open.
- **Vents.** Slots 1.2 mm wide on both long sides, alongside the ESP32 module
  and away from the lens. Total open area ≥ 60 mm². No direct line of sight
  from a vent to the camera's inside face, to keep out stray light.
- **W-Mount knuckle.** A two-finger knuckle on the rear face (section 6),
  centered horizontally and placed 22 mm from the USB end (`knuckle_y`). The
  tilt axis is parallel to the short edge, so the camera tilts up and down.
- **Antenna end.** Keep the top 15 mm of the shell free of metal and of solid
  walls thicker than `wall`.

## 6. W-Mount interface (fixed contract)

The W-Mount follows the de facto GoPro finger mount, so off-the-shelf GoPro
accessories work. **Astra must check the final parameters against a genuine
GoPro buckle and adjust them until both mate with a firm, wobble-free fit.**

| Parameter | Default | Notes |
|---|---|---|
| `finger_t` | 3.0 | Finger thickness |
| `finger_gap` | 3.2 | Gap between fingers |
| `finger_r` | 7.5 | Outer radius around the pivot |
| `finger_reach` | 9.0 | Pivot center to base surface |
| `bolt_d` | 5.3 | Clearance for M5 |
| Camera side (`knuckle2`) | 2 fingers | Total width 9.2 |
| Foot side (`knuckle3`) | 3 fingers | Total width 15.4. One outer finger has a captive M5 hex-nut pocket, 8.1 mm across flats × 4.0 deep. |
| Bolt | M5 × 25 thumbscrew or socket-head bolt | Printed knob cap `m5_knob` for socket heads |

Print fingers so the layers stack across the finger thickness (finger faces
parallel to the bed). This avoids layer splits at the pivot. Fillet the finger
roots with r = 1.5.

Publish `knuckle2()` and `knuckle3()` as standalone modules in their own file
(`w_mount.scad` or `w_mount.py`), with a doc comment showing how to attach them
to a custom base.

## 7. Feet

### 7.1 Screw clamp (`foot_clamp`)

A C-clamp for shelf edges, robot frames, 2020/3030 extrusion, bench tops and
tubes.

| Parameter | Default | Notes |
|---|---|---|
| `jaw_open` | 35 | Maximum opening; 0 is the minimum |
| `throat` | 25 | Depth from jaw edge to the frame's back |
| `frame_section` | 10 × 14 | Cross-section of the C frame. Stiff enough in PETG for hand tightening. |
| Thread | Tr14 × 3 trapezoidal, printed | `fit_thread` clearance |
| Screw length | `jaw_open + 20` | |
| Knob | Ø30 knurled, or a 40 mm T-bar (`knob_style`) | |
| Pad | Ø20 swivel pad, snap-fit ball (Ø8) into a socket on the screw tip | TPU or PETG face, 1 mm grip ridges |
| Fixed jaw | 20 × 14 face with 1 mm ridges; optional TPU overlay | |
| Knuckle | `knuckle3` on the back of the frame, axis parallel to the jaw faces by default | `knuckle_axis` = 0° or 90° |

**Print orientation.** Print the C frame lying flat, with the C shape in the XY
plane, so bending loads run along the layers. The internal thread would then
print sideways, so the frame instead has a hex pocket that accepts a separate
printed **`clamp_nut`**, printed upright for clean threads and pressed in
(`fit_press`). Provide a variant pocket that accepts a steel M8 nut, with an
M8 bolt in place of the printed screw (`steel_screw = true`). Print the screw
upright.

**Load check.** The clamp must hold a camera (under 60 g) cantilevered 60 mm
from the jaw under hand tightening without slipping, and survive 20 tighten and
loosen cycles without the thread wearing out.

### 7.2 Magnet puck (`foot_magnet`)

A puck that sticks to steel enclosures, machine frames and steel plates. Magnets
slide in from the side and are glued in place.

| Parameter | Default | Notes |
|---|---|---|
| `puck_d` × `puck_h` | 40 × 9 | |
| `magnet_shape` | `disc` | `disc` or `block` |
| Disc magnets | 4 × Ø10 × 3 mm N52 | Pockets `Ø10.2 × 3.15` |
| Block magnets (alt.) | 2 × 20 × 10 × 3 mm N52 | Pockets `20.2 × 10.2 × 3.15` |
| `skin` | 0.4 | Plastic between the magnet and the contact face. 0 leaves the magnet face flush and exposed. |
| Pocket entry | Each pocket opens to the puck's outer edge through a slot of the same section, so a magnet slides in radially | |
| Retention | A 0.15 mm detent bump at the slot mouth, plus a Ø1.8 glue port from the top face into each pocket for CA or epoxy | |
| Contact face | Optional 0.8 mm recess for a 40 mm self-adhesive silicone or TPU anti-slip disc | `grip_recess` |
| Knuckle | `knuckle3` on the top face, centered | |
| Pry notch | 6 × 2 mm notch on the rim to lever the puck off steel | |

**Print orientation.** Print the contact face down on the bed so the skin is
smooth and flat. Pocket ceilings are bridges of 10–20 mm, which is acceptable.

**Notes for the user guide.** Magnet polarity does not matter on thick steel.
Alternating N/S between pockets grips thin sheet better. Four Ø10 × 3 N52
discs behind a 0.4 mm skin hold the camera on a vertical steel face with a
large safety margin. Keep the puck at least 15 mm from the camera's antenna end.

### 7.3 Foot template (`foot_template`)

A minimal example of a custom foot: a 30 × 30 × 4 base plate with `knuckle3`,
two M3 through holes and a VHB tape recess. Include a short comment block that
explains how to attach `knuckle3()` to any geometry. This is the starting
point for community feet such as zip-tie, 2020 T-slot, 1/4"-20 tripod adapter
and suction cup.

## 8. Assembly (for `PARTS.md`)

1. Insert the microSD card into the board.
2. Seat the camera module in the front-cover pocket and route the flex along
   its channel.
3. Slide the PCB into the sled until the detent clicks.
4. Drop the sled onto the rear-shell locating pins. Check the USB-C ports line
   up with their openings.
5. Close the front cover onto the rear shell and fit 2 × M2 × 8 screws.
6. Press the nut into the foot's `knuckle3`, join it to the camera's
   `knuckle2`, and fit the M5 bolt.

**Hardware list:** 2 × M2 × 8 self-tapping screws; 1 × M5 × 25 bolt or
thumbscrew; 1 × M5 nut; magnets per 7.2; optional M8 × 60 bolt and nut for the
steel clamp variant; optional TPU pads.

## 9. Acceptance checks

| # | Check | Pass criterion |
|---|---|---|
| 1 | Board fit | Board slides in and out by hand. No PCB flex. USB-C plugs seat fully in both ports. |
| 2 | Optics | No vignetting at 160° FOV, no LED glare in a dark-room capture, lens square to the window within 2° |
| 3 | SD access | Card inserts and ejects with the case closed and the door open |
| 4 | Buttons | RST and BOOT can be pressed with the case closed |
| 5 | Thermal | After 2 hours recording at 22 °C ambient, shell surface stays under 45 °C and the camera keeps running |
| 6 | W-Mount | Mates with a genuine GoPro buckle and with the printed feet; holds a set tilt with the bolt hand-tight |
| 7 | Clamp | Load check in 7.1 |
| 8 | Magnet | Holds on a vertical steel plate at the 60 mm cantilever; a 0.4 mm skin survives 50 attach and detach cycles |
| 9 | Printability | Every part prints without supports in PETG at 0.2 mm, in the 3MF orientation |
| 10 | Wi-Fi | RSSI at 5 m is no worse than 6 dB below the bare board, measured with the magnet puck on a steel plate |

## 10. Open questions for Astra

1. Confirm the TBV dimensions in section 3 from a physical board, especially the
   lens position, USB-C centers, button and LED positions, and SD insertion
   edge.
2. If a camera flex is too short to put the lens where the front cover needs it,
   propose the lens position and we will standardize on it.
3. Advise whether OpenSCAD or CadQuery gives the community the better
   parametric source for these shapes.
