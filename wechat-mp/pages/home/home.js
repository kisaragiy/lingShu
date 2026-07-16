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
      timeout: 5000,
      header: {
        'X-API-Token': app.globalData.apiToken
      },
      success: () => this.setData({ connected: true, checking: false }),
      fail: () => this.setData({ connected: false, checking: false })
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
