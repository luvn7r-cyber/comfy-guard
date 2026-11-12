# -*- coding: utf-8 -*-
"""
comfy-guard 核心引擎：
- 插件级依赖安装前快照与事务级回滚 (Per-Plugin Rollback)
- 核心 CUDA/PyTorch 大包防误覆盖保护罩 (Core Runtime Guard)
- 依赖版本冲突静态预检 (Conflict Pre-check)
- Git Commit SHA + Pip 精确版本三合一锁文件导出 (comfy-lock.json)
- 云端 GPU 多线程并发克隆 + uv 增量极速同步 (Cloud Fast Sync)
- ComfyUI 工作流节点与模型引用扫描 (Workflow Scanner)
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_PROTECTED_PACKAGES = {
    "torch",
    "torchvision",
    "torchaudio",
    "xformers",
    "triton",
    "bitsandbytes",
    "nvidia-cublas-cu12",
    "nvidia-cuda-cupti-cu12",
    "nvidia-cuda-nvrtc-cu12",
    "nvidia-cuda-runtime-cu12",
    "nvidia-cudnn-cu12",
    "nvidia-cufft-cu12",
    "nvidia-curand-cu12",
    "nvidia-cusolver-cu12",
    "nvidia-cusparse-cu12",
    "nvidia-nccl-cu12",
    "nvidia-nvjitlink-cu12",
    "nvidia-nvtx-cu12",
}

REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)")
SPEC_OP_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*(?:\[[^\]]+\])?\s*((?:(?:==|!=|<=|>=|<|>|~=)\s*[^,;#]+(?:\s*,\s*)?)+)?")


def norm_pkg_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _run(cmd: List[str], cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def has_uv() -> bool:
    return shutil.which("uv") is not None


def pip_freeze_map(python_exe: str = sys.executable) -> Dict[str, str]:
    """获取当前 Python 环境的 {规范化包名: 精确版本号} 映射"""
    if has_uv() and python_exe == sys.executable:
        res = _run(["uv", "pip", "list", "--format=json", "--python", python_exe], check=False)
        if res.returncode == 0 and res.stdout.strip():
            try:
                items = json.loads(res.stdout)
                return {norm_pkg_name(x["name"]): x["version"] for x in items}
            except Exception:
                pass
    res = _run([python_exe, "-m", "pip", "list", "--format=json"], check=True)
    items = json.loads(res.stdout)
    return {norm_pkg_name(x["name"]): x["version"] for x in items}


def pip_install_specs(specs: List[str], python_exe: str = sys.executable, no_deps: bool = False) -> Tuple[bool, str]:
    if not specs:
        return True, "无需安装任何增量包"
    if has_uv():
        cmd = ["uv", "pip", "install", "--python", python_exe]
        if no_deps:
            cmd.append("--no-deps")
        cmd.extend(specs)
    else:
        cmd = [python_exe, "-m", "pip", "install", "-q"]
        if no_deps:
            cmd.append("--no-deps")
        cmd.extend(specs)
    res = _run(cmd, check=False)
    return res.returncode == 0, (res.stdout + "\n" + res.stderr).strip()


def pip_uninstall_pkgs(pkgs: List[str], python_exe: str = sys.executable) -> Tuple[bool, str]:
    if not pkgs:
        return True, ""
    if has_uv():
        cmd = ["uv", "pip", "uninstall", "--python", python_exe] + pkgs
    else:
        cmd = [python_exe, "-m", "pip", "uninstall", "-y", "-q"] + pkgs
    res = _run(cmd, check=False)
    return res.returncode == 0, (res.stdout + "\n" + res.stderr).strip()


def git_info(repo_dir: Path) -> Dict[str, str]:
    if not (repo_dir / ".git").exists():
        return {"repo": "", "commit": "", "branch": ""}
    commit = _run(["git", "rev-parse", "HEAD"], cwd=repo_dir, check=False).stdout.strip()
    remote = _run(["git", "config", "--get", "remote.origin.url"], cwd=repo_dir, check=False).stdout.strip()
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir, check=False).stdout.strip()
    return {"repo": remote, "commit": commit, "branch": branch}


def parse_requirements_file(req_path: Path) -> List[str]:
    if not req_path.exists():
        return []
    lines = []
    for raw in req_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        lines.append(line)
    return lines


def parse_spec_tuple(spec: str) -> Tuple[str, str]:
    m = SPEC_OP_RE.match(spec)
    if not m:
        return norm_pkg_name(spec), ""
    return norm_pkg_name(m.group(1)), (m.group(2) or "").replace(" ", "")


def _ver_tuple(v: str) -> Tuple[int, ...]:
    nums = re.findall(r"\d+", v.split("+")[0])
    return tuple(int(x) for x in nums[:4]) if nums else (0,)


def version_satisfies(installed_ver: str, constraint: str) -> bool:
    if not constraint:
        return True
    parts = [c.strip() for c in constraint.split(",") if c.strip()]
    iv = _ver_tuple(installed_ver)
    for p in parts:
        for op in ("==", "!=", "<=", ">=", "~=", "<", ">"):
            if p.startswith(op):
                tv = _ver_tuple(p[len(op) :])
                if op == "==" and iv != tv:
                    return False
                if op == "!=" and iv == tv:
                    return False
                if op == "<=" and not (iv <= tv):
                    return False
                if op == ">=" and not (iv >= tv):
                    return False
                if op == "<" and not (iv < tv):
                    return False
                if op == ">" and not (iv > tv):
                    return False
                if op == "~=" and not (iv >= tv and iv[: max(1, len(tv) - 1)] == tv[: max(1, len(tv) - 1)]):
                    return False
                break
    return True


class ComfyGuard:
    def __init__(self, comfy_dir: Path, python_exe: str = sys.executable):
        self.comfy_dir = Path(comfy_dir).resolve()
        self.custom_nodes_dir = self.comfy_dir / "custom_nodes"
        self.state_dir = self.comfy_dir / ".comfy-guard"
        self.trash_dir = self.state_dir / "trash"
        self.journal_file = self.state_dir / "transactions.json"
        self.python_exe = python_exe

    def ensure_dirs(self):
        self.custom_nodes_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        if not self.journal_file.exists():
            self.journal_file.write_text("[]", encoding="utf-8")

    def load_journal(self) -> List[dict]:
        self.ensure_dirs()
        return json.loads(self.journal_file.read_text(encoding="utf-8"))

    def save_journal(self, entries: List[dict]):
        self.ensure_dirs()
        self.journal_file.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_plugins(self) -> List[dict]:
        if not self.custom_nodes_dir.exists():
            return []
        plugins = []
        for d in sorted(self.custom_nodes_dir.iterdir()):
            if not d.is_dir() or d.name.startswith(".") or d.name == "__pycache__":
                continue
            gi = git_info(d)
            reqs = parse_requirements_file(d / "requirements.txt")
            plugins.append({
                "name": d.name,
                "path": str(d),
                "repo": gi["repo"],
                "commit": gi["commit"],
                "branch": gi["branch"],
                "requirements": reqs,
            })
        return plugins

    def status(self) -> dict:
        core_gi = git_info(self.comfy_dir)
        plugins = self.list_plugins()
        txs = self.load_journal()
        return {
            "ok": True,
            "comfy_dir": str(self.comfy_dir),
            "comfy_core": core_gi,
            "plugin_count": len(plugins),
            "plugins": plugins,
            "transaction_count": len(txs),
            "uv_accelerated": has_uv(),
        }

    def check_requirements_conflicts(
        self, candidate_reqs: List[str], allow_core_override: bool = False
    ) -> dict:
        """安装前静态冲突预检：检查是否试图篡改核心 CUDA 包，或与已装插件锁定的依赖版本冲突"""
        installed = pip_freeze_map(self.python_exe)
        existing_constraints: Dict[str, List[Tuple[str, str]]] = {}
        for plg in self.list_plugins():
            for r in plg["requirements"]:
                pkg, spec = parse_spec_tuple(r)
                if spec:
                    existing_constraints.setdefault(pkg, []).append((plg["name"], spec))

        skipped_core = []
        filtered_specs = []
        conflicts = []
        to_install_or_change = []

        for raw_spec in candidate_reqs:
            pkg, spec = parse_spec_tuple(raw_spec)
            if not allow_core_override and pkg in DEFAULT_PROTECTED_PACKAGES and pkg in installed:
                skipped_core.append({"package": pkg, "installed": installed[pkg], "requested": raw_spec})
                continue
            filtered_specs.append(raw_spec)
            cur_v = installed.get(pkg)
            if cur_v is None:
                to_install_or_change.append({"package": pkg, "action": "install", "spec": raw_spec})
            elif not version_satisfies(cur_v, spec):
                to_install_or_change.append({"package": pkg, "action": "change_version", "current": cur_v, "requested": raw_spec})
                # 检查如果改变该包版本，是否会违反其它现有插件的约束
                probe_ver = None
                if spec.startswith("=="):
                    probe_ver = spec[2:].split(",")[0]
                elif spec.startswith(">="):
                    probe_ver = spec[2:].split(",")[0]
                elif spec.startswith(">"):
                    probe_ver = spec[1:].split(",")[0] + ".1"
                if probe_ver:
                    for other_plg, other_spec in existing_constraints.get(pkg, []):
                        if not version_satisfies(probe_ver, other_spec):
                            conflicts.append({
                                "package": pkg,
                                "installed": cur_v,
                                "new_request": raw_spec,
                                "conflicts_with_plugin": other_plg,
                                "existing_constraint": other_spec,
                            })

        return {
            "ok": len(conflicts) == 0,
            "skipped_protected_core": skipped_core,
            "filtered_specs": filtered_specs,
            "planned_changes": to_install_or_change,
            "conflicts": conflicts,
        }

    def add_plugin(
        self,
        repo_or_path: str,
        commit: Optional[str] = None,
        dry_run: bool = False,
        allow_core_override: bool = False,
        force: bool = False,
    ) -> dict:
        """安装或更新插件，自动记录前后 pip 环境差集事务，支持冲突阻断与一键回滚"""
        self.ensure_dirs()
        clean_ref = repo_or_path.rstrip("/\\")
        plugin_name = re.split(r"[/\\]", clean_ref)[-1].replace(".git", "")
        target_dir = self.custom_nodes_dir / plugin_name
        existed_before = target_dir.exists()
        old_commit = git_info(target_dir)["commit"] if existed_before else ""

        before_pkgs = pip_freeze_map(self.python_exe)

        # 若为本地已有目录或先克隆到临时区做预检
        staging_dir = self.state_dir / f"_staging_{plugin_name}"
        if staging_dir.exists():
            shutil.rmtree(staging_dir)

        if existed_before:
            req_lines = parse_requirements_file(target_dir / "requirements.txt")
        else:
            res = _run(["git", "clone", "--depth", "1", repo_or_path, str(staging_dir)], check=False)
            if res.returncode != 0:
                return {"ok": False, "error": f"Git 克隆失败: {res.stderr.strip()}"}
            if commit:
                _run(["git", "fetch", "--depth", "1", "origin", commit], cwd=staging_dir, check=False)
                co = _run(["git", "checkout", commit], cwd=staging_dir, check=False)
                if co.returncode != 0:
                    shutil.rmtree(staging_dir, ignore_errors=True)
                    return {"ok": False, "error": f"无法检出指定 Commit {commit}: {co.stderr.strip()}"}
            req_lines = parse_requirements_file(staging_dir / "requirements.txt")

        precheck = self.check_requirements_conflicts(req_lines, allow_core_override=allow_core_override)
        if dry_run:
            shutil.rmtree(staging_dir, ignore_errors=True)
            return {"ok": precheck["ok"], "dry_run": True, "plugin": plugin_name, "precheck": precheck}

        if not precheck["ok"] and not force:
            shutil.rmtree(staging_dir, ignore_errors=True)
            return {
                "ok": False,
                "plugin": plugin_name,
                "error": "检测到与现有插件存在版本冲突，已自动阻断安装以保护工作流（如需强行安装请加 --force）",
                "conflicts": precheck["conflicts"],
            }

        # 正式移动或更新插件代码
        if not existed_before:
            shutil.move(str(staging_dir), str(target_dir))
        elif commit and commit != old_commit:
            _run(["git", "fetch", "--depth", "1", "origin", commit], cwd=target_dir, check=False)
            _run(["git", "checkout", commit], cwd=target_dir, check=True)

        # 安装过滤了受保护核心包之后的增量依赖
        ok_pip, pip_log = pip_install_specs(precheck["filtered_specs"], python_exe=self.python_exe)
        after_pkgs = pip_freeze_map(self.python_exe)

        added_pkgs = {k: v for k, v in after_pkgs.items() if k not in before_pkgs}
        changed_pkgs = {
            k: {"before": before_pkgs[k], "after": v}
            for k, v in after_pkgs.items()
            if k in before_pkgs and before_pkgs[k] != v
        }
        removed_pkgs = {k: v for k, v in before_pkgs.items() if k not in after_pkgs}

        if not ok_pip:
            # 安装依赖失败时自动回滚现场
            if not existed_before and target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            elif existed_before and old_commit:
                _run(["git", "checkout", old_commit], cwd=target_dir, check=False)
            return {"ok": False, "plugin": plugin_name, "error": "Python 依赖安装失败，已自动恢复现场", "log": pip_log}

        new_commit = git_info(target_dir)["commit"]
        journal = self.load_journal()
        tx_id = f"tx_{len(journal) + 1:04d}_{plugin_name}"
        tx_record = {
            "tx_id": tx_id,
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "action": "update" if existed_before else "add",
            "plugin": plugin_name,
            "repo": repo_or_path,
            "existed_before": existed_before,
            "old_commit": old_commit,
            "new_commit": new_commit,
            "packages_added": added_pkgs,
            "packages_changed": changed_pkgs,
            "packages_removed": removed_pkgs,
            "skipped_protected_core": precheck["skipped_protected_core"],
            "rolled_back": False,
        }
        journal.append(tx_record)
        self.save_journal(journal)
        return {"ok": True, "tx": tx_record}

    def rollback_plugin(self, plugin_or_tx: str, purge_orphan_added: bool = True) -> dict:
        """按插件名或事务 ID 精准回滚：还原被它篡改的所有依赖版本，并回退或移除插件目录"""
        journal = self.load_journal()
        target_tx = None
        for entry in reversed(journal):
            if not entry.get("rolled_back") and (entry["plugin"] == plugin_or_tx or entry["tx_id"] == plugin_or_tx):
                target_tx = entry
                break
        if not target_tx:
            return {"ok": False, "error": f"未找到插件或事务 '{plugin_or_tx}' 的可回滚记录"}

        plugin_name = target_tx["plugin"]
        plugin_dir = self.custom_nodes_dir / plugin_name

        # 1. 收集其它仍在服役插件所需的包名，避免误删共享新增包
        other_needed_pkgs = set()
        for plg in self.list_plugins():
            if plg["name"] == plugin_name:
                continue
            for r in plg["requirements"]:
                p_name, _ = parse_spec_tuple(r)
                other_needed_pkgs.add(p_name)

        # 2. 精确还原被升级/降级的旧包版本 + 恢复被删的包
        restore_specs = [f"{k}=={v['before']}" for k, v in target_tx.get("packages_changed", {}).items()]
        restore_specs += [f"{k}=={v}" for k, v in target_tx.get("packages_removed", {}).items()]
        ok_res, res_log = pip_install_specs(restore_specs, python_exe=self.python_exe, no_deps=True)

        # 3. 卸载该插件独享的新增包
        uninstalled = []
        if purge_orphan_added:
            orphans = [k for k in target_tx.get("packages_added", {}).keys() if k not in other_needed_pkgs and k not in DEFAULT_PROTECTED_PACKAGES]
            if orphans:
                pip_uninstall_pkgs(orphans, python_exe=self.python_exe)
                uninstalled = orphans

        # 4. 代码目录回退：原本不存在则移入回收站备份移除；原本存在则 git checkout 回旧 commit
        code_action = "none"
        if plugin_dir.exists():
            if not target_tx.get("existed_before"):
                backup_dst = self.trash_dir / f"{plugin_name}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"
                shutil.move(str(plugin_dir), str(backup_dst))
                code_action = f"moved_to_trash:{backup_dst}"
            elif target_tx.get("old_commit"):
                _run(["git", "checkout", target_tx["old_commit"]], cwd=plugin_dir, check=False)
                code_action = f"checked_out:{target_tx['old_commit']}"

        target_tx["rolled_back"] = True
        target_tx["rolled_back_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        self.save_journal(journal)

        return {
            "ok": ok_res,
            "rolled_back_tx": target_tx["tx_id"],
            "plugin": plugin_name,
            "restored_packages": restore_specs,
            "uninstalled_orphans": uninstalled,
            "code_action": code_action,
            "log": res_log,
        }

    def scan_workflows(self, workflow_paths: List[Path]) -> dict:
        """扫描 ComfyUI 工作流 JSON，提取引用的 class_type 节点列表与模型权重文件名"""
        class_types = set()
        models = set()
        model_exts = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".onnx")
        for wp in workflow_paths:
            data = json.loads(Path(wp).read_text(encoding="utf-8"))
            nodes_iter = data.get("nodes", []) if isinstance(data, dict) and "nodes" in data else (data.values() if isinstance(data, dict) else [])
            for n in nodes_iter:
                if not isinstance(n, dict):
                    continue
                ct = n.get("class_type") or n.get("type")
                if ct:
                    class_types.add(ct)
                inputs = n.get("inputs", {})
                vals = list(inputs.values()) if isinstance(inputs, dict) else []
                vals += n.get("widgets_values", []) if isinstance(n.get("widgets_values"), list) else []
                for v in vals:
                    if isinstance(v, str) and v.lower().endswith(model_exts):
                        models.add(v)
        return {"class_types": sorted(class_types), "referenced_models": sorted(models)}

    def export_lock(self, out_file: Path, workflow_paths: Optional[List[Path]] = None) -> dict:
        """导出三合一锁文件 comfy-lock.json（ComfyUI Commit + 插件精确 Commit + 剥离核心包后的增量锁定依赖）"""
        installed = pip_freeze_map(self.python_exe)
        core_runtime = {k: v for k, v in sorted(installed.items()) if k in DEFAULT_PROTECTED_PACKAGES}
        # 收集所有插件与 ComfyUI 本体显式声明的直接依赖及其当前工作版本，同时保存全量非核心快照以供精确还原
        explicit_pkgs = set()
        for req_line in parse_requirements_file(self.comfy_dir / "requirements.txt"):
            p, _ = parse_spec_tuple(req_line)
            explicit_pkgs.add(p)
        plugins = self.list_plugins()
        for plg in plugins:
            for req_line in plg["requirements"]:
                p, _ = parse_spec_tuple(req_line)
                explicit_pkgs.add(p)

        pinned_direct = {
            k: installed[k]
            for k in sorted(explicit_pkgs)
            if k in installed and k not in DEFAULT_PROTECTED_PACKAGES
        }
        all_non_core = {
            k: v
            for k, v in sorted(installed.items())
            if k not in DEFAULT_PROTECTED_PACKAGES and k not in {"pip", "setuptools", "wheel"}
        }

        wf_info = self.scan_workflows(workflow_paths) if workflow_paths else {"class_types": [], "referenced_models": []}
        lock_data = {
            "schema_version": "1.0",
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "python_version": sys.version.split()[0],
            "comfyui_core": git_info(self.comfy_dir),
            "plugins": [
                {
                    "name": p["name"],
                    "repo": p["repo"],
                    "commit": p["commit"],
                    "requirements": p["requirements"],
                }
                for p in plugins
            ],
            "core_runtime_reference": core_runtime,
            "pinned_plugin_dependencies": pinned_direct,
            "full_environment_lock": all_non_core,
            "workflow_manifest": wf_info,
        }
        out_file = Path(out_file)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(lock_data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "ok": True,
            "lock_file": str(out_file.resolve()),
            "plugin_count": len(plugins),
            "pinned_direct_count": len(pinned_direct),
            "referenced_models_count": len(wf_info["referenced_models"]),
        }

    def cloud_sync(
        self,
        lock_file: Path,
        full_env: bool = False,
        workers: int = 8,
        dry_run: bool = False,
    ) -> dict:
        """
        云端极速省钱同步：
        1. 校验/拉取 ComfyUI 本体指定 Commit；
        2. 多线程并发拉取所有插件并精确 checkout 到锁定 Commit SHA（杜绝上游偷偷更新炸工作流）；
        3. 计算与云端已有环境的纯增量差集（自动保护云端预装 torch/cuda），用 uv 一次性秒装缺失包。
        """
        t0 = dt.datetime.now()
        lock = json.loads(Path(lock_file).read_text(encoding="utf-8"))
        core_info = lock.get("comfyui_core", {})

        if not dry_run:
            if not (self.comfy_dir / "main.py").exists() and core_info.get("repo"):
                self.comfy_dir.parent.mkdir(parents=True, exist_ok=True)
                _run(["git", "clone", "--depth", "1", core_info["repo"], str(self.comfy_dir)], check=True)
            if core_info.get("commit") and (self.comfy_dir / ".git").exists():
                cur_c = git_info(self.comfy_dir)["commit"]
                if cur_c != core_info["commit"]:
                    _run(["git", "fetch", "--depth", "1", "origin", core_info["commit"]], cwd=self.comfy_dir, check=False)
                    _run(["git", "checkout", core_info["commit"]], cwd=self.comfy_dir, check=False)
            self.ensure_dirs()

        plugins = lock.get("plugins", [])

        def _sync_one_plugin(plg: dict) -> dict:
            name = plg["name"]
            repo = plg.get("repo", "")
            commit = plg.get("commit", "")
            pdir = self.custom_nodes_dir / name
            if dry_run:
                return {"name": name, "status": "would_sync", "commit": commit}
            if not repo:
                return {"name": name, "status": "local_only_skipped"}
            if not pdir.exists():
                r = _run(["git", "clone", "--depth", "1", repo, str(pdir)], check=False)
                if r.returncode != 0:
                    return {"name": name, "status": "clone_failed", "error": r.stderr.strip()}
            if commit and (pdir / ".git").exists():
                if git_info(pdir)["commit"] != commit:
                    _run(["git", "fetch", "--depth", "1", "origin", commit], cwd=pdir, check=False)
                    _run(["git", "checkout", commit], cwd=pdir, check=False)
            return {"name": name, "status": "synced", "commit": git_info(pdir)["commit"]}

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            plugin_results = list(ex.map(_sync_one_plugin, plugins))

        target_map = lock.get("full_environment_lock" if full_env else "pinned_plugin_dependencies", {})
        installed = pip_freeze_map(self.python_exe)
        delta_specs = []
        already_satisfied = 0
        for pkg, locked_ver in target_map.items():
            npkg = norm_pkg_name(pkg)
            if npkg in DEFAULT_PROTECTED_PACKAGES and npkg in installed:
                continue
            if installed.get(npkg) == locked_ver:
                already_satisfied += 1
            else:
                delta_specs.append(f"{pkg}=={locked_ver}")

        pip_ok, pip_log = (True, "dry_run") if dry_run else pip_install_specs(delta_specs, python_exe=self.python_exe)
        elapsed = (dt.datetime.now() - t0).total_seconds()
        return {
            "ok": pip_ok and all(x["status"] != "clone_failed" for x in plugin_results),
            "dry_run": dry_run,
            "elapsed_sec": round(elapsed, 2),
            "uv_used": has_uv(),
            "plugins_synced": plugin_results,
            "packages_already_satisfied": already_satisfied,
            "delta_packages_installed": len(delta_specs),
            "delta_specs": delta_specs,
            "log": pip_log if not pip_ok else "",
        }
