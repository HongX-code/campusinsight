"""端到端自检脚本（不依赖 pytest，直接运行即可）。

    python tests/test_smoke.py

使用独立的临时数据库，不会污染演示数据。
"""
from __future__ import annotations

import os
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
        from app.services import reltime, validator                    # noqa: E402
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

        qa_corpus = [
            _qa_item(1, "关于举办2026年武汉理工大学数学建模竞赛暨全国大学生数学建模竞赛校内选拔的通知",
                     "学科竞赛", "2026-11-20",
                     "各学院：关于举办2026年数学建模竞赛的通知。报名时间2026年11月3日至2026年11月20日，"
                     "报名截止后不再受理。提交材料：报名表、个人简历、作品说明。面向全日制本科生，"
                     "以3人团队形式报名，参赛队员须无违纪记录。",
                     materials=["报名表", "个人简历", "作品说明"], target=["本科生"],
                     conditions=["3人团队", "无违纪记录"], registration_start="2026-11-03",
                     evidence={"materials": "提交材料：报名表、个人简历、作品说明"}),
            _qa_item(3, "关于组织参加第十一届华为ICT大赛创新赛的通知", "学科竞赛", "2026-11-15",
                     "各学院：关于组织参加第十一届华为ICT大赛创新赛的通知，报名截止2026年11月15日，"
                     "须以团队形式参赛并提交作品材料。",
                     materials=["作品信息收集表", "作品介绍PPT"], target=["本科生", "硕士研究生"]),
            _qa_item(4, "2026年ACM-ICPC程序设计竞赛校内选拔赛报名启动", "学科竞赛", "2026-11-25",
                     "关于2026年ACM-ICPC程序设计竞赛校内选拔赛报名的通知，报名截止2026年11月25日。",
                     target=["本科生"]),
            _qa_item(5, "关于开展2025—2026学年国家奖学金评审工作的通知", "奖助学金", "2026-11-06",
                     "关于开展国家奖学金评审工作的通知。申请条件：学习成绩排名专业前10%，无不及格科目，"
                     "二年级以上。提交材料：申请审批表、成绩单、获奖证书。报名截止2026年11月6日。",
                     conditions=["专业前10%", "无不及格科目", "二年级以上"],
                     materials=["申请审批表", "成绩单", "获奖证书"],
                     target=["本科生", "大二"], grade_requirement=["本科生", "大二"]),
            _qa_item(7, "关于评选2025—2026学年国家励志奖学金的通知", "奖助学金", "2026-11-12",
                     "关于评选国家励志奖学金的通知。申请条件：家庭经济困难、专业前30%。"
                     "提交材料：申请表、成绩单、家庭经济困难认定证明、个人总结。报送截止2026年11月12日。",
                     conditions=["家庭经济困难", "专业前30%"],
                     materials=["申请表", "成绩单", "家庭经济困难认定证明", "个人总结"],
                     target=["本科生"], deadline_kind="报送截止"),
            _qa_item(20, "关于举办2026年阳光体育冬季长跑比赛的通知", "校园活动", "2026-11-11",
                     "关于举办阳光体育冬季长跑比赛的通知，报名截止2026年11月11日。"),
            _qa_item(22, "华为技术有限公司2027届校园招聘宣讲会（武汉理工大学站）", "实习就业", "2026-11-11",
                     "关于校园招聘宣讲会的通知，预约办理截止2026年11月11日。"),
            _qa_item(99, "关于举办第五届武汉理工大学MPAcc案例大赛的通知", "学科竞赛", "2025-01-28",
                     "关于举办MPAcc案例大赛的通知，报名截止2025年1月28日，已过期的历史通知。",
                     materials=["案例解决方案", "授权书"]),
        ]

        QA_EXPECT = {
            "有哪些学科竞赛可以报名？": {"must": ["尚未截止"],
                                  "forbid": ["2025-01-28", "（已截止）"]},
            "数学建模竞赛需要提交哪些材料？": {"must": ["报名表", "个人简历", "作品说明"],
                                     "forbid": ["暂未找到"]},
            "国家奖学金的申请条件是什么？": {"must": ["专业前10%", "无不及格科目"],
                                    "forbid": ["国家励志奖学金"]},
            "华为ICT大赛的报名截止时间？": {"must": ["2026-11-15"], "forbid": ["MPAcc"]},
            # 不绑定具体日期字面量：固定日期会随时间过期，导致「近期截止」正确排除后断言反而变红。
            # 日期正确性由下方「近期截止按日期升序且排除过期」用当天日期动态断言，这里只校验答复口径。
            "近期有哪些通知即将截止？": {"must": ["由近到远", "尚未截止"],
                                 "forbid": ["2025-01-28", "2026-11-06"]},
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
