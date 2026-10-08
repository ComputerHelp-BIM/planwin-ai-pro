# Changelog

All notable changes to this project are documented here. Versions follow [Semantic Versioning](https://semver.org/).

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
