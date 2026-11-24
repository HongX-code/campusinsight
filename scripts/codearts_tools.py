"""CodeArts 资产工具：doctor / bootstrap / split / index / verify。

用法（在工程根目录执行；`python` 需指向已装依赖的 3.10+ 解释器，见 docs §1.0）：
    python scripts/codearts_tools.py doctor              # 环境与命令前缀体检
    python scripts/codearts_tools.py doctor --strict     # 当前解释器不可用时返回非 0（供门禁用）
    python scripts/codearts_tools.py bootstrap           # 一键生成 11_CodeArts使用记录/ 骨架
    python scripts/codearts_tools.py bootstrap --check   # 只检查骨架完整性
    python scripts/codearts_tools.py split               # 从 docs/codearts_prompts.md 拆出独立提示词文件
    python scripts/codearts_tools.py split --check       # 只检查一致性，不写文件
    python scripts/codearts_tools.py index               # 检查 CodeArts 代码库索引覆盖
    python scripts/codearts_tools.py verify              # 交付件口径体检
    python scripts/codearts_tools.py all                 # doctor + split --check + index + verify

设计原则：
- 提示词正文的唯一数据源是 docs/codearts_prompts.md，拆分结果禁止手改；
- 所有子命令幂等、不覆盖已填写内容、不生成任何「已实操」的伪证据；
- 只依赖标准库；可选地用 pptx / pypdf / docx 做交付件检查，缺库时明确标 SKIP；
- doctor 只做诊断，不安装依赖、不修改环境，也不写文件。
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import re
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parents[1]
DOC = ROOT / "docs" / "codearts_prompts.md"
PROMPT_DIR = ROOT / "codearts" / "prompts"
TEMPLATE_DIR = ROOT / "codearts" / "templates"
AGENTS = ROOT / ".codeartsdoer" / "AGENTS.md"
INDEX_FILE = ROOT / ".codeartsdoer" / ".codebase" / "codebase_embedded_file.txt"
SUBMIT_DIR = PROJECT / "11_CodeArts使用记录"
CONSOLE = ROOT / "codearts" / "CodeArts实操作战台.html"
SAMPLE = TEMPLATE_DIR / "填写示例.md"
FENCE = "```"

FILE_MAP = {
    "P0": "P00_system.md",
    "P1": "P01_requirements.md",
    "P2": "P02_bugfix.md",
    "P3": "P03_tests.md",
    "P4": "P04_static_check.md",
    "P5": "P05_pipeline.md",
    "P6": "P06_cloud_resources.md",
    "P7": "P07_deploy.md",
    "P8": "P08_deliverables.md",
    "P9": "P09_ui.md",
    "P10": "P10_opensource.md",
}

TEMPLATE_FILES = (
    "采纳说明.md",
    "排错记录.md",
    "开发日志.md",
    "会话对照表.md",
    "每日检查清单.md",
)

# 旧口径「说明性引用」按行豁免：只有含 LEGACY_HINTS 标记的行才放过，其余命中即漂移。
# 整文件白名单会让新增的旧值静默通过（实测：往 test_report.md 写「效率提升 30%」仍全绿）。
LEGACY_ALLOWLIST = {
    "docs/codearts_prompts.md",
    "docs/codearts_prompts_advanced.md",   # v2.0 进阶文档的禁语清单是反例
    "docs/test_report.md",
    "docs/ui_design.md",
    "docs/video_script.md",
    "scripts/codearts_tools.py",
    "scripts/ci_gate.py",
    ".codeartsdoer/AGENTS.md",             # 常驻约束里的禁用表述是禁令，不是主张
}
LEGACY_HINTS = (
    "此前", "当时", "由 81", "扩到", "历史", "旧口径", "旧自检", "旧源码", "旧文档",
    "旧性能", "旧值", "禁用", "不写", "不把", "不能", "不得", "禁止", "红线",
    "冲突", "硬约束", "扫描", "应为 0", "说明性引用", "说明：", "示例", "见 §2",
    "0 命中", "重新导出", "全绿，但", "并不代表", "不出现",
)
LEGACY_PATTERNS = (
    (r"81 项", "旧自检项数"),
    (r"9700", "旧源码行数"),
    (r"五层保护", "禁用表述（禁令中允许）"),
    (r"96% 匹配", "禁用表述（禁令中允许）"),
    (r"效率提升\s*\d", "未实测的效率百分比"),
    (r"16 份 Markdown", "旧文档份数"),
)

# requirements.txt 包名 -> 可 import 的模块名；doctor 只做存在性检查，不校验版本
REQUIRED_MODULES = {
    "fastapi": "fastapi", "uvicorn": "uvicorn", "pydantic": "pydantic",
    "SQLAlchemy": "sqlalchemy", "PyMySQL": "pymysql", "beautifulsoup4": "bs4",
    "lxml": "lxml", "requests": "requests", "httpx": "httpx",
    "redis": "redis", "python-docx": "docx",
}
OPTIONAL_MODULES = {"python-pptx": "pptx", "pypdf": "pypdf", "playwright": "playwright"}
# 交付件结构检查（PPTX/PDF/DOCX 页数与口径）所需的三个依赖。
# 本地自检里它们是可选的，但门禁与质量证据链里是必需的：缺一个就会把
# 最关键的验收项记为 SKIP，而「失败 0」看上去仍然全绿。
DELIVERABLE_MODULES = {"python-pptx": "pptx", "pypdf": "pypdf", "python-docx": "docx"}

# 提交目录骨架：路径 -> 用途 / 对应提示词 / 必留截图编号
SUBMIT_SUBDIRS = (
    ("01_需求对话", "P1 需求理解与符合度盘点", "#1 需求对话"),
    ("02_代码生成与修改", "P5 构建配置、P8 口径同步", "#2 代码生成 / 修改"),
    ("03_缺陷定位与修复", "P2 两轮会话（先定位、后修复）", "#3 缺陷修复"),
    ("04_构建与测试", "P3 边界用例、P5 流水线门禁", "#4 测试记录 / #5 构建记录"),
    ("05_云资源与部署", "P6 资源开通、P7 部署上线", "#6 云资源 / #7 部署"),
    ("06_会话导出", "Prompt 与生成结果的一一对照", "会话导出 D1—D7"),
)
SUBMIT_ROOT_FILES = (
    ("07_采纳说明.md", "采纳说明.md"),
    ("08_排错记录.md", "排错记录.md"),
    ("09_开发日志.md", "开发日志.md"),
    ("10_会话对照表.md", "会话对照表.md"),
)


# --------------------------------------------------------------------------- doctor
def _probe_code() -> str:
    mods = sorted(set(REQUIRED_MODULES.values()))
    return ("import importlib.util, sys\n"
            f"mods = {mods!r}\n"
            "miss = [m for m in mods if importlib.util.find_spec(m) is None]\n"
            "print('VERSION=' + sys.version.split()[0])\n"
            "print('MISS=' + ','.join(miss))\n")


def _candidates() -> list[tuple[str, list[str]]]:
    """返回 (可复制的命令前缀, argv 前缀)。第一个恒为当前解释器。"""
    out: list[tuple[str, list[str]]] = [("当前解释器", [sys.executable])]
    for label in ("py -3.12", "py -3", "python3.12", "python3", "python"):
        out.append((label, label.split()))
    for venv in (ROOT / ".venv", ROOT / "venv"):
        for exe in (venv / "Scripts" / "python.exe", venv / "bin" / "python"):
            if exe.exists():
                s = str(exe)
                out.append((f'"{s}"', [s]))
    # Windows 常见的默认安装位置；非 Windows 平台上这些路径无意义，直接跳过。
    if os.name == "nt":
        for base in (Path("D:/Python"), Path("C:/Python"),
                     Path.home() / "AppData" / "Local" / "Programs" / "Python"):
            if base.is_dir():
                for exe in sorted(base.glob("Python3*/python.exe")):
                    s = exe.as_posix()
                    out.append((f'"{s}"', [s]))
    return out


def _probe(argv: list[str]) -> tuple[str, list[str]] | None:
    try:
        proc = subprocess.run(argv + ["-c", _probe_code()], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=25)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    version = missing_line = None
    for line in proc.stdout.splitlines():
        if line.startswith("VERSION="):
            version = line[len("VERSION="):].strip()
        elif line.startswith("MISS="):
            missing_line = line[len("MISS="):].strip()
    if version is None or missing_line is None:
        return None
    return version, [x for x in missing_line.split(",") if x]


def cmd_doctor(strict: bool = False) -> int:
    """诊断运行环境和命令前缀；不修改环境、不安装依赖、不写文件。"""
    print("=== 运行环境体检（doctor） ===")
    print(f"工程根：{ROOT}")
    print(f"当前解释器：{sys.executable}  ({sys.version.split()[0]})")

    current = _probe([sys.executable])
    if current is None:
        print("[FAIL] 无法用当前解释器执行探测脚本")
        return 1
    version, missing = current
    ready = not missing
    if ready:
        print(f"[PASS] 当前解释器 {version} 已具备 {len(REQUIRED_MODULES)}/{len(REQUIRED_MODULES)} 项必需依赖")
    else:
        print(f"[WARN] 当前解释器 {version} 缺 {len(missing)} 项依赖：{', '.join(missing)}")
        print("       文档里的 `python` 只是代称；直接照抄会在这里失败（见 docs/codearts_prompts.md §1.0）。")

    # 可选依赖与前端工具
    opt_miss = [pkg for pkg, mod in OPTIONAL_MODULES.items()
                if importlib.util.find_spec(mod) is None]
    print(f"[INFO] 可选依赖：{('缺 ' + ', '.join(opt_miss)) if opt_miss else '无缺失'}"
          "（仅影响交付件结构检查 / 截图重拍，不阻塞自检与评测）")
    deliverable_miss = [pkg for pkg, mod in DELIVERABLE_MODULES.items()
                        if importlib.util.find_spec(mod) is None]
    print(f"[{'PASS' if not deliverable_miss else 'WARN'}] 交付件结构检查依赖："
          + ("齐全" if not deliverable_miss else
             "缺 " + ", ".join(deliverable_miss) + "（证据链采集 / verify --strict 会判失败）"))
    node = shutil.which("node")
    print(f"[{'PASS' if node else 'WARN'}] node：{node or '未安装（前端语法检查会记为 SKIP）'}")

    material = PROJECT / "申报材料"
    locked: list[str] = []
    for pattern in ("*.pptx", "*.pdf", "*.docx"):
        for p in material.glob(pattern):
            try:
                with open(p, "r+b"):
                    pass
            except OSError:
                locked.append(p.name)
    print(f"[{'WARN' if locked else 'PASS'}] 交付件可写："
          + ("被占用 " + ", ".join(locked) + "（先关闭 Office 预览再重建）" if locked else "PPTX / PDF / DOCX 均未被占用"))

    # 找可直接复制的可用前缀
    recommended: str | None = None
    for label, argv in _candidates()[1:]:
        probed = _probe(argv)
        if probed and not probed[1]:
            recommended = label
            tag = "备用可用前缀" if ready else "找到可用解释器"
            print(f"[{'INFO' if ready else 'PASS'}] {tag}：{label}  (Python {probed[0]}，依赖齐全)")
            break
    if recommended is None and not ready:
        print("[FAIL] 未找到依赖齐全的解释器；请先执行：")
        print(f"       {sys.executable} -m pip install -r requirements.txt")

    cmd_prefix = "python" if ready else (recommended or "python")
    print("\n--- 推荐命令前缀（把文档里的 `python` 替换为它） ---")
    if ready:
        print("  python        # 当前解释器即可，文档命令可原样复制")
        print("  例：python scripts/ci_gate.py")
    elif recommended:
        print(f"  {recommended}")
        print(f"  例：{recommended} scripts/ci_gate.py")
        print(f"      {recommended} scripts/codearts_tools.py doctor")
    print("  其他环境：Windows 用 `py -3.12`，Linux/构建机用 `python3.12` 或先激活 venv；")
    print("  激活 venv 后，`python` 就指向正确环境，文档命令无需改动。")

    print("\n--- 开工三步 ---")
    print(f"  1. {cmd_prefix} scripts/codearts_tools.py doctor")
    print(f"  2. {cmd_prefix} scripts/codearts_tools.py bootstrap")
    print(f"  3. {cmd_prefix} scripts/ci_gate.py --dry-run")

    if strict and (not ready or deliverable_miss):
        print("\n[STRICT] 当前解释器不可用，或交付件结构检查依赖不全；请按上面的前缀重跑门禁。")
        if deliverable_miss:
            print(f"       安装：{sys.executable} -m pip install " + " ".join(deliverable_miss))
        return 1
    print(f"\n环境结论：{'READY' if ready else 'USE_ALTERNATE'}（doctor 不修改任何文件）")
    return 0


# --------------------------------------------------------------------------- bootstrap
def _submit_index() -> str:
    today = dt.date.today().isoformat()
    rows = "\n".join(f"| `{d}/` | {p} | {s} |" for d, p, s in SUBMIT_SUBDIRS)
    return f"""# 11 · CodeArts 使用记录（目录索引）

> **状态：骨架已生成，内容全部为空模板，不是 CodeArts 实操证据。**
> 本目录由 `python scripts/codearts_tools.py bootstrap` 幂等生成于 {today}。
> 只有填入真实会话导出、带日期的过程截图、人工复核意见之后，本目录才构成提交材料。
> 生成脚本不会覆盖已填写的文件；`--force` 才会覆盖，慎用于已有真实记录的目录。

## 双闭环 · 三层证据

- 内环（开发闭环）：需求理解 → 生成／修改 → 构建 → 调试定位 → 修复 → 测试 → 集成 → 部署；
- 外环（证据闭环）：提示词 → 会话导出 → 过程截图 → 采纳／排错说明 → 回归输出 → 归档；
- 三层证据：L1 可复现（命令与退出码）、L2 可追溯（Prompt 与产出一一对上）、
  L3 可核验（页数、字数、口径、带日期截图）。
- 完整方法论与逐日排期见 `作品文件/gaoxiao-zhixun/docs/codearts_prompts.md`。

## 目录约定

| 目录 | 对应环节 | 必留证据 |
| --- | --- | --- |
{rows}
| `每日检查/` | Day 1—Day 7 收工检查 | 7 份逐日打勾清单 |
| `07_采纳说明.md` | 哪些建议采纳／未采纳及原因 | 至少 1 条「未采纳」 |
| `08_排错记录.md` | 一次完整的发现→定位→修复→回归闭环 | 含修复前失败、修复后通过 |
| `09_开发日志.md` | 逐日逐次操作台账 | 日期／提示词／产出／验收／截图编号 |
| `10_会话对照表.md` | Prompt 与生成结果一一对应 | 与 `06_会话导出/` 文件名互引 |

命名统一：`YYYYMMDD_环节_编号_简述.png`，例如 `20261108_缺陷修复_03_500根因与回归通过.png`。
每张截图保留日期信息，并**遮挡账号 ID、密钥与内网 IP**。

## 七类截图清单

| 编号 | 内容 | 归属目录 |
| --- | --- | --- |
| #1 | 需求对话（提问全文 + 智能体产出的三类结果） | `01_需求对话/` |
| #2 | 代码生成／修改（Prompt + 生成代码 + 最小 diff） | `02_代码生成与修改/` |
| #3 | 缺陷修复（根因 + 修复 diff + 测试全绿） | `03_缺陷定位与修复/` |
| #4 | 测试记录（用例数 + 自检全通过） | `04_构建与测试/` |
| #5 | 构建记录（门禁各阶段状态） | `04_构建与测试/` |
| #6 | 云资源开通（控制台 + 连通性输出，真实开通后） | `05_云资源与部署/` |
| #7 | 部署记录（服务状态 + 外网浏览器含地址栏） | `05_云资源与部署/` |

## 提交前自检

```bash
python scripts/codearts_tools.py bootstrap --check   # 骨架是否齐全
python scripts/codearts_tools.py verify              # 口径 / 页数 / 字数 / 资产
python scripts/ci_gate.py                            # 9 阶段门禁
```

> 在上述真实记录补齐前，`docs/design_compliance.md`、README、PPT 第 16 页与测试报告中
> CodeArts 一项的状态一律保持「待补齐」，不得提前改成已完成。
"""


def _submit_dir_note(name: str, purpose: str, shot: str) -> str:
    return f"""# {name}

> 骨架说明文件，不是实操证据。请把本目录的真实过程记录放进来后删除或保留本说明。

| 项 | 内容 |
| --- | --- |
| 对应环节 | {purpose} |
| 必留证据 | {shot} |
| 文件命名 | `YYYYMMDD_环节_编号_简述.png` / `D{{1..7}}_会话_{{环节}}.md` |
| 脱敏要求 | 遮挡账号 ID、密钥、内网 IP；保留系统时间或文件名日期 |
| 验收方式 | 与 `09_开发日志.md` 当天行、`10_会话对照表.md` 的 Prompt 编号能互相对上 |

截图与会话必须是 CodeArts 客户端中的真实操作，不能用模板、示意图或本工程代码截图替代。
"""


def _skeleton_files() -> list[tuple[str, str]]:
    """返回 (相对路径, 期望内容)。模板正文直接取 codearts/templates/，保证单一来源。"""
    files: list[tuple[str, str]] = [("00_索引.md", _submit_index())]
    for name, purpose, shot in SUBMIT_SUBDIRS:
        files.append((f"{name}/说明.md", _submit_dir_note(name, purpose, shot)))
    daily = (TEMPLATE_DIR / "每日检查清单.md").read_text(encoding="utf-8")
    for day in range(1, 8):
        banner = f"> 本文件由 bootstrap 生成，对应 Day {day}；填写前它是模板，不构成实操证据。\n\n"
        files.append((f"每日检查/Day{day}_每日检查清单.md", banner + daily))
    for target, source in SUBMIT_ROOT_FILES:
        files.append((target, (TEMPLATE_DIR / source).read_text(encoding="utf-8")))
    return files


def cmd_bootstrap(check: bool = False, force: bool = False, out: str | None = None) -> int:
    """幂等生成提交目录骨架；默认不覆盖任何已存在文件。"""
    target = Path(out).resolve() if out else SUBMIT_DIR
    for source in (*(s for _, s in SUBMIT_ROOT_FILES), "每日检查清单.md"):
        if not (TEMPLATE_DIR / source).exists():
            print(f"[FAIL] 缺少留痕模板：codearts/templates/{source}")
            return 1
    expected = _skeleton_files()
    missing = [rel for rel, _ in expected if not (target / rel).is_file()]

    if check:
        if missing:
            print(f"[FAIL] 提交目录骨架缺 {len(missing)} 个文件：{target}")
            for rel in missing[:12]:
                print("  -", rel)
            if len(missing) > 12:
                print(f"  ... 其余 {len(missing) - 12} 个")
            print("  生成：python scripts/codearts_tools.py bootstrap")
            return 1
        print(f"[PASS] 提交目录骨架齐全（{len(expected)} 个文件）：{target}")
        return 0

    created: list[str] = []
    kept: list[str] = []
    for rel, content in expected:
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not force:
            kept.append(rel)
            continue
        path.write_text(content, encoding="utf-8")
        created.append(rel)

    print(f"提交目录：{target}")
    print(f"[OK] 新建 {len(created)} 个文件，保留 {len(kept)} 个已存在文件（未覆盖）")
    print("     骨架只是空白模板，不构成 CodeArts 实操证据；真实会话与截图需团队按排期归档。")
    print("     下一步：按 docs/codearts_prompts.md §4.2 的 Day 1—Day 7 排期执行；")
    print("             收工前跑 python scripts/codearts_tools.py bootstrap --check")
    return 0


# --------------------------------------------------------------------------- split
def _extract_sections(text: str) -> dict[str, tuple[str, str]]:
    """返回 {编号: (标题, 段落原文)}。P0 取 §2，P1—P10 取 §3 下的三级标题。"""
    sections: dict[str, tuple[str, str]] = {}

    p0 = re.search(r"^## 2\..*?$(.*?)^## 3\.", text, re.M | re.S)
    if p0:
        sections["P0"] = ("P0　系统级常驻提示词", p0.group(1))

    heads = list(re.finditer(r"^### (P\d+)　(.+?)$", text, re.M))
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else text.find("\n## 4.", m.end())
        if end < 0:
            end = len(text)
        sections[m.group(1)] = (m.group(0).lstrip("# ").strip(), text[m.end():end])
    return sections


def _first_block(body: str, lang: str) -> str:
    m = re.search(rf"^{FENCE}{lang}\n(.*?)\n{FENCE}", body, re.M | re.S)
    return m.group(1).rstrip() if m else ""


def _all_blocks(body: str, lang: str) -> list[str]:
    return [b.rstrip() for b in re.findall(rf"^{FENCE}{lang}\n(.*?)\n{FENCE}", body, re.M | re.S)]


def _render(code: str, heading: str, body: str) -> str:
    prompt = _first_block(body, "text")
    if not prompt:
        raise ValueError(f"{code} 段落里找不到 {FENCE}text 提示词正文")

    meta = {k.strip(): v.strip() for k, v in re.findall(r"^\*\*(.+?)\*\*：(.*)$", body, re.M)}
    lines = [
        f"# {heading}",
        "",
        f"> 自动生成：由 `scripts/codearts_tools.py split` 从 `docs/codearts_prompts.md` 拆出。",
        "> **请勿手改本文件**；要改提示词请改主文档后重新执行 `split`。",
        f"> 完整说明、执行顺序、红线与留痕模板见 `docs/codearts_prompts.md`（{code}）。",
        "",
    ]
    for key in ("目的", "建议附加文件", "期望产出", "验收", "留痕", "人工复核"):
        if meta.get(key):
            lines.append(f"**{key}**：{meta[key]}")
    if lines[-1] != "":
        lines.append("")
    lines += [
        "## 提示词正文（整段复制到 CodeArts）",
        "",
        f"{FENCE}text",
        prompt,
        FENCE,
        "",
    ]
    bash = _all_blocks(body, "bash")
    if bash:
        lines += ["## 验收命令", ""]
        for block in bash:
            lines += [f"{FENCE}bash", block, FENCE, ""]
    lines += [
        "---",
        "",
        f"投喂前请先读 `docs/codearts_prompts.md` §1（尤其是 §1.1 的代码库索引重建）与 §2 的 P0 常驻约束；",
        f"本条对应留痕要求见主文档 §4.2 逐日排期与 §5 留痕方案。",
        "",
    ]
    return "\n".join(lines)


def cmd_split(check: bool) -> int:
    if not DOC.exists():
        print(f"[FAIL] 主文档不存在：{DOC}")
        return 1
    text = DOC.read_text(encoding="utf-8")
    sections = _extract_sections(text)
    missing_codes = [c for c in FILE_MAP if c not in sections]
    if missing_codes:
        print(f"[FAIL] 主文档缺少提示词段落：{', '.join(missing_codes)}")
        return 1

    PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    drift: list[str] = []
    for code, filename in FILE_MAP.items():
        heading, body = sections[code]
        try:
            expected = _render(code, heading, body)
        except ValueError as exc:
            print(f"[FAIL] {exc}")
            return 1
        target = PROMPT_DIR / filename
        if check:
            if not target.exists():
                drift.append(f"{filename}（缺失）")
            elif target.read_text(encoding="utf-8") != expected:
                drift.append(f"{filename}（与主文档不一致）")
        else:
            target.write_text(expected, encoding="utf-8")

    if check:
        extra = sorted(p.name for p in PROMPT_DIR.glob("P*.md") if p.name not in FILE_MAP.values())
        drift.extend(f"多余文件：{name}" for name in extra)
        if drift:
            print("[FAIL] 独立提示词文件与主文档不一致：")
            for item in drift:
                print("  -", item)
            print("  修复：python scripts/codearts_tools.py split")
            return 1
        print(f"[PASS] {len(FILE_MAP)} 个独立提示词文件与 docs/codearts_prompts.md 完全一致")
    else:
        print(f"[OK] 已从主文档拆出 {len(FILE_MAP)} 个提示词文件 -> {PROMPT_DIR.relative_to(ROOT)}/")
        print("     接着执行：python scripts/codearts_tools.py split --check")
    return 0


# --------------------------------------------------------------------------- index
def cmd_index() -> int:
    if not INDEX_FILE.exists():
        print(f"[WARN] 未找到 CodeArts 索引文件：{INDEX_FILE.relative_to(ROOT)}")
        print("       在 CodeArts 客户端重新打开工程并全量重建索引后再运行本命令。")
        return 0
    indexed: set[str] = set()
    for line in INDEX_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line:
            indexed.add(Path(line.replace("\\", "/")).name.lower())

    def skip(p: Path) -> bool:
        parts = {x.lower() for x in p.parts}
        return bool(parts & {"__pycache__", ".codeartsdoer", ".workbuddy", ".workbuild"})

    actual = sorted(
        p.name.lower() for p in ROOT.rglob("*.py") if not skip(p)
    )
    missing = [name for name in actual if name not in indexed]
    print(f"索引收录 {len(indexed)} 个文件；工程现有 {len(actual)} 个 .py 文件")
    if not missing:
        print("[PASS] CodeArts 代码库索引覆盖全部 Python 文件")
        return 0
    print(f"[WARN] 索引缺失 {len(missing)} 个文件（智能体看不到这些代码）：")
    for name in missing:
        print("  -", name)
    print("  处理：在 CodeArts 客户端重新打开工作区 → 全量重建代码库索引 → 重跑本命令。")
    print("  不要手工编辑 .codeartsdoer/.codebase/ 下的索引数据库。")
    return 0  # 环境待办，不作为门禁失败


# --------------------------------------------------------------------------- verify
class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, status: str, item: str, detail: str = "") -> None:
        self.rows.append((status, item, detail))

    def finish(self, strict: bool = False) -> int:
        width = max((len(r[1]) for r in self.rows), default=10)
        for status, item, detail in self.rows:
            tail = f"  {detail}" if detail else ""
            print(f"  [{status:4}] {item.ljust(width)}{tail}")
        failed = sum(1 for r in self.rows if r[0] == "FAIL")
        skipped = sum(1 for r in self.rows if r[0] == "SKIP")
        print(f"\n合计 {len(self.rows)} 项：失败 {failed}，跳过 {skipped}，通过 {len(self.rows)-failed-skipped}")
        if failed:
            return 1
        if strict and skipped:
            print(f"[STRICT] 有 {skipped} 项被跳过，交付件体检不完整，判为失败（缺依赖请先安装）。")
            return 1
        return 0


def _py_lines() -> int:
    total = 0
    for folder in ("app", "scripts", "tests"):
        for p in (ROOT / folder).rglob("*.py"):
            if "__pycache__" not in p.parts:
                total += len(p.read_text(encoding="utf-8").splitlines())
    return total


def _front_lines() -> int:
    return sum(
        len((ROOT / rel).read_text(encoding="utf-8").splitlines())
        for rel in ("app/static/js/app.js", "app/static/css/style.css", "app/templates/index.html")
    )


def _endpoints() -> int:
    n = 0
    for p in (ROOT / "app").rglob("*.py"):
        n += len(re.findall(r"@(?:app|router)\.(?:get|post|put|patch|delete)\b",
                            p.read_text(encoding="utf-8")))
    return n


def _metrics() -> dict:
    """现场采集快照；缺失时返回空字典，由 verify 明确报 FAIL 而不是用旧值兜底。"""
    p = ROOT / "docs" / "metrics.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def cmd_verify(strict: bool = False) -> int:
    r = Report()
    print("=== 交付件口径体检 ===")

    # 1 实测统计（不作为硬门槛，仅打印，供人工核对文档概数）
    py, front, endpoints = _py_lines(), _front_lines(), _endpoints()
    docs_md = sorted((ROOT / "docs").glob("*.md"))
    shots = sorted((ROOT / "docs").glob("shot-*.png"))
    print(f"实测：Python {py} 行 / 前端 {front} 行 / 合计 {py + front} 行；"
          f"端点 {endpoints}；docs {len(docs_md)} 份；截图 {len(shots)} 张")
    r.add("INFO", "工程规模实测", f"{py + front} 行 / {endpoints} 端点 / {len(docs_md)} 份 docs / {len(shots)} 张截图")

    # 2 文档概数是否跟得上实测
    for rel in ("README.md", "docs/architecture.md", "docs/design_compliance.md",
                "docs/codearts_prompts.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        m = re.search(
            r"约\s*\*{0,2}\s*(\d{4,5})\s*\*{0,2}\s*行源码|源码约\s*\*{0,2}\s*(\d{4,5})\s*\*{0,2}\s*行",
            text,
        )
        if m:
            stated = int(m.group(1) or m.group(2))
            # 概数目标由现场值按百位现算，不再比对某个写死的历史基线；
            # 这样新增代码不会让「容差只剩 2 行」这类隐患悄悄回来。
            expected = round((py + front) / 100) * 100
            ok = abs(stated - expected) <= 50
            r.add("PASS" if ok else "FAIL", f"{rel} 行数概数",
                  f"写 {stated} / 应写 {expected}（实测 {py + front}）" if not ok
                  else f"写 {stated} / 实测 {py + front}")
        # 位数不设上限但至少 4 位：源码突破一万后 \d{4} 会把 10100 截成 1010 造成误报，
        # 而直接放宽到 \d+ 又会误抓「Python 3.12」里的版本号 3。
        m_detail = re.search(r"Python\s*\*{0,2}\s*(\d{4,})", text)
        if m_detail:
            stated = int(m_detail.group(1))
            expected = round(py / 100) * 100
            ok = abs(stated - expected) <= 50
            r.add("PASS" if ok else "FAIL", f"{rel} Python 明细",
                  f"写 {stated} / 应写 {expected}（实测 {py}）" if not ok
                  else f"写 {stated} / 实测 {py}")
        m_front = re.search(r"前端\s*\*{0,2}\s*(\d{4,})", text)
        if m_front:
            stated = int(m_front.group(1))
            ok = stated == front
            r.add("PASS" if ok else "FAIL", f"{rel} 前端明细",
                  f"写 {stated} / 实测 {front}" + ("" if ok else "（需同步口径）"))
        m2 = re.search(r"Markdown 文档 \*\*(\d+) 份\*\*", text)
        if m2:
            stated = int(m2.group(1))
            ok = stated == len(docs_md) + 1  # docs + 根 README
            r.add("PASS" if ok else "FAIL", f"{rel} 文档份数",
                  f"写 {stated} / 实测 {len(docs_md)} + README")

    for rel in ("docs/architecture.md", "docs/design_compliance.md"):
        m = re.search(r"(\d+) 个路由端点", (ROOT / rel).read_text(encoding="utf-8"))
        stated = int(m.group(1)) if m else -1
        r.add("PASS" if stated == endpoints else "FAIL", f"{rel} 端点数", f"写 {stated} / 实测 {endpoints}")

    # 3 关键口径在交付件里都存在
    smoke_n = _metrics().get("checks", {}).get("smoke_passed", -1)
    r.add("PASS" if smoke_n > 0 else "FAIL", "自检项数快照",
          f"metrics.json -> {smoke_n} 项" if smoke_n > 0
          else "缺少现场快照，先跑 python scripts/evidence.py --collect")
    smoke_key = f"{smoke_n} 项"
    checks = {
        "README.md": (smoke_key, "Arctic Glass", "四层保护 + 规则校验"),
        "docs/test_report.md": (smoke_key, "四层保护 + 规则校验"),
        "docs/design_compliance.md": (smoke_key, "CodeArts"),
        "docs/codearts_prompts.md": ("Day 1", "Day 7", "P10", "§9"),
    }
    for rel, keys in checks.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        miss = [k for k in keys if k not in text]
        r.add("PASS" if not miss else "FAIL", f"{rel} 关键口径",
              "齐全" if not miss else "缺 " + " / ".join(miss))

    # 4 旧值扫描（行级豁免：白名单文件里只有说明性引用行可放过）
    # 常驻提示词也纳入扫描：它每次会话都会被加载，一旦写回旧基线影响面最大。
    scope: list[Path] = [ROOT / "README.md", ROOT / ".codeartsdoer" / "AGENTS.md", *docs_md,
                         *(ROOT / "scripts").glob("*.py"),
                         *(PROJECT / "申报材料").glob("*.py")]
    scope = [p for p in scope if p.exists()]
    hits: list[str] = []
    for p in scope:
        try:
            rel = p.relative_to(ROOT).as_posix()
        except ValueError:
            rel = "申报材料/" + p.name
        soft = rel in LEGACY_ALLOWLIST
        for lineno, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if soft and any(h in line for h in LEGACY_HINTS):
                continue
            for pattern, label in LEGACY_PATTERNS:
                for m in re.finditer(pattern, line):
                    hits.append(f"{rel}:{lineno} {label} -> {m.group(0)!r}")
    r.add("PASS" if not hits else "FAIL", "旧口径扫描",
          "0 命中（白名单外）" if not hits else f"{len(hits)} 命中")
    for h in hits[:12]:
        print("       ", h)

    # 5 CodeArts 资产完整性
    prompt_missing = [f for f in FILE_MAP.values() if not (PROMPT_DIR / f).exists()]
    r.add("PASS" if not prompt_missing else "FAIL", "独立提示词文件",
          f"{len(FILE_MAP) - len(prompt_missing)}/{len(FILE_MAP)}" +
          ("" if not prompt_missing else " 缺 " + ", ".join(prompt_missing)))
    tpl_missing = [f for f in TEMPLATE_FILES if not (TEMPLATE_DIR / f).exists()]
    r.add("PASS" if not tpl_missing else "FAIL", "留痕模板",
          f"{len(TEMPLATE_FILES) - len(tpl_missing)}/{len(TEMPLATE_FILES)}" +
          ("" if not tpl_missing else " 缺 " + ", ".join(tpl_missing)))
    # 常驻上下文最容易静默漂移：既要锚点齐全，也不许写死易变数字。
    agents_text = AGENTS.read_text(encoding="utf-8") if AGENTS.exists() else ""
    agents_keys = ("硬约束", "docs/metrics.json", "scripts/evidence.py",
                   "scripts/mutation_audit.py", "127.0.0.1", "codearts_prompts_advanced.md")
    agents_miss = [k for k in agents_keys if k not in agents_text]
    stale_numbers = re.findall(r"约 \d{4,5} 行源码|当前 \d+ 项断言|docs（\d+ 份 Markdown", agents_text)
    agents_ok = not agents_miss and not stale_numbers
    r.add("PASS" if agents_ok else "FAIL", "AGENTS.md 常驻约束",
          "锚点齐全、口径指向现场快照" if agents_ok
          else "缺 " + " / ".join(agents_miss) + ("；含写死的旧基线数字" if stale_numbers else ""))
    advanced = ROOT / "docs" / "codearts_prompts_advanced.md"
    adv_text = advanced.read_text(encoding="utf-8") if advanced.exists() else ""
    adv_keys = ("## 2. B0", "## 3. A0", "### A1", "### A10", "## 5. 验收矩阵",
                "## 7. 状态边界", "## 9. 维护规则", "docs/metrics.json")
    adv_miss = [k for k in adv_keys if k not in adv_text]
    r.add("PASS" if not adv_miss else "FAIL", "进阶提示词文档",
          "B0/A0/A1—A10/验收矩阵/状态边界齐全" if not adv_miss else "缺 " + " / ".join(adv_miss))
    for script in ("scripts/codearts_tools.py", "scripts/ci_gate.py", "scripts/sync_metrics.py",
                   "scripts/evidence.py", "scripts/scale_sync.py", "scripts/mutation_audit.py",
                   "start_lan.bat", "start_lan.sh"):
        r.add("PASS" if (ROOT / script).exists() else "FAIL", script, "存在" if (ROOT / script).exists() else "缺失")
    pane_text = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
    pane_ok = all(k in pane_text for k in ("qaTabs", "qaPaneLab", "btnLabRun")) \
        and "qaPaneDevice" not in pane_text
    r.add("PASS" if pane_ok else "FAIL", "AI 助手双页签",
          "问答 / 解析实验室齐全，已移除多端接入" if pane_ok else "缺少关键锚点或仍残留多端接入")
    run_text = (ROOT / "run.py").read_text(encoding="utf-8")
    lan_ok = "--lan" in run_text and "def lan_addresses" in run_text
    r.add("PASS" if lan_ok else "FAIL", "run.py 局域网模式",
          "--lan 与地址探测就绪" if lan_ok else "缺少 --lan 或地址探测")
    console_ok = CONSOLE.exists() and all(
        k in CONSOLE.read_text(encoding="utf-8")
        for k in ("双闭环", "三层证据", "Day 1", "localStorage")
    )
    r.add("PASS" if console_ok else "FAIL", "实操作战台 HTML",
          "存在且含双闭环/逐日排期/本地进度" if console_ok else "缺失或关键区块不完整")
    sample_ok = SAMPLE.exists() and "示例" in SAMPLE.read_text(encoding="utf-8")
    r.add("PASS" if sample_ok else "FAIL", "填写示例", "存在且标注为示例" if sample_ok else "缺失或未标注")
    export_ok = (PROJECT / "申报材料" / "export_pdf.py").exists()
    r.add("PASS" if export_ok else "FAIL", "申报材料/export_pdf.py", "存在" if export_ok else "缺失")

    # 5b 提交目录骨架：没生成不算失败（实操由团队完成），生成了就必须齐全
    if not SUBMIT_DIR.exists():
        r.add("SKIP", "提交目录骨架", "未生成（bootstrap 可一键创建；不阻塞本地构建）")
    else:
        skel_missing = [rel for rel, _ in _skeleton_files() if not (SUBMIT_DIR / rel).is_file()]
        r.add("PASS" if not skel_missing else "FAIL", "提交目录骨架",
              f"{len(_skeleton_files()) - len(skel_missing)}/{len(_skeleton_files())} 齐全"
              + ("" if not skel_missing else " 缺 " + ", ".join(skel_missing[:4])))

    # 6 交付件（可选依赖）
    try:
        from pptx import Presentation
        from pypdf import PdfReader
        from docx import Document

        material = PROJECT / "申报材料"

        def rank(pattern: str, prefer: tuple[str, ...], avoid: tuple[str, ...]):
            """按「非初赛阶段 → 正式模板/作品名 → 修改时间」排序候选交付件。"""
            candidates = [p for p in material.glob(pattern) if p.is_file()]
            candidates.sort(
                key=lambda p: (
                    0 if any(x in p.name for x in avoid) else 1,
                    1 if any(x in p.name for x in prefer) else 0,
                    p.stat().st_mtime,
                ),
                reverse=True,
            )
            return candidates

        def choose_ok(paths: list[Path], reader, expect: int):
            """目录可能同时存在多个版本：任一候选满足期望即通过，并报告实际选中的文件。"""
            checked: list[tuple[Path, int]] = []
            for p in paths[:6]:
                try:
                    value = reader(p)
                except Exception:
                    continue
                checked.append((p, value))
                if value == expect:
                    return p, value
            return checked[0] if checked else (None, None)

        pptx_paths = rank("*答辩PPT*.pptx", ("模板2",), ("初赛阶段",))
        pdf_paths = rank("*答辩PPT*.pdf", ("模板2",), ("初赛阶段",))
        docx_paths = rank("*作品信息收集表*.docx", ("模板1",), ("初赛阶段",))

        for label, paths, reader in (
            ("PPTX 20 页", pptx_paths, lambda p: len(Presentation(str(p)).slides)),
            ("PDF 20 页", pdf_paths, lambda p: len(PdfReader(str(p)).pages)),
        ):
            if not paths:
                r.add("SKIP", label, "未找到文件")
                continue
            path, value = choose_ok(paths, reader, 20)
            if path is None:
                r.add("SKIP", label, "候选文件均无法打开")
                continue
            r.add("PASS" if value == 20 else "FAIL", label,
                  f"{value}（期望 20）· {path.name}（候选 {len(paths)} 份）")

        # DOCX 要同时满足 16 行表格与 P0—P10 口径；多版本共存时取第一份合规的
        docx_first: tuple[Path, int, str] | None = None
        docx_ok: Path | None = None
        for p in docx_paths[:6]:
            d = Document(str(p))
            rows = sum(len(t.rows) for t in d.tables)
            joined = "\n".join(c.text for t in d.tables for row in t.rows for c in row.cells)
            if docx_first is None:
                docx_first = (p, rows, joined)
            if rows == 16 and "P0—P10" in joined:
                docx_ok = p
                break
        if not docx_paths:
            r.add("SKIP", "DOCX 16 行", "未找到文件")
            r.add("SKIP", "DOCX 含 CodeArts 提示词口径", "未找到文件")
        else:
            chosen = docx_ok or (docx_first[0] if docx_first else None)
            if chosen is None:
                r.add("SKIP", "DOCX 16 行", "候选文件均无法打开")
            else:
                d = Document(str(chosen))
                rows = sum(len(t.rows) for t in d.tables)
                joined = "\n".join(c.text for t in d.tables for row in t.rows for c in row.cells)
                r.add("PASS" if rows == 16 else "FAIL", "DOCX 16 行",
                      f"{rows}（期望 16）· {chosen.name}（候选 {len(docx_paths)} 份）")
                r.add("PASS" if "P0—P10" in joined else "FAIL", "DOCX 含 CodeArts 提示词口径",
                      ("已写入 · " if "P0—P10" in joined else "缺失 · ") + chosen.name)
    except ImportError as exc:
        r.add("SKIP", "PPTX/PDF/DOCX 结构检查", f"缺依赖 {exc.name}，安装 python-pptx / pypdf / python-docx 后启用")

    return r.finish(strict)


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="CodeArts 资产工具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_doctor = sub.add_parser("doctor", help="运行环境与命令前缀体检")
    p_doctor.add_argument("--strict", action="store_true", help="当前解释器依赖不全时返回非 0")
    p_boot = sub.add_parser("bootstrap", help="一键生成 11_CodeArts使用记录/ 骨架")
    p_boot.add_argument("--check", action="store_true", help="只检查骨架完整性，不写文件")
    p_boot.add_argument("--force", action="store_true", help="覆盖已存在文件（默认保留）")
    p_boot.add_argument("--out", help="自定义输出目录（默认为比赛项目总目录下的 11_CodeArts使用记录/）")
    p_split = sub.add_parser("split", help="从主文档拆出独立提示词文件")
    p_split.add_argument("--check", action="store_true", help="只检查一致性，不写文件")
    sub.add_parser("index", help="检查 CodeArts 代码库索引覆盖")
    p_verify = sub.add_parser("verify", help="交付件口径体检")
    p_verify.add_argument("--strict", action="store_true",
                          help="任一检查被 SKIP 也判失败（供门禁与证据链使用）")
    p_all = sub.add_parser("all", help="doctor + split --check + index + verify")
    p_all.add_argument("--strict", action="store_true",
                       help="同 verify --strict，且严格检查解释器依赖")
    args = ap.parse_args()

    if args.cmd == "doctor":
        return cmd_doctor(args.strict)
    if args.cmd == "bootstrap":
        return cmd_bootstrap(args.check, args.force, args.out)
    if args.cmd == "split":
        return cmd_split(args.check)
    if args.cmd == "index":
        return cmd_index()
    if args.cmd == "verify":
        return cmd_verify(args.strict)
    # all
    print("--- 1/4 运行环境 ---")
    rc0 = cmd_doctor(args.strict)
    print("\n--- 2/4 提示词一致性 ---")
    rc1 = cmd_split(check=True)
    print("\n--- 3/4 代码库索引 ---")
    rc2 = cmd_index()
    print("\n--- 4/4 交付件体检 ---")
    rc3 = cmd_verify(args.strict)
    return rc0 or rc1 or rc2 or rc3


if __name__ == "__main__":
    sys.exit(main())
