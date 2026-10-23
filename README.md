# AET India v0.3 — PDF + DXF Geometry Audit Prototype

## What this prototype does
- Upload multiple architectural and structural PDF/DXF files.
- Parse selectable PDF text and ASCII DXF model-space entities.
- Report DXF `$INSUNITS`, model-space entity counts, layer counts and paper-space layout names.
- Audit `DIMENSION` entities stored inside DXF block definitions (important because many CAD drawings keep dimensions and geometry in blocks).
- Inventory block definitions and top-level model-space block insert counts.
- Compare a user-entered known dimension with the closest stored DXF dimension value.
- Extract candidate dimensions from text and closed model-space polylines for human review.
- Generate a clearly-labelled preliminary BOQ scaffold and export Excel/CSV.

## Important limits
- This is a development prototype, not an approved quantity-surveying tool.
- Dimension matching compares stored DXF dimension values. It does not certify whole-drawing scale or prove that a dimension belongs to the intended building element.
- The current candidate geometry extraction does not yet transform nested block geometry into a unified building coordinate model. It must not be treated as complete geometry extraction.
- Paper-space viewports/layouts are inventoried but not interpreted as independent floor plans.
- Floor classification, wall/opening detection, structural member classification, direct DWG reading, scanned-PDF OCR, and fully automated RCC/rebar/earthwork takeoff are not implemented.
- Structural quantities stay unavailable unless adequate dimensions, levels, sections, material grades and reinforcement details are verified. No structural assumptions are silently invented.
- Default rates are illustrative demo values, not live Solapur rates or an approved DSR.

## Run locally
1. Install Python 3.10+.
2. Extract the ZIP.
3. Open a terminal in the extracted folder.
4. Run:

   ```bash
   python -m pip install -r requirements.txt
   python -m streamlit run app.py
   ```
5. Open the local URL printed in the terminal (normally `http://localhost:8501`). Keep the terminal running while using the app.

## DXF preparation
In AutoCAD, use **Save As** and choose an ASCII DXF version if available. Keep the original DWG unchanged. Confirm `$INSUNITS` and compare at least two known dimensions from different directions before relying on extracted geometry.

## First validation files
The project was inspected against `KR_ARC_FLOOR PLANS FINAL.dxf` and `KR_ARC_BEAM LAYOUTS.dxf`.
- Architectural file: `$INSUNITS=4` (millimetres), 1,815 model-space entities, layouts `KR_01`–`KR_06` and `KR_MP_01`–`KR_MP_03`, and 111 dimension entities found across block definitions.
- The supplied 35,038 mm reference occurs as a stored dimension value three times in architectural blocks. This is a dimension-data match, not a full drawing scale certification.
- Beam-layout file: `$INSUNITS=4` (millimetres), 7 model-space entities and 148 dimension entities in block definitions. This demonstrates why block-level inspection matters.

## Next development step
Build a transformed nested-block geometry model, then validate known distances and selected beam lengths against manual measurements before enabling more takeoff categories.


### v0.3.1 note
This release fixes DXF parsing by using ezdxf's file-level reader on a temporary file. See `VALIDATION_NOTES.md`. Parsing success is not quantity validation.
