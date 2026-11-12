---
name: comfy-guard
description: Manage ComfyUI custom_nodes and Python dependencies safely with per-plugin transactional rollback, conflict pre-checks, commit-pinned lockfiles (comfy-lock.json), and ultra-fast cloud GPU delta sync. Trigger whenever installing/updating/removing ComfyUI plugins, fixing broken ComfyUI workflows/imports, or deploying ComfyUI to Colab/RunPod/cloud GPUs.
---

# Comfy-Guard Skill (Agent-Native ComfyUI Dependency & Rollback Manager)

Never run raw `git clone` + `pip install -r requirements.txt` inside a production ComfyUI environment again. Raw installs overwrite core CUDA/PyTorch wheels, upgrade shared libraries (`transformers`, `protobuf`, `diffusers`), break existing workflows, and burn expensive cloud GPU minutes.

Always operate ComfyUI plugins and cloud syncs through `comfy-guard` with the `--json` flag.

## Core Capabilities & Commands

Assume `<COMFY_DIR>` is the target ComfyUI root directory (e.g. `/content/ComfyUI` on Colab or local path).

### 1. Inspect Current Environment & Plugins
```bash
python comfy_guard_cli.py -C "<COMFY_DIR>" --json status
```

### 2. Pre-Check Conflicts Before Installing Anything (`--dry-run`)
Before adding a new custom node repository, always run a dry-run conflict check:
```bash
python comfy_guard_cli.py -C "<COMFY_DIR>" --json add https://github.com/author/ComfyUI-SomeNode.git --dry-run
```
- `comfy-guard` automatically strips overrides of pre-installed `torch / torchvision / torchaudio / xformers / triton` so cloud CUDA runtimes are never destroyed.
- If `conflicts` is non-empty, **STOP** and report the exact conflicting package and existing plugin to the user instead of forcing installation.

### 3. Transactional Plugin Install (Auto-Snapshot Before Install)
```bash
python comfy_guard_cli.py -C "<COMFY_DIR>" --json add https://github.com/author/ComfyUI-SomeNode.git [--commit <SHA>]
```
Records exact `packages_added`, `packages_changed` (`before` -> `after`), and Git commits in `<COMFY_DIR>/.comfy-guard/transactions.json`.

### 4. Per-Plugin Rollback (Instant Recovery When a Workflow Breaks)
If a newly installed or updated plugin causes `IMPORT FAILED` or breaks an existing workflow, roll back that specific plugin and every Python package version it altered in one command:
```bash
python comfy_guard_cli.py -C "<COMFY_DIR>" --json rollback ComfyUI-SomeNode
```

### 5. Export Commit-Pinned Lockfile (`comfy-lock.json`)
Once a workflow runs cleanly, freeze the entire ComfyUI core commit + all plugin commits + exact dependency versions + referenced model filenames:
```bash
python comfy_guard_cli.py -C "<COMFY_DIR>" --json export -o comfy-lock.json -w workflow.json
```

### 6. Ultra-Fast Cloud GPU Sync (Save Colab / Cloud GPU Time)
On cloud startup, never loop `pip install` across plugins. Sync all pinned plugin commits in parallel (8 threads) and install **only the missing package delta** via `uv` in a single batch while preserving pre-installed cloud PyTorch/CUDA:
```bash
python comfy_guard_cli.py -C "/content/ComfyUI" --json cloud-sync -f comfy-lock.json
```

### 7. Scan Workflow for Required Nodes & Model Weights
```bash
python comfy_guard_cli.py --json scan-workflow path/to/workflow.json
```
