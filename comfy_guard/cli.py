# -*- coding: utf-8 -*-
"""
comfy-guard 命令行界面 (Human + AI Agent 双模式)
加 --json 参数即可输出严格结构化 JSON，供 Codex / Claude / Antigravity 直接解析决策。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from .core import ComfyGuard, parse_requirements_file

sys.stdout.reconfigure(encoding="utf-8")


def emit(data: dict, as_json: bool, human_lines=None):
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        if human_lines:
            for line in human_lines:
                print(line)
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2))
    sys.exit(0 if data.get("ok", True) else 1)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="comfy-guard",
        description="ComfyUI 防炸依赖管理与云端极速部署 CLI（支持按插件事务级回滚 & Agent JSON 模式）",
    )
    p.add_argument(
        "-C",
        "--comfy-dir",
        type=Path,
        default=Path(os.environ.get("COMFYUI_DIR", ".")),
        help="ComfyUI 根目录路径（默认取环境变量 COMFYUI_DIR 或当前目录）",
    )
    p.add_argument("--python", default=sys.executable, help="目标 ComfyUI 使用的 Python 解释器路径")
    p.add_argument("--json", action="store_true", help="输出纯 JSON 结构（推荐 AI Agent 使用）")

    sub = p.add_subparsers(dest="cmd", required=True)

    # status
    sub.add_parser("status", help="查看当前 ComfyUI 本体、已装插件、Git Commit 与事务数量")

    # history
    sub.add_parser("history", help="查看插件安装/更新与依赖变动事务历史")

    # check
    sp_chk = sub.add_parser("check", help="安装前对 requirements.txt 或依赖列表做静态冲突预检")
    sp_chk.add_argument("-r", "--requirements", type=Path, help="待检查的 requirements.txt 路径")
    sp_chk.add_argument("specs", nargs="*", help="直接传入依赖表达式，如 'transformers==4.46.3'")
    sp_chk.add_argument("--allow-core-override", action="store_true", help="允许修改受保护的 torch/cuda 核心包")

    # add
    sp_add = sub.add_parser("add", help="安全安装/更新插件（自动过滤核心包重装、预检冲突并记录回滚快照）")
    sp_add.add_argument("repo", help="插件 Git 仓库 URL 或本地路径")
    sp_add.add_argument("--commit", default=None, help="可选锁定到指定的 Git Commit SHA")
    sp_add.add_argument("--dry-run", action="store_true", help="仅做冲突与依赖变动预演，不实际修改环境")
    sp_add.add_argument("--allow-core-override", action="store_true", help="允许插件覆盖 torch/cuda 核心大包")
    sp_add.add_argument("--force", action="store_true", help="忽略冲突警告强制安装")

    # rollback
    sp_rb = sub.add_parser("rollback", help="按插件名或事务 ID 一键回滚代码与被篡改的 Python 依赖")
    sp_rb.add_argument("target", help="插件文件夹名称（如 ComfyUI-KJNodes）或事务 ID（如 tx_0002_...）")
    sp_rb.add_argument("--keep-added", action="store_true", help="保留该插件新增的独立包，仅还原被升降级的旧包")

    # export
    sp_exp = sub.add_parser("export", help="导出三合一锁文件 comfy-lock.json（锁死本体 + 插件 Commit + 依赖版本）")
    sp_exp.add_argument("-o", "--output", type=Path, default=Path("comfy-lock.json"), help="输出锁文件路径")
    sp_exp.add_argument("-w", "--workflow", type=Path, nargs="*", help="可选：扫描指定工作流 JSON 并附带所需模型清单")

    # cloud-sync
    sp_sync = sub.add_parser("cloud-sync", help="云端 GPU 极速同步：并发拉取锁定 Commit + uv 仅装增量依赖差集")
    sp_sync.add_argument("-f", "--lock-file", type=Path, default=Path("comfy-lock.json"), help="输入的 comfy-lock.json")
    sp_sync.add_argument("--full-env", action="store_true", help="同步全量非核心包（默认仅同步插件显式依赖差集，最快最省时）")
    sp_sync.add_argument("--workers", type=int, default=8, help="并发克隆插件线程数")
    sp_sync.add_argument("--dry-run", action="store_true", help="仅查看在当前机器上需要安装的增量差集")

    # scan-workflow
    sp_scan = sub.add_parser("scan-workflow", help="扫描工作流 JSON 中引用的所有节点类型与模型权重文件名")
    sp_scan.add_argument("workflows", type=Path, nargs="+", help="一个或多个 ComfyUI 工作流 JSON 文件")

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    guard = ComfyGuard(comfy_dir=args.comfy_dir, python_exe=args.python)

    if args.cmd == "status":
        res = guard.status()
        lines = [
            f"ComfyUI 目录 : {res['comfy_dir']}",
            f"本体 Commit  : {res['comfy_core']['commit'][:12] or '(非Git目录)'}",
            f"已安装插件数 : {res['plugin_count']} 个 | 可回滚事务记录: {res['transaction_count']} 条 | uv 加速: {'已开启' if res['uv_accelerated'] else '未安装'}",
        ]
        for plg in res["plugins"]:
            lines.append(f"  - {plg['name']} @ {plg['commit'][:10] or 'local'} (依赖项: {len(plg['requirements'])})")
        emit(res, args.json, lines)

    elif args.cmd == "history":
        txs = guard.load_journal()
        data = {"ok": True, "transactions": txs}
        lines = [f"共 {len(txs)} 条插件事务记录："]
        for t in txs:
            st = "[已回滚]" if t.get("rolled_back") else "[生效中]"
            lines.append(
                f"  {st} {t['tx_id']} | {t['action']} {t['plugin']} | 新增包:{len(t['packages_added'])} 变更包:{len(t['packages_changed'])}"
            )
        emit(data, args.json, lines)

    elif args.cmd == "check":
        specs = list(args.specs or [])
        if args.requirements:
            specs.extend(parse_requirements_file(args.requirements))
        res = guard.check_requirements_conflicts(specs, allow_core_override=args.allow_core_override)
        lines = [
            f"预检结论: {'通过（零冲突）' if res['ok'] else '阻断（发现版本冲突！）'}",
            f"自动跳过受保护核心包: {len(res['skipped_protected_core'])} 个 | 预计安装/变更: {len(res['planned_changes'])} 个",
        ]
        for c in res["conflicts"]:
            lines.append(f"  [冲突] {c['package']}: 新请求 {c['new_request']} 与已装插件 {c['conflicts_with_plugin']} ({c['existing_constraint']}) 冲突！")
        emit(res, args.json, lines)

    elif args.cmd == "add":
        res = guard.add_plugin(
            args.repo,
            commit=args.commit,
            dry_run=args.dry_run,
            allow_core_override=args.allow_core_override,
            force=args.force,
        )
        if not res["ok"]:
            emit(res, args.json, [f"[拦截/失败] {res.get('error')}", json.dumps(res.get("conflicts", []), ensure_ascii=False, indent=2)])
        if args.dry_run:
            emit(res, args.json, [f"[预演通过] 插件 {res['plugin']} 可安全安装，计划变更包数: {len(res['precheck']['planned_changes'])}"])
        tx = res["tx"]
        lines = [
            f"[安装成功] 插件: {tx['plugin']} (事务ID: {tx['tx_id']})",
            f"  Commit: {tx['new_commit'][:12]} | 新增包: {len(tx['packages_added'])} 个 | 版本变更包: {len(tx['packages_changed'])} 个",
            f"  如需一键撤销本次安装及所有依赖改动，执行: comfy-guard -C \"{args.comfy_dir}\" rollback {tx['plugin']}",
        ]
        emit(res, args.json, lines)

    elif args.cmd == "rollback":
        res = guard.rollback_plugin(args.target, purge_orphan_added=not args.keep_added)
        if not res["ok"]:
            emit(res, args.json, [f"[回滚失败] {res.get('error')}"])
        lines = [
            f"[回滚完成] 已撤销事务 {res['rolled_back_tx']} ({res['plugin']})",
            f"  还原被改动的包版本: {res['restored_packages'] or '无'}",
            f"  清理独享新增包    : {res['uninstalled_orphans'] or '无'}",
            f"  代码目录操作      : {res['code_action']}",
        ]
        emit(res, args.json, lines)

    elif args.cmd == "export":
        res = guard.export_lock(args.output, workflow_paths=args.workflow)
        lines = [
            f"[锁文件已导出] {res['lock_file']}",
            f"  锁定插件: {res['plugin_count']} 个 | 锁定插件直接依赖: {res['pinned_direct_count']} 个 | 关联模型权重: {res['referenced_models_count']} 个",
        ]
        emit(res, args.json, lines)

    elif args.cmd == "cloud-sync":
        res = guard.cloud_sync(args.lock_file, full_env=args.full_env, workers=args.workers, dry_run=args.dry_run)
        lines = [
            f"[云端极速同步{'预演' if args.dry_run else '完成'}] 耗时: {res['elapsed_sec']}s (uv加速: {res['uv_used']})",
            f"  同步插件: {len(res['plugins_synced'])} 个 | 云端已满足跳过包: {res['packages_already_satisfied']} 个 | 仅需增量安装包: {res['delta_packages_installed']} 个",
        ]
        emit(res, args.json, lines)

    elif args.cmd == "scan-workflow":
        info = guard.scan_workflows(args.workflows)
        info["ok"] = True
        lines = [
            f"扫描到节点类型 ({len(info['class_types'])} 个): {', '.join(info['class_types'][:15])}{'...' if len(info['class_types']) > 15 else ''}",
            f"引用模型权重 ({len(info['referenced_models'])} 个):",
        ] + [f"  - {m}" for m in info["referenced_models"]]
        emit(info, args.json, lines)


if __name__ == "__main__":
    main()
