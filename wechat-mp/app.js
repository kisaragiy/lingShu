// app.js
App({
  globalData: {
    apiBase: 'http://127.0.0.1:8788',  // 主服务端口，真机调试改为电脑局域网 IP
    apiToken: 'dev-token-placeholder',
    userInfo: null,
    chatMode: 'normal'  // normal | research | cs
  },

  onLaunch() {
    // 尝试读取本地存储的 API 配置
    const settings = wx.getStorageSync('lingShu_settings')
    if (settings) {
      this.globalData.apiBase = settings.apiBase || this.globalData.apiBase
      this.globalData.apiToken = settings.apiToken || this.globalData.apiToken
    }
  }
})
