// pages/mine/mine.js
const app = getApp()

Page({
  data: {
    connected: false
  },

  onShow() {
    this.checkConnection()
  },

  checkConnection() {
    const base = app.globalData.apiBase
    if (!base) { return }
    wx.request({
      url: base + '/health',
      timeout: 5000,
      header: {
        'X-API-Token': app.globalData.apiToken
      },
      success: () => this.setData({ connected: true }),
      fail: () => this.setData({ connected: false })
    })
  },

  goSettings() {
    wx.navigateTo({ url: '/pages/settings/settings' })
  },

  goAbout() {
    wx.navigateTo({ url: '/pages/about/about' })
  },

  goContact() {
    wx.showActionSheet({
      itemList: ['复制微信号: kisaragiy', '拨打电话 (仅模拟)'],
      success: (res) => {
        if (res.tapIndex === 0) {
          wx.setClipboardData({
            data: 'kisaragiy',
            success: () => {
              wx.showToast({ title: '微信号已复制', icon: 'success' })
            }
          })
        }
      }
    })
  }
})
