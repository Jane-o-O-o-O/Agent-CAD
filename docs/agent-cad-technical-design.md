# Agent-CAD Technical Design

## Scope

Agent-CAD will specialize the current general-purpose AI Agent system into a mechanical 2D CAD assistant. The first product slice focuses on generating, editing, previewing, validating, and exporting simple mechanical 2D drawings from conversational and file-based inputs.

The initial CAD domain is mechanical 2D only:

- mounting plates
- flanges
- simple brackets
- hole patterns
- slots
- fillets
- chamfers
- centerlines
- dimensions and notes
- DXF export

Out of scope for the first slice:

- building floor plans
- native DWG export
- 3D solids
- assemblies
- STEP/STL export
- full manufacturing standards compliance

## Product Experience

The target UI is a two-pane workflow:

- left pane: chat and task history
- right pane: live 2D CAD canvas

The CAD canvas should show the current `MechanicalCADDocument` state, not a static generated image. Every Agent operation should update the document state, produce a renderable event, and preserve enough history for undo/versioning later.

Example user input:

```text
Draw a 120x80x10 mm mounting plate, R5 rounded corners, four M6 countersunk holes, hole centers 12 mm from the edges.
```

Expected system behavior:

1. Extract mechanical requirements.
2. Normalize ambiguous wording into a `MechanicalDesignBrief`.
3. Generate CAD operations.
4. Apply operations to a `MechanicalCADDocument`.
5. Render the updated document on the right.
6. Export DXF on request.

## High-Level Architecture

```text
Raw user input
  -> CAD Intake Pipeline
  -> MechanicalDesignBrief
  -> Mechanical CAD Planner
  -> CAD Operation Tools
  -> MechanicalCADDocument
  -> CAD Preview Renderer
  -> DXF / SVG / PDF / PNG export
```

The existing platform remains useful as the base:

- session lifecycle
- file uploads
- GridFS storage
- SSE event streaming
- sandbox execution
- tool calling
- chat UI

The general Agent flow should become CAD-specific over time. The immediate implementation starts with a standalone CAD module so the core document and export APIs can stabilize before deep Agent rewiring.

## Core Data Models

### MechanicalDesignBrief

`MechanicalDesignBrief` is the normalized result of input understanding. It captures the intended part, units, features, constraints, unknowns, and source references.

```json
{
  "part_type": "mounting_plate",
  "units": "mm",
  "features": [
    {
      "type": "base_plate",
      "width": 120,
      "height": 80,
      "thickness": 10,
      "corner_radius": 5
    },
    {
      "type": "hole_pattern",
      "count": 4,
      "hole_type": "countersunk",
      "thread": "M6",
      "edge_offset": 12
    }
  ],
  "constraints": [
    "hole pattern is symmetric about the part center"
  ],
  "unknowns": [],
  "manufacturing_notes": []
}
```

### MechanicalCADDocument

`MechanicalCADDocument` is the authoritative CAD state used by the renderer and exporters. It is not a DXF file. DXF is an output format.

```json
{
  "id": "cad_doc_id",
  "session_id": "session_id",
  "title": "Mounting plate",
  "units": "mm",
  "layers": [],
  "entities": [],
  "dimensions": [],
  "constraints": [],
  "version": 1
}
```

### CADOperation

Agents should modify CAD state through controlled operations instead of directly writing DXF.

```json
{
  "operation": "add_hole",
  "params": {
    "center": [12, 12],
    "diameter": 6.5,
    "hole_type": "through",
    "layer": "M-HOLE"
  }
}
```

Initial operations:

- `create_plate`
- `add_circle`
- `add_hole`
- `add_slot`
- `add_centerline`
- `add_dimension`
- `add_note`
- `delete_entity`
- `export_dxf`

## Specialized Skills / Modules

### Mechanical Intake

Extracts mechanical requirements from raw user input and attachments. It should identify part type, dimensions, units, features, constraints, manufacturing notes, and missing information.

### Mechanical Vision

Understands sketches, screenshots, scanned drawings, and reference images. It should extract contours, holes, slots, centerlines, dimensions, symbols, and visible technical notes.

### Mechanical Drawing Parser

Parses PDF, DOCX, and drawing notes into mechanical semantics such as:

- `4-Φ6.5 THRU`
- `M8x1.25`
- `R5`
- `C2`
- `Ra3.2`
- general tolerances

### CAD Operation Tooling

Converts briefs and user edits into controlled CAD operations. This module should be exposed as Agent tools once the core CAD service is stable.

### Geometry Validation

Checks deterministic geometry rules:

- closed contours
- holes inside parent profiles
- feature collisions
- unit consistency
- missing dimensions
- invalid layer names

LLMs may explain validation results, but deterministic code should own the checks.

### Export

Exports the internal document to CAD-friendly artifacts:

- DXF first
- SVG preview
- PDF drawing sheet later
- PNG thumbnail later

## Backend Implementation Plan

Initial module layout:

```text
backend/app/domain/models/cad.py
backend/app/application/services/cad_service.py
backend/app/interfaces/schemas/cad.py
backend/app/interfaces/api/cad_routes.py
```

First API surface:

- `POST /api/v1/cad/documents`
- `GET /api/v1/cad/documents/{document_id}`
- `POST /api/v1/cad/documents/{document_id}/operations`
- `GET /api/v1/cad/documents/{document_id}/export/dxf`

The first implementation may use an in-memory repository so the API and document schema can be exercised immediately. Persistent MongoDB storage can be added once the data model settles.

## Frontend Implementation Plan

The right panel should evolve into a CAD canvas.

Initial rendering can use SVG because it maps well to selectable 2D entities and dimensions. Canvas or a dedicated CAD renderer can be introduced later if performance or interaction complexity requires it.

Initial canvas controls:

- zoom
- pan
- fit to view
- grid
- layer visibility
- selection
- export DXF

## Agent Integration Plan

After the standalone CAD API is stable:

1. Add a Mechanical CAD prompt set.
2. Add CAD tools that call `CADService`.
3. Replace generic planning for CAD sessions with CAD-specific planning.
4. Stream CAD operation events to the frontend.
5. Use the right panel to render document updates in real time.

## MVP Acceptance Criteria

The first usable slice is complete when:

1. A user can create a mechanical CAD document.
2. The system can apply basic operations for a plate, holes, slots, centerlines, dimensions, and notes.
3. The document can be retrieved as structured JSON.
4. The document can be exported as DXF.
5. The right-side frontend can render the document state.
6. Follow-up user edits can modify the same document.

