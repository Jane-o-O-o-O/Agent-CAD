---
name: cad-drawing-analysis
description: CAD dedicated ReAct workflow for CAD, DXF, DWG-style output, mechanical drawing, engineering drawing, 制图, 画图, 图纸, 安装板, 孔位, 圆角, 槽, 工程图, 机械图, 电气控制原理图, and process diagrams.
---

# CAD Dedicated Agent Workflow

Use this protocol whenever the user asks for a CAD drawing, DXF/DWG-style output, mechanical drawing, engineering drawing, electrical control schematic, process diagram, or drawing based on uploaded files.

This is still an Agent workflow. Do not bypass reasoning. The CAD tools are fixed upstream/downstream tools for the Agent to call in a ReAct loop.

## Required ReAct Loop

For every CAD drawing task, follow this loop:

1. Observe the user request and uploaded attachment paths.
2. Call `cad_analyze_request` after any necessary file intake.
3. Reason about whether the parsed brief is complete enough.
4. If important values are missing, call `message_ask_user`.
5. If complete enough, convert the requirements into explicit geometry and call `cad_generate_dxf_from_spec`.
6. Call `cad_validate_dxf`.
7. If validation fails, regenerate once or explain the blocking issue.
8. Deliver the validated DXF file path and a concise summary.

Do not directly write ad hoc DXF with shell scripts unless the CAD tool fails and there is no other way.
Use `cad_generate_dxf` only as a fallback when the geometry cannot be represented as a structured spec.

## Fixed Plan Shape

For CAD requests with attachments, create no more than four plan steps:

1. Extract drawing requirements from uploaded files.
2. Normalize the extracted requirements into a CAD brief and decide if enough data is available.
3. Generate the DXF drawing with `cad_generate_dxf_from_spec`.
4. Validate and deliver the DXF output.

For CAD requests without attachments, create no more than three plan steps:

1. Parse the requested geometry or diagram and decide if enough data is available.
2. Generate the DXF drawing with `cad_generate_dxf_from_spec`.
3. Validate and deliver the DXF output.

Do not create separate plan steps for tool selection, environment checks, OCR attempts, document conversion, table detection, image extraction, or intermediate scripts. Those are implementation details inside a step.

## Completeness Check

Before generating, verify the brief has enough information for the drawing type:

- Mechanical 2D: units, base shape or part type, key dimensions, hole/slot patterns, radius/chamfer values when requested.
- Electrical/process diagrams: symbols/equipment, labels/tags, connection relationships, flow/control direction.
- Uploaded references: confirmed extracted requirements and uncertain items.

Ask the user only when a missing value changes the drawing type, main topology, or safety-critical meaning. Otherwise use practical defaults and list assumptions.

## Defaults

- Units: mm.
- Output: DXF.
- Drawing style: clean 2D CAD drawing, not photorealistic reconstruction.
- Mechanical drawings: prioritize dimensions, holes, slots, centerlines, annotations, and layers.
- Process/electrical drawings: prioritize equipment tags, labels, connections, and flow/control direction over exact page layout.

## Validation

After generating DXF, always call `cad_validate_dxf`. Treat the result as the observation for the next reasoning step.

## Final DXF Tool

Prefer `cad_generate_dxf_from_spec` for final output. Give it explicit geometry instead of prose:

- Mechanical plates: use `rectangle`, `hole`, `slot`, `center_mark`, `text`, and visible `dimensions`.
- Process/electrical diagrams: use `line`, `polyline`, `rectangle`, `circle`, and `text` for labeled blocks and connectors.
- Keep coordinates in the chosen units and place the main drawing near the origin.
- Put outlines on `M-OBJECT`, holes/slots on `M-HOLE`, center marks on `M-CENTER`, dimensions on `M-DIM`, notes on `M-NOTE`.

Only use `cad_generate_dxf` when the older prompt-to-CAD planner is specifically needed as a fallback.

A valid delivery must mention:

- What drawing was produced.
- What source information or assumptions were used.
- The delivered DXF file path.
- Any uncertain items or defaults.
