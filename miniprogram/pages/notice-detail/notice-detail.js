/**
 * 通知详情：解析结果 + 证据溯源 + 截止时间轴 + 办理清单 + 订阅提醒。
 *
 * 这一页是整个产品的价值落点：用户不是来看原文的，是来看
 * 「我能不能报、什么时候截止、我要准备什么、下一步点什么」。
 */
const app = getApp();

/* 画像里的年级写法（博二 / 研三 / 大三）与时间轴节点的身份词（博士研究生 /
   硕士研究生 / 本科生）不是一套词汇，不归一的话「本人办理」永远标不出来。 */
function roleFromGrade(grade) {
  const g = String(grade || '');
  if (!g) return '';
  if (/(博士|博[一二三四五])/.test(g)) return '博士研究生';
  if (/(硕士|研[一二三]|研究生)/.test(g)) return '硕士研究生';
  if (/(专科|高职|专[一二三])/.test(g)) return '专科生';
  if (/(本科|大[一二三四五])/.test(g)) return '本科生';
  return '';
}

function leftText(date) {
  if (!date) return '';
  const d = new Date(`${date}T00:00:00`);
  if (isNaN(d.getTime())) return '';
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.round((d - today) / 86400000);
  if (days < 0) return `已过期 ${-days} 天`;
  if (days === 0) return '今天截止';
  return `剩 ${days} 天`;
}

Page({
  data: {
    id: 0,
    loading: true,
    error: '',
    notice: null,
    analysis: null,
    evidence: [],
    checklist: null,
    checklistLoading: false,
    timeline: [],
    timelineTracks: [],
    timelineGroups: [],
    parsedTimeline: [],
    expanded: {},      // 步骤详情展开状态 {'0': true}
    task: null,        // 已存在的待办
    isMatched: false,
    subscribeTip: '',
  },

  onLoad(options) {
    const id = parseInt(options.id, 10);
    if (!id) {
      this.setData({ loading: false, error: '参数错误：缺少通知编号' });
      return;
    }
    this.setData({ id });
    wx.setNavigationBarTitle({ title: '通知详情' });
    this.load();
  },

  load() {
    return app.ensureLogin()
      .then((user) => Promise.all([
        app.request(`/api/notices/${this.data.id}`),
        app.request(`/api/notices/${this.data.id}/checklist`).catch(() => null),
        app.request(`/api/notices/${this.data.id}/timeline`).catch(() => null),
        user ? app.request(`/api/users/${user.id}/tasks`).catch(() => []) : Promise.resolve([]),
      ]).then(([notice, checklist, timeline, tasks]) => {
        const analysis = notice.analysis || null;
        const mine = (tasks || []).find((t) => t.notice_id === this.data.id) || null;
        const role = roleFromGrade(user && user.grade);

        // 证据字典转成数组：WXML 里对对象做 wx:for 的 key 语义不直观，
        // 转成 [{key, value}] 后模板只处理数组，行为可预期。
        let evidence = [];
        if (analysis && analysis.evidence) {
          evidence = Object.keys(analysis.evidence)
            .filter((k) => analysis.evidence[k])
            .map((k) => ({ key: k, value: analysis.evidence[k] }));
        }

        // 时间轴节点补三样模板要用的派生值：是否本人办理、剩余天数文案、是否已过期。
        // 派生放在 JS 里而不是 WXML 表达式中，是因为小程序模板不能调函数。
        const nodes = ((timeline && timeline.nodes) || []).map((n) => {
          const nodeRole = n.role || '';
          const isMine = !!role && n.scope === 'student'
            && (role.indexOf(nodeRole) >= 0 || nodeRole.indexOf(role) >= 0);
          const left = leftText(n.date);
          return Object.assign({}, n, {
            mine: isMine,
            left_text: left,
            overdue: left.indexOf('已过期') === 0,
          });
        });
        const tracks = (timeline && timeline.tracks) || [];
        // 按轨道分组：同一批次的学生只需要看自己那一行，混在一起会读错日期
        const groups = tracks.length
          ? tracks.map((t) => ({ track: t, nodes: nodes.filter((n) => (n.track || '') === t) }))
          : [{ track: '', nodes }];

        // 清单里「在 X 前完成提交」是系统补的时间锚点而非原文动作，单独标记，
        // 避免用户误以为原文写了这么一句（这是本项目「不编造」原则的一部分）。
        // 多轨道通知会有多条锚点，标题后面还带括号说明轨道与身份，所以只匹配前缀。
        const steps = checklist && checklist.steps
          ? checklist.steps.map((s) => Object.assign({}, s, {
            anchor: /^在 .+ 前完成提交/.test(s.title || ''),
          }))
          : [];

        this.setData({
          loading: false,
          error: '',
          notice,
          analysis,
          evidence,
          checklist: checklist ? Object.assign({}, checklist, { steps }) : null,
          timeline: nodes,
          timelineTracks: tracks,
          timelineGroups: groups,
          parsedTimeline: nodes,
          task: mine,
        });
        wx.setNavigationBarTitle({
          title: (notice.category || '通知') + '详情',
        });
      }))
      .catch((err) => {
        this.setData({ loading: false, error: err.message || '加载失败' });
      });
  },

  onPullDownRefresh() {
    this.load().then(() => wx.stopPullDownRefresh());
  },

  // ------------------------------------------------------------ 清单交互
  toggleStep(e) {
    const seq = String(e.currentTarget.dataset.seq);
    const expanded = Object.assign({}, this.data.expanded);
    expanded[seq] = !expanded[seq];
    this.setData({ expanded });
  },

  // ------------------------------------------------------------ 待办
  addTask() {
    const user = app.globalData.user;
    if (!user) {
      wx.showToast({ title: '请先登录', icon: 'none' });
      return;
    }
    app.request(`/api/users/${user.id}/tasks`, {
      method: 'POST',
      data: { notice_id: this.data.id, note: '' },
    }).then((task) => {
      this.setData({ task });
      wx.showToast({ title: '已加入待办', icon: 'success' });
    }).catch((err) => {
      wx.showToast({ title: err.message || '操作失败', icon: 'none' });
    });
  },

  cycleTask() {
    const task = this.data.task;
    if (!task) return;
    const next = task.status === 'todo' ? 'doing' : (task.status === 'doing' ? 'done' : 'todo');
    app.request(`/api/tasks/${task.id}`, {
      method: 'PATCH',
      data: { status: next },
    }).then((updated) => {
      this.setData({ task: updated });
    }).catch((err) => {
      wx.showToast({ title: err.message || '操作失败', icon: 'none' });
    });
  },

  removeTask() {
    const task = this.data.task;
    if (!task) return;
    app.request(`/api/tasks/${task.id}`, { method: 'DELETE' })
      .then(() => {
        this.setData({ task: null });
        wx.showToast({ title: '已移除', icon: 'none' });
      })
      .catch((err) => {
        wx.showToast({ title: err.message || '操作失败', icon: 'none' });
      });
  },

  // ------------------------------------------------------------ 订阅提醒
  /**
   * 微信一次性订阅：每次授权只能下发一条，所以要按「通知条数」收费似地攒次数。
   * 用户点一次「提醒我」= 申请一次下发额度；后端按 remain_count 计数扣减。
   */
  subscribe() {
    if (!app.globalData.status || !app.globalData.status.push_ready) {
      wx.showModal({
        title: '暂未开启微信推送',
        content: '后端未配置订阅消息模板 ID，可在「我的」里使用站内待办与日历订阅。',
        showCancel: false,
      });
      return;
    }
    const tmplId = app.globalData.status.template_id || '';
    if (!tmplId) {
      wx.showModal({
        title: '缺少模板 ID',
        content: '请在后端 .env 里配置 WECHAT_TEMPLATE_DEADLINE。',
        showCancel: false,
      });
      return;
    }

    wx.requestSubscribeMessage({
      tmplIds: [tmplId],
      success: (res) => {
        if (res[tmplId] !== 'accept') {
          wx.showToast({ title: '未授权提醒', icon: 'none' });
          return;
        }
        app.request('/api/wechat/subscribe', {
          method: 'POST',
          auth: true,
          data: { template_id: tmplId, notice_id: this.data.id, count: 1 },
        }).then(() => {
          wx.showToast({ title: '已开启截止提醒', icon: 'success' });
        }).catch((err) => {
          wx.showToast({ title: err.message || '开启失败', icon: 'none' });
        });
      },
      fail: () => {
        wx.showToast({ title: '订阅未完成', icon: 'none' });
      },
    });
  },

  copyLink() {
    const url = this.data.notice && this.data.notice.source_url;
    if (!url) {
      wx.showToast({ title: '该通知没有来源链接', icon: 'none' });
      return;
    }
    wx.setClipboardData({ data: url });
  },

  retry() {
    this.setData({ loading: true, error: '' });
    this.load();
  },
});
