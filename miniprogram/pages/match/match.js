/**
 * 匹配页：按用户的年级/专业/兴趣对全部已解析通知打分，三级结论。
 *
 * 关键设计：match / maybe / no 三档都展示，且每档都给出「为什么」。
 * 只显示 match 的推荐是在骗用户——「为什么我不符合」同样是有效信息。
 */
const app = getApp();

Page({
  data: {
    loading: true,
    error: '',
    level: '',           // '' 全部 / match / maybe / no
    onlyOpen: true,
    counts: { match: 0, maybe: 0, no: 0 },
    items: [],
    user: null,
    profileMissing: true,
  },

  onLoad() {
    this.load();
  },

  onShow() {
    if (this.loadedOnce) {
      this.load(true);
    }
    this.loadedOnce = true;
  },

  onPullDownRefresh() {
    this.load().then(() => wx.stopPullDownRefresh());
  },

  load(silent = false) {
    if (!silent) this.setData({ loading: true, error: '' });

    return app.ensureLogin()
      .then((user) => {
        if (!user) throw new Error('未登录');
        const qs = [
          this.data.level ? `level=${this.data.level}` : '',
          this.data.onlyOpen ? 'only_open=true' : '',
        ].filter(Boolean).join('&');
        return app.request(`/api/users/${user.id}/matches${qs ? '?' + qs : ''}`)
          .then((res) => {
            const items = (res.items || []).map((x) => ({
              noticeId: x.notice_id,
              level: x.level,
              levelText: x.level_text,
              score: x.score,
              title: x.notice ? x.notice.title : '',
              category: x.notice ? x.notice.category : '',
              summary: x.notice ? x.notice.summary : '',
              reasons: x.reasons || [],
            }));
            this.setData({
              loading: false,
              error: '',
              user: res.user,
              counts: res.counts || { match: 0, maybe: 0, no: 0 },
              items,
              profileMissing: !(res.user && res.user.major && res.user.grade),
            });
          });
      })
      .catch((err) => {
        this.setData({ loading: false, error: err.message || '加载失败' });
      });
  },

  pickLevel(e) {
    const value = e.currentTarget.dataset.value || '';
    if (value === this.data.level) return;
    this.setData({ level: value }, () => this.load());
  },

  toggleOpen() {
    this.setData({ onlyOpen: !this.data.onlyOpen }, () => this.load());
  },

  openNotice(e) {
    const id = e.currentTarget.dataset.id;
    if (!id) return;
    wx.navigateTo({ url: `/pages/notice-detail/notice-detail?id=${id}` });
  },

  goMine() {
    wx.switchTab({ url: '/pages/mine/mine' });
  },

  retry() {
    this.load();
  },
});
