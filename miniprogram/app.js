/**
 * 高校智讯 · 微信小程序端
 *
 * 与 Web 端共用同一套后端与同一份用户画像：
 *   - 登录只发生在 login()，拿到 token 后存本地，后续所有请求都带 x-token；
 *   - token 过期由后端返回 401，request() 统一拦截后重新静默登录一次；
 *   - 未配置 AppID/Secret 时后端进入「游客演示模式」，device_key 稳定复现同一账号，
 *     这样演示环境刷新也不会丢数据。
 */

const BASE_URL = 'http://127.0.0.1:8000'; // 真机调试改成本机局域网 IP
const TOKEN_KEY = 'ci.token';
const USER_KEY = 'ci.user';
const DEVICE_KEY = 'ci.device';

const DEVICE_POOL = 'abcdefghijkmnpqrstuvwxyz23456789';

function randomDeviceKey() {
  let out = '';
  for (let i = 0; i < 12; i += 1) {
    out += DEVICE_POOL[Math.floor(Math.random() * DEVICE_POOL.length)];
  }
  return out;
}

App({
  globalData: {
    baseUrl: BASE_URL,
    token: '',
    user: null,
    status: null,
    booted: false,
  },

  onLaunch() {
    this.globalData.token = wx.getStorageSync(TOKEN_KEY) || '';
    this.globalData.user = wx.getStorageSync(USER_KEY) || null;
    this.loadStatus();
  },

  // ------------------------------------------------------------ 底层请求
  /**
   * 统一请求入口。
   * @param {string} path 以 / 开头的后端路径
   * @param {object} options { method, data, auth, retry }
   * retry 只在内部用于「401 后重新登录再打一次」，业务调用方不要传。
   */
  request(path, options = {}) {
    const { method = 'GET', data = {}, auth = false, retry = true } = options;
    const header = { 'content-type': 'application/json' };
    if (auth && this.globalData.token) {
      header['x-token'] = this.globalData.token;
    }

    return new Promise((resolve, reject) => {
      wx.request({
        url: BASE_URL + path,
        method,
        data,
        header,
        timeout: 15000,
        success: (res) => {
          if (res.statusCode === 401 && auth && retry) {
            // 票据过期：静默重登一次再重放，用户无感
            this.login({ silent: true })
              .then(() => this.request(path, Object.assign({}, options, { retry: false })))
              .then(resolve)
              .catch(reject);
            return;
          }
          if (res.statusCode >= 200 && res.statusCode < 300) {
            resolve(res.data);
            return;
          }
          const detail = (res.data && res.data.detail) || `请求失败（${res.statusCode}）`;
          reject(new Error(detail));
        },
        fail: (err) => {
          const msg = err && err.errMsg ? err.errMsg : '网络不可用';
          reject(new Error(msg.indexOf('timeout') >= 0 ? '请求超时，请检查后端是否启动' : msg));
        },
      });
    });
  },

  // ------------------------------------------------------------ 登录
  login({ silent = false } = {}) {
    let deviceKey = wx.getStorageSync(DEVICE_KEY);
    if (!deviceKey) {
      deviceKey = randomDeviceKey();
      wx.setStorageSync(DEVICE_KEY, deviceKey);
    }

    // 真实环境下拿到 code 走 code2session；开发者工具里 code 取不到时后端会退回游客态
    const proceed = (code) => this.request('/api/wechat/login', {
      method: 'POST',
      data: { code: code || '', device_key: deviceKey, nickname: '' },
    }).then((res) => {
      this.globalData.token = res.token;
      this.globalData.user = res.user;
      wx.setStorageSync(TOKEN_KEY, res.token);
      wx.setStorageSync(USER_KEY, res.user);
      return res;
    });

    return new Promise((resolve, reject) => {
      wx.login({
        success: (r) => proceed(r.code).then(resolve).catch(reject),
        fail: () => proceed('').then(resolve).catch(reject),
      });
    }).catch((err) => {
      if (!silent) {
        wx.showToast({ title: err.message || '登录失败', icon: 'none' });
      }
      throw err;
    });
  },

  ensureLogin() {
    if (this.globalData.token) return Promise.resolve(this.globalData.user);
    return this.login({ silent: true }).then((res) => res.user);
  },

  setUser(user) {
    this.globalData.user = user;
    wx.setStorageSync(USER_KEY, user);
  },

  logout() {
    this.globalData.token = '';
    this.globalData.user = null;
    wx.removeStorageSync(TOKEN_KEY);
    wx.removeStorageSync(USER_KEY);
  },

  // ------------------------------------------------------------ 服务状态
  loadStatus() {
    return this.request('/api/wechat/status')
      .then((res) => {
        this.globalData.status = res;
        this.globalData.booted = true;
        return res;
      })
      .catch(() => {
        this.globalData.booted = true;
        return null;
      });
  },
});
