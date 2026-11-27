"""端到端验证小程序端实际调用的接口链路。

覆盖：登录 → bootstrap → 通知列表 → 详情 → 清单 → 建待办 → 切换状态 →
订阅 → 冲突扫描 → 日历订阅。跑在临时库上，不污染 data/gaoxiao_zhixun.db。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

os.environ.setdefault("LLM_PROVIDER", "none")

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = "sqlite:///" + _tmp.name.replace("\\", "/")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app.database.connection import init_db  # noqa: E402
from app.main import app  # noqa: E402

ok = 0
fail = 0


def check(cond, name, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [PASS] {name}")
    else:
        fail += 1
        print(f"  [FAIL] {name} {extra}")


def main() -> int:
    init_db()
    client = TestClient(app)

    # ---------------------------------------------------------- 1. 状态
    r = client.get("/api/wechat/status")
    check(r.status_code == 200, "GET /api/wechat/status 200", r.text[:200])
    status = r.json()
    check("mode_label" in status, "状态含 mode_label", status)
    check("template_id" in status, "状态含 template_id")
    check("miniprogram" in status.get("platforms", {}), "platforms 含 miniprogram")

    # ---------------------------------------------------------- 2. 登录
    r = client.post("/api/wechat/login", json={"device_key": "e2e-device", "nickname": "端到端"})
    check(r.status_code == 200, "POST /api/wechat/login 200", r.text[:200])
    login = r.json()
    token = login["token"]
    user_id = login["user"]["id"]
    check(bool(token), "拿到 token")
    check(login["openid_mode"] == "guest", "游客模式 openid", login["openid_mode"])

    # 同一 device_key 再登录应复用账号（重新登录会签发新票据，旧票据作废）
    r2 = client.post("/api/wechat/login", json={"device_key": "e2e-device", "nickname": "端到端"})
    check(r2.json()["user"]["id"] == user_id, "同设备复登录复用账号")
    token = r2.json()["token"]

    hdr = {"x-token": token}

    # 旧票据应失效：单账号只保留一个有效票据，避免多处登录后状态互相踩
    r_old = client.get("/api/mobile/bootstrap", headers={"x-token": login["token"]})
    check(r_old.status_code == 401, "重新登录后旧票据失效", r_old.status_code)

    # ---------------------------------------------------------- 3. 造数据
    sample = {
        "title": "关于开展2026年研究生学业奖学金评审工作的通知",
        "content": (
            "各学院：\n"
            "为做好2026年研究生学业奖学金评审工作，现将有关事项通知如下。\n"
            "一、申报条件\n"
            "全日制在读研究生，专业不限，2024级、2025级研究生均可申报。\n"
            "二、申报材料\n"
            "1. 填写《研究生学业奖学金申请表》，需导师签字并加盖学院公章；\n"
            "2. 提交成绩单原件及科研成果证明材料。\n"
            "三、申报方式\n"
            "请于2026年12月15日前将纸质材料交至研究生院302办公室，同时将电子版发送至yjsy@example.edu.cn。\n"
            "联系人：张老师，联系电话：027-87654321。\n"
        ),
        "source_url": "https://gs.example.edu.cn/notice/1",
        "source_name": "研究生院",
        "publish_time": "2026-11-20",
    }
    r = client.post("/api/notices", json=sample)
    check(r.status_code in (200, 201), "创建通知 200/201", r.text[:300])
    notice_id = r.json()["id"]
    r = client.post(f"/api/notices/{notice_id}/analyze")
    check(r.status_code == 200, "解析通知", r.text[:300])
    analysis = r.json().get("analysis") or {}
    check(bool(analysis.get("deadline")), "解析出截止日期", analysis.get("deadline"))
    check(analysis.get("actionable") is not False, "判定为可办理事项")

    # ---------------------------------------------------------- 4. bootstrap
    r = client.get("/api/mobile/bootstrap", headers=hdr)
    check(r.status_code == 200, "GET /api/mobile/bootstrap 200", r.text[:300])
    boot = r.json()
    for key in ("user", "stats", "agenda", "matched", "push_ready", "server_time"):
        check(key in boot, f"bootstrap 含 {key}")
    check(boot["stats"]["open_notices"] >= 1, "统计到办理中通知", boot["stats"])

    # ---------------------------------------------------------- 5. 列表与详情
    r = client.get("/api/notices?page=1&size=20")
    check(r.status_code == 200, "GET /api/notices 200")
    check(r.json()["total"] >= 1, "列表有数据")

    r = client.get(f"/api/notices/{notice_id}")
    check(r.status_code == 200, "GET 通知详情 200")
    check(r.json()["summary"], "详情含 summary 字段")

    # ---------------------------------------------------------- 6. 清单
    r = client.get(f"/api/notices/{notice_id}/checklist")
    check(r.status_code == 200, "GET 办理清单 200", r.text[:300])
    cl = r.json()
    check(cl["stats"]["total"] >= 1, "清单有步骤", cl["stats"])
    steps = cl["steps"]
    check(any(s["offline"] for s in steps), "识别出线下办理步骤", [s["title"] for s in steps])
    check(any("截止" in s["title"] or "提交" in s["title"] for s in steps), "含截止锚点步骤")
    # 不得把联系人当作步骤
    check(not any("联系电话" in s["title"] or "联系人" in s["title"] for s in steps),
          "联系人未被误判为步骤", [s["title"] for s in steps])

    # ---------------------------------------------------------- 7. 待办
    r = client.post(f"/api/users/{user_id}/tasks", json={"notice_id": notice_id, "note": ""})
    check(r.status_code in (200, 201), "创建待办", r.text[:300])
    task = r.json()
    task_id = task["id"]
    check(task["status"] == "todo", "待办初始状态 todo", task["status"])
    check(task.get("notice_id") == notice_id, "待办含 notice_id")

    r = client.get(f"/api/users/{user_id}/tasks")
    check(r.status_code == 200 and len(r.json()) == 1, "待办列表有 1 条")

    r = client.patch(f"/api/tasks/{task_id}", json={"status": "doing"})
    check(r.json()["status"] == "doing", "待办状态可切换为 doing")

    # ---------------------------------------------------------- 8. 订阅
    r = client.post("/api/wechat/subscribe", headers=hdr,
                    json={"template_id": "tpl-demo", "notice_id": notice_id, "count": 2})
    check(r.status_code == 200, "订阅接口 200", r.text[:300])
    sub = r.json()
    check(sub.get("code") == "no_template" or sub.get("ok") is True,
          "未配模板时按 no_template 降级", sub)

    r = client.get("/api/wechat/subscriptions", headers=hdr)
    check(r.status_code == 200, "订阅列表 200")

    # ---------------------------------------------------------- 9. 冲突
    twin = dict(sample)
    twin["title"] = sample["title"]
    twin["content"] = sample["content"].replace("2026年12月15日", "2026年12月20日")
    r = client.post("/api/notices", json=twin)
    twin_id = r.json()["id"]
    client.post(f"/api/notices/{twin_id}/analyze")

    r = client.post("/api/conflicts/scan")
    check(r.status_code == 200, "冲突扫描 200", r.text[:300])
    scan = r.json()
    check(scan["found"] >= 1, "同标题不同截止被判为冲突", scan)
    r = client.get("/api/conflicts")
    conflicts = r.json()
    check(len(conflicts["items"]) >= 1, "冲突列表非空")
    if conflicts["items"]:
        cid = conflicts["items"][0]["id"]
        r = client.patch(f"/api/conflicts/{cid}", json={"resolution": "use_a"})
        check(r.status_code == 200, "冲突可裁决", r.text[:200])

    # ---------------------------------------------------------- 10. 日历
    r = client.get("/api/calendar.ics")
    check(r.status_code == 200, "GET /api/calendar.ics 200")
    body = r.text
    check("BEGIN:VCALENDAR" in body and "BEGIN:VEVENT" in body, "ICS 结构完整")
    check("SUMMARY" in body, "ICS 含 SUMMARY（折行未吞内容）")
    longest = max((len(line.encode("utf-8")) for line in body.split("\r\n")), default=0)
    check(longest <= 75, f"ICS 每行不超过 75 字节（实测 {longest}）")

    r = client.get(f"/api/users/{user_id}/calendar.ics")
    check(r.status_code == 200, "用户日历订阅 200")
    check("BEGIN:VEVENT" in r.text, "用户日历含事件")

    # ---------------------------------------------------------- 11. 鉴权
    r = client.get("/api/mobile/bootstrap")
    check(r.status_code == 401, "无票据访问 bootstrap 返回 401", r.status_code)
    r = client.get("/api/mobile/bootstrap", headers={"x-token": "bad-token"})
    check(r.status_code == 401, "错误票据返回 401", r.status_code)
    r = client.get(f"/api/mobile/bootstrap?token={token}")
    check(r.status_code == 200, "query token 通道可用（供 downloadFile 场景）")

    print("=" * 56)
    print(f"端到端：通过 {ok} 项，失败 {fail} 项")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        try:
            os.unlink(_tmp.name)
        except OSError:
            pass
