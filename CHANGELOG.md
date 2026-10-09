# Changelog

All notable changes to this project are documented here. Versions follow [Semantic Versioning](https://semver.org/).

## [1.2.0] – 2026-10-09
Interface release: a cleaner, modern look and two new Excel exports. Projects and results are
unchanged from 1.1.0.

### Added
- **Start page** on launch: describe a building to create it with AI, blank / open / import,
  recent projects, and the template gallery with a plan thumbnail of each building.
  (View ▸ Start page brings it back; "Show this page at startup" turns it off.)
- **Command search** (Ctrl+Q) on the title bar: type what you want to do ("stair", "export staad",
  "drift") and press Enter – finds every ribbon command, results table and plan.
- **BOQ & cost estimate (Excel):** standard BOQ layout (item, description, unit, quantity, rate,
  amount) with live formulas – edit a rate on the *Rates* sheet and every amount and total updates.
  Also by floor, by member type and revision comparison.
- **Member schedules (Excel):** column schedule (by storey range) and column list, beam schedule
  (identical beams grouped), footing schedule (types F1, F2 …), combined footings, slab and wall schedules.
- **Results navigator:** results grouped as Plan / Analysis / Design / Code checks / Quantities with
  failure badges (✖ n) and ticks, a filter box, *Copy* (paste into Excel) and *Excel* (this table or
  all tables) buttons.
- One-line **hover help** on every ribbon command, panel field, table and button.

### Changed
- **Modern Fluent look** in light and dark: layered surfaces, rounded cards, thin scrollbars,
  consistent buttons, inputs, tables, menus and tooltips; ribbon toggles show as soft pills.
- **Project and Properties panels** are scrolling, collapsible sections (open/closed state is
  remembered); the plan list and levels table show their full contents; Enter applies a property,
  *Revert* discards edits.
- **Plan labels no longer overlap:** column labels sit in the free corner on a small pill, beam
  labels shorten or hide when there is no room, slab text only shows when it fits.
- Ribbon: captions for the snap-step and units boxes; the Results panel opens at a quarter of the
  window height so the plan stays the main view.
- AI assistant and command line: "export boq", "cost estimate", "column schedule", "export schedules" …

### Fixed
- Properties panel showed a large black box on Windows (system dark palette leaking through).
- Plan names were cut off in the levels table ("Typica", "Mumty"), and the plan list showed two rows.
- Results tabs overflowed behind scroll arrows.
- Menus could show black corners on Windows.

## [1.1.0] – 2026-10-08
Feature release. Projects saved by 1.0.x open unchanged: they keep the equivalent static method
and no diaphragm until you switch them on (Frame ▸ Seismic), so their results do not move.

### Added – analysis and design
- **Response spectrum analysis** (IS 1893-1:2016 cl 7.7): modal analysis with condensed storey
  masses, CQC combination, base shear scaled up to the static V_B (cl 7.7.3), RS member forces,
  reactions, displacements and drifts in the ±RSX/±RSY load combinations. Method *Auto* uses the
  dynamic method wherever cl 7.7.1 requires it.
- **Irregularity checks** (Tables 5 and 6): torsional, re-entrant corners, diaphragm openings,
  out-of-plane offsets, non-parallel systems, soft storey, mass, vertical geometry, in-plane
  discontinuity and floating columns, with the clause consequence of each.
- **Rigid floor diaphragms:** master–slave constraint at the centre of mass of each floor; storey
  forces and accidental torsion are applied at the master node.
- **RC shear walls:** drawn in the plan, modelled as wide columns with rigid links, designed to
  IS 456 cl 32 / IS 13920 cl 9 (P–M interaction, shear, boundary elements); exported to STAAD and ETABS.
- **IS 13920:2016 ductile detailing:** beam steel limits, bottom ≥ ½ top, capacity-design shear,
  end-zone links, column confinement (l0, hoop spacing), strong-column–weak-beam, minimum sizes.
  *Optimise sizes* now also fixes ductile failures.
- **Beam torsion design** (IS 456 cl 41): equivalent shear and moment, longitudinal and
  transverse steel, side-face reinforcement.
- **Combined footings** wherever isolated footings overlap: rectangular footing centred on the
  load resultant, longitudinal BM/SF, punching and one-way shear.
- **Staircase wizard** (waist slab design, loads on the support beams) and **overhead water-tank
  wizard** (tank and water weight on the supporting columns). Both re-apply without double counting.

### Added – documents and quantities
- **Bar bending schedule** (Excel) for beams, columns, footings and slabs.
- **Reinforcement detail drawings** (DXF): beam elevations and sections, column and wall schedule,
  footing details.
- **Design calculation sheets** (PDF), step by step with clause references, for all members or
  only the members selected on the plan.
- **BOQ by floor and by member type**, saved **revisions** and revision comparison.
- Excel and PDF reports gain seismic method, modal/RS, irregularity, IS 13920, walls, combined
  footings and BOQ-by-floor sections.

### Added – application
- **Ribbon interface** (Home · Plan · Loads · Frame · Design · Output · View · Help) with a File
  menu and quick-access bar; double-click a tab to collapse it.
- **Plan editor:** shear-wall tool (`W`), area/perimeter tool (`A`), dimensions (`Shift+D`),
  grid lines with bubbles (editor or *generate from columns*), snapping to ends, midpoints and
  grid intersections, ortho mode (`F8`, or hold Shift), *Copy floor* (duplicate a plan, add storeys).
- **Display units:** kN or tonnes (t, t·m) for tables, properties and reports; models and files stay in kN.
- 3-D view draws walls as panels and marks each floor's centre of mass.
- New results tabs: Walls, Combined footings, IS 13920, Irregularity, Modal / RS, BOQ by floor.
- AI assistant and command line understand the new features (seismic method, diaphragm, walls,
  stairs, tanks, grids, revisions, BOQ, units, every export). New `cli boq` command and
  `cli run --method / --diaphragm / --units`.

### Changed
- **Licensing hardened:** the offline trial is tamper-resistant (signed records in several
  places; clock rollback cannot extend it), and licences can be bound to a machine code shown
  in Help ▸ Licence (`keygen issue --machine`). Existing licences keep working.
- Code reorganised: `design/is456/` package, report writers split, one export registry used by
  the GUI, AI and CLI, main window split into command modules, dialogs package.
- Project schema 2 (walls, grids, dimensions, stairs, tanks, seismic method and diaphragm).
- STAAD/ETABS exports carry walls, rigid links, diaphragms and joint moments.

### Fixed
- Results tables showed blank cells for some numbers (e.g. modal periods), and the "BOQ & cost"
  tab title lost its "&".
- Part-load descriptions were replaced with "W" when a beam was edited in the Properties panel.

## [1.0.1] – 2026-10-08
Bug-fix release. Every fix has a regression test that fails on 1.0.0.

### Fixed – design safety
- **Footings:** structural design (flexure, punching, one-way shear) now uses the factored
  reactions of every ultimate combination. 1.0.0 scaled the service cases by 1.2 and so missed
  1.5(DL±EL) and 0.9DL±1.5EL, under-estimating the design pressure by up to ~20 %.
- **Columns in net tension:** the tension check used the moment from the other end of the column
  (both ends share a combination name), which could under-design steel (0.8 % instead of 1.2 %).

### Fixed – design accuracy
- **Columns:** the minimum eccentricity (IS 456 cl 25.4) is now applied about one axis at a time
  for biaxial bending, as the code allows; 1.0.0 applied it about both axes together (over-design).
- **Seismic:** the building dimension used for the period Ta = 0.09h/√d and for accidental torsion
  came from *all* plans, including spare plans not used by any level. Only plans assigned to levels
  are used now, and accidental torsion uses the plan dimension of each level (cl 7.8.2).
- **BOQ:** beam concrete and formwork no longer double-count the slab depth and the beam–column
  joint (≈ 7 % less concrete on the G+4 template); footing steel used the wrong bar length.

### Fixed – application
- Multi-select "Set Value" overwrote **every** property of all selected objects with the first
  object's values. Only the fields you change are applied now; fields that differ are marked `*`.
- An AI reply without actions deleted the user's last undo step. AI changes are now exactly one
  undo step, and replies that change nothing leave undo/redo untouched.
- Design result tables kept showing out-of-date results after the model was edited.
- "Modify building" (AI) kept the old slab thickness and beam sizes when bays changed (e.g. a
  125 mm slab for 6.5 m spans), and kept the importance factor when the occupancy changed.
- AI actions treated the text `"false"` as true (e.g. "disable seismic" enabled it); invalid soil
  types are now rejected.
- Exports failed for project names containing `/ : * ? " < > |` and the PDF title silently
  dropped text in `< >`; file names are now sanitised (including Windows reserved names).
- Crash recovery could offer a stale autosave from an older, already-saved session.
- The AI worker thread read the project while the user kept editing (possible crash).
- City lookup: deterministic matching; a one- or two-letter entry no longer picks a random city.
- Command line: missing files and unknown templates print an error instead of a traceback.

### Changed
- Template library rebuilt; generated projects store the user's input spec (`grid_spec`) instead
  of the auto-sized one. Projects saved by 1.0.0 still open and modify correctly.
- Test that every version number (package, pyproject, exe resource, installer, changelog) agrees.

## [1.0.0] – 2026-10-08
### Added
- First release of PlanWin AI Pro, a Python rewrite of PlanWin / FrameWin.
- **PlanWin:**
  - plan editor
  - slab/beam/column tools, auto columns and auto beams
  - load take-down with equilibrium check
  - BM/SF diagrams
  - legacy `.plw` import
- **FrameWin:**
  - level stacking and floating columns
  - IS 1893-1:2016 seismic: equivalent static, cracked sections, accidental torsion
  - IS 875-3:2015 wind
  - built-in 3-D frame solver
- **Design (IS 456):**
  - biaxial columns (strain compatibility)
  - beams with shear and deflection checks
  - isolated footings and slabs
  - storey drift check
  - BOQ and cost estimate
  - size optimiser
- **Exports:** STAAD `.std`, ETABS `.e2k`, DXF 2-D/3-D, Excel and PDF.
- **AI:**
  - side-panel assistant (offline engine, Claude, OpenAI, Ollama)
  - 10 pre-optimised templates
- **Product:**
  - Ed25519 licensing with a 30-day trial
  - autosave and crash recovery, undo/redo
  - dark theme
  - CLI
  - Windows installer and portable exe built by GitHub Actions
