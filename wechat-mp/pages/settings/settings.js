// pages/settings/settings.js
const app = getApp()

Page({
  data: {
    apiBase: '',
    apiToken: '',
    version: '1.0.0'
  },

  onLoad() {
    const settings = wx.getStorageSync('lingShu_settings') || {}
    this.setData({
      apiBase: settings.apiBase || app.globalData.apiBase,
      apiToken: settings.apiToken || app.globalData.apiToken
    })
  },

  onApiBaseInput(e) {
    this.setData({ apiBase: e.detail.value })
  },

  onApiTokenInput(e) {
    this.setData({ apiToken: e.detail.value })
  },

  saveSettings() {
    if (!this.data.apiBase) {
      wx.showToast({ title: '请输入服务器地址', icon: 'none' })
      return
    }
    const settings = {
      apiBase: this.data.apiBase,
      apiToken: this.data.apiToken
    }
    wx.setStorageSync('lingShu_settings', settings)
    app.globalData.apiBase = this.data.apiBase
    app.globalData.apiToken = this.data.apiToken
    wx.showToast({ title: '已保存', icon: 'success' })
  },

  testConnection() {
    const baseUrl = this.data.apiBase
    if (!baseUrl) {
      wx.showToast({ title: '请先输入服务器地址', icon: 'none' })
      return
    }
    wx.showLoading({ title: '测试连接...' })
    wx.request({
      url: `${baseUrl}/health`,
      method: 'GET',
      timeout: 8000,
      header: {
        'X-API-Token': app.globalData.apiToken || this.data.apiToken,
        'Authorization': 'Bearer ' + (app.globalData.apiToken || this.data.apiToken)
      },
      success: (res) => {
        wx.hideLoading()
        if (res.statusCode === 200) {
          wx.showToast({ title: '连接成功', icon: 'success' })
        } else if (res.statusCode === 401) {
          wx.showToast({ title: '已连接，Token 无效', icon: 'none' })
        } else {
          wx.showToast({ title: `状态码: ${res.statusCode}`, icon: 'none' })
        }
      },
      fail: () => {
        wx.hideLoading()
        wx.showToast({ title: '连接失败，检查地址', icon: 'none' })
      }
    })
  }
})
