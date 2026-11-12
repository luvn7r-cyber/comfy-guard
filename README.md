# comfy-guard 🛡️
### 防炸工作流的 ComfyUI 插件级依赖回滚与云端 GPU 极速同步 CLI（原生支持 AI Agent / Skill）

> **痛点 1**：昨天还能跑的工作流，今天新装了一个插件 / 点了一下更新，`transformers`、`protobuf` 或 `torch` 版本被悄悄顶掉，整个 ComfyUI 直接 `IMPORT FAILED` 全线瘫痪，而且根本不知道该把哪些包降回哪个版本！
> **痛点 2**：在 Colab / RunPod 租着几十块一小时的 A100 / H100，每次开机循环跑十几个插件的 `pip install -r requirements.txt`，甚至把云端预装的 2.5GB `torch+cu12x` 卸掉重下，光等配环境就白烧 10 分钟显卡钱！

**`comfy-guard`** 用**零第三方依赖**的纯标准库 Python 实现了一套专为**人类创作者与 AI Agent（Codex / Claude Code / Antigravity / Cursor）**设计的 ComfyUI 环境护城河：

1. **按插件事务级一键回滚（`add` & `rollback <插件名>`）**：装任何插件前自动做依赖快照，记录它新增了哪些包、篡改了哪些旧包版本；一旦新插件炸了旧工作流，一条命令把代码和被改乱的 Python 库精准还原。
2. **核心 CUDA / PyTorch 保护罩**：自动识别并拦截野生插件 `requirements.txt` 对云端预装 `torch / torchvision / torchaudio / triton / xformers` 的暴力覆盖。
3. **安装前静态冲突阻断（`check` / `--dry-run`）**：还没真正动环境前，先扫出新插件与现有插件的依赖版本死锁并提前阻断。
4. **三合一锁死导出（`export` → `comfy-lock.json`）**：一键锁死 **ComfyUI 本体 Commit SHA + 每个插件 Git Commit SHA + 精确依赖版本 + 工作流所需模型清单**，彻底免疫上游作者删改代码。
5. **云端 GPU 极速省钱同步（`cloud-sync`）**：8 线程并发拉取锁定插件版本 + 计算云端环境纯增量差集（已满足的包 0 触碰）+ 自动调用 `uv` 单批次极速秒装，**将云端冷启动装依赖耗时压缩 80%–90%**。
6. **100% 原生 AI Agent 友好**：全命令支持 `--json` 结构化输出，自带标准 `skills/comfy-guard/SKILL.md`，拖进 `.codex/skills` 或 `.claude/skills` 即可让 AI 自动安全接管你的 ComfyUI 环境。

---

## 一、零依赖安装与极速上手

```bash
# 方式 1：作为全局 CLI 工具安装（注册 comfy-guard 与简写 cguard 命令）
pip install -e .

# 方式 2：在 Colab / 云端免安装单脚本直接运行
python comfy_guard_cli.py --help
```

### 常用命令速览

```bash
# 1. 查看当前 ComfyUI 本体 Commit、已装插件与可回滚事务
comfy-guard -C /path/to/ComfyUI status

# 2. 安装新插件前先干跑预检（检查是否与现有插件存在版本冲突）
comfy-guard -C /path/to/ComfyUI add https://github.com/kijai/ComfyUI-KJNodes.git --dry-run

# 3. 安全安装插件（自动保护 torch/cuda 不被覆盖，并生成回滚快照）
comfy-guard -C /path/to/ComfyUI add https://github.com/kijai/ComfyUI-KJNodes.git

# 4. 发现新插件把工作流搞挂了？按插件名一键回滚代码与被它改动的全部 Python 包！
comfy-guard -C /path/to/ComfyUI rollback ComfyUI-KJNodes

# 5. 工作流跑通后，一键导出三合一锁死快照（顺带扫描工作流里引用的所有模型名）
comfy-guard -C /path/to/ComfyUI export -o comfy-lock.json -w my_workflow.json

# 6. 在 Colab / RunPod 云端开机时，8 线程并发锁版本同步 + uv 仅装增量差集（极省 GPU 时间！）
comfy-guard -C /content/ComfyUI cloud-sync -f comfy-lock.json
```

---

## 二、让 AI Agent（Codex / Claude / Antigravity）接管

1. 将本仓库下的 `skills/comfy-guard` 目录复制到你的 Agent 技能目录：
   - **Codex**: `~/.codex/skills/comfy-guard/`
   - **Antigravity / Gemini**: `~/.gemini/config/skills/comfy-guard/`
   - **Claude Code**: `~/.claude/skills/comfy-guard/`
2. Agent 会自动调用 `--json` 模式获取零废话的结构化输出，在遇到依赖冲突时自动停机汇报，在导入报错时自动执行 `rollback` 自愈。

---

## 三、开源协议
MIT License
