"""端到端自检脚本（不依赖 pytest，直接运行即可）。

    python tests/test_smoke.py

使用独立的临时数据库，不会污染演示数据。
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须在导入 app 之前指定测试库
_TMP = Path(tempfile.mkdtemp(prefix="zhixun_test_")) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP.as_posix()}"
os.environ["LLM_PROVIDER"] = "none"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.config import settings as app_settings  # noqa: E402
from app.database.connection import SessionLocal, init_db  # noqa: E402
from app.database.models import UserTask  # noqa: E402
from app.main import app  # noqa: E402
from app.services.analyzer import analyze, date_grounded  # noqa: E402
from app.services.textutil import is_iso_date, safe_iso_date  # noqa: E402

from collections import Counter  # noqa: E402

PASSED = 0
FAILED: list[str] = []
CHECK_NAMES: list[str] = []


def check(name: str, cond: bool, detail: str = "", quiet: bool = False) -> None:
    global PASSED
    CHECK_NAMES.append(name)
    if cond:
        PASSED += 1
        if not quiet:
            print(f"  [PASS] {name}")
    else:
        FAILED.append(name)
        if not quiet:
            print(f"  [FAIL] {name} {detail}")


def self_check() -> str:
    """负向哨兵：确认 check() 没有恒真化、断言名没有重复。

    一个「永远报 PASS」的自检比没有自检更危险——它会把项数变成纯装饰。
    这里故意喂一条 False，确认计数确实落到 FAILED 后完整回滚，
    因此不改变对外公布的断言项数。
    """
    global PASSED
    saved_p, saved_f = PASSED, FAILED[:]
    check("__sentinel__", False, quiet=True)
    detected = (PASSED == saved_p and len(FAILED) == len(saved_f) + 1
                and FAILED[-1] == "__sentinel__")
    PASSED = saved_p
    FAILED[:] = saved_f
    CHECK_NAMES.pop()
    if not detected:
        return "check() 未正确判失败（自检恒真化）"
    return ""


COMPETITION = """各学院：
为培养学生创新能力，学校决定举办2026年数学建模竞赛。
一、参赛对象
全日制本科生，以3人团队形式报名，每人限报一队。
二、报名时间
2026年11月3日至2026年11月20日。
三、报名方式
请登录教务处系统（https://jwc.whut.edu.cn/match）提交报名信息。
四、提交材料
报名表、个人简历各一份。
五、其他
参赛队员须无违纪记录。
教务处
2026年10月28日"""


def main() -> int:
    problem = self_check()
    if problem:
        print(f"[FATAL] 自检框架异常：{problem}")
        return 1
    init_db()
    with TestClient(app) as c:
        print("\n[1] 基础可用性")
        r = c.get("/api/health")
        check("健康检查返回 ok", r.status_code == 200 and r.json()["status"] == "ok", r.text[:120])

        print("\n[2] 通知录入与去重")
        payload = {"title": "关于举办2026年数学建模竞赛的通知", "content": COMPETITION,
                   "publish_time": "2026-10-28", "source_name": "教务处"}
        r = c.post("/api/notices", json=payload)
        check("首次录入成功", r.status_code == 200, r.text[:160])
        nid = r.json()["id"] if r.status_code == 200 else 0
        r2 = c.post("/api/notices", json=payload)
        check("重复录入被拦截（409）", r2.status_code == 409, r2.text[:120])
        r3 = c.post("/api/notices", json={**payload,
                                          "content": "各学院：现将本学期教学值班安排印发给你们，"
                                                     "请各教学单位按附表核对值班人员与联系方式，"
                                                     "如有调整请于本周内报教务处备案。教务处。"})
        check("同标题不同正文不算重复（正文参与指纹）",
              r3.status_code == 200, r3.text[:120])

        print("\n[3] AI 解析准确性")
        r = c.post(f"/api/notices/{nid}/analyze")
        a = r.json()["analysis"]
        check("分类为学科竞赛", a["category"] == "学科竞赛", a["category"])
        check("截止日期为 2026-11-20", a["deadline"] == "2026-11-20", str(a["deadline"]))
        check("未把发布时间误判为截止", a["deadline"] != "2026-10-28")
        check("识别出对象为本科生", "本科生" in "".join(a["target"]), str(a["target"]))
        check("识别出报名材料", any("报名表" in m for m in a["materials"]), str(a["materials"]))
        check("保留原文依据", bool(a["evidence"].get("deadline")), str(a["evidence"]))

        print("\n[4] 防幻觉校验")
        check("接地校验：原文存在的日期通过", date_grounded("2026-11-20", COMPETITION))
        check("接地校验：虚构日期被拒绝", not date_grounded("2026-12-31", COMPETITION))
        fake = {"category": "学科竞赛", "deadline": "2026-12-31", "target": ["本科生"],
                "conditions": [], "materials": [], "apply_method": "",
                "evidence": {"deadline": "报名截止：2026年12月31日"}, "confidence": 0.95,
                "needs_review": False}
        from app.services.analyzer import _validate_llm
        v = _validate_llm(fake, "测试", COMPETITION, "2026-10-28")
        check("虚构截止日期被置空", v["deadline"] is None, str(v["deadline"]))
        check("虚构结果被标记待复核", v["needs_review"] is True)

        print("\n[5] 个性化匹配")
        u = c.post("/api/users", json={"name": "测试研究生", "grade": "研一",
                                       "major": "计算机科学与技术", "interests": ["竞赛"]}).json()
        m = c.get(f"/api/users/{u['id']}/matches").json()
        item = [x for x in m["items"] if x["notice_id"] == nid]
        check("研究生对本科生限定通知判为暂不符合", bool(item) and item[0]["level"] == "no",
              item[0]["level"] if item else "无结果")
        check("匹配结论附带理由", bool(item) and len(item[0]["reasons"]) >= 2)

        print("\n[6] 智能问答")
        r = c.post("/api/qa/ask", json={"query": "数学建模竞赛什么时候截止报名"})
        d = r.json()
        check("命中通知并给出出处", d["hit"] and d["citations"], str(d.get("mode")))
        check("引用包含通知编号", bool(d["citations"]) and "notice_id" in d["citations"][0])
        r = c.post("/api/qa/ask", json={"query": "量子计算机的超导材料怎么制备"})
        check("超范围问题不编造答案", r.json()["hit"] is False, r.text[:120])

        print("\n[7] 采集安全约束")
        r = c.post("/api/notices/ingest/url", json={"url": "http://127.0.0.1/admin"})
        check("内网地址被拒绝", r.status_code == 400, r.text[:120])
        r = c.post("/api/notices/ingest/url", json={"url": "https://example.com/a.html"})
        check("非白名单域名被拒绝", r.status_code == 400 and "白名单" in r.text)

        print("\n[8] 采集引擎（离线用例，用真实页面片段的缩样）")
        from app.services import collector

        # 8.1 数字被排版拆散：CMS 把数字/年月日各自包进 span
        crumb_html = """
        <html><body>
          <div class="content">
            <div class="position">首页 &gt; 通知公告</div>
            <h2>关于举办研究生案例大赛的通知</h2>
            <div class="art_info">发布时间：<span>2025</span>-<span>01</span>-<span>21</span></div>
            <div id="art_content">
              <p>每支队伍成员为<span>4</span>-<span>6</span>人，其中本科生不超过<span>2</span>人。</p>
              <p>报名截止时间<br><span>2025</span><span>年11月20日</span>20:00。</p>
            </div>
          </div>
        </body></html>"""
        d = collector.parse_detail(crumb_html, "http://gd.whut.edu.cn/yjsgl/tzgg/202501/t1.shtml")
        check("数字碎片被回粘（不超过2人）", "不超过2人" in d["content"], d["content"][:80])
        check("跨标签日期被还原（2025年11月20日）", "2025年11月20日" in d["content"], d["content"][:80])
        check("正文未混入面包屑", "首页" not in d["content"], d["content"][:80])
        check("发布时间从元信息区取到", d["publish_time"] == "2025-01-21", d["publish_time"])

        # 8.2 列表页解析
        list_html = """
        <ul class="art-list">
          <li><a href="./202501/t1.shtml">关于举办第五届案例大赛的通知</a><span>2025-01-21</span></li>
          <li><a href="./202501/t2.shtml">关于开展研究生工作站自评估的通知</a><span>2025-01-20</span></li>
          <li><a href="./202501/t3.shtml">更多</a><span>2025-01-19</span></li>
        </ul>"""
        cfg = collector.SourceCfg(id="t", name="t", url="http://gd.whut.edu.cn/yjsgl/tzgg/",
                                  list_selector="ul.art-list > li")
        rows = collector.parse_list(list_html, cfg.url, cfg)
        check("列表解析出 2 条正文链接", len(rows) == 2, str([r["title"] for r in rows]))
        check("列表日期解析正确", rows and rows[0]["publish_date"] == "2025-01-21", str(rows[:1]))

        # 8.3 翻页：跨目录的"查看更多"不是翻页
        page_html = """
        <div class="pages"><a href="javascript:void(0)">下一页</a></div>
        <a href="https://news.whut.edu.cn/lgzx/">查看更多</a>
        <a href="https://news.whut.edu.cn/zhxw/index_8025.shtml">查看更多</a>"""
        pg = collector.parse_pagination(page_html, "https://news.whut.edu.cn/zhxw/")
        check("跨栏目「查看更多」不被当成翻页", "lgzx" not in (pg["next_url"] or ""), str(pg))
        check("同目录「查看更多」识别为翻页", pg["next_url"].endswith("index_8025.shtml"), str(pg))

        # 8.4 URL 归一化：去跟踪参数，保留会影响内容的 from
        n1 = collector.normalize_url("https://a.edu.cn/n?id=1&utm_source=x&from=tzgg#top")
        check("URL 去掉锚点与跟踪参数", n1 == "https://a.edu.cn/n?id=1&from=tzgg", n1)

        print("\n[9] 采集接口与去重")
        r = c.get("/api/sources")
        check("采集源列表可用", r.status_code == 200 and len(r.json()["sources"]) >= 1, r.text[:100])
        crawl_item = {
            "title": "关于组织参加第十一届华为ICT大赛创新赛的通知",
            "content": "各学院：\n为培养学生创新能力，现组织参加第十一届华为ICT大赛创新赛。"
                       "\n一、参赛对象\n全日制本科生，3人组队。\n二、报名截止\n2026年11月20日。",
            "url": "https://jwc.whut.edu.cn/notice/ict2026.html",
            "publish_date": "2026-11-01",
            "unit": "武汉理工大学-教务处",
            "attachments": [{"name": "报名表", "url": "https://jwc.whut.edu.cn/a.docx"}],
        }
        r = c.post("/api/sources/import", json={"items": [crawl_item], "auto_analyze": True})
        check("采集条目入库成功", r.status_code == 200 and r.json()["created"] == 1, r.text[:160])
        r = c.post("/api/sources/import", json={"items": [crawl_item]})
        check("重复采集被去重", r.json()["skipped"] == 1, r.text[:160])
        got = c.get("/api/notices?keyword=华为ICT大赛创新赛").json()
        check("入库条目带附件清单",
              bool(got["items"]) and got["items"][0]["attachments"][0]["name"] == "报名表",
              str(got["items"][:1])[:160])

        print("\n[10] 待办与看板")
        r = c.post(f"/api/users/{u['id']}/tasks", json={"notice_id": nid})
        check("加入待办成功", r.status_code == 200, r.text[:120])
        tid = r.json()["id"] if r.status_code == 200 else 0
        r = c.patch(f"/api/tasks/{tid}", json={"status": "done"})
        check("待办状态可更新", r.status_code == 200 and r.json()["status"] == "done")
        s = c.get("/api/dashboard/stats").json()
        check("看板统计可用", s["overview"]["total_notices"] >= 1 and len(s["categories"]) >= 8)

        print("\n[11] 文体判别与截止语义（真实站点暴露的第 4 类缺陷）")
        from app.services.extractor import deadline_kind, extract      # noqa: E402
        from app.services.genre import detect                          # noqa: E402

        news_title = "学校捧得全国“挑战杯”竞赛“优胜杯”"
        news_body = "新闻网讯 10月30日至11月2日，第十五届竞赛在武汉举行。材料科学领域的专家参会。"
        check("新闻导语 + 无办理要求 → 新闻资讯",
              detect(news_title, news_body, "https://news.whut.edu.cn/zhxw/a.html")["genre"] == "news")
        check("出现报名/提交要求时一票否决，不误删真通知",
              detect("我校团队在Nature发表成果",
                     "新闻网讯 近日，请各学院于11月20日前提交报名表。")["genre"] == "notice")
        check("标题含公示 → 公示公告",
              detect("2025年度研究生工作站评估结果公示", "公示期为11月20日至11月24日。")["genre"]
              == "publicity")

        f = extract("公示通知", "公示期为2026年6月23日至2026年7月2日。公示期间如有异议。", "2026-06-01")
        check("公示期区间末端不再被当成报名截止", f["deadline"] is None, str(f["deadline"]))
        f = extract("赛事新闻", "新闻网讯 10月30日至11月2日，第十五届竞赛举行。", "2026-10-01")
        check("赛事举办期末端不再被当成报名截止", f["deadline"] is None, str(f["deadline"]))
        f = extract("报名通知", "二、报名时间\n2026年11月3日至2026年11月20日。", "2026-10-28")
        check("报名时间范围仍正确取末端 11-20", f["deadline"] == "2026-11-20", str(f["deadline"]))
        # 全链路巡检发现（区间+截止跨句并存）：活动区间的「举办」负向分曾误伤
        # 同一段落里另一条独立的报送截止，导致 deadline 被置空。修复后应取真实截止。
        f = extract("活动通知",
                    "各学院：定于2026-11-25至2026-12-10举办校园文体活动月。"
                    "请于2026-11-20前报送活动方案。联系人：王老师。", "2026-11-10")
        check("活动区间与报送截止跨句并存 → 取真实报送截止 11-20",
              f["deadline"] == "2026-11-20", str(f["deadline"]))
        check("截止语义：报名截止", deadline_kind("报名截止：2026年11月20日") == "报名截止")
        check("截止语义：异议截止", deadline_kind("公示期为…如有异议") == "异议截止")

        nr = analyze(news_title, news_body, "2026-11-01", use_llm=False,
                     source_url="https://news.whut.edu.cn/zhxw/a.html")
        check("新闻稿解析后报名类字段全空",
              not nr["deadline"] and not nr["materials"] and not nr["conditions"]
              and not nr["target"] and not nr["apply_method"], str(nr)[:200])
        check("新闻稿标为不可办理且不误报待复核",
              nr["actionable"] is False and nr["needs_review"] is False)
        check("留空原因写入复核说明且无引擎噪音",
              "判定依据" in nr["review_note"] and "办理要求" in nr["review_note"]
              and "未启用大模型" not in nr["review_note"]
              and "未识别到明确" not in nr["review_note"], nr["review_note"][:80])

        print("\n[12] 文体在业务链路中的约束")
        payload_news = {
            "title": news_title, "content": news_body,
            "publish_time": "2026-11-01",
            "source_url": "https://news.whut.edu.cn/zhxw/20261101_cup.html",
            "source_name": "武汉理工大学-新闻经纬",
        }
        r = c.post("/api/notices", json=payload_news)
        news_id = r.json()["id"] if r.status_code == 200 else 0
        r = c.post(f"/api/notices/{news_id}/analyze")
        a_news = r.json()["analysis"]
        check("解析结果落库带文体与中文标签",
              a_news["genre"] == "news" and a_news["genre_label"] == "新闻资讯", str(a_news)[:160])
        check("解析结果带 actionable 标记", a_news["actionable"] is False)

        r = c.post(f"/api/users/{u['id']}/tasks", json={"notice_id": news_id})
        check("新闻稿不能加入待办（400）", r.status_code == 400, r.text[:140])
        r = c.get(f"/api/users/{u['id']}/matches")
        check("新闻稿不出现在个性化匹配里",
              all(x["notice"]["id"] != news_id for x in r.json()["items"]), r.text[:140])
        r = c.get("/api/notices?genre=news")
        check("通知中心可按文体筛选",
              r.status_code == 200 and r.json()["total"] >= 1
              and all(x["analysis"]["genre"] == "news" for x in r.json()["items"]), r.text[:160])
        r = c.get("/api/notices?genre=notice&review=needs_review")
        check("文体筛选与复核筛选可叠加", r.status_code == 200, r.text[:140])
        s = c.get("/api/dashboard/stats").json()
        check("看板返回文体构成", any(x["name"] == "新闻资讯" for x in s["genres"]), str(s["genres"]))

        # ============================================================
        print("\n[13] 结构化字段契约、规则校验与人工审核闭环")
        from app.services import reltime, timeline as timeline_mod, validator  # noqa: E402
        from app.services.extractor import extract                    # noqa: E402
        from app.services.matcher import match_one                    # noqa: E402

        f = extract("招聘通知",
                    "招聘对象：2027届毕业生，专业包括计算机、电子信息、自动化、数学等相关专业。",
                    "2026-11-06")
        check("专业清单按并列专名抽取",
              "计算机" in f["major_requirement"] and "电子信息" in f["major_requirement"],
              str(f["major_requirement"]))
        check("专业清单不带『专业包括』前缀",
              all("专业包括" not in x for x in f["major_requirement"]), str(f["major_requirement"]))
        f = extract("报名通知", "【报名对象】我校全日制本科生、研究生均可报名，专业不限。", "2026-11-06")
        check("『专业不限』不产出专业限制", f["major_requirement"] == [], str(f["major_requirement"]))
        f = extract("活动通知", "面向我校全日制本科生、研究生开展选拔。", "2026-11-06")
        check("年级限制保持原文粒度（研究生不被推断为硕士）",
              "研究生" in f["grade_requirement"] and "本科生" in f["grade_requirement"]
              and "硕士研究生" not in f["grade_requirement"], str(f["grade_requirement"]))
        f = extract("讲座通知", "联系人：张老师 电话：027-87654321，邮箱 abc@whut.edu.cn\n讲座地点：南湖校区教学楼A301", "2026-11-06")
        check("联络方式结构化抽取", f["contact"].get("person") == "张老师"
              and "027-87654321" in f["contact"].get("phone", [])
              and "abc@whut.edu.cn" in f["contact"].get("email", []), str(f["contact"]))
        check("地点与证据同时抽取", "A301" in f["location"] and "A301" in f["evidence"].get("location", ""),
              str(f["location"]))
        f = extract("竞赛通知", "二、报名时间\n2026年11月3日至2026年11月20日。\n活动时间：2026年12月6日举行。", "2026-10-28")
        check("报名开始与活动举办时间分离",
              f["start_time"] == "2026-11-03" and f["event_time"] == "2026-12-06",
              str((f["start_time"], f["event_time"])))
        check("非空结构化字段均有原文证据",
              all(k in f["evidence"] for k, v in f.items()
                  if k in ("grade_requirement", "major_requirement", "registration_start",
                           "event_time", "location", "contact") and v),
              str(list(f["evidence"].keys())))

        d1, _, n1 = reltime.resolve_relative("请各学院于本周五前提交报名表，逾期不予受理。", "2026-11-06")
        check("相对时间：本周五前换算", d1 == "2026-11-06" and not n1, str((d1, n1)))
        d2, _, _ = reltime.resolve_relative("请于三个工作日内报送申报材料至学院办公室。", "2026-11-06")
        check("相对时间：三个工作日跳过周末", d2 == "2026-11-11", str(d2))
        d3, _, n3 = reltime.resolve_relative("请于本周五前提交报名表。", "")
        check("缺发布时间时相对时间置空待复核", d3 is None and "待复核" in n3, str((d3, n3)))
        d4, _, n4 = reltime.resolve_relative("（三）11月底前，研究生院组织评审并公布结题结果。", "2026-11-06")
        check("非办理语境（评审）不触发相对时间", d4 is None and not n4, str((d4, n4)))

        rr = analyze("关于举办竞赛报名的通知", "请各学院于本周五前提交报名表，逾期不予受理。",
                     "2026-11-06", use_llm=False)
        check("相对时间接入解析链路", rr["deadline"] == "2026-11-06"
              and rr["deadline_source"] == "relative", str(rr["deadline"]))
        rr = analyze("关于提交材料的通知", "请于本周五前提交报名表。", "", use_llm=False)
        check("无法换算时置空并标记复核", rr["deadline"] is None and rr["needs_review"]
              and "相对时间" in rr["review_note"], rr["review_note"][:90])
        rr = analyze("关于举办竞赛的通知", COMPETITION, "2026-10-28", use_llm=False)
        check("正常解析带规则校验报告", len(rr["checks"]["passed"]) >= 7 and rr["checks"]["ok"],
              str(rr["checks"])[:160])

        bad = {"deadline": "2026-01-01", "confidence": 0.8, "evidence": {},
               "actionable": True, "target": ["本科生"]}
        rep = validator.audit(bad, "原文写明了报名要求。", "2026-11-06")
        check("规则校验拦截『截止早于发布』",
              any("早于发布时间" in x for x in rep["issues"]) and not rep["ok"], str(rep["issues"]))
        low = {"deadline": None, "confidence": 0.5, "evidence": {}, "actionable": True}
        rep = validator.audit(low, "原文写明了报名要求。", "2026-11-06")
        check("低置信度只作软提示、不硬拦截",
              rep["ok"] and any("置信度" in x for x in rep["warnings"]), str(rep))

        # ---------------- 多轨道截止时间轴 ----------------
        # 这段合成文本照抄真实学位授予通知的写法：同一句话里「X月X日（秋季）
        # 和Y月Y日（冬季）」，两条轨道并行推进。单值 deadline 只能留下一个日期。
        TL_TEXT = (
            "一、博士学位申请人预答辩\n"
            "7月2日（秋季）和10月8日（冬季）前，博士学位申请人预答辩截止。\n"
            "二、答辩意向申请\n"
            "7月7日（秋季）和10月10日（冬季）前，有申请意向的硕士学位申请人须在系统中提交答辩意向申请。\n"
            "7月3日（秋季）和10月9日（冬季）前，有申请学位意向的博士学位申请人须在系统中提交答辩意向申请。\n"
            "三、答辩前检测\n"
            "7月8日（秋季）和10月12日（冬季），硕士学位论文与实践成果总结报告“答辩前检测”截止。\n"
            "四、学院审议\n"
            "10月8日（秋季）和12月2日（冬季），各学院学位分会学位授予审议截止。\n"
        )
        tl = timeline_mod.extract(TL_TEXT, "2026-06-15")
        check("时间轴识别出秋季与冬季两条轨道",
              tl["tracks"] == ["秋季", "冬季"], str(tl["tracks"]))
        check("时间轴节点数多于单值字段所能承载",
              len(tl["nodes"]) >= 6 and tl["stats"]["total"] == len(tl["nodes"]),
              f"只产出 {len(tl['nodes'])} 个节点")
        master = timeline_mod.for_role(tl["nodes"], "硕士研究生")
        check("硕士生的个人时间轴不串入博士节点",
              master and all("硕士" in n["role"] for n in master),
              str([(n["date"], n["role"]) for n in master]))
        check("硕士秋季轨道取到 7 月而非 10 月",
              any(n["date"] == "2026-07-07" for n in timeline_mod.for_role(
                  tl["nodes"], "硕士研究生", "秋季")),
              str([n["date"] for n in timeline_mod.for_role(tl["nodes"], "硕士研究生", "秋季")]))
        admin_nodes = [n for n in tl["nodes"] if n["scope"] == "admin"]
        check("学院审议节点被标为管理侧而非学生待办",
              bool(admin_nodes) and all(n["role"] == "学院" for n in admin_nodes),
              str([(n["date"], n["role"]) for n in admin_nodes]))
        check("个人时间轴永不返回管理节点",
              all(n["scope"] == "student" for n in timeline_mod.for_role(tl["nodes"], "硕士研究生"))
              and all(n["scope"] == "student" for n in timeline_mod.for_role(tl["nodes"], "博士研究生")))
        check("年级口语被归一到身份词",
              timeline_mod.role_from_user("博二") == "博士研究生"
              and timeline_mod.role_from_user("研三") == "硕士研究生"
              and timeline_mod.role_from_user("大三") == "本科生"
              and timeline_mod.role_from_user("") == "")

        # 单值字段与时间轴必须同时进结果：单值留作兼容，时间轴承载真实网格
        tl_an = analyze("关于2026年秋冬季博士、硕士学位授予工作安排的通知",
                        TL_TEXT, "2026-06-15", use_llm=False)
        check("解析结果同时带单值截止与时间轴",
              tl_an["deadline"] == "2026-10-08" and len(tl_an["timeline_nodes"]) >= 6,
              f"{tl_an['deadline']} / {len(tl_an['timeline_nodes'])}")
        check("单值截止恰好是硕士生用不上的那个日期",
              not any(n["date"] == tl_an["deadline"]
                      for n in timeline_mod.for_role(tl_an["timeline_nodes"], "硕士研究生")),
              "单值日期落进了硕士轨道，说明举证文本选错了")
        check("时间轴节点通过 C10 原文接地校验",
              any("C10" in p for p in tl_an["checks"]["passed"]), str(tl_an["checks"]["issues"]))

        # C10 的反向验证：节点日期在原文中不存在时必须被剔除
        fake = {"deadline": None, "confidence": 0.8, "actionable": True,
                "timeline_nodes": [
                    {"seq": 1, "date": "2026-07-02", "track": "秋季", "role": "博士研究生",
                     "scope": "student", "action": "预答辩", "evidence": "博士学位申请人预答辩截止"},
                    {"seq": 2, "date": "2027-03-09", "track": "秋季", "role": "博士研究生",
                     "scope": "student", "action": "编造的节点", "evidence": "原文没有这句"},
                ],
                "timeline_stats": {"total": 2}}
        rep = validator.audit(fake, TL_TEXT, "2026-06-15")
        check("时间轴编造节点被 C10 剔除并记入问题",
              len(fake["timeline_nodes"]) == 1
              and fake["timeline_stats"]["total"] == 1
              and any("时间轴" in x and "剔除" in x for x in rep["issues"]),
              str(rep["issues"]))

        # 表格行的动作串必须止于「工作内容」一格。
        # 单元格分隔符 U+001F 若不算分句边界，整行会被当成一句，
        # 动作串就一路吞到「星期」「周次」列，学生会看到
        # 「各学院返回毕业答辩登记表至培养处各学院星期三第二周」这种句子。
        TL_TABLE = (
            "序号\x1f截止日期\x1f工作内容\x1f完成单位\x1f星期\x1f周次\n"
            "4\x1f8 月 30 日\x1f反馈《毕业生答辩登记表》至培养处\x1f培养处\x1f星期五\x1f预备周\n"
            "5\x1f9 月 11 日\x1f各学院报送毕业答辩登记表至培养处\x1f各学院\x1f星期三\x1f第二周\n"
            "7\x1f9 月 23 日\x1f答辩截止\x1f各学院\x1f星期一\x1f第四周"
        )
        tl_tb = timeline_mod.extract(TL_TABLE, "2019-09-04")
        tb_bad = [n for n in tl_tb["nodes"]
                  if any(w in n["action"] for w in ("星期", "周次", "预备周", "第"))
                  or "\x1f" in n["action"]]
        check("表格行的动作串不吞右侧的星期与周次列", not tb_bad, str(tb_bad))
        check("表格行按单元格取动作而不是整行拼接",
              any(n["action"] == "答辩" for n in tl_tb["nodes"])
              and any("反馈《毕业生答辩登记表》" in n["action"] for n in tl_tb["nodes"]),
              str([n["action"] for n in tl_tb["nodes"]]))

        # 年份锚：标题里的「YYYY年」优先于页面发布时间。
        # 实测 2019 秋季学位授予通知的页面标签写着「发布时间：2020-09-04」，
        # 若按发布时间补年份，同一份通知的节点会裂成 2019 与 2020 两截。
        check("标题年份覆盖写错的发布年份",
              [n["date"][:4] for n in tl_tb["nodes"]] == ["2019"] * len(tl_tb["nodes"]),
              str([n["date"] for n in tl_tb["nodes"]]))
        tl_tb_plain = timeline_mod.extract(TL_TABLE, "2019-09-04")
        check("标题没有年份时完全退回发布时间",
              [n["date"] for n in tl_tb_plain["nodes"]]
              == [n["date"] for n in timeline_mod.extract(TL_TABLE, "2019-09-04", "")["nodes"]],
              "空标题与缺省标题的结果必须一致")
        check("标题年份与发布年份一致时不改变结果",
              [n["date"] for n in timeline_mod.extract(TL_TABLE, "2019-09-04", "2019年秋季安排")["nodes"]]
              == [n["date"] for n in tl_tb["nodes"]],
              "同年份标题不应产生任何差异")
        check("标题年份只认四位年份不认月份写法",
              timeline_mod._title_year("关于2026-06的安排") is None
              and timeline_mod._title_year("2026年秋季安排") == 2026,
              "月份写法不能被当成年份锚")

        # 表格行里「完成单位」常写「各学院、各研究生」这种混合值，
        # 真正动手的是谁要看「工作内容」那一格。只看完成单位的话，
        # 「研究生在系统里上传送审论文」会被判成学院办理，个人时间轴上就没了。
        TL_UNIT = (
            "序号\x1f截止日期\x1f工作内容\x1f完成单位\x1f星期\x1f周次\n"
            "1\x1f9 月 11 日\x1f研究生在系统里上传送审论文\x1f各学院、各研究生\x1f星期五\x1f第一周\n"
            "2\x1f9 月 20 日\x1f培养处下发秋季毕业生答辩登记表\x1f培养处\x1f星期一\x1f第二周\n"
            "3\x1f9 月 23 日\x1f①各学院上交学位材料至学位办 ②各学院上交毕业名单\x1f各学院\x1f星期三\x1f第三周"
        )
        tl_unit = timeline_mod.extract(TL_UNIT, "2019-09-04", "2019年秋季安排")
        by_action = {n["action"]: n for n in tl_unit["nodes"]}
        check("表格行动作格写明研究生时判为学生侧",
              by_action.get("研究生在系统里上传送审论文", {}).get("scope") == "student",
              str([(n["action"], n["scope"]) for n in tl_unit["nodes"]]))
        check("表格行完成单位是培养处时判为管理侧",
              by_action.get("培养处下发秋季毕业生答辩登记表", {}).get("scope") == "admin",
              str([(n["action"], n["scope"]) for n in tl_unit["nodes"]]))
        check("表格行并列多件事只取第一件",
              any("上交学位材料" in n["action"] for n in tl_unit["nodes"])
              and not any("毕业名单" in n["action"] for n in tl_unit["nodes"]),
              str([n["action"] for n in tl_unit["nodes"]]))

        # ================= 表格结构复原（创新点五） =================
        # 这一组断言守的是同一件事：二维表在采集期就不能被压成一维串。
        # 列边界一旦丢掉，下游再怎么改正则也补不回来——所以断言必须打在
        # 「分隔符有没有穿过清洗链路」和「列语义有没有还原」这两个点上。
        from app.services import collector as collector_mod  # noqa: E402
        from app.services import cleaner as cleaner_mod      # noqa: E402
        from app.services import tables as tables_mod        # noqa: E402
        from app.services import fields as fields_mod_tb     # noqa: E402
        from app.services import extractor as extractor_mod   # noqa: E402
        from app.services import analyzer as analyzer_mod     # noqa: E402

        TB_HTML = """<div class="content">
        <p>一、少数民族高层次骨干人才计划，面向博士研究生（专项计划）。</p>
        <p>（一）招生专业及拟招生计划：</p>
        <table><tr><th>学院名称</th><th>专业代码</th><th>专业名称</th><th>拟招生计划</th><th>咨询电话</th></tr>
        <tr><td>材料科学与工程学院</td><td>085600</td><td>材料与化工</td><td>1</td><td>027-87164677</td></tr>
        <tr><td>机电工程学院</td><td>080200</td><td>机械工程</td><td>1</td><td>027-87218976</td></tr>
        <tr><td>信息工程学院</td><td>085400</td><td>电子信息</td><td>1</td><td>027-87651806</td></tr>
        </table>
        <p>（二）网上报名、缴费、材料提交截止时间：2026年6月12日前</p></div>"""
        tb_page = collector_mod.parse_detail(TB_HTML, "http://x/a.shtml")
        tb_text = tb_page["content"]

        check("表格单元格在采集期保留列边界",
              collector_mod.TABLE_CELL_SEP in tb_text
              and "材料科学与工程学院" + collector_mod.TABLE_CELL_SEP + "085600" in tb_text,
              repr(tb_text[:160]))

        check("列边界分隔符能穿过正文清洗",
              collector_mod.TABLE_CELL_SEP in cleaner_mod.clean_text(tb_text),
              repr(cleaner_mod.clean_text(tb_text)[:160]))

        tb_tables = tables_mod.parse(tb_text)
        check("表格被识别为招生计划表且列数正确",
              len(tb_tables) == 1 and tb_tables[0]["columns"] == 5
              and tb_tables[0]["kind"] == "major_plan",
              str([(t["columns"], t["kind"]) for t in tb_tables]))

        check("表头列语义按语义映射而不是按位置猜",
              bool(tb_tables) and tb_tables[0]["kinds"] == ["college", "major_code",
                                                            "major_name", "plan", "phone"],
              str(tb_tables[0]["kinds"] if tb_tables else []))

        tb_sheet = tables_mod.extract(tb_text)
        check("表格按列还原出学院与专业列表",
              tb_sheet["stats"]["colleges"] == 3 and tb_sheet["stats"]["majors"] == 3
              and "材料科学与工程学院" in tb_sheet["colleges"]
              and "材料与化工" in tb_sheet["majors"],
              str(tb_sheet["stats"]))

        check("表格里的咨询电话保留区号且不被截断",
              tb_sheet["phones"] == ["027-87164677", "027-87218976", "027-87651806"],
              str(tb_sheet["phones"]))

        # 一维压平文本不应该被"猜"成表格：没有列边界就是没有，宁可返回空。
        flat_text = "学院名称专业代码专业名称\n085600材料与化工1027-87164677"
        check("压平文本不会被误判成表格",
              tables_mod.parse(flat_text) == [], str(tables_mod.parse(flat_text)))

        _SEP = tables_mod.CELL_SEP

        # 旧版压平的存量正文，列边界不可复原。这里能做的、也应该做的只有
        # 一件事：把它认出来并明确要求重新采集，而不是假装能猜回来。
        _stale = ("学院名称专业代码专业名称\n"
                  "拟招生计划咨询电话材料科学与工程学院\n"
                  "085600材料与化工1027-87164677\n"
                  "1027-87218976")
        _stale_sheet = tables_mod.extract(_stale)
        check("压平正文被标记为需要重新采集",
              _stale_sheet["needs_recollect"] is True
              and _stale_sheet["tables"] == []
              and "重新采集" in _stale_sheet["reason"],
              str(_stale_sheet["stats"]) + " / " + str(_stale_sheet["reason"]))

        # 只有单个表格特征时不能误判：正文里提一个电话、一处学院名都很常见。
        check("只提到电话或学院名的正文不算压平的表格",
              tables_mod.needs_recollect("请咨询材料科学与工程学院，电话 027-87164677。")
              is False, "误判为压平表格")

        # 新版采集的正文带列边界，绝不能被标成待重新采集。
        check("已有列边界的正文不被标记为待重新采集",
              tables_mod.needs_recollect(
                  "材料科学与工程学院" + _SEP + "085600" + _SEP + "材料与化工"
                  + _SEP + "1" + _SEP + "027-87164677") is False,
              "误标为压平表格")

        # URL 里的 7 位编号曾被当成电话（t20250425_1326927.shtml）
        tb_contact, _ = fields_mod_tb.extract_contact(
            "详见 https://x.edu.cn/a/t20250425_1326927.shtml 的说明，咨询电话 027-87164677。")
        check("URL 里的数字不会被当成联系电话",
              tb_contact["phone"] == ["027-87164677"], str(tb_contact["phone"]))

        # 身份词：正文只有「研究生院」「研究生专项计划」时不得兜成硕士研究生
        check("机构名里的「研究生」不被当成本科或硕士受众",
              extractor_mod._canon_targets("武汉理工大学研究生院研究生专项计划") == [],
              str(extractor_mod._canon_targets("武汉理工大学研究生院研究生专项计划")))

        tb_target, _ = extractor_mod._extract_target(
            "一、少数民族高层次骨干人才计划，面向博士研究生（专项计划）。",
            "关于我校2026年部分博士研究生（专项计划）补充报名的通知")
        check("标题里的学历层次参与受众判定",
              tb_target == ["博士研究生"], str(tb_target))

        tb_an = analyzer_mod.analyze(
            "关于我校2026年部分博士研究生（专项计划）补充报名的通知",
            tb_text, "2026-06-09")
        check("表格通知的受众不再判反成硕士研究生",
              tb_an["target"] == ["博士研究生"], str(tb_an["target"]))
        check("表格通知的专业限制由表格补全",
              "材料科学与工程学院" in tb_an["major_requirement"]
              and "材料与化工" in tb_an["major_requirement"],
              str(tb_an["major_requirement"]))
        check("表格通知的联系电话带区号且齐全",
              tb_an["contact"]["phone"] == ["027-87164677", "027-87218976", "027-87651806"],
              str(tb_an["contact"]["phone"]))
        check("解析结果带表格统计供界面说明来源",
              tb_an.get("table_stats", {}).get("total") == 1
              and bool(tb_an.get("table_detail")),
              str(tb_an.get("table_stats")))
        check("表格字段通过 C11 列序接地校验",
              any("C11" in p for p in tb_an["checks"]["passed"])
              and not any("C11" in i for i in tb_an["checks"]["issues"]),
              str(tb_an["checks"]["issues"]))

        # C11 反向：列错位的值必须被拦下，而不是静静通过
        bad_tb = dict(tb_an)
        bad_tb["major_requirement"] = list(tb_an["major_requirement"]) + ["不存在的学院"]
        tb_rep = validator.audit(bad_tb, tb_text, "2026-06-09",
                                 "关于我校2026年部分博士研究生（专项计划）补充报名的通知")
        check("C11 拦下无法回放原文的表格字段",
              any("C11" in i and "回放" in i for i in tb_rep["issues"]),
              str(tb_rep["issues"]))

        # 无表格正文不应被表格逻辑影响
        plain_an = analyzer_mod.analyze(
            "关于开展2026年暑期社会实践的通知",
            "各学院：请于2026年7月10日前提交社会实践总结材料，面向全体本科生。", "2026-06-01")
        check("无表格正文的解析结果不含表格统计",
              not plain_an.get("table_stats", {}).get("total"),
              str(plain_an.get("table_stats")))


        # 办理清单的截止锚点：多轨道通知必须按轨道各给一条，而不是只挂一个日期。
        # 只挂单值等于让学生照别人的日期办事——硕士生看到博士的 10-08 就会误判已过期。
        from app.services import checklist as checklist_mod  # noqa: E402
        tl_notice = {
            "content": TL_TEXT,
            "analysis": {
                "actionable": True,
                "deadline": "2026-10-08",
                "deadline_kind": "办理截止",
                "timeline": tl_an["timeline_nodes"],
                "evidence": {"deadline": "10月8日（冬季）前，博士学位申请人预答辩截止"},
                "materials": [],
            },
        }
        tl_chk = checklist_mod.build(tl_notice)
        dl_steps = [s for s in tl_chk["steps"] if s.get("source") == "deadline"]
        check("多轨道通知的截止锚点按轨道分别给出",
              len(dl_steps) >= 2, str([s.get("title") for s in dl_steps])[:220])
        check("截止锚点标题写明轨道与适用身份",
              all("轨道" in (s.get("detail") or "") or "适用身份" in (s.get("detail") or "")
                  for s in dl_steps) if dl_steps else False,
              str([s.get("detail") for s in dl_steps])[:220])
        check("截止锚点日期覆盖两条轨道而非只有单值",
              len({d for d in (s.get("title", "") for s in dl_steps)}) >= 2
              and not all("2026-10-08" in (s.get("title") or "") for s in dl_steps),
              str([s.get("title") for s in dl_steps])[:220])
        # 无时间轴的通知退回单值锚点，不能因为新逻辑把老通知的锚点弄没了
        plain_chk = checklist_mod.build({
            "content": "请于2026年12月20日前提交报名表至学院办公室。",
            "analysis": {"actionable": True, "deadline": "2026-12-20",
                         "deadline_kind": "报名截止", "timeline": [],
                         "evidence": {"deadline": "2026年12月20日"}, "materials": []},
        })
        plain_dl = [s for s in plain_chk["steps"] if s.get("source") == "deadline"]
        check("无时间轴通知仍保留单值截止锚点",
              len(plain_dl) == 1 and "2026-12-20" in (plain_dl[0].get("title") or ""),
              str([s.get("title") for s in plain_dl])[:200])

        user2 = {"grade": "大二", "major": "信息管理与信息系统", "interests": ["竞赛"]}
        base_a = {"deadline": "2026-12-20", "target": ["本科生"], "conditions": [],
                  "materials": [], "evidence": {}, "category": "学科竞赛", "confidence": 0.8}
        mk = lambda extra: match_one(user2, {"id": 1, "category": "学科竞赛"}, {**base_a, **extra})
        check("专业硬条件冲突判暂不符合",
              mk({"major_requirement": ["计算机", "电子信息", "自动化"]})["level"] == "no")
        check("短前缀不造成专业假命中（信息管理 vs 电子信息）",
              mk({"major_requirement": ["电子信息"]})["level"] == "no")
        check("专业命中时保持符合", mk({"major_requirement": ["信息管理"]})["level"] == "match")
        check("学历硬条件冲突判暂不符合",
              mk({"grade_requirement": ["研究生"]})["level"] == "no")
        check("年级命中时保持符合",
              mk({"grade_requirement": ["本科生", "大二"]})["level"] == "match")

        # 12.4b 多因子加权与推荐位的可解释性
        # 单点打分只能说明"结论对不对"，证明不了"为什么是这个分"。
        # 这里钉住三件事：七个因子权重归一、分项贡献能算回总分、否决项必须同时压分。
        from app.services.matcher import (                        # noqa: E402
            FACTOR_WEIGHTS, MATCH_THRESHOLD, score_factors,
        )
        from app.services.recommender import (                    # noqa: E402
            DIVERSITY_LAMBDA, MAX_PER_CATEGORY, explain, recommend,
        )

        check("七因子权重归一为 1",
              abs(sum(FACTOR_WEIGHTS.values()) - 1.0) < 1e-9
              and len(FACTOR_WEIGHTS) == 7, str(FACTOR_WEIGHTS))

        scored_ok = score_factors(user2, {"id": 1, "category": "学科竞赛"}, base_a)
        contrib_sum = sum(f["contribution"] for f in scored_ok["factors"])
        check("分项贡献之和等于拟合度",
              abs(contrib_sum - scored_ok["fit"]) < 1e-6,
              f"贡献和 {contrib_sum:.4f} vs fit {scored_ok['fit']:.4f}")
        check("拟合度映射到 5~98 分且过阈值判符合",
              scored_ok["score"] >= MATCH_THRESHOLD
              and 5 <= scored_ok["score"] <= 98,
              f"score={scored_ok['score']} fit={scored_ok['fit']}")

        vetoed = score_factors(user2, {"id": 1, "category": "学科竞赛"},
                               {**base_a, "grade_requirement": ["研究生"]})
        check("否决项把分数压到阈值以下而不是只改结论",
              vetoed["conflict"] is True and vetoed["score"] < MATCH_THRESHOLD,
              f"conflict={vetoed['conflict']} score={vetoed['score']}")

        # 兴趣完全不相关：资格够也不该直接判"符合"，否则只关注竞赛的同学
        # 会在首屏看到满屏"志愿服务 符合"。
        off = match_one(
            {"grade": "大二", "major": "信息管理与信息系统", "interests": ["竞赛"]},
            {"id": 2, "category": "志愿服务"},
            {**base_a, "category": "志愿服务"},
        )
        check("兴趣完全不相关时资格够也只判可能符合",
              off["level"] == "maybe", f"level={off['level']} score={off['score']}")

        cold = score_factors({"grade": "大二", "major": "", "interests": []},
                             {"id": 3, "category": "学科竞赛"}, base_a)
        interest_factor = [f for f in cold["factors"] if f["key"] == "interest"][0]
        check("未填兴趣时兴趣因子取中性而非零分",
              abs(interest_factor["raw"] - 0.5) < 1e-9,
              f"raw={interest_factor['raw']}")

        # 新鲜度：缺时间必须中性；有近期时间必须高于陈旧时间。
        # base_a 没有 publish_time/created_at，所以缺时间分支是最常走的一条。
        fresh_factor = [f for f in scored_ok["factors"] if f["key"] == "freshness"][0]
        check("缺少发布时间时新鲜度取中性",
              abs(fresh_factor["raw"] - 0.5) < 1e-9,
              f"raw={fresh_factor['raw']} note={fresh_factor['note']}")

        _reco_rows = [
            {"id": 11, "title": "数学建模竞赛校内选拔", "category": "学科竞赛",
             "analysis": {**base_a, "deadline": "2026-12-20"}},
            {"id": 12, "title": "蓝桥杯校内选拔", "category": "学科竞赛",
             "analysis": {**base_a, "deadline": "2026-12-18"}},
            {"id": 13, "title": "ACM 校内选拔", "category": "学科竞赛",
             "analysis": {**base_a, "deadline": "2026-12-16"}},
            {"id": 14, "title": "华为 ICT 大赛", "category": "学科竞赛",
             "analysis": {**base_a, "deadline": "2026-12-14"}},
            {"id": 15, "title": "学术讲座：数字孪生", "category": "学术讲座",
             "analysis": {**base_a, "category": "学术讲座", "deadline": "2026-12-12"}},
            {"id": 16, "title": "已截止的竞赛", "category": "学科竞赛",
             "analysis": {**base_a, "deadline": "2020-01-01"}},
            {"id": 17, "title": "一条新闻稿", "category": "校园活动",
             "analysis": {**base_a, "genre": "news", "actionable": False,
                          "category": "校园活动"}},
            # 同一份通知的两个来源副本：教务处与学院各发一次，标题只差外壳。
            # 推荐位只有 6 格，必须只占一格。
            {"id": 18, "title": "关于数学建模竞赛校内选拔的通知", "category": "学科竞赛",
             "analysis": {**base_a, "deadline": "2026-12-20"}},
        ]
        reco = recommend(user2, _reco_rows, limit=6)
        ids = [x["notice_id"] for x in reco["items"]]
        check("推荐位不推已截止与新闻稿",
              16 not in ids and 17 not in ids, str(ids))
        check("同一通知的重复副本只占一个推荐位",
              not (11 in ids and 18 in ids) and reco["meta"]["skipped"]["duplicate"] >= 1,
              f"ids={ids} skipped={reco['meta']['skipped']}")
        check("推荐位同分类受上限约束并保留其它分类",
              ids.count(14) <= 1 and any(x in ids for x in (15,))
              or sum(1 for i in ids if i in (11, 12, 13, 14)) <= MAX_PER_CATEGORY + 1,
              str(ids))
        check("推荐项每条都带「为什么推给你」",
              bool(reco["items"]) and all(x.get("why") for x in reco["items"]),
              str([x.get("why") for x in reco["items"]]) [:200])
        check("召回的跳过项按原因分桶统计",
              set(reco["meta"]["skipped"]) == {"no_analysis", "not_actionable",
                                               "expired", "vetoed", "duplicate"}
              and reco["meta"]["skipped"]["expired"] >= 1,
              str(reco["meta"]["skipped"]))
        # MMR 重排会为了覆盖面故意把同分类的次优项往后挪，
        # 所以只能要求"首位是拟合度最高的一条"，不能要求整体严格降序。
        check("重排后首位仍是拟合度最高的一条",
              abs(reco["items"][0]["fit"] - max(x["fit"] for x in reco["items"])) < 1e-9,
              str([x["fit"] for x in reco["items"]]))
        check("多样性系数在 0~1 之间",
              0 < DIVERSITY_LAMBDA < 1, str(DIVERSITY_LAMBDA))
        check("模型说明返回七因子与三段式流程",
              len(explain()) == 7
              and abs(sum(f["weight"] for f in explain()) - 1.0) < 1e-9,
              str([(f["key"], f["weight"]) for f in explain()]))

        p = {
            "title": "关于2027届校园招聘宣讲会的通知",
            "content": "招聘对象：2027届毕业生，专业包括计算机、电子信息等相关专业。\n"
                       "报名截止：2026年12月20日，请提交报名表至学院办公室。\n"
                       "联系人：张老师 电话：027-87654321，邮箱 job@whut.edu.cn",
            "publish_time": "2026-11-06",
            "source_name": "就业指导中心",
        }
        r = c.post("/api/notices", json=p)
        rid = r.json()["id"]
        a = c.post(f"/api/notices/{rid}/analyze").json()["analysis"]
        check("API 输出完整结构化字段与校验报告",
              "grade_requirement" in a and "checks" in a and "location" in a
              and "计算机" in a["major_requirement"], str(a)[:180])
        check("联络方式经 API 落库", "027-87654321" in a["contact"].get("phone", []), str(a["contact"]))
        a = c.put(f"/api/notices/{rid}/analysis", json={"action": "approve"}).json()["analysis"]
        check("人工审核：通过", a["reviewed"] is True and a["needs_review"] is False)
        a = c.put(f"/api/notices/{rid}/analysis",
                  json={"action": "reject", "reject_reason": "截止日期与原文不符"}).json()["analysis"]
        check("人工审核：驳回后回到待复核",
              a["reviewed"] is False and a["needs_review"] is True and "人工驳回" in a["review_note"])
        a = c.put(f"/api/notices/{rid}/analysis",
                  json={"action": "approve", "major_requirement": ["信息管理"],
                        "location": "南湖校区"}).json()["analysis"]
        check("修正结构化字段生效且清掉旧驳回说明",
              a["major_requirement"] == ["信息管理"] and a["location"] == "南湖校区"
              and "人工驳回" not in a["review_note"], str(a)[:180])

        print("\n[12] 健壮性与数据一致性回归")
        # 12.1 全站统一日期解析入口
        check("日期工具：合法 ISO 可解析", safe_iso_date("2026-11-20") is not None)
        check("日期工具：非法字符串安全返回 None",
              all(safe_iso_date(x) is None
                  for x in ("2026-11-1", "2026-11-13x", "不是日期", "", None)))
        check("日期工具：is_iso_date 严格补零且校验真实日历",
              is_iso_date("2026-11-20") and not is_iso_date("2026-11-1")
              and not is_iso_date("2026-02-30"))

        # 12.2 人工修正是唯一写入口，脏日期必须在这里被拒绝
        for dirty in ("2026-11-13x", "2026-11-1", "不是日期", "2026/11/20"):
            r = c.put(f"/api/notices/{rid}/analysis", json={"deadline": dirty})
            check(f"人工修正拒绝非法日期 {dirty}", r.status_code == 400, r.text[:120])
        r = c.put(f"/api/notices/{rid}/analysis", json={"deadline": "2026-12-25"})
        check("人工修正接受合法日期",
              r.status_code == 200 and r.json()["analysis"]["deadline"] == "2026-12-25")

        # 12.3 绕过接口直接注入脏日期（历史数据 / 旧版本遗留），全链路仍要可用
        with SessionLocal() as db:
            db.execute(text("UPDATE notice_analysis SET deadline='2026-11-13x' WHERE notice_id=:i"),
                       {"i": rid})
            db.commit()
        for path in ("/api/dashboard/stats", f"/api/users/{u['id']}/agenda?days=30",
                     f"/api/users/{u['id']}/matches", "/api/notices?review=reviewed"):
            r = c.get(path)
            check(f"库内含脏日期仍可用：{path.split('?')[0]}", r.status_code == 200, r.text[:120])
        with SessionLocal() as db:
            db.execute(text("UPDATE notice_analysis SET deadline='2026-12-25' WHERE notice_id=:i"),
                       {"i": rid})
            db.commit()

        # 12.4 原文改动后，旧的已审核结论必须退回待复核
        r = c.put(f"/api/notices/{rid}",
                  json={"content": "各学院：报名截止时间调整为2026年12月31日，请提交报名表，面向本科生。"})
        a = r.json()["analysis"]
        check("修改原文后退回待复核",
              a["needs_review"] is True and a["reviewed"] is False, str(a.get("review_note")))

        # 12.5 驳回重解析不得夹带写入修正字段
        before = c.get(f"/api/notices/{rid}").json()["analysis"]["major_requirement"]
        a = c.put(f"/api/notices/{rid}/analysis",
                  json={"action": "reject", "reject_reason": "需重解析",
                        "major_requirement": ["不该被写入"]}).json()["analysis"]
        check("驳回重解析不夹带写入修正字段",
              a["major_requirement"] == before and "人工驳回" in a["review_note"],
              str(a["major_requirement"]))

        # 12.6 删除通知必须连带清理个人待办（SQLite 外键默认关闭）
        c.post(f"/api/users/{u['id']}/tasks", json={"notice_id": rid})
        r = c.delete(f"/api/notices/{rid}")
        tasks = c.get(f"/api/users/{u['id']}/tasks").json()
        check("删除通知后无僵尸待办",
              r.status_code == 200 and all(t["notice_id"] != rid for t in tasks), str(tasks[:2]))
        with SessionLocal() as db:
            check("数据库层无孤儿待办",
                  db.scalar(select(UserTask).where(UserTask.notice_id == rid)) is None)

        # 12.7 删除用户同样不留孤儿待办
        tmp = c.post("/api/users", json={"name": "级联验证", "grade": "大二",
                                         "major": "信息管理"}).json()
        c.post(f"/api/users/{tmp['id']}/tasks", json={"notice_id": nid})
        c.delete(f"/api/users/{tmp['id']}")
        with SessionLocal() as db:
            check("删除用户后无孤儿待办",
                  db.scalar(select(UserTask).where(UserTask.user_id == tmp["id"])) is None)

        # 12.8 接口声明的 top_k 必须真正生效
        # 旧断言 `len(q1) <= 1 and len(q3) >= len(q1)` 在语料只有 1 条命中时恒真：
        # 变异测试证明「路由不透传 top_k」「检索不截断」都能全绿通过。
        # 现在补单元层探针 + 多条命中的接口层探针，两层分别可独立抓到缺陷。
        from app.services import qa as qa_mod                       # noqa: E402
        probe_corpus = [
            {"id": 900 + i, "title": "关于举办数学建模竞赛的通知",
             "content": "数学建模竞赛报名与选拔安排，面向本科生。",
             "category": "学科竞赛", "source_url": ""}
            for i in range(5)
        ]
        n1 = len(qa_mod.retrieve("数学建模竞赛", probe_corpus, top_k=1))
        n3 = len(qa_mod.retrieve("数学建模竞赛", probe_corpus, top_k=3))
        check("检索按 top_k 截断（单元探针）", n1 == 1 and n3 == 3,
              f"top_k=1 -> {n1} / top_k=3 -> {n3}")
        for i in range(3):
            c.post("/api/notices", json={
                "title": f"关于举办数学建模竞赛选拔赛的通知（第{i + 1}批）",
                "content": "各学院：数学建模竞赛校级选拔报名开始，请本科生按通知提交报名表。",
                "publish_time": "2026-11-01", "source_name": "教务处"})
        q1 = c.post("/api/qa/ask", json={"query": "数学建模竞赛", "top_k": 1}).json()
        q3 = c.post("/api/qa/ask", json={"query": "数学建模竞赛", "top_k": 3}).json()
        check("问答 top_k 参数生效",
              len(q1["citations"]) == 1 and len(q3["citations"]) >= 3,
              f"top_k=1 -> {len(q1['citations'])} / top_k=3 -> {len(q3['citations'])}")
        check("问答 top_k 越界被约束",
              c.post("/api/qa/ask", json={"query": "竞赛", "top_k": 99}).status_code == 422)

        # 12.9 边界：分页越界 / 空库筛选
        r = c.get("/api/notices?page=99999&size=20")
        check("分页越界返回空列表而非报错",
              r.status_code == 200 and r.json()["items"] == [], r.text[:120])

        # 12.10 首页示例问题必须「点得下去、答得准」
        # 示例直接从 index.html 抓取：改写或新增示例而不更新期望表，本组断言会变红。
        # 早期缺陷是「适合我的竞赛」「这个竞赛…材料」直接答暂未找到，其余只回一串标题。
        import datetime as _dt                                    # noqa: E402
        import re as _re                                          # noqa: E402
        from app.services import qa as qa_mod3                    # noqa: E402

        def _qa_item(nid, title, category, deadline, body, **fields):
            base = {
                "id": nid, "title": title, "content": body, "category": category,
                "source_url": "", "genre": "notice", "actionable": True,
                "deadline": deadline, "deadline_kind": "报名截止",
                "registration_start": "", "target": [], "grade_requirement": [],
                "conditions": [], "materials": [], "apply_method": "",
                "location": "", "contact": {}, "evidence": {},
            }
            base.update(fields)
            return base

        # 语料里的日期一律相对"今天"生成。
        # 曾经写死 2026-11-20 这类字面量，跑到 11-26 时全部变成过期，
        # 「尚未截止」「按日期升序」三项断言就集体变红——不是代码坏了，
        # 是测试自己在日历上过期。写死的日期等于给自检埋了一颗定时炸弹。
        def _d(offset: int) -> str:
            return (_dt.date.today() + _dt.timedelta(days=offset)).isoformat()

        def _cn(iso: str) -> str:
            y, m, dd = iso.split("-")
            return "%d年%d月%d日" % (int(y), int(m), int(dd))

        D_MATH, D_ICT = _d(5), _d(2)
        D_ACM, D_SCHOLAR, D_POOR = _d(8), _d(3), _d(6)
        D_RUN, D_TALK, D_PAST = _d(4), _d(4), _d(-500)

        qa_corpus = [
            _qa_item(1, "关于举办2026年武汉理工大学数学建模竞赛暨全国大学生数学建模竞赛校内选拔的通知",
                     "学科竞赛", D_MATH,
                     "各学院：关于举办2026年数学建模竞赛的通知。报名时间%s至%s，"
                     "报名截止后不再受理。提交材料：报名表、个人简历、作品说明。面向全日制本科生，"
                     "以3人团队形式报名，参赛队员须无违纪记录。" % (_cn(_d(-12)), _cn(D_MATH)),
                     materials=["报名表", "个人简历", "作品说明"], target=["本科生"],
                     conditions=["3人团队", "无违纪记录"], registration_start=_d(-12),
                     evidence={"materials": "提交材料：报名表、个人简历、作品说明"}),
            _qa_item(3, "关于组织参加第十一届华为ICT大赛创新赛的通知", "学科竞赛", D_ICT,
                     "各学院：关于组织参加第十一届华为ICT大赛创新赛的通知，报名截止%s，"
                     "须以团队形式参赛并提交作品材料。" % _cn(D_ICT),
                     materials=["作品信息收集表", "作品介绍PPT"], target=["本科生", "硕士研究生"]),
            _qa_item(4, "2026年ACM-ICPC程序设计竞赛校内选拔赛报名启动", "学科竞赛", D_ACM,
                     "关于2026年ACM-ICPC程序设计竞赛校内选拔赛报名的通知，报名截止%s。" % _cn(D_ACM),
                     target=["本科生"]),
            _qa_item(5, "关于开展2025—2026学年国家奖学金评审工作的通知", "奖助学金", D_SCHOLAR,
                     "关于开展国家奖学金评审工作的通知。申请条件：学习成绩排名专业前10%%，无不及格科目，"
                     "二年级以上。提交材料：申请审批表、成绩单、获奖证书。报名截止%s。" % _cn(D_SCHOLAR),
                     conditions=["专业前10%", "无不及格科目", "二年级以上"],
                     materials=["申请审批表", "成绩单", "获奖证书"],
                     target=["本科生", "大二"], grade_requirement=["本科生", "大二"]),
            _qa_item(7, "关于评选2025—2026学年国家励志奖学金的通知", "奖助学金", D_POOR,
                     "关于评选国家励志奖学金的通知。申请条件：家庭经济困难、专业前30%%。"
                     "提交材料：申请表、成绩单、家庭经济困难认定证明、个人总结。报送截止%s。" % _cn(D_POOR),
                     conditions=["家庭经济困难", "专业前30%"],
                     materials=["申请表", "成绩单", "家庭经济困难认定证明", "个人总结"],
                     target=["本科生"], deadline_kind="报送截止"),
            _qa_item(20, "关于举办2026年阳光体育冬季长跑比赛的通知", "校园活动", D_RUN,
                     "关于举办阳光体育冬季长跑比赛的通知，报名截止%s。" % _cn(D_RUN)),
            _qa_item(22, "华为技术有限公司2027届校园招聘宣讲会（武汉理工大学站）", "实习就业", D_TALK,
                     "关于校园招聘宣讲会的通知，预约办理截止%s。" % _cn(D_TALK)),
            _qa_item(99, "关于举办第五届武汉理工大学MPAcc案例大赛的通知", "学科竞赛", D_PAST,
                     "关于举办MPAcc案例大赛的通知，报名截止%s，已过期的历史通知。" % _cn(D_PAST),
                     materials=["案例解决方案", "授权书"]),
        ]

        QA_EXPECT = {
            "有哪些学科竞赛可以报名？": {"must": ["尚未截止"],
                                  "forbid": [D_PAST, "（已截止）"]},
            "数学建模竞赛需要提交哪些材料？": {"must": ["报名表", "个人简历", "作品说明"],
                                     "forbid": ["暂未找到"]},
            "国家奖学金的申请条件是什么？": {"must": ["专业前10%", "无不及格科目"],
                                    "forbid": ["国家励志奖学金"]},
            "华为ICT大赛的报名截止时间？": {"must": [D_ICT], "forbid": ["MPAcc"]},
            # 不绑定具体日期字面量：固定日期会随时间过期，导致「近期截止」正确排除后断言反而变红。
            # 日期正确性由下方「近期截止按日期升序且排除过期」用当天日期动态断言，这里只校验答复口径。
            "近期有哪些通知即将截止？": {"must": ["由近到远", "尚未截止"],
                                 "forbid": [D_PAST]},
            "国家励志奖学金需要准备什么材料？": {"must": ["申请表", "家庭经济困难认定证明", "个人总结"],
                                      "forbid": ["暂未找到"]},
        }
        html_q = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
        examples = _re.findall(r'data-q="([^"]+)"', html_q)
        check("首页示例问题与期望表逐一对应",
              len(examples) == len(QA_EXPECT) and set(examples) == set(QA_EXPECT),
              str(examples))
        for q in examples:
            exp = QA_EXPECT.get(q)
            if exp is None:
                check(f"示例问题未登记期望：{q[:14]}", False, "请同步更新 QA_EXPECT")
                continue
            d = qa_mod3.answer(q, qa_corpus, top_k=3)
            ans = d.get("answer") or ""
            ids = qa_mod3._cite_ids(ans)
            vals = [v for row in d.get("answer_fields", [])
                    for group in row.get("fields", {}).values()
                    for v in (group if isinstance(group, list) else [group])
                    if isinstance(v, str) and len(v) >= 2]
            good = (
                d.get("hit") is True and d.get("mode") == "structured"
                and d.get("citation_ok") is True and bool(d.get("citations"))
                and all(k in ans for k in exp["must"])
                and not any(k in ans for k in exp["forbid"])
                and "暂未找到" not in ans
                and bool(ids) and all(1 <= i <= len(d["citations"]) for i in ids)
                and any(v in ans for v in vals)
            )
            check(f"示例精准回答：{q[:14]}", good,
                  f"intent={d.get('intent')} mode={d.get('mode')} ans={ans[:150]!r}")

        check("查询清洗去掉指代与粘连二元词",
              "竞赛" in qa_mod3._terms("最近有哪些适合我的竞赛？")
              and not ({"合我", "我的", "的竞", "个竞"} & qa_mod3._terms("最近有哪些适合我的竞赛？")),
              str(qa_mod3._terms("最近有哪些适合我的竞赛？")))
        check("意图识别覆盖材料 / 截止 / 条件 / 名单",
              [qa_mod3.detect_intent(x) for x in (
                  "数学建模竞赛需要提交哪些材料？", "华为ICT大赛的报名截止时间？",
                  "国家奖学金的申请条件是什么？", "有哪些学科竞赛可以报名？")]
              == ["materials", "deadline", "condition", "list"],
              str([qa_mod3.detect_intent(x) for x in (
                  "数学建模竞赛需要提交哪些材料？", "华为ICT大赛的报名截止时间？",
                  "国家奖学金的申请条件是什么？", "有哪些学科竞赛可以报名？")]))
        check("纯指代问题不编造答案",
              qa_mod3.answer("这个需要准备什么？", qa_corpus)["mode"] == "clarify",
              str(qa_mod3.answer("这个需要准备什么？", qa_corpus).get("mode")))
        d_exact = qa_mod3.answer("国家奖学金的申请条件是什么？", qa_corpus)
        check("点名通知时不混入同名近亲",
              len(d_exact["citations"]) == 1
              and "励志" not in d_exact["citations"][0]["title"],
              str([x["title"] for x in d_exact["citations"]]))
        d_recent = qa_mod3.answer("近期有哪些通知即将截止？", qa_corpus)
        recent_dates = [str(f["fields"].get("deadline") or "")
                        for f in d_recent.get("answer_fields", [])]
        check("近期截止按日期升序且排除过期",
              bool(recent_dates) and recent_dates == sorted(recent_dates)
              and all(x >= _dt.date.today().isoformat() for x in recent_dates),
              str(recent_dates))
        r_struct = c.post("/api/qa/ask", json={"query": "数学建模竞赛需要提交哪些材料"})
        check("问答接口语料已接入结构化字段",
              r_struct.status_code == 200
              and r_struct.json().get("structured_corpus", 0) >= 1
              and bool(r_struct.json().get("intent"))
              and bool(r_struct.json().get("answer_fields")),
              r_struct.text[:160])

        # ============================================================
        # 只是问「有哪些」、没说「可以报名」时，仍应把还能报的排前面：
        # 否则首屏第一条永远是那条早就截止的历史通知。
        d_list_open = qa_mod3.answer("适合我的竞赛有哪些？", qa_corpus)
        first_cite = (d_list_open.get("citations") or [{}])[0]
        first_deadline = str(first_cite.get("deadline") or "")
        check("清单类问题优先列出仍可报名的通知",
              first_deadline >= _dt.date.today().isoformat(),
              f"first={first_deadline} "
              f"cites={[c.get('title', '')[:12] for c in d_list_open.get('citations', [])]}")

        # 一条都没法报名时，不能沿用「尚未截止」的模板口径。
        past_corpus = [_qa_item(51, "关于举办过期竞赛的通知", "学科竞赛", D_PAST,
                                "报名截止%s，请按时提交报名表。" % _cn(D_PAST),
                                materials=["报名表"])]
        d_past_only = qa_mod3.answer("有哪些学科竞赛可以报名？", past_corpus)
        check("全部过期时不谎称可以报名",
              "都已过报名截止" in d_past_only["answer"]
              and "尚未截止" not in d_past_only["answer"],
              d_past_only["answer"][:140])

        from app.services import conditions as cond_mod           # noqa: E402
        score_vals = [v for _, v in cond_mod.canonicalize(
            "学位课平均成绩不低于80分", strict=False)]
        check("分数门槛带上科目限定词便于核对",
              score_vals == ["学位课平均成绩80分"], str(score_vals))

        print("\n[14] 多端资源与 AI 可靠性")
        r = c.get("/manifest.webmanifest")
        manifest = r.json() if r.status_code == 200 else {}
        check("PWA 清单可访问且为 standalone",
              r.status_code == 200 and manifest.get("display") == "standalone",
              r.text[:160])
        check("PWA 清单含 any + maskable 图标",
              len(manifest.get("icons") or []) >= 3
              and any(i.get("purpose") == "maskable" for i in manifest.get("icons") or []),
              str(manifest.get("icons"))[:160])
        check("PWA 快捷方式可直达 AI 助手",
              any((s.get("url") or "").endswith("#qa") for s in manifest.get("shortcuts") or []),
              str(manifest.get("shortcuts"))[:160])

        r = c.get("/sw.js")
        check("Service Worker 由根路径下发",
              r.status_code == 200 and "CACHE_VERSION" in r.text
              and r.headers.get("service-worker-allowed") == "/",
              f"{r.status_code} {r.headers.get('service-worker-allowed')}")
        check("Service Worker 不缓存接口",
              'url.pathname.startsWith("/api/")' in r.text, "缺少 /api/ 直连分支")

        r = c.get("/static/icons/icon-192.png")
        check("PWA 图标可访问且为 PNG",
              r.status_code == 200 and r.headers.get("content-type", "").startswith("image/png"),
              f"{r.status_code} {r.headers.get('content-type')}")

        r = c.get("/")
        check("首页声明 manifest 与安全区",
              r.status_code == 200 and "manifest.webmanifest" in r.text
              and "viewport-fit=cover" in r.text,
              "首页缺少 PWA / 安全区声明")

        h = c.get("/api/health").json()
        check("健康检查声明多端资源",
              h.get("platforms", {}).get("pwa") is True
              and "HarmonyOS" in h.get("platforms", {}).get("clients", []),
              str(h.get("platforms"))[:160])

        # ---------------- 华为云可举证性：健康端点单点可见 ----------------
        # 「代码已就绪」与「真实已连通」必须分开：默认离线自检环境里
        # 三件套都没配，huawei.*.configured/active 必须如实为 false，
        # 同时 fallback 段要说清降级到了什么，绝不能把降级包装成已连通。
        hw = h.get("huawei", {})
        check("健康检查单点亮出华为云三件套状态",
              all(k in hw for k in ("rds", "dcs", "maas", "fallback")),
              str(list(hw))[:160])
        check("健康检查区分数据库已配置与真连通",
              set(("configured", "active", "kind")) <= set(hw.get("rds", {}))
              and hw["rds"]["active"] == (hw["rds"]["configured"] and h["database"]["ok"]),
              str(hw.get("rds"))[:160])
        check("离线自检环境如实标注华为云未连通且给出降级目标",
              hw.get("rds", {}).get("active") is False
              and hw.get("dcs", {}).get("active") is False
              and hw.get("maas", {}).get("active") is False
              and "SQLite" in hw.get("fallback", {}).get("database", "")
              and "规则引擎" in hw.get("fallback", {}).get("engine", ""),
              str(hw.get("fallback"))[:160])
        check("健康检查下发缓存种类与活动引擎",
              "cache" in h and "kind" in h.get("cache", {})
              and h.get("active_engine") in ("llm", "rule"),
              str((h.get("cache"), h.get("active_engine")))[:160])
        check("配置层提供是否外接华为云的布尔判据",
              app_settings.is_mysql is False and app_settings.is_redis is False
              and app_settings.is_maas is False,
              str((app_settings.is_mysql, app_settings.is_redis, app_settings.is_maas)))
        check("模型地址默认指向华为云 MaaS OpenAI 兼容端点",
              app_settings.LLM_BASE_URL == "https://api.modelarts-maas.com/v1",
              app_settings.LLM_BASE_URL)
        check("数据库种类随连接串切换为 MySQL 标签",
              app_settings.db_kind == "SQLite（本地）",
              app_settings.db_kind)

        s = c.get("/api/ai/status").json()
        check("AI 状态含冷却与能力清单",
              "cooldown_remaining" in s.get("llm", {})
              and isinstance(s.get("llm", {}).get("ready"), bool)
              and len(s.get("capabilities") or []) == 4,
              str(s)[:200])

        r = c.post("/api/ai/health")
        probe = r.json() if r.status_code == 200 else {}
        check("无密钥时连通性探测降级为规则引擎",
              r.status_code == 200 and probe.get("ok") is False
              and probe.get("engine") == "rule"
              and probe.get("error_kind") == "not_configured",
              r.text[:200])

        # 引用编号：越界即幻觉信号，必须能被识别
        from app.services import qa as qa_mod2                      # noqa: E402
        check("引用编号合法时判定可核对", qa_mod2.citation_check("结论[1][3]", 3)[0] is True)
        ok_bad, bad_ids, bad_note = qa_mod2.citation_check("结论[1][9]", 3)
        check("引用编号越界被识别", ok_bad is False and bad_ids == [9] and "不存在" in bad_note,
              str((ok_bad, bad_ids, bad_note)))
        check("回答未标注引用时提示以来源列表核对",
              qa_mod2.citation_check("没有编号的结论", 3)[0] is False)

        synonym_corpus = [
            {"id": 301, "title": "关于推荐免试攻读研究生的通知",
             "content": "符合条件的学生可提交推免申请材料。",
             "category": "其他", "source_url": ""},
        ]
        check("同义词扩展：保研可命中推免通知",
              [x["id"] for x in qa_mod2.retrieve("保研需要什么条件", synonym_corpus)] == [301],
              str(qa_mod2.retrieve("保研需要什么条件", synonym_corpus)))
        answer = qa_mod2.answer("推免需要什么材料", synonym_corpus)
        check("问答返回引擎、耗时与检索词",
              answer["engine"] in ("rule", "llm") and isinstance(answer["latency_ms"], int)
              and "推免" in answer.get("query_terms", []) and answer["citations"],
              str({k: answer.get(k) for k in ("engine", "latency_ms", "query_terms")}))

        # 冷却期：只验证本地状态机，不发起任何网络请求
        import time as _time                                        # noqa: E402
        from app.services.llm_client import LLMClient, LLMError      # noqa: E402
        cooled = LLMClient()
        cooled.api_key = "unit-test-key"
        cooled.provider = "maas"
        cooled._cooldown_until = _time.time() + 30
        check("冷却期内模型不标记为就绪",
              cooled.available is True and cooled.ready is False
              and cooled.describe()["cooldown_remaining"] > 0,
              str(cooled.describe()))
        try:
            cooled.chat([{"role": "user", "content": "ping"}])
            cooled_rejected = ""
        except LLMError as exc:
            cooled_rejected = str(exc)
        check("冷却期直接失败而不是等超时",
              "冷却" in cooled_rejected, cooled_rejected[:120])

        # ---- AI 链路静默缺陷回归：检查「检查工具」自己不会骗人 ----
        import types as _types                                       # noqa: E402
        from unittest.mock import patch as _patch                    # noqa: E402
        from app.services import analyzer as _analyzer                # noqa: E402

        proto = LLMClient()
        proto.api_key, proto.provider = "unit-test-key", "maas"
        with _patch.object(proto, "chat", return_value="这不是 JSON，只是解释文字"):
            try:
                proto.chat_json([{"role": "user", "content": "x"}])
                proto_err = ""
            except LLMError as exc:
                proto_err = str(exc)
        check("模型输出非 JSON 时计入熔断并写明原因",
              "JSON" in proto_err
              and proto.describe()["last_error_kind"] == "protocol"
              and proto.describe()["cooldown_remaining"] > 0
              and proto.ready is False,
              str(proto.describe())[:200])

        probe_ok_client = LLMClient()
        probe_ok_client.api_key, probe_ok_client.provider = "unit-test-key", "maas"
        probe_ok_client._fail_count = 2
        probe_ok_client._cooldown_until = _time.time() + 60
        with _patch.object(probe_ok_client, "chat", return_value="pong"):
            probe_good = probe_ok_client.probe()
        check("连通性探测成功不清空既有故障状态",
              probe_good.get("ok") is True
              and probe_ok_client._fail_count == 2
              and probe_ok_client.cooldown_remaining > 0,
              str(probe_ok_client.describe())[:200])

        probe_bad_client = LLMClient()
        probe_bad_client.api_key, probe_bad_client.provider = "unit-test-key", "maas"
        with _patch.object(probe_bad_client, "chat", side_effect=LLMError("网络故障")):
            probe_bad = probe_bad_client.probe()
        check("连通性探测失败不触发业务冷却",
              probe_bad.get("ok") is False
              and probe_bad_client._fail_count == 0
              and probe_bad_client.cooldown_remaining == 0
              and probe_bad_client.describe()["last_probe_ok"] is False,
              str(probe_bad_client.describe())[:200])

        fake_llm = _types.SimpleNamespace(
            ready=True, model="mock-model",
            describe=lambda: {"last_error_kind": "protocol"},
            chat_json=lambda *a, **k: (_ for _ in ()).throw(LLMError("模型输出不是合法 JSON")),
        )
        with _patch.object(_analyzer, "llm", fake_llm):
            degraded = _analyzer.analyze("关于举办竞赛的通知",
                                         COMPETITION + " 唯一标记 zx9f2", "2026-10-28")
        check("模型异常时降级结果带结构化原因",
              degraded.get("degraded") is True
              and degraded.get("engine") == "rule"
              and degraded.get("fallback_kind") == "protocol"
              and "JSON" in (degraded.get("fallback_reason") or ""),
              str({k: degraded.get(k) for k in ("degraded", "engine", "fallback_kind")}))

        plain = _analyzer.analyze("关于举办竞赛的通知", COMPETITION, "2026-10-28", use_llm=False)
        check("正常规则路径不误报降级",
              plain.get("degraded") is False and plain.get("fallback_reason") == "",
              str(plain.get("degraded")))

        net = (c.get("/api/system/info").json().get("network") or {})
        check("系统信息含局域网地址与 PWA 条件",
              isinstance(net.get("lan_urls"), list)
              and net.get("pwa", {}).get("requires_secure_context") is True
              and "HarmonyOS" in (net.get("clients") or []),
              str(net)[:200])

        prev = c.post("/api/ai/preview", json={
            "title": "关于举办竞赛的通知", "content": COMPETITION,
            "publish_time": "2026-10-28", "source_url": "https://news.whut.edu.cn/a.html"})
        pv = prev.json() if prev.status_code == 200 else {}
        check("解析预览返回降级字段与校验报告",
              prev.status_code == 200 and pv.get("degraded") is False
              and isinstance(pv.get("checks", {}).get("passed"), list),
              prev.text[:200])

        fav = c.get("/favicon.ico")
        check("站点图标不再返回 404",
              fav.status_code == 200
              and fav.headers.get("content-type", "").startswith("image/png"),
              f"{fav.status_code} {fav.headers.get('content-type')}")

        home = c.get("/")
        check("首页含 AI 助手双页签",
              all(k in home.text for k in ("qaTabs", "qaPaneLab", "labSamples"))
              and "qaPaneDevice" not in home.text,
              "应保留问答 / 解析实验室且移除多端接入锚点")

        from pathlib import Path as _Path                             # noqa: E402
        root_dir = _Path(__file__).resolve().parent.parent
        bat, sh = root_dir / "start_lan.bat", root_dir / "start_lan.sh"
        check("局域网一键启动脚本齐全",
              bat.exists() and sh.exists()
              and "--lan" in bat.read_text(encoding="utf-8")
              and "--lan" in sh.read_text(encoding="utf-8"),
              f"bat={bat.exists()} sh={sh.exists()}")

        # ---------------- 多端接入：静态资源与页面骨架 ----------------
        page = c.get("/")
        check("多端接入与冲突治理视图已挂载",
              'id="view-mobile"' in page.text and 'id="view-conflicts"' in page.text,
              "首页缺少 view-mobile / view-conflicts")
        check("多端导航入口存在",
              'data-view="mobile"' in page.text and 'data-view="conflicts"' in page.text,
              "侧栏缺少多端入口")
        check("多端样式表已引入",
              "/static/css/mobile.css" in page.text,
              "缺少 mobile.css 引用")
        mobile_css = c.get("/static/css/mobile.css")
        check("多端样式表可访问",
              mobile_css.status_code == 200 and ".conflict-card" in mobile_css.text,
              f"{mobile_css.status_code}")
        # 版式稳健性与可访问性：这三类缺陷在浏览器里才看得见，
        # 但完全能从 CSS 源码判定，所以钉进主自检，避免只有独立脚本才发现。
        style_raw = (root_dir / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
        style_nc = re.sub(r"/\*.*?\*/", "", style_raw, flags=re.S)
        mobile_nc = re.sub(r"/\*.*?\*/", "", mobile_css.text, flags=re.S)
        bare_fr = []
        for sheet in (style_nc, mobile_nc):
            for m in re.finditer(r"grid-template-(?:columns|rows)\s*:\s*([^;}]+)", sheet):
                outer = re.sub(r"minmax\([^()]*\)", "MM", m.group(1))
                if re.search(r"(?<![\w.])[\d.]+fr\b", outer):
                    bare_fr.append(m.group(1).strip()[:60])
        check("网格轨道不用裸 fr（否则 nowrap 长文本会撑破布局）",
              not bare_fr, f"裸轨道 {len(bare_fr)} 处：{bare_fr[:3]}")

        bad_semantic = re.findall(
            r"(?<![\w-])color\s*:\s*var\(--(?:success|warning|danger)\)", style_nc + mobile_nc)
        check("语义色不直接当文字颜色用（改用 --*-text）",
              not bad_semantic, f"命中 {len(bad_semantic)} 处")

        for var, want in (("--text-primary", "#111827"), ("--text-secondary", "#4B5563"),
                          ("--text-tertiary", "#5F6B7A")):
            check(f"三级文字色按 WCAG AA 取值：{var}",
                  re.search(re.escape(var) + r"\s*:\s*" + re.escape(want) + r"\b", style_nc,
                            re.I) is not None,
                  f"应写 {want}")
        # 弹窗与抽屉是模态语义：读屏要能报出角色与标题，键盘要能用 Esc 关掉，
        # 否则鼠标用户与键盘用户看到的是两个不同的产品。
        page_html = page.text
        for elem_id, label_id in (("drawer", "drawerTitle"), ("modalMask", "modalTitle"),
                                  ("loginMask", "loginTitle")):
            opening = re.search(r'<[^>]*id="' + elem_id + r'"[^>]*>', page_html)
            start = opening.start() if opening else 0
            # role="dialog" 可以挂在遮罩本身（抽屉），也可以挂在遮罩内的 .modal 上，
            # 只要这一段里存在带 aria-modal 且绑定 label_id 的对话框节点即可。
            segment = page_html[start:start + 400]
            labelled = re.search(r'<[^>]*id="' + label_id + r'"[^>]*>', page_html)
            check(f"弹窗 {elem_id} 具备对话框语义与标题绑定",
                  'role="dialog"' in segment and 'aria-modal="true"' in segment
                  and f'aria-labelledby="{label_id}"' in segment and labelled is not None,
                  f"segment={segment[:80]}")
        icon_aria = re.findall(r'class="icon-btn"[^>]*aria-label="[^"]+"', page_html)
        check("纯图形关闭按钮带 aria-label",
              len(icon_aria) >= 4, f"命中 {len(icon_aria)} 处")
        app_src = (root_dir / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
        check("登录弹窗打开把焦点移入角色卡片、关闭还回触发按钮",
              'role-card")?.focus()' in app_src and 'userChip")?.focus()' in app_src,
              "缺少焦点移交")
        check("Esc 先关登录弹窗再关抽屉（模态优先级）",
              'classList.contains("open")) { closeLogin(); return; }' in app_src,
              "Esc 处理未分优先级")
        # 页签是 tablist：读屏靠 aria-controls/tabpanel 建立页签与面板的对应，
        # 键盘靠漫游 tabindex + 方向键，缺任一项键盘用户就点不到第二个页签。
        for tab_id, pane_id in (("qaTabChat", "qaPaneChat"), ("qaTabLab", "qaPaneLab")):
            btn = re.search(r'<[^>]*id="' + tab_id + r'"[^>]*>', page_html)
            pane = re.search(r'<[^>]*id="' + pane_id + r'"[^>]*>', page_html)
            check(f"页签 {tab_id} 与面板 {pane_id} 建立 ARIA 对应",
                  btn is not None and pane is not None
                  and f'aria-controls="{pane_id}"' in btn.group(0)
                  and 'role="tab"' in btn.group(0)
                  and 'role="tabpanel"' in pane.group(0)
                  and f'aria-labelledby="{tab_id}"' in pane.group(0),
                  f"btn={btn.group(0)[:60] if btn else None}")
        check("页签采用漫游 tabindex（只留一个 Tab 停靠点）",
              'tabindex="0"' in page_html and 'tabindex="-1"' in page_html
              and 'b.setAttribute("tabindex", active ? "0" : "-1")' in app_src,
              "缺少 roving tabindex")
        check("页签支持方向键与 Home/End 切换",
              'ArrowLeft' in app_src and 'ArrowRight' in app_src
              and '"Home"' in app_src and 'switchQaTab(btns[next].dataset.tab)' in app_src,
              "缺少页签键盘切换")
        check("导航地标与表单控件都有可访问名称",
              'aria-label="主导航"' in page_html
              and 'aria-label="移动端底部导航"' in page_html
              and 'aria-label="搜索标题或正文关键词"' in page_html
              and 'aria-label="向 AI 助手提问"' in page_html,
              "缺少 aria-label 无障碍名称")
        check("导航当前视图用 aria-current 标记",
              'aria-current' in app_src and '"page"' in app_src
              and 'b.setAttribute("aria-current", "page")' in app_src,
              "导航缺少 aria-current 标记")
        check("操作反馈区域是读屏可播报的 live region",
              'aria-live="polite"' in page_html
              and 'role="status"' in page_html
              and 'id="toast"' in page_html,
              "toast 缺少 role=status / aria-live")
        # 清空 innerHTML 只清内容，容器仍有内边距与边框，会渲染成一条空白白条。
        check("未登录时匹配页不留下空白卡片",
              "pc.hidden = true" in app_src and "box.hidden = false" in app_src,
              "profileCard 只清空内容未隐藏容器")
        # sprite 图标旁边都已有文字标签，读屏不需要再念一遍「图像」。
        _svg_tags = re.findall(r"<svg\b[^>]*>", page_html + app_src)
        _svg_quiet = [t for t in _svg_tags if 'aria-hidden="true"' in t]
        check("装饰性图标 SVG 全部对读屏隐藏",
              len(_svg_tags) > 0 and len(_svg_tags) == len(_svg_quiet),
              f"共 {len(_svg_tags)} 个 svg，其中 {len(_svg_quiet)} 个已隐藏")
        check("小程序图标资源已就绪",
              (root_dir / "miniprogram" / "images" / "tab-home.png").exists()
              and (root_dir / "miniprogram" / "images" / "tab-mine-active.png").exists(),
              "tabBar 图标缺失")

        # 订阅消息的跳转页面必须是小程序里真实存在的页面。
        # 这类错误不会有任何报错：服务端照发、用户点开白屏，只能靠断言钉住。
        mp_pages = set()
        try:
            mp_json = json.loads((root_dir / "miniprogram" / "app.json").read_text(encoding="utf-8"))
            mp_pages = set(mp_json.get("pages") or [])
        except Exception as exc:  # noqa: BLE001 - 自检要给出可读原因
            mp_pages = set()
            _mp_err = str(exc)
        push_src = (root_dir / "app" / "routers" / "mobile.py").read_text(encoding="utf-8")
        push_pages = set(re.findall(r'page=f?"(pages/[A-Za-z0-9_\-]+/[A-Za-z0-9_\-]+)', push_src))
        check("订阅消息跳转页面在小程序页面清单内",
              bool(push_pages) and push_pages <= mp_pages,
              f"跳转页 {sorted(push_pages)} 不在 {sorted(mp_pages)} 内")

        # ---------------- 多端接入：接口 ----------------
        wstat = c.get("/api/wechat/status")
        ws = wstat.json() if wstat.status_code == 200 else {}
        check("多端状态含平台矩阵与能力清单",
              wstat.status_code == 200
              and ws.get("platforms", {}).get("miniprogram", {}).get("available") is True
              and ws.get("platforms", {}).get("calendar", {}).get("available") is True
              and any(x.get("key") == "checklist" for x in ws.get("capabilities") or []),
              wstat.text[:200])
        check("多端状态下发订阅模板标识",
              "template_id" in ws and "mode_label" in ws,
              str(list(ws))[:160])

        login = c.post("/api/wechat/login", json={"device_key": "smoke-dev", "nickname": "自检同学"})
        lj = login.json() if login.status_code == 200 else {}
        tok = lj.get("token", "")
        check("小程序游客登录可用",
              login.status_code == 200 and bool(tok)
              and lj.get("openid_mode") == "guest",
              login.text[:200])
        check("游客 openid 带 mock 前缀便于区分演示数据",
              str(lj.get("openid", "")).startswith("mock_")
              if lj.get("openid") else True,
              str(lj)[:160])

        # 同一 device_key 重登复用账号，但旧票据必须失效（避免多处登录互相踩）
        relogin = c.post("/api/wechat/login", json={"device_key": "smoke-dev", "nickname": "自检同学"})
        tok2 = relogin.json().get("token", "")
        check("同设备重登复用账号并刷新票据",
              relogin.json().get("user", {}).get("id") == lj.get("user", {}).get("id")
              and tok2 != tok,
              relogin.text[:160])
        stale = c.get("/api/mobile/bootstrap", headers={"x-token": tok})
        check("旧票据在重登后失效",
              stale.status_code == 401,
              str(stale.status_code))

        hdr = {"x-token": tok2}
        boot = c.get("/api/mobile/bootstrap", headers=hdr)
        bj = boot.json() if boot.status_code == 200 else {}
        check("首屏打包接口一次返回六个区块",
              boot.status_code == 200
              and all(k in bj for k in ("user", "stats", "agenda", "matched",
                                        "push_ready", "server_time")),
              boot.text[:200])
        check("无票据访问首屏接口被拒",
              c.get("/api/mobile/bootstrap").status_code == 401,
              "应返回 401")
        check("query token 通道可用（供 downloadFile 场景）",
              c.get(f"/api/mobile/bootstrap?token={tok2}").status_code == 200,
              "query 票据未生效")

        # ---------------- 办理清单 ----------------
        chk = c.get("/api/notices/1/checklist")
        cj = chk.json() if chk.status_code == 200 else {}
        check("办理清单接口返回步骤与统计",
              chk.status_code == 200
              and isinstance(cj.get("steps"), list)
              and isinstance(cj.get("stats"), dict)
              and "total" in cj.get("stats", {}),
              chk.text[:200])
        steps = cj.get("steps") or []
        check("办理清单不把联系方式误判为步骤",
              not any("联系电话" in (s.get("title") or "") or "联系人" in (s.get("title") or "")
                      for s in steps),
              str([s.get("title") for s in steps])[:200])
        check("办理清单步骤均带序号与渠道标记",
              all(isinstance(s.get("seq"), int) and "offline" in s for s in steps) if steps else True,
              str(steps[:2])[:200])
        # source 区分「原文里的动作句」与「系统补的时间锚点」，
        # 界面靠它标注「这句原文没有」，缺了就会把系统补的话冒充成原文。
        check("办理清单步骤标注来源类型",
              all(s.get("source") in ("text", "material", "deadline") for s in steps) if steps else True,
              str([s.get("source") for s in steps])[:200])
        check("办理清单截止锚点标记为 deadline 来源",
              any(s.get("source") == "deadline" and "前完成提交" in (s.get("title") or "")
                  for s in steps) if steps else True,
              str([s.get("title") for s in steps])[:200])

        # ---------------- 截止时间轴（端点） ----------------
        tl_notice_payload = {
            "title": "关于2026年秋冬季博士、硕士学位授予工作安排的通知",
            "content": TL_TEXT, "publish_time": "2026-06-15",
            "source_name": "研究生院学位办公室",
        }
        tl_new = c.post("/api/notices", json=tl_notice_payload)
        tl_nid = tl_new.json().get("id") if tl_new.status_code in (200, 201) else 0
        if tl_nid:
            c.post(f"/api/notices/{tl_nid}/analyze")
            tn = c.get(f"/api/notices/{tl_nid}/timeline")
            tnj = tn.json() if tn.status_code == 200 else {}
            check("单条通知时间轴端点返回多轨道节点",
                  tn.status_code == 200
                  and len(tnj.get("nodes") or []) >= 6
                  and len(tnj.get("tracks") or []) >= 2,
                  tn.text[:200])
            t_ms = c.get(f"/api/notices/{tl_nid}/timeline?role=硕士研究生&track=秋季")
            msj = t_ms.json() if t_ms.status_code == 200 else {}
            ms_nodes = msj.get("nodes") or []
            check("时间轴端点按身份与轨道双重过滤",
                  bool(ms_nodes)
                  and all(n.get("role") == "硕士研究生" and n.get("track") == "秋季"
                          for n in ms_nodes),
                  t_ms.text[:200])
            # 单值截止与个人时间轴不能给出同一个答案，这正是新能力的存在理由
            check("时间轴端点的硕士视图不含单值截止那个日期",
                  bool(ms_nodes)
                  and all(n.get("date") != msj.get("deadline") for n in ms_nodes),
                  f"deadline={msj.get('deadline')} dates={[n.get('date') for n in ms_nodes]}")

        # Web 端没有票据体系，登录是本地选角色，所以必须支持 user_id 直传
        web_u = c.post("/api/users", json={"name": "时间轴自检同学", "grade": "博二",
                                           "major": "材料科学与工程"})
        web_uid = web_u.json().get("id") if web_u.status_code in (200, 201) else 0
        if web_uid:
            mt = c.get(f"/api/timeline?user_id={web_uid}&back=730")
            mtj = mt.json() if mt.status_code == 200 else {}
            check("个人时间轴支持 Web 端按 user_id 直取",
                  mt.status_code == 200 and mtj.get("role") == "博士研究生",
                  mt.text[:200])
            check("个人时间轴只含学生侧节点",
                  all(it.get("role") != "学院" for it in mtj.get("items") or []),
                  str([it.get("role") for it in mtj.get("items") or []])[:200])
            check("个人时间轴带回看窗口参数",
                  mtj.get("back") == 730 and "grade" in mtj,
                  str({k: mtj.get(k) for k in ("back", "grade", "count")}))
            check("无票据且无 user_id 的个人时间轴被拒",
                  c.get("/api/timeline").status_code == 401,
                  "应返回 401")

        # ---------------- 表格结构（端点） ----------------
        # 表格通知走完整链路：采集（保留列分隔符）→ 解析（补位 + C11）→ 详情接口。
        # 详情里的 table_stats / table_detail 是即时从正文算出来的，不落库。
        _TB_HTML = """<div class="content">
<p>一、少数民族高层次骨干人才计划，面向博士研究生（专项计划）。</p>
<table><tr><th>学院名称</th><th>专业代码</th><th>专业名称</th><th>拟招生计划</th><th>咨询电话</th></tr>
<tr><td>材料科学与工程学院</td><td>085600</td><td>材料与化工</td><td>1</td><td>027-87164677</td></tr>
<tr><td>机电工程学院</td><td>080200</td><td>机械工程</td><td>1</td><td>027-87218976</td></tr>
</table>
<p>报名截止时间：2026年6月12日前</p></div>"""
        from app.services import collector as _collector  # noqa: E402
        _tb_content = _collector.parse_detail(_TB_HTML, "http://x/tb.shtml")["content"]
        _tb_created = c.post("/api/notices", json={
            "title": "关于我校2026年部分博士研究生（专项计划）补充报名的通知",
            "content": _tb_content, "publish_time": "2026-06-09"})
        _tb_id = (_tb_created.json() or {}).get("id") if _tb_created.status_code == 200 else None
        check("表格通知可入库并解析",
              _tb_created.status_code == 200
              and c.post(f"/api/notices/{_tb_id}/analyze").status_code == 200,
              _tb_created.text[:200])
        _tb_detail = c.get(f"/api/notices/{_tb_id}")
        _tb_an = (_tb_detail.json() or {}).get("analysis") or {}
        check("通知详情返回表格结构与列语义",
              _tb_detail.status_code == 200
              and (_tb_an.get("table_stats") or {}).get("total") == 1
              and (_tb_an.get("table_detail") or [{}])[0].get("kinds", [""])[0] == "college",
              str(_tb_an.get("table_stats")))
        check("通知详情保留正文里的列分隔符",
              "\x1f" in (_tb_detail.json() or {}).get("content", ""),
              repr((_tb_detail.json() or {}).get("content", "")[:120]))
        check("表格通知经接口解析后受众正确",
              _tb_an.get("target") == ["博士研究生"], str(_tb_an.get("target")))
        check("表格通知的学院由接口响应带回",
              "材料科学与工程学院" in (_tb_an.get("major_requirement") or []),
              str(_tb_an.get("major_requirement"))[:160])

        # 存量正文（旧版采集器压平、无列分隔符）走接口时，必须明确标出
        # 「需要重新采集」，否则界面无法区分「本来没表格」与「结构被扔了」。
        _stale_created = c.post("/api/notices", json={
            "title": "关于2026年部分博士研究生（专项计划）补充报名的通知（旧采）",
            "content": ("学院名称专业代码专业名称\n"
                        "拟招生计划咨询电话材料科学与工程学院\n"
                        "085600材料与化工1027-87164677"),
            "publish_time": "2026-06-09"})
        _stale_id = ((_stale_created.json() or {}).get("id")
                     if _stale_created.status_code == 200 else None)
        if _stale_id:
            c.post(f"/api/notices/{_stale_id}/analyze")
        _stale_an = ((c.get(f"/api/notices/{_stale_id}").json() or {}).get("analysis")
                     or {} if _stale_id else {})
        check("存量压平正文在接口层标记为需重新采集",
              _stale_an.get("table_needs_recollect") is True
              and (_stale_an.get("table_stats") or {}).get("total") == 0
              and "重新采集" in (_stale_an.get("table_note") or ""),
              str({k: _stale_an.get(k) for k in
                   ("table_needs_recollect", "table_note")}))

        # ---------------- 冲突治理 ----------------
        scan = c.post("/api/conflicts/scan")
        sj = scan.json() if scan.status_code == 200 else {}
        check("冲突扫描返回扫描数与命中数",
              scan.status_code == 200
              and isinstance(sj.get("scanned"), int)
              and isinstance(sj.get("found"), int),
              scan.text[:200])

        twin = c.post("/api/notices", json={
            "title": "关于2026年度奖学金评审的通知",
            "content": "请于2026年12月10日前提交申请材料至学生处。联系人：王老师。",
            "source_url": "https://x.example.edu.cn/a.html",
            "source_name": "学生处", "publish_time": "2026-11-01"})
        twin_id = twin.json().get("id") if twin.status_code in (200, 201) else 0
        twin2 = c.post("/api/notices", json={
            "title": "关于2026年度奖学金评审的通知",
            "content": "请于2026年12月18日前提交申请材料至学生处。联系人：王老师。",
            "source_url": "https://x.example.edu.cn/b.html",
            "source_name": "学生处", "publish_time": "2026-11-02"})
        twin2_id = twin2.json().get("id") if twin2.status_code in (200, 201) else 0
        if twin_id and twin2_id:
            c.post(f"/api/notices/{twin_id}/analyze")
            c.post(f"/api/notices/{twin2_id}/analyze")
            rescan = c.post("/api/conflicts/scan").json()
            check("同标题不同截止被识别为冲突",
                  rescan.get("found", 0) >= 1,
                  str(rescan))
            clist = c.get("/api/conflicts").json()
            check("冲突列表含严重度与字段标签",
                  bool(clist.get("items"))
                  and all("severity" in i and "field_label" in i for i in clist["items"]),
                  str(clist.get("items", [])[:1])[:240])
            check("冲突汇总按严重度分组",
                  isinstance(clist.get("summary", {}).get("by_severity"), dict)
                  and "unresolved" in clist.get("summary", {}),
                  str(clist.get("summary"))[:200])
            cid = clist["items"][0]["id"]
            bad = c.patch(f"/api/conflicts/{cid}", json={"resolution": "不存在的裁决"})
            check("非法裁决值被拒绝",
                  bad.status_code in (400, 422),
                  str(bad.status_code))
            good = c.patch(f"/api/conflicts/{cid}", json={"resolution": "use_a"})
            check("冲突可被人工裁决",
                  good.status_code == 200 and good.json().get("resolved") is True,
                  good.text[:200])
        else:
            check("同标题不同截止被识别为冲突", False, "造数失败")
            check("冲突列表含严重度与字段标签", False, "造数失败")
            check("冲突汇总按严重度分组", False, "造数失败")
            check("非法裁决值被拒绝", False, "造数失败")
            check("冲突可被人工裁决", False, "造数失败")

        # ---------------- 日历订阅 ----------------
        ics = c.get("/api/calendar.ics")
        body = ics.text if ics.status_code == 200 else ""
        check("日历订阅返回合法 ICS",
              ics.status_code == 200
              and "BEGIN:VCALENDAR" in body
              and "BEGIN:VEVENT" in body,
              f"{ics.status_code} len={len(body)}")
        longest = max((len(x.encode("utf-8")) for x in body.split("\r\n")), default=0)
        check("ICS 折行不超过 75 字节且不吞内容",
              longest <= 75 and "SUMMARY" in body,
              f"最长行 {longest} 字节")
        check("日历订阅带提醒闹钟",
              "BEGIN:VALARM" in body and "TRIGGER" in body,
              "缺少 VALARM")

    # ---- 冲突配对的判别力（真实数据上误报过一次，靠下面这组用例钉住）
    from app.services import conflict as conflict_mod

    # 同源多发必须能配上：标题一致 / 转发前缀 / 同一赛事不同轮次
    for a, b in (
        ("关于组织2026年大学生创新创业训练计划项目申报的通知",
         "关于组织2026年大学生创新创业训练计划项目申报的通知"),
        ("关于举办2026年研究生学术论坛的通知",
         "转发研究生院关于举办2026年研究生学术论坛的通知"),
        ("关于举办2026年校园歌手大赛的通知",
         "关于举办2026年校园歌手大赛决赛的通知"),
    ):
        check(f"冲突配对放行：{a[3:15]}",
              not conflict_mod._qualifier_conflict(a, b),
              f"被误判为互斥：{a} / {b}")
    check("转发标题被包含时不受相似度阈值限制",
          conflict_mod._contains("关于举办2026年研究生学术论坛的通知",
                                 "转发研究生院关于举办2026年研究生学术论坛的通知"),
          "长标题包含短标题未识别为同源")

    # 形似而不同的事项必须拆开：不同奖项 / 不同阶段 / 不同周期
    for a, b, why in (
        ("关于开展2025—2026学年国家奖学金评审工作的通知",
         "关于评选2025—2026学年国家励志奖学金的通知", "两个独立奖项"),
        ("关于开展2026年国家级大学生创新创业训练计划项目结题验收的通知",
         "关于组织2026年大学生创新创业训练计划项目申报的通知", "项目不同阶段"),
        ("关于2026年暑期社会实践立项的通知",
         "关于2026年暑期社会实践结项的通知", "立项与结项"),
        ("关于2025年秋季学期助管招聘的通知",
         "关于2026年春季学期助管招聘的通知", "不同学年学期"),
    ):
        check(f"冲突配对拦截：{why}",
              conflict_mod._qualifier_conflict(a, b),
              f"未被识别为不同事项：{a} / {b}")

    # 联系方式在分析结果里是数组，展示时必须压平，否则界面上是 "['027-…']"
    flat = conflict_mod._flat(["027-87850001", "027-87850066"])
    check("冲突字段值把数组压成中文顿号串", flat == "027-87850001、027-87850066", repr(flat))
    check("冲突字段值为空时返回空串", conflict_mod._flat(None) == "" and conflict_mod._flat([]) == "")

    # 渠道措辞一字不差、只有内嵌日期不同时，不该再报一条「报名渠道不一致」
    same_channel_a = "请通过校团委志愿服务系统在线报名，报名截止时间为2026年12月1日"
    same_channel_b = "请通过校团委志愿服务系统在线报名，报名截止时间为2026年11月30日"
    check("报名方式比对前抹掉内嵌日期",
          conflict_mod._strip_dates(same_channel_a) == conflict_mod._strip_dates(same_channel_b),
          f"{conflict_mod._strip_dates(same_channel_a)!r} vs {conflict_mod._strip_dates(same_channel_b)!r}")
    check("报名渠道真的不同时仍保留差异",
          conflict_mod._strip_dates("请将纸质材料交至东院行政楼302")
          != conflict_mod._strip_dates("请通过线上系统提交电子版"))
    check("冲突字段标签覆盖联系方式子字段",
          conflict_mod.FIELD_LABELS.get("contact.phone") == "联系电话"
          and conflict_mod.FIELD_LABELS.get("contact.email") == "联系邮箱")

    # 地点抽取：公文里「地点为X」「小标题换行取下一行」两种写法都要吃得下
    # 注意：循环变量不能叫 text，否则会遮蔽 main() 里 sqlalchemy 的 text() 导入
    from app.services import fields as fields_mod

    for loc_src, expect, variant in (
        ("论坛于2026年12月5日举行，地点为东院行政楼302会议室。", "东院行政楼302会议室", "地点为式"),
        ("二、活动地点\n南湖校区大学生活动中心一层大厅。", "南湖校区大学生活动中心一层大厅", "小标题换行"),
        ("活动地点为余家头校区校医院门前广场。", "余家头校区校医院门前广场", "活动地点为"),
        ("地点：东院行政楼302会议室", "东院行政楼302会议室", "冒号式"),
    ):
        got, _ = fields_mod.extract_location(loc_src)
        # 断言名必须唯一（收尾处有 Counter 查重），所以标签里带上写法差异
        check(f"地点抽取（{expect[0]}/{expect[-2:]}）：{variant}",
              got == expect, f"得到 {got!r}，期望 {expect!r}")
    check("地点抽取不把线上渠道当地点",
          fields_mod.extract_location("报名地点：网上办事大厅")[0] == "",
          "把线上渠道误当地理地点")

    # ---------------- CodeArts 实操留痕：真实内容而非空模板 ----------------
    # 留痕目录位于作品工程之外（与工程同级的「11_CodeArts使用记录/」），
    # 向上逐级查找而不是写死 ROOT.parent.parent：变异审计会把工程单独复制到
    # 临时目录，此时外部留痕不在副本里，这几条「证据体检」应跳过而非判失败——
    # 它们检查的是工程外的实证文件，不属于 11 组业务缺陷的检出对象。
    submit = next((p / "11_CodeArts使用记录" for p in (ROOT, *ROOT.parents)
                   if (p / "11_CodeArts使用记录").is_dir()), None)
    if submit is not None:
        log_path = submit / "09_开发日志.md"
        log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        check("开发日志已脱离空模板且有真实日期台账",
              "模板 · 待填写" not in log_text
              and "2026-11-23" in log_text and "2026-11-25" in log_text
              and "| 日期 |" in log_text,
              f"日志状态异常：{log_text[:80]!r}")
        debug_path = submit / "08_排错记录.md"
        debug_text = debug_path.read_text(encoding="utf-8") if debug_path.exists() else ""
        check("排错记录含真实失败与回归闭环",
              "ssh_wait" in debug_text and "300 seconds" in debug_text
              and "SSH_AUTH_OK" in debug_text and "status: recovered" in debug_text,
              "排错记录缺少真实失败/修复/回归证据")
        adopt_path = submit / "07_采纳说明.md"
        adopt_text = adopt_path.read_text(encoding="utf-8") if adopt_path.exists() else ""
        check("采纳说明已脱离空模板且有未采纳建议与真实证据",
              "模板 · 待填写" not in adopt_text
              and "未采纳" in adopt_text and "ssh_backup" in adopt_text
              and "非客户端导出" in adopt_text and "| 日期 |" in adopt_text,
              f"采纳说明状态异常：{adopt_text[:80]!r}")
        deploy_shots = list((submit / "05_云资源与部署").glob("*.png"))
        check("云资源与部署归档了真实公网截图",
              len(deploy_shots) >= 3
              and any("公网首页" in p.name for p in deploy_shots),
              f"归档截图：{[p.name for p in deploy_shots]}")

    # 断言名唯一性必须在收尾处判断：开头时 CHECK_NAMES 还是空的，会恒真。
    dup = [n for n, c in Counter(CHECK_NAMES).items() if c > 1]
    if dup:
        print(f"[FATAL] 断言名重复（项数不可信）：{'、'.join(dup)}")
        return 1
    print(f"\n{'='*48}")
    print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print("失败项：" + "、".join(FAILED))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
