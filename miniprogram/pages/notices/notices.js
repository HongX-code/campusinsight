/**
 * 通知列表：支持关键词搜索、分类筛选、文案体裁筛选。
 *
 * 与 Web 端 /api/notices 同一接口，但小程序一次性只取 20 条并支持上拉加载，
 * 避免首屏一次拉几百条把弱网用户的流量和等待都吃掉。
 */
const app = getApp();

const PAGE_SIZE = 20;

Page({
  data: {
    loading: true,
    error: '',
    keyword: '',
    category: '全部',
    categories: ['全部'],
    items: [],
    total: 0,
    page: 1,
    hasMore: false,
    loadingMore: false,
  },

  onLoad() {
    this.load(true);
  },

  onShow() {
    // 从详情页返回时列表里的待办/解析状态可能变了，静默刷新首屏
    if (this.data.items.length > 0) {
      this.load(true, true);
    }
  },

  onPullDownRefresh() {
    this.load(true).then(() => wx.stopPullDownRefresh());
  },

  onReachBottom() {
    if (this.data.hasMore && !this.data.loadingMore) {
      this.load(false);
    }
  },

  /**
   * @param {boolean} reset true=重载第一页 false=追加下一页
   * @param {boolean} silent true=不显示 loading 骨架（用于返回刷新）
   */
  load(reset = true, silent = false) {
    const page = reset ? 1 : this.data.page + 1;
    if (reset && !silent) {
      this.setData({ loading: true, error: '' });
    }
    if (!reset) {
      this.setData({ loadingMore: true });
    }

    const query = [
      `page=${page}`,
      `size=${PAGE_SIZE}`,
      this.data.category && this.data.category !== '全部'
        ? `category=${encodeURIComponent(this.data.category)}` : '',
      this.data.keyword ? `keyword=${encodeURIComponent(this.data.keyword)}` : '',
    ].filter(Boolean).join('&');

    return app.request(`/api/notices?${query}`)
      .then((res) => {
        const rows = (res.items || []).map((n) => {
          const a = n.analysis || null;
          const actionable = a ? a.actionable !== false : true;
          return {
            id: n.id,
            title: n.title,
            category: n.category,
            summary: n.summary,
            source_name: n.source_name,
            publish_time: n.publish_time,
            deadline: a ? a.deadline : '',
            deadline_kind: a ? a.deadline_kind : '',
            genre: a ? a.genre : '',
            genre_label: a ? a.genre_label : '',
            actionable,
            parsed: !!a,
            needs_review: a ? !!a.needs_review : false,
            confidence: a ? a.confidence : 0,
          };
        });
        const merged = reset ? rows : this.data.items.concat(rows);
        this.setData({
          loading: false,
          loadingMore: false,
          error: '',
          items: merged,
          total: res.total || merged.length,
          page,
          categories: res.categories || this.data.categories,
          hasMore: merged.length < (res.total || 0),
        });
      })
      .catch((err) => {
        this.setData({ loading: false, loadingMore: false, error: err.message || '加载失败' });
      });
  },

  onSearchInput(e) {
    this.setData({ keyword: e.detail.value });
  },

  onSearchConfirm() {
    this.load(true);
  },

  clearSearch() {
    this.setData({ keyword: '' });
    this.load(true);
  },

  pickCategory(e) {
    const value = e.currentTarget.dataset.value;
    if (value === this.data.category) return;
    this.setData({ category: value }, () => this.load(true));
  },

  openNotice(e) {
    const id = e.currentTarget.dataset.id;
    if (!id) return;
    wx.navigateTo({ url: `/pages/notice-detail/notice-detail?id=${id}` });
  },

  retry() {
    this.load(true);
  },
});
