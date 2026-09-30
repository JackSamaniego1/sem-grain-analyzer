"""
Build-time helper for the GPU pack (decision D-32).

Compares the CPU bundle (dist/GrainAnalyzer) with the CUDA bundle built from
the same sources and
  * copies every file that is new or different into <out_dir>, and
  * writes the relative paths that exist ONLY in the CPU bundle (e.g. the
    torch-*+cpu.dist-info folder) to <out_dir>_removed.txt, one per line.
The pack installer overlays the copied files onto an existing CPU
installation and deletes the removed ones, so the result equals the CUDA
bundle and the pack stays well under the 2 GB limits of NSIS / GitHub.

    python make_gpu_pack.py <cpu_dir> <gpu_dir> <out_dir>

Build tooling only -- never shipped, never run on the work PC.
"""
import hashlib
import os
import shutil
import sys


def removed_list_path(out_dir):
    return os.path.normpath(out_dir) + "_removed.txt"


def _lp(path):
    """Windows long-path form (\\\\?\\) so deep torch/PySide trees never hit MAX_PATH."""
    if sys.platform != "win32":
        return path
    path = os.path.abspath(path)
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _sha(path):
    h = hashlib.sha256()
    with open(_lp(path), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk_rel(root):
    """Yield relative file paths under root (long-path safe)."""
    base = _lp(root)
    for dirpath, _dirs, files in os.walk(base):
        for fn in files:
            yield os.path.relpath(os.path.join(dirpath, fn), base)


def build_pack(cpu_dir, gpu_dir, out_dir):
    """Returns (files_copied, bytes_copied, files_removed)."""
    if os.path.exists(_lp(out_dir)):
        shutil.rmtree(_lp(out_dir))
    n = total = 0
    gpu_files = set()
    for rel in _walk_rel(gpu_dir):
        gpu_files.add(os.path.normcase(rel))
        src = os.path.join(gpu_dir, rel)
        ref = os.path.join(cpu_dir, rel)
        if (os.path.isfile(_lp(ref))
                and os.path.getsize(_lp(ref)) == os.path.getsize(_lp(src))
                and _sha(ref) == _sha(src)):
            continue
        dst = os.path.join(out_dir, rel)
        os.makedirs(_lp(os.path.dirname(dst)), exist_ok=True)
        shutil.copy2(_lp(src), _lp(dst))
        n += 1
        total += os.path.getsize(_lp(src))
    removed = sorted(rel for rel in _walk_rel(cpu_dir)
                     if os.path.normcase(rel) not in gpu_files)
    os.makedirs(_lp(out_dir), exist_ok=True)
    with open(removed_list_path(out_dir), "w", encoding="utf-8") as f:
        f.write("".join(r + "\n" for r in removed))
    return n, total, len(removed)


def main(argv):
    if len(argv) != 4:
        print(__doc__)
        return 2
    cpu_dir, gpu_dir, out_dir = argv[1:]
    for d in (cpu_dir, gpu_dir):
        if not os.path.isdir(d):
            print(f"ERROR: {d} is not a directory")
            return 1
    n, total, rm = build_pack(cpu_dir, gpu_dir, out_dir)
    if n == 0:
        print("ERROR: CUDA bundle is identical to the CPU bundle - was "
              "GA_TORCH_FLAVOR=cuda set and a +cuXXX torch installed?")
        return 1
    print(f"GPU pack: {n} files, {total / 1e6:.0f} MB uncompressed, "
          f"{rm} CPU-only files to delete -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
