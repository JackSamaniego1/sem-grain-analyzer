"""BUILD: SAM checkpoint is fetched at build time only, hash-verified, and
bundled at the path core/grain_detector.py looks for it (HARD CONSTRAINT
D-14: the installed app must never touch the network; missing asset ->
tell the user to reinstall, never a download link).

These are static checks over the build tooling (BUILD_WINDOWS.bat,
.github/workflows/build.yml, grain_analyzer.spec) plus a guard that the
runtime download URL/hash never leak into first-party application source
(core/ui/utils/data/reports), which must only ever read a local file.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CHECKPOINT_NAME = "sam_vit_b_01ec64.pth"
SAM_SHA256 = "ec2df62732614e57411cdcf32a23ffdf28910380d03139ee0f4fcbe91eb8c912"
SAM_URL = "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth"


def _read(rel_path):
    with open(os.path.join(ROOT, rel_path), "r", encoding="utf-8") as f:
        return f.read()


def test_build_windows_bat_downloads_and_verifies_checkpoint_hash():
    src = _read("BUILD_WINDOWS.bat")
    assert SAM_URL in src
    assert SAM_SHA256 in src
    assert "Get-FileHash" in src
    # Must fail the build (exit /b 1) on a hash mismatch, not just warn.
    assert "SHA-256 mismatch" in src
    assert "exit /b 1" in src


def test_ci_workflow_downloads_and_verifies_checkpoint_hash_on_both_platforms():
    src = _read(os.path.join(".github", "workflows", "build.yml"))
    assert src.count(SAM_URL) >= 2  # windows + macos jobs
    assert src.count(SAM_SHA256) >= 2
    assert "Get-FileHash" in src  # windows job
    assert "shasum -a 256" in src  # macos job
    assert "SHA-256 mismatch" in src


def test_spec_bundles_checkpoint_to_models_dir():
    src = _read("grain_analyzer.spec")
    assert f"models/{CHECKPOINT_NAME}" in src
    assert "'models'" in src


def test_checkpoint_gitignored():
    src = _read(".gitignore")
    assert "models/*.pth" in src or "*.pth" in src


def test_third_party_licenses_documents_checkpoint_provenance():
    src = _read("THIRD_PARTY_LICENSES.txt")
    assert "segment-anything" in src
    assert CHECKPOINT_NAME in src
    assert SAM_SHA256 in src


def test_no_first_party_source_references_the_checkpoint_download_url():
    """The download only happens in build scripts/CI. Application source
    (core/ui/utils/data/reports) must only ever look for a local file --
    this is a narrower, checkpoint-specific companion to the broader URL
    scan in tests/test_offline.py."""
    scan_dirs = ["core", "ui", "utils", "data", "reports"]
    hits = []
    for scan_dir in scan_dirs:
        base = os.path.join(ROOT, scan_dir)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in
                           {".venv", "tests", "build", "dist", "scratch", "__pycache__"}]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                full = os.path.join(dirpath, fn)
                with open(full, "r", encoding="utf-8") as f:
                    text = f.read()
                if "dl.fbaipublicfiles.com" in text:
                    hits.append(os.path.relpath(full, ROOT))
    assert not hits, f"checkpoint download URL leaked into app source: {hits}"


def test_grain_detector_looks_for_checkpoint_locally_only():
    src = _read(os.path.join("core", "grain_detector.py"))
    assert CHECKPOINT_NAME in src
    assert "dl.fbaipublicfiles.com" not in src
    assert "reinstall" in src.lower()  # missing asset -> reinstall, never a download link
