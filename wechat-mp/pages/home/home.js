// pages/home/home.js
const app = getApp()

Page({
  data: {
    connected: false,
    checking: true
  },

  onShow() {
    this.checkConnection()
  },

  checkConnection() {
    const base = app.globalData.apiBase
    if (!base) {
      this.setData({ connected: false, checking: false })
      return
    }
    this.setData({ checking: true })
    wx.request({
      url: base + '/health',
      method: 'GET',
      timeout: 3000,
      header: {
        'Authorization': 'Bearer ' + app.globalData.token
      },
      success: () => this.setData({ connected: true, checking: false }),
      fail: () => {
        // fallback: try :8788 if current port is :8765, or vice versa
        const fallbackPort = base.includes(':8765') ? base.replace(':8765', ':8788') : ''
        if (fallbackPort) {
          wx.request({
            url: fallbackPort + '/health',
            method: 'GET',
            timeout: 3000,
            header: { 'Authorization': 'Bearer ' + app.globalData.token },
            success: () => {
              // Auto-fix the apiBase for future requests
              app.globalData.apiBase = fallbackPort
              wx.setStorageSync('lingShu_settings', {
                apiBase: fallbackPort
              })
              this.setData({ connected: true, checking: false })
            },
            fail: () => this.setData({ connected: false, checking: false })
          })
        } else {
          this.setData({ connected: false, checking: false })
        }
      }
    })
  },

  goChat() {
    app.globalData.chatMode = 'normal'
    wx.switchTab({ url: '/pages/chat/chat' })
  },

  goResearch() {
    app.globalData.chatMode = 'research'
    wx.switchTab({ url: '/pages/chat/chat' })
  },

  goCS() {
    wx.navigateTo({ url: '/pages/cs/cs' })
  },

  goReports() {
    wx.switchTab({ url: '/pages/reports/reports' })
  },

  goAbout() {
    wx.navigateTo({ url: '/pages/about/about' })
  }
})
