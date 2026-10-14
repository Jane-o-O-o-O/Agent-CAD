"""Run the repository's actual prompt-to-DXF service and render its exported file.

From backend/: python examples/generate_cad_preview.py --output /absolute/output
Requires the CAD service dependencies and matplotlib for the optional PNG preview.
No LLM, MongoDB, Redis, frontend, or Docker sandbox is involved.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ezdxf
from ezdxf import bbox
from app.application.services.cad_service import CADService

PROMPT = '单位mm，120x80矩形安装板，四角R5，4个直径8的通孔，孔距边缘10。加外形尺寸和孔径标注。'


async def generate(output, render=True):
    output.mkdir(parents=True, exist_ok=True)
    service = CADService()
    result = await service.create_document_from_prompt('source-demo', PROMPT)
    dxf_path = output / 'source-mounting-plate.dxf'
    dxf_path.write_text(await service.export_dxf(result.document.id, 'source-demo'), encoding='utf-8')

    # Read the exported artifact again, independently of the service's document.
    drawing = ezdxf.readfile(dxf_path)
    audit = drawing.audit()
    entities = list(drawing.modelspace())
    circles = [e for e in entities if e.dxftype() == 'CIRCLE' and e.dxf.layer == 'M-HOLE']
    outline = [e for e in entities if e.dxf.layer == 'M-OBJECT']
    bounds = bbox.extents(outline)
    centers = sorted((round(e.dxf.center.x, 6), round(e.dxf.center.y, 6)) for e in circles)
    corner_arcs = [e for e in outline if e.dxftype() == 'ARC']
    checks = {
        'units_mm': drawing.units == 4,
        'outline_120_by_80': abs(bounds.size.x - 120) < 1e-6 and abs(bounds.size.y - 80) < 1e-6,
        'four_holes': len(circles) == 4,
        'diameter_8': all(abs(e.dxf.radius - 4) < 1e-6 for e in circles),
        'hole_centers': centers == [(10, 10), (10, 70), (110, 10), (110, 70)],
        'four_R5_corners': len(corner_arcs) == 4 and all(abs(e.dxf.radius - 5) < 1e-6 for e in corner_arcs),
        'dxf_audit': not audit.errors and not audit.fixes,
    }
    root = Path(__file__).resolve().parents[2]
    source_path = root / 'backend/app/application/services/cad_service.py'
    evidence = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'repository': 'Jane-o-O-o-O/Agent-CAD',
        'head_at_run': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        'service_file_sha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
        'prompt': PROMPT,
        'entrypoints': ['CADService.create_document_from_prompt', 'CADService.export_dxf'],
        'scope': 'Actual deterministic CAD service; no LLM or full-stack UI execution',
        'dxf_sha256': hashlib.sha256(dxf_path.read_bytes()).hexdigest(),
        'entity_counts': dict(Counter(e.dxftype() for e in entities)),
        'checks': checks,
        'annotation_texts': [e.dxf.text for e in entities if e.dxftype() == 'TEXT'],
        'limitations': ['The original service exports overall size text without dimension lines and omits requested hole-diameter labels.'],
        'ezdxf_version': ezdxf.__version__,
    }
    (output / 'source-generation-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if not all(checks.values()):
        raise RuntimeError(f'Exported geometry checks failed: {checks}')
    if render:
        import matplotlib
        matplotlib.use('Agg')
        from ezdxf.addons.drawing.matplotlib import qsave
        from ezdxf.addons.drawing.config import ColorPolicy, Configuration
        qsave(drawing.modelspace(), output / 'source-mounting-plate.png',
              bg='#ffffff', dpi=180, size_inches=(10, 7),
              config=Configuration(color_policy=ColorPolicy.BLACK))
    print(json.dumps({'dxf': str(dxf_path), 'checks': checks, 'annotations': evidence['annotation_texts']}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--no-render', action='store_true')
    args = parser.parse_args()
    asyncio.run(generate(args.output.resolve(), not args.no_render))
