"""Historical research_v1 regression loader. Its exposed final split is NOT independent.
Use ai_dataset.load_ai_queries for the current project; old files are retained unchanged.
"""
from hashlib import sha256
import json
from pathlib import Path

from deepresearch.models import UserQuery


def verify_manifest(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "data/manifests/research_v1.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError("manifest_path_outside_project")
        if sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("dataset_integrity_mismatch: " + name)
    return manifest


def load_queries(root, split="dev", *, final_evaluation=False):
    if split not in ("dev", "final"):
        raise ValueError("invalid_split")
    if split == "final" and not final_evaluation:
        raise PermissionError("final_split_is_reserved_for_preregistered_B3_evaluation")
    root = Path(root)
    verify_manifest(root)
    rows = json.loads((root / f"data/research_v1/{split}/tasks.json").read_text(encoding="utf-8"))
    # Only user text and task ID reach the Agent. No reference, type, source hint, or annotation.
    return [UserQuery(row["query"], row["task_id"]) for row in rows]
