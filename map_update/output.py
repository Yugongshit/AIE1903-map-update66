"""Portable offline output, written as a new directory without clobbering data."""

import json
from pathlib import Path
import os
import shutil
import tempfile


def _write_files(files, template_name, payload_object, output):
    dest = Path(output).resolve()
    if dest.exists():
        raise ValueError(f"output already exists; choose a new directory: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".map-output-", dir=dest.parent))
    try:
        for name, value in files.items():
            (staging/f"{name}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
        # Escape markup delimiters so upstream text cannot terminate the JSON script.
        payload = json.dumps(payload_object, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        template = Path(__file__).with_name(template_name).read_text(encoding="utf-8")
        (staging/"index.html").write_text(template.replace("__BUNDLE_JSON__", payload), encoding="utf-8")
        os.rename(staging, dest)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return dest


def write_bundle(bundle, output):
    return _write_files({"bundle": bundle, "map": bundle["current_map"], "events": bundle["events"],
                         "planning": bundle["planning_view"], "versions": bundle["versions"], "audit": bundle["audit"]},
                        "viewer.html", bundle, output)


def write_intake(intake, output):
    return _write_files({"intake": intake, "candidates": intake["candidates"], "validation": intake["validation"],
                         "summary": intake["summary"]}, "intake_viewer.html", intake, output)


def write_scene(scene, output):
    return _write_files({"scene": scene, "reference_map": scene["reference_map"], "replay": scene["replay"],
                         "candidates": scene["candidates"], "validation": scene["raw_validation"], "summary": scene["summary"]},
                        "scene_viewer.html", scene, output)


def write_maintenance(scene, output):
    return _write_files({"scene": scene, "reference_map": scene["reference_map"],
                         "replay": scene["replay"], "candidates": scene["candidates"],
                         "event_layer": scene["event_layer"], "reliability": scene["m3"],
                         "maintenance": scene["maintenance"], "summary": scene["summary"],
                         "validation": {"source": scene["raw_validation"], "m3": scene["m3"]["validation"]}},
                        "scene_viewer.html", scene, output)
