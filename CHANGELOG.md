# Changelog

All notable changes to this project are documented here. Versions follow [Semantic Versioning](https://semver.org/).

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
