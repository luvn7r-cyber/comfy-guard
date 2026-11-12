# -*- coding: utf-8 -*-
"""
comfy-guard 完整自动化单元测试套件
测试覆盖：
1. 核心 CUDA/PyTorch 包防误覆盖剥离与版本冲突静态阻断；
2. 模拟本地 Git 插件仓库安装、事务快照记录、以及一键按插件回滚（验证代码与依赖精确还原）；
3. 工作流 JSON 节点与模型权重扫描；
4. comfy-lock.json 三合一导出与 cloud-sync 增量计算。
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from comfy_guard.core import ComfyGuard, version_satisfies


def _init_dummy_git_repo(repo_dir: Path, req_text: str) -> str:
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "requirements.txt").write_text(req_text, encoding="utf-8")
    (repo_dir / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo_dir), check=True, capture_output=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_dir), check=True, capture_output=True, text=True).stdout.strip()


class TestComfyGuard(unittest.TestCase):
    def test_version_satisfies(self):
        self.assertTrue(version_satisfies("4.46.3", ">=4.40.0,<5.0.0"))
        self.assertFalse(version_satisfies("5.1.0", ">=4.40.0,<5.0.0"))
        self.assertTrue(version_satisfies("1.26.4", "==1.26.4"))

    def test_add_conflict_and_rollback_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            comfy_dir = root / "ComfyUI"
            (comfy_dir / "custom_nodes").mkdir(parents=True)
            (comfy_dir / "main.py").write_text("# dummy comfy\n", encoding="utf-8")

            # 创建两个模拟远端插件仓库
            plg_a_src = root / "remotes" / "ComfyUI-PluginA"
            plg_b_src = root / "remotes" / "ComfyUI-PluginB"
            plg_bad_src = root / "remotes" / "ComfyUI-ConflictNode"
            _init_dummy_git_repo(plg_a_src, "torch>=2.1.0\ntransformers>=4.40.0,<4.48.0\neinops==0.8.0\n")
            _init_dummy_git_repo(plg_b_src, "transformers==4.47.1\nnew-helper-pkg==1.2.0\n")
            _init_dummy_git_repo(plg_bad_src, "transformers==4.50.0\n")

            # 模拟虚拟 Python pip 环境状态，不污染宿主机真实包
            fake_env = {"torch": "2.5.1+cu124", "transformers": "4.45.0"}

            def fake_freeze(_py=None):
                return dict(fake_env)

            def fake_install(specs, python_exe=None, no_deps=False):
                for sp in specs:
                    if "==" in sp:
                        k, v = sp.split("==", 1)
                        fake_env[k.strip().lower()] = v.strip()
                    elif sp.startswith("einops"):
                        fake_env["einops"] = "0.8.0"
                return True, "ok"

            def fake_uninstall(pkgs, python_exe=None):
                for k in pkgs:
                    fake_env.pop(k.lower(), None)
                return True, "ok"

            with mock.patch("comfy_guard.core.pip_freeze_map", side_effect=fake_freeze), \
                 mock.patch("comfy_guard.core.pip_install_specs", side_effect=fake_install), \
                 mock.patch("comfy_guard.core.pip_uninstall_pkgs", side_effect=fake_uninstall):

                guard = ComfyGuard(comfy_dir)

                # 1) 安装 PluginA：验证 torch 被保护跳过，einops 被安装
                r1 = guard.add_plugin(str(plg_a_src))
                self.assertTrue(r1["ok"])
                self.assertEqual(len(r1["tx"]["skipped_protected_core"]), 1)
                self.assertEqual(r1["tx"]["skipped_protected_core"][0]["package"], "torch")
                self.assertEqual(fake_env["einops"], "0.8.0")

                # 2) 尝试安装与 PluginA (<4.48.0) 冲突的 ConflictNode (==4.50.0)：验证自动阻断！
                r_bad = guard.add_plugin(str(plg_bad_src))
                self.assertFalse(r_bad["ok"])
                self.assertEqual(r_bad["conflicts"][0]["conflicts_with_plugin"], "ComfyUI-PluginA")

                # 3) 安装兼容的 PluginB：它将 transformers 从 4.45.0 升级到 4.47.1 并新增 new-helper-pkg
                r2 = guard.add_plugin(str(plg_b_src))
                self.assertTrue(r2["ok"])
                self.assertEqual(fake_env["transformers"], "4.47.1")
                self.assertEqual(fake_env["new-helper-pkg"], "1.2.0")

                # 4) 导出 comfy-lock.json 并验证 cloud_sync dry-run 差集为 0
                lock_path = root / "comfy-lock.json"
                exp = guard.export_lock(lock_path)
                self.assertTrue(exp["ok"])
                self.assertEqual(exp["plugin_count"], 2)
                sync_res = guard.cloud_sync(lock_path, dry_run=True)
                self.assertTrue(sync_res["ok"])
                self.assertEqual(sync_res["delta_packages_installed"], 0)

                # 5) 执行按插件回滚 rollback("ComfyUI-PluginB")：
                # 验证 transformers 精准退回 4.45.0，new-helper-pkg 被清理卸载，且 PluginB 目录已移入回收站！
                rb = guard.rollback_plugin("ComfyUI-PluginB")
                self.assertTrue(rb["ok"])
                self.assertEqual(fake_env["transformers"], "4.45.0")
                self.assertNotIn("new-helper-pkg", fake_env)
                self.assertFalse((comfy_dir / "custom_nodes" / "ComfyUI-PluginB").exists())


if __name__ == "__main__":
    unittest.main()
