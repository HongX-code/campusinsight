/**
 * 首页：一次 /api/mobile/bootstrap 拿全身份、统计、近期截止、推荐机会。
 *
 * 不做「先拿用户、再拿统计、再拿列表」的串行请求——弱网下那会把首屏拖过 3 秒。
 */
const app = getApp();

Page({
  data: {
    loading: true,
    error: '',
    user: null,
    stats: { open_notices: 0, todo_tasks: 0, matched: 0, overdue_soon: 0 },
    agenda: [],
    matched: [],
    pushReady: false,
    modeLabel: '',
    greeting: '你好',
  },

  onLoad() {
    this.setGreeting();
  },

  onShow() {
    // 从详情页返回时待办数可能变了，重新拉一次
    this.refresh();
  },

  onPullDownRefresh() {
    this.refresh().then(() => wx.stopPullDownRefresh());
  },

  setGreeting() {
    const h = new Date().getHours();
    let text = '你好';
    if (h < 6) text = '夜深了';
    else if (h < 11) text = '早上好';
    else if (h < 14) text = '中午好';
    else if (h < 18) text = '下午好';
    else text = '晚上好';
    this.setData({ greeting: text });
  },

  refresh() {
    return app.ensureLogin()
      .then(() => app.request('/api/mobile/bootstrap', { auth: true }))
      .then((res) => {
        const deadlineTone = (days) => {
          if (days < 0) return 'over';
          if (days <= 3) return 'urgent';
          if (days <= 7) return 'soon';
          return 'normal';
        };
        const agenda = (res.agenda || []).map((x) => Object.assign({}, x, {
          tone: deadlineTone(x.days_left),
          days_text: x.days_left === 0 ? '今天截止' : `还剩 ${x.days_left} 天`,
        }));
        const matched = (res.matched || []).map((x) => ({
          noticeId: x.notice ? x.notice.id : 0,
          title: x.notice ? x.notice.title : '',
          category: x.notice ? x.notice.category : '',
          deadline: x.notice ? x.notice.deadline : '',
          score: x.score,
          level: x.level,
          levelText: x.level_text || '',
          // 后端给的是 reasons（数组），取前两条作为一句话依据
          reason: (x.reasons || []).slice(0, 2).join('；'),
        }));
        const status = app.globalData.status || {};
        this.setData({
          loading: false,
          error: '',
          user: res.user,
          stats: res.stats,
          agenda,
          matched,
          pushReady: !!res.push_ready,
          modeLabel: status.mode_label || '',
        });
      })
      .catch((err) => {
        this.setData({ loading: false, error: err.message || '加载失败' });
      });
  },

  openNotice(e) {
    const id = e.currentTarget.dataset.id;
    if (!id) return;
    wx.navigateTo({ url: `/pages/notice-detail/notice-detail?id=${id}` });
  },

  goNotices() {
    wx.switchTab({ url: '/pages/notices/notices' });
  },

  goMatch() {
    wx.switchTab({ url: '/pages/match/match' });
  },

  goMine() {
    wx.switchTab({ url: '/pages/mine/mine' });
  },

  retry() {
    this.setData({ loading: true, error: '' });
    this.refresh();
  },
});
