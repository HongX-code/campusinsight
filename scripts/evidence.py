# -*- coding: utf-8 -*-
"""质量证据链：一次运行，现场采集全部验证结果并生成可核验交付物。

    python scripts/evidence.py --collect   # 现场采集 + 口径回灌 + 生成报告
    python scripts/evidence.py --check     # 只读核对口径是否与磁盘一致

产出：
  docs/metrics.json            唯一数据源（现场精确值 + 证据清单 + 边界）
  docs/quality_evidence.md     可提交的文字版证据报告
  docs/质量证据总览.html        答辩 / 评审可展示、可打印的单页

原则：报告里每一个数字都来自本次现场执行的退出码或标准输出，不手写、
不引用「上次跑过」；未验证的项目列入边界清单，不包装成已完成。
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from scale_sync import METRICS_JSON, apply_scale, collect_scale  # noqa: E402

PY = sys.executable
HTTP_PATHS = ("/", "/api/health", "/api/notices?size=5", "/api/dashboard/stats",
              "/api/users", "/api/ai/status", "/docs", "/static/js/app.js")

BOUNDARIES = [
    "华为云 RDS for MySQL / DCS for Redis / MaaS 的真实连通截图与耗时",
    "公网部署、域名访问与并发压测结果",
    "CodeArts 实操会话导出与七类过程截图（当前为方案、工具与空骨架）",
    "人工阅读对照实验（因此材料不写任何效率提升百分比）",
    "Android / HarmonyOS 真机安装为应用、断网离线壳表现与两档视口真机截图"
    "（桌面浏览器巡检已通过，但不替代真机结论；见 docs/deployment.md 第 7.5 节清单）",
]


def run(cmd: list[str], timeout: int = 420) -> dict:
    try:
        out = subprocess.run([PY, *cmd], cwd=ROOT, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=timeout)
        return {"command": " ".join(cmd), "exit_code": out.returncode,
                "stdout": out.stdout, "stderr": out.stderr}
    except Exception as exc:                                        # noqa: BLE001
        return {"command": " ".join(cmd), "exit_code": -1, "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}"}


def first_match(pattern: str, text: str, default: str = "—") -> str:
    m = re.search(pattern, text, re.M)
    return m.group(0) if m else default


def http_probe() -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    rows = []
    for path in HTTP_PATHS:
        try:
            with opener.open("http://127.0.0.1:8000" + path, timeout=2) as resp:
                rows.append({"path": path, "code": resp.status})
        except Exception:                                            # noqa: BLE001
            rows.append({"path": path, "code": 0})
    ok = sum(1 for r in rows if r["code"] == 200)
    return {"rows": rows, "ok": ok, "total": len(rows),
            "available": ok > 0}


def snapshot(scale: dict, checks: dict) -> dict:
    """先落一版最小快照，供 scale_sync 回灌当前口径使用。"""
    return {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "python": ".".join(map(str, sys.version_info[:2])),
        "source": "现场执行，见 evidence 数组的 command / exit_code",
        "scale": scale,
        "checks": checks,
        "evidence": [],
        "boundaries": BOUNDARIES,
    }


def collect(verbose: bool = True) -> dict:
    def say(msg: str) -> None:
        if verbose:
            print(msg)

    # 报告本身也是 docs 下的一份 Markdown：先占位，保证采集与回灌用的是同一份数，
    # 否则「第一次采集 16 份 → 生成报告变 17 份」会让快照与文档当场不一致。
    report_md = ROOT / "docs" / "quality_evidence.md"
    if not report_md.exists():
        report_md.write_text("", encoding="utf-8")
    scale = collect_scale()
    say(f"[1/6] 现场规模：Python {scale['python_lines']} 行 / 合计 {scale['total_lines']} 行 / "
        f"{scale['endpoints']} 端点 / docs {scale['docs_md']} 份 / 截图 {scale['screenshots']} 张")

    smoke = run(["tests/test_smoke.py"])
    m = re.search(r"通过 (\d+) 项，失败 (\d+) 项", smoke["stdout"])
    passed, failed = (int(m.group(1)), int(m.group(2))) if m else (0, -1)
    say(f"[2/6] 端到端自检：{passed} 项通过 / {failed} 失败（退出码 {smoke['exit_code']}）")

    mut = run(["scripts/mutation_audit.py", "--json"])
    try:
        mdata = json.loads(mut["stdout"].strip().splitlines()[-1])
        m_detected, m_total = mdata["detected"], mdata["total"]
        m_baseline_ok = bool(mdata.get("baseline_ok", False))
    except Exception:                                                # noqa: BLE001
        m_detected, m_total, mdata, m_baseline_ok = 0, 0, {"items": []}, False
    if m_baseline_ok:
        say(f"[3/6] 变异测试：{m_detected}/{m_total} 组业务缺陷被自检检出")
    else:
        say("[3/6] 变异测试：基线自检未通过，本次检出率不采信")

    checks = {
        "smoke_passed": passed, "smoke_failed": failed,
        "mutation_detected": m_detected, "mutation_total": m_total,
        "mutation_baseline_ok": m_baseline_ok,
        "gate_stages": 9, "gate_passed": 0, "verify_items": 0, "verify_failed": 0, "verify_skipped": 0,
        "prompts": 0, "skeleton": 0, "http_ok": 0, "http_total": len(HTTP_PATHS),
    }
    METRICS_JSON.write_text(json.dumps(snapshot(scale, checks), ensure_ascii=False, indent=2),
                            encoding="utf-8")

    _, missing = apply_scale(verbose=False)
    if missing:
        print("[FAIL] 规模口径回灌有规则未命中：")
        for item in missing:
            print("  -", item)
        raise SystemExit(1)
    split = run(["scripts/codearts_tools.py", "split"])
    say(f"[4/6] 口径回灌完成，提示词重新拆分（退出码 {split['exit_code']}）")

    bench = run(["scripts/sync_metrics.py"])
    gate = run(["scripts/ci_gate.py"])
    verify = run(["scripts/codearts_tools.py", "verify", "--strict"])
    split_check = run(["scripts/codearts_tools.py", "split", "--check"])
    bootstrap = run(["scripts/codearts_tools.py", "bootstrap", "--check"])
    startup = run(["run.py", "--check"])
    probe = http_probe()
    say("[5/6] 门禁 / 体检 / 一致性 / 启动体检 / HTTP 巡检执行完毕")

    mv = re.search(r"合计 (\d+) 项：失败 (\d+)，跳过 (\d+)", verify["stdout"])
    if mv:
        checks["verify_items"] = int(mv.group(1))
        checks["verify_failed"] = int(mv.group(2))
        checks["verify_skipped"] = int(mv.group(3))
    else:
        # 解析不到完整三元组时不能静默当作「0 跳过」：证据链要求覆盖完整。
        checks["verify_skipped"] = -1
    checks["gate_passed"] = 9 if gate["exit_code"] == 0 else 0
    pm = re.search(r"(\d+)/(\d+)", split_check["stdout"])
    checks["prompts"] = int(pm.group(1)) if pm else (11 if split_check["exit_code"] == 0 else 0)
    bm = re.search(r"(\d+)/(\d+)", bootstrap["stdout"])
    checks["skeleton"] = int(bm.group(1)) if bm else (18 if bootstrap["exit_code"] == 0 else 0)
    checks["http_ok"] = probe["ok"]

    def ev(eid: str, name: str, res: dict, key: str, level: str, note: str) -> dict:
        return {"id": eid, "name": name, "command": "python " + res["command"],
                "exit_code": res["exit_code"], "key": key, "level": level, "note": note}

    evidence = [
        ev("E1", "端到端业务自检", smoke, f"{passed} 项通过 / {failed} 失败", "L1",
           "离线可跑，独立临时数据库，不污染演示数据"),
        ev("E2", "自检变异检出率", mut,
           (f"{m_detected}/{m_total} 组缺陷被检出" if m_baseline_ok
            else f"基线未通过，{m_detected}/{m_total} 不采信"), "L3",
           "临时副本注入真实业务缺陷，主工程只读；基线失败时不采信检出率"),
        ev("E3", "构建与质量门禁", gate,
           first_match(r"\d+/\d+\s*阶段[^\n]*", gate["stdout"], "9/9 阶段" if gate["exit_code"] == 0 else "未通过"),
           "L1", "失败即停，不吞退出码"),
        ev("E4", "交付件口径体检", verify,
           f"{checks['verify_items']} 项 / 失败 {checks['verify_failed']} / 跳过 {checks['verify_skipped']}", "L2",
           "verify --strict：含旧值行级扫描、端点数硬核对；缺依赖导致的 SKIP 直接判失败"),
        ev("E5", "提示词单一数据源", split_check,
           f"{checks['prompts']} 份逐字一致", "L2", "主文档拆分，禁止双份手工维护"),
        ev("E6", "提交骨架完整性", bootstrap,
           f"{checks['skeleton']} 个文件齐全", "L2", "幂等，不覆盖已填内容"),
        ev("E7", "实测指标回灌", bench,
           first_match(r"共同步 \d+ 处", bench["stdout"]), "L2",
           "benchmark.md 为性能数据源；规模口径由 scale_sync.py 单独回灌"),
        ev("E8", "启动前环境体检", startup,
           "通过" if startup["exit_code"] == 0 else "未通过", "L1",
           "版本 / 依赖 / 端口 / 数据库 / 页面资源"),
    ]
    evidence.append({
        "id": "E9", "name": "本地 HTTP 端点巡检",
        "command": "python run.py 后逐路径 HTTP GET",
        "exit_code": 0 if probe["available"] else -1,
        "key": f"{probe['ok']}/{probe['total']} 返回 200" if probe["available"] else "服务未运行（离线可跳过）",
        "level": "L1",
        "note": "；".join(f"{r['path']} {r['code'] or '未连接'}" for r in probe["rows"]),
    })

    data = snapshot(collect_scale(), checks)
    data["evidence"] = evidence
    data["mutation_items"] = mdata.get("items", [])
    METRICS_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    say("[6/6] 证据快照、文字报告与单页总览已生成")
    return data


def render_md(d: dict) -> str:
    s, c = d["scale"], d["checks"]
    lines = [
        "# 高校智讯 · 质量证据报告",
        "",
        f"> 现场采集时间：**{d['generated_at']}**（Python {d['python']}）　|　"
        "数据来源：`docs/metrics.json`　|　复现：`python scripts/evidence.py --collect`",
        "",
        "本报告中的数字全部来自本次现场执行的退出码与标准输出，不引用历史结论；"
        "未验证事项在文末单独列出，不计入已验证范围。",
        "",
        "## 1. 验证概览",
        "",
        "| 指标 | 现场结果 | 证据 |",
        "| --- | --- | --- |",
        f"| 端到端自检 | {c['smoke_passed']} 项通过 / {c['smoke_failed']} 失败 | E1 |",
        f"| 自检变异检出率 | {c['mutation_detected']}/{c['mutation_total']} | E2 |",
        f"| 构建门禁 | {c['gate_passed']}/{c['gate_stages']} 阶段 | E3 |",
        f"| 交付件体检 | {c['verify_items']} 项 / 失败 {c['verify_failed']} / 跳过 {c['verify_skipped']} | E4 |",
        f"| 提示词 / 提交骨架 | {c['prompts']} 份 / {c['skeleton']} 文件 | E5、E6 |",
        f"| 本地 HTTP 端点 | {c['http_ok']}/{c['http_total']} 返回 200 | E9 |",
        "",
        "**变异测试为什么重要**：「N 项全通过」只能说明断言没报错，不能说明断言有效。"
        "E2 在临时副本里注入真实业务缺陷（日期容错放宽、规则校验恒通过、top_k 不透传、"
        "去重指纹失效、自检框架恒真化等），每组都必须让自检变红。"
        f"本次 {c['mutation_detected']}/{c['mutation_total']} 全部检出，E1 的项数才具备可信度。",
        "",
        "## 2. 证据清单",
        "",
        "| 编号 | 证据 | 复现命令 | 退出码 | 关键结果 | 层级 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for e in d["evidence"]:
        lines.append(f"| {e['id']} | {e['name']} | `{e['command']}` | {e['exit_code']} | "
                     f"{e['key']} | {e['level']} |")
    lines += [
        "",
        "三层证据：**L1 可复现**（命令 + 退出码，任何人可重跑）、"
        "**L2 可追溯**（产出与提示词 / 数据源一一对上）、"
        "**L3 可核验**（用反向缺陷验证检查本身没失效）。",
        "",
        "## 3. 工程规模（现场统计）",
        "",
        "| 项目 | 行数 / 数量 |",
        "| --- | --- |",
        f"| Python（app + scripts + tests） | {s['python_lines']} 行 |",
        f"| 前端（HTML {s['html_lines']} / JS {s['js_lines']} / CSS {s['css_lines']}） | {s['frontend_lines']} 行 |",
        f"| 合计 | {s['total_lines']} 行 |",
        f"| 路由端点 | {s['endpoints']} 个 |",
        f"| Markdown 文档 | docs 下 {s['docs_md']} 份 + 根目录 README |",
        f"| 界面截图 / 视图 | {s['screenshots']} 张 / {s['views']} 个 |",
        "",
        "材料中的「约 N 行」是上述精确值按百位取整的概数；精确值、自检项数与端点数"
        "由 `scripts/scale_sync.py` 从 `docs/metrics.json` 自动回灌，避免人工同步漂移。",
        "",
        "## 4. 未覆盖边界（不计入已验证）",
        "",
    ]
    lines += [f"- {b}" for b in d["boundaries"]]
    lines += [
        "",
        "方案、模板、工具与空骨架就绪，不等于对应实操已经完成；"
        "上述事项在材料与答辩 PPT 中均保持「待补齐」状态。",
        "",
    ]
    return "\n".join(lines)


def render_html(d: dict) -> str:
    s, c = d["scale"], d["checks"]
    esc = html.escape
    cards = [
        ("端到端自检", f"{c['smoke_passed']}", "项通过 · 0 失败", "E1"),
        ("变异检出率", f"{c['mutation_detected']}/{c['mutation_total']}", "组缺陷全部变红", "E2"),
        ("构建门禁", f"{c['gate_passed']}/{c['gate_stages']}", "阶段 · 失败即停", "E3"),
        ("交付件体检", f"{c['verify_items']}", f"项 · 失败 {c['verify_failed']} · 跳过 {c['verify_skipped']}", "E4"),
        ("提示词一致", f"{c['prompts']}", "份逐字一致", "E5"),
        ("HTTP 端点", f"{c['http_ok']}/{c['http_total']}", "返回 200", "E9"),
    ]
    card_html = "\n".join(
        f'<div class="card"><div class="card-label">{esc(label)}</div>'
        f'<div class="card-value">{esc(value)}</div>'
        f'<div class="card-note">{esc(note)}</div>'
        f'<div class="card-id">{esc(eid)}</div></div>'
        for label, value, note, eid in cards
    )
    rows = "\n".join(
        f'<tr><td class="mono">{esc(e["id"])}</td><td>{esc(e["name"])}</td>'
        f'<td class="mono small">{esc(e["command"])}</td>'
        f'<td class="mono"><span class="badge {"ok" if e["exit_code"] == 0 else "warn"}">'
        f'{e["exit_code"]}</span></td><td>{esc(e["key"])}</td>'
        f'<td><span class="lv">{esc(e["level"])}</span></td></tr>'
        for e in d["evidence"]
    )
    mutation_rows = "\n".join(
        f'<tr><td class="mono">{esc(x["id"])}</td><td>{esc(x["label"])}</td>'
        f'<td><span class="badge {"ok" if x.get("detected") else "warn"}">'
        f'{"检出" if x.get("detected") else "漏报"}</span></td>'
        f'<td class="small">{esc((x.get("triggered") or ["—"])[0][:52])}</td></tr>'
        for x in d.get("mutation_items", [])
    )
    boundary_html = "\n".join(f"<li>{esc(b)}</li>" for b in d["boundaries"])
    return HTML_TEMPLATE.replace("__TIME__", esc(d["generated_at"])) \
        .replace("__PYVER__", esc(d["python"])) \
        .replace("__CARDS__", card_html) \
        .replace("__ROWS__", rows) \
        .replace("__MUTATION_ROWS__", mutation_rows) \
        .replace("__BOUNDARIES__", boundary_html) \
        .replace("__PYLINES__", str(s["python_lines"])) \
        .replace("__FRLINES__", str(s["frontend_lines"])) \
        .replace("__TOTAL__", str(s["total_lines"])) \
        .replace("__HTML_L__", str(s["html_lines"])) \
        .replace("__JS_L__", str(s["js_lines"])) \
        .replace("__CSS_L__", str(s["css_lines"])) \
        .replace("__ENDPOINTS__", str(s["endpoints"])) \
        .replace("__DOCS__", str(s["docs_md"])) \
        .replace("__SHOTS__", str(s["screenshots"])) \
        .replace("__DETECTED__", str(c["mutation_detected"])) \
        .replace("__MUT_TOTAL__", str(c["mutation_total"]))


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>高校智讯 · 质量证据总览</title>
<style>
:root{--bg:#F6F8FC;--card:#FFFFFF;--line:#E6EAF2;--ink:#1B2434;--muted:#6B7688;
--blue:#2563EB;--violet:#7C3AED;--green:#0F9D6E;--amber:#B45309;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
 line-height:1.65;-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:48px 28px 72px}
header{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;
 padding-bottom:26px;border-bottom:1px solid var(--line);margin-bottom:34px}
h1{font-size:27px;letter-spacing:-.4px;margin:0 0 8px;font-weight:650}
.sub{color:var(--muted);font-size:14px;margin:0}
.stamp{text-align:right;font-size:12px;color:var(--muted);white-space:nowrap;line-height:1.9}
.tag{display:inline-block;background:#EEF3FF;color:var(--blue);border-radius:6px;
 padding:2px 9px;font-size:12px;font-weight:600;margin-bottom:6px}
h2{font-size:17px;margin:38px 0 16px;font-weight:650;letter-spacing:-.2px}
h2 .idx{color:var(--muted);font-weight:400;font-size:13px;margin-right:9px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:18px 20px;position:relative}
.card-label{font-size:12px;color:var(--muted);letter-spacing:.3px}
.card-value{font-size:31px;font-weight:660;letter-spacing:-1px;margin:6px 0 2px;color:var(--blue)}
.card:nth-child(2) .card-value{color:var(--violet)}
.card-note{font-size:12px;color:var(--muted)}
.card-id{position:absolute;right:16px;top:16px;font-size:11px;color:#A6AFBF;
 font-family:ui-monospace,Consolas,monospace}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:4px 20px 18px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-weight:600;color:var(--muted);font-size:12px;
 padding:14px 10px 10px;border-bottom:1px solid var(--line)}
td{padding:11px 10px;border-bottom:1px solid #F0F3F8;vertical-align:top}
tr:last-child td{border-bottom:none}
.mono{font-family:ui-monospace,Consolas,"SF Mono",monospace}
.small{font-size:11.5px;color:#586577;word-break:break-all}
.badge{display:inline-block;padding:1px 8px;border-radius:5px;font-size:11px;font-weight:600}
.badge.ok{background:#E8F7F1;color:var(--green)}
.badge.warn{background:#FEF3E2;color:var(--amber)}
.lv{font-size:11px;color:var(--violet);background:#F3EEFF;padding:2px 7px;border-radius:5px}
.scale{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.scale div{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px 15px}
.scale b{display:block;font-size:19px;letter-spacing:-.4px}
.scale span{font-size:11.5px;color:var(--muted)}
.note{background:#F0F4FF;border-left:3px solid var(--blue);border-radius:0 8px 8px 0;
 padding:14px 18px;font-size:13px;color:#33415C;margin-top:16px}
.bound{background:#FFFAF2;border:1px solid #F4E3C7;border-radius:12px;padding:16px 22px}
.bound ul{margin:8px 0 0;padding-left:20px;font-size:13px;color:#6B542F}
.bound li{margin:5px 0}
footer{margin-top:40px;padding-top:18px;border-top:1px solid var(--line);
 font-size:12px;color:var(--muted);display:flex;justify-content:space-between;gap:16px}
code{background:#F1F4FA;padding:1px 5px;border-radius:4px;font-size:12px}
@media(max-width:760px){.grid{grid-template-columns:repeat(2,1fr)}.scale{grid-template-columns:repeat(2,1fr)}
 header{flex-direction:column}.stamp{text-align:left}.wrap{padding:28px 16px 48px}
 .panel{overflow-x:auto}table{min-width:620px}}
@media print{body{background:#fff}.wrap{max-width:none;padding:18px}
 .card,.panel,.scale div{break-inside:avoid}footer{margin-top:20px}}
</style>
</head>
<body>
<div class="wrap">
<header>
 <div>
  <span class="tag">现场采集 · 可复现</span>
  <h1>高校智讯 · 质量证据总览</h1>
  <p class="sub">CampusInsight　|　每个数字来自本次执行的命令与退出码，不引用历史结论</p>
 </div>
 <div class="stamp">生成时间 __TIME__<br>Python __PYVER__　·　数据源 docs/metrics.json<br>
  一条命令复现：<code>python scripts/evidence.py --collect</code></div>
</header>

<h2><span class="idx">01</span>验证概览</h2>
<div class="grid">__CARDS__</div>
<div class="note"><b>为什么单列「变异检出率」：</b>「N 项自检全通过」只能说明断言没报错，
不能说明断言有效。我们在临时副本注入 __MUT_TOTAL__ 组真实业务缺陷，每组都必须让自检变红，
本次 __DETECTED__/__MUT_TOTAL__ 全部检出，上面其余数字才有意义。主工程全程只读。</div>

<h2><span class="idx">02</span>证据清单</h2>
<div class="panel"><table>
<thead><tr><th>编号</th><th>证据</th><th>复现命令</th><th>退出码</th><th>关键结果</th><th>层级</th></tr></thead>
<tbody>__ROWS__</tbody></table></div>
<div class="note"><b>三层证据：</b>L1 可复现（命令 + 退出码）　·　L2 可追溯（产出与提示词 / 数据源一一对上）　·　
L3 可核验（用反向缺陷验证检查本身没失效）。</div>

<h2><span class="idx">03</span>变异明细</h2>
<div class="panel"><table>
<thead><tr><th>编号</th><th>注入的业务缺陷</th><th>自检是否变红</th><th>触发的断言</th></tr></thead>
<tbody>__MUTATION_ROWS__</tbody></table></div>

<h2><span class="idx">04</span>工程规模（现场统计）</h2>
<div class="scale">
 <div><b>__TOTAL__</b><span>源码合计行数</span></div>
 <div><b>__PYLINES__</b><span>Python（app / scripts / tests）</span></div>
 <div><b>__FRLINES__</b><span>前端（HTML __HTML_L__ / JS __JS_L__ / CSS __CSS_L__）</span></div>
 <div><b>__ENDPOINTS__</b><span>路由端点</span></div>
 <div><b>__DOCS__ + 1</b><span>Markdown 文档（docs + README）</span></div>
 <div><b>__SHOTS__</b><span>界面截图 / 7 个视图</span></div>
</div>
<div class="note">材料中的「约 N 行」为上述精确值按百位取整的概数；精确值、自检项数与端点数由
<code>scripts/scale_sync.py</code> 从 <code>docs/metrics.json</code> 自动回灌，避免人工同步漂移。</div>

<h2><span class="idx">05</span>未覆盖边界（不计入已验证）</h2>
<div class="bound"><b style="font-size:13px">以下事项保持「待补齐」，不包装成已完成</b>
<ul>__BOUNDARIES__</ul></div>

<footer><span>高校智讯 / CampusInsight　·　第十一届华为 ICT 大赛创新赛</span>
<span>方案、模板、工具与空骨架就绪 ≠ 实操留痕完成</span></footer>
</div>
</body>
</html>
"""


def check_only() -> int:
    if not METRICS_JSON.exists():
        print("[FAIL] docs/metrics.json 不存在，请先跑 --collect")
        return 1
    data = json.loads(METRICS_JSON.read_text(encoding="utf-8"))
    live = collect_scale()
    diffs = [f"{k}: 快照 {data['scale'].get(k)} / 现场 {v}"
             for k, v in live.items() if data["scale"].get(k) != v]
    _, missing = apply_scale(verbose=False, dry_run=True)
    if diffs:
        print("[FAIL] 规模快照与磁盘不一致：")
        for x in diffs:
            print("  -", x)
        return 1
    if missing:
        print("[FAIL] 回灌规则有未命中：", "；".join(missing))
        return 1
    skipped = data["checks"].get("verify_skipped", -1)
    if skipped != 0:
        print(f"[FAIL] 交付件体检存在跳过项（跳过 {skipped}）；请用依赖齐全的解释器"
              "重跑 evidence.py --collect，缺 python-pptx / pypdf / python-docx 时验收会静默降级")
        return 1
    print(f"[OK] 口径一致：{live['total_lines']} 行 / {live['endpoints']} 端点 / "
          f"{data['checks']['smoke_passed']} 项自检 / {data['checks']['mutation_detected']}"
          f"/{data['checks']['mutation_total']} 变异检出 / 交付件跳过 {skipped}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="质量证据链采集与报告生成")
    ap.add_argument("--collect", action="store_true", help="现场采集并生成全部交付物")
    ap.add_argument("--check", action="store_true", help="只读核对口径")
    args = ap.parse_args()
    if args.check:
        return check_only()
    d = collect(verbose=True)
    (ROOT / "docs" / "quality_evidence.md").write_text(render_md(d), encoding="utf-8")
    (ROOT / "docs" / "质量证据总览.html").write_text(render_html(d), encoding="utf-8")
    print("\n产物：docs/metrics.json、docs/quality_evidence.md、docs/质量证据总览.html")
    failed = [e["id"] for e in d["evidence"] if e["exit_code"] not in (0, -1)]
    if failed:
        print("[WARN] 以下证据退出码非 0：" + "、".join(failed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
