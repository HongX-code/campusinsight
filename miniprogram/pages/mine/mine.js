/**
 * 我的：画像编辑 + 待办清单 + 推送/日历设置。
 *
 * 待办状态用「未开始 → 进行中 → 已完成」三态循环，而不是二态勾选：
 * 「报名材料在准备了」和「还没开始」在提醒策略上必须区分。
 */
const app = getApp();

const STATUS_TEXT = { todo: '未开始', doing: '进行中', done: '已完成' };

Page({
  data: {
    loading: true,
    error: '',
    user: null,
    tasks: [],
    taskGroups: { doing: [], todo: [], done: [] },
    editing: false,
    form: { name: '', grade: '', major: '', student_no: '', interests: '' },
    status: null,
    pushReady: false,
    initial: '?',
    calendarUrl: '',
    taskCounts: { total: 0, doing: 0, todo: 0, done: 0 },
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
        return Promise.all([
          app.request(`/api/users/${user.id}`),
          app.request(`/api/users/${user.id}/tasks`),
          app.request('/api/wechat/status').catch(() => null),
        ]);
      })
      .then(([user, tasks, status]) => {
        app.setUser(user);
        const rows = (tasks || []).map((t) => ({
          id: t.id,
          noticeId: t.notice_id,
          status: t.status,
          statusText: STATUS_TEXT[t.status] || '未开始',
          title: t.notice ? t.notice.title : '（通知已删除）',
          category: t.notice ? t.notice.category : '',
          deadline: t.notice && t.notice.analysis ? t.notice.analysis.deadline : '',
          created_at: t.created_at,
        }));

        const groups = { doing: [], todo: [], done: [] };
        rows.forEach((r) => {
          if (groups[r.status]) groups[r.status].push(r);
        });

        const base = status && status.platforms ? status : app.globalData.status;
        const baseUrl = app.globalData.baseUrl;
        this.setData({
          loading: false,
          error: '',
          user,
          initial: (user.name || '?').slice(0, 1),
          tasks: rows,
          taskGroups: groups,
          taskCounts: {
            total: rows.length,
            doing: groups.doing.length,
            todo: groups.todo.length,
            done: groups.done.length,
          },
          status: base,
          pushReady: !!(base && base.push_ready),
          calendarUrl: `${baseUrl}/api/users/${user.id}/calendar.ics`,
          form: {
            name: user.name || '',
            grade: user.grade || '',
            major: user.major || '',
            student_no: user.student_no || '',
            interests: (user.interests || []).join('、'),
          },
        });
      })
      .catch((err) => {
        this.setData({ loading: false, error: err.message || '加载失败' });
      });
  },

  // ------------------------------------------------------------ 画像编辑
  startEdit() {
    this.setData({ editing: true });
  },

  cancelEdit() {
    this.setData({ editing: false });
  },

  onFieldInput(e) {
    const key = e.currentTarget.dataset.key;
    const form = Object.assign({}, this.data.form);
    form[key] = e.detail.value;
    this.setData({ form });
  },

  saveProfile() {
    const user = this.data.user;
    if (!user) return;
    const form = this.data.form;
    if (!form.name || !form.name.trim()) {
      wx.showToast({ title: '姓名不能为空', icon: 'none' });
      return;
    }
    const interests = form.interests
      .split(/[、,，\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);

    app.request(`/api/users/${user.id}`, {
      method: 'PUT',
      data: {
        name: form.name.trim(),
        grade: form.grade.trim(),
        major: form.major.trim(),
        student_no: form.student_no.trim(),
        interests,
      },
    }).then((updated) => {
      app.setUser(updated);
      this.setData({ user: updated, editing: false });
      wx.showToast({ title: '已保存', icon: 'success' });
    }).catch((err) => {
      wx.showToast({ title: err.message || '保存失败', icon: 'none' });
    });
  },

  // ------------------------------------------------------------ 待办
  cycleTask(e) {
    const id = e.currentTarget.dataset.id;
    const status = e.currentTarget.dataset.status;
    const next = status === 'todo' ? 'doing' : (status === 'doing' ? 'done' : 'todo');
    app.request(`/api/tasks/${id}`, { method: 'PATCH', data: { status: next } })
      .then(() => this.load(true))
      .catch((err) => wx.showToast({ title: err.message || '操作失败', icon: 'none' }));
  },

  removeTask(e) {
    const id = e.currentTarget.dataset.id;
    wx.showModal({
      title: '移出待办',
      content: '确定要把这条移出我的待办吗？',
      success: (res) => {
        if (!res.confirm) return;
        app.request(`/api/tasks/${id}`, { method: 'DELETE' })
          .then(() => {
            wx.showToast({ title: '已移除', icon: 'none' });
            this.load(true);
          })
          .catch((err) => wx.showToast({ title: err.message || '操作失败', icon: 'none' }));
      },
    });
  },

  openTask(e) {
    const noticeId = e.currentTarget.dataset.notice;
    if (!noticeId) return;
    wx.navigateTo({ url: `/pages/notice-detail/notice-detail?id=${noticeId}` });
  },

  // ------------------------------------------------------------ 日历订阅
  copyCalendar() {
    wx.setClipboardData({
      data: this.data.calendarUrl,
      success: () => {
        wx.showModal({
          title: '日历订阅链接已复制',
          content: '粘贴到系统日历的「添加订阅日历」中，截止日期会自动同步，无需任何推送权限。',
          showCancel: false,
        });
      },
    });
  },

  // ------------------------------------------------------------ 其它
  goNotices() {
    wx.switchTab({ url: '/pages/notices/notices' });
  },

  showAbout() {
    const status = this.data.status || {};
    const lines = [
      `登录模式：${status.login || '未知'}`,
      `服务模式：${status.mode || '未知'}`,
      `微信推送：${status.push_ready ? '已开启' : '未配置模板'}`,
      '支持端：Web / PWA / 微信小程序 / 日历订阅',
      '',
      '所有解析结果均可回溯到原文，定位不到的字段留空而不猜测。',
    ];
    wx.showModal({
      title: '关于高校智讯',
      content: lines.join('\n'),
      showCancel: false,
    });
  },

  retry() {
    this.load();
  },
});
