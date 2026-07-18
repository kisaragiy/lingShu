// pages/settings/settings.js
const app = getApp()

Page({
  data: {
    apiBase: '',
    userId: '',
    username: '',
    loginTime: ''
  },

  onLoad() {
    const stored = wx.getStorageSync('mp_auth')
    const settings = wx.getStorageSync('lingShu_settings') || {}
    this.setData({
      apiBase: settings.apiBase || app.globalData.apiBase,
      userId: app.globalData.userInfo ? app.globalData.userInfo.userId : (stored && stored.userInfo ? stored.userInfo.userId : '未登录'),
      username: app.globalData.userInfo ? app.globalData.userInfo.username : (stored && stored.userInfo ? stored.userInfo.username : '-'),
      loginTime: wx.getStorageSync('device_id') ? '自动登录' : '待登录'
    })
  },

  onApiBaseInput(e) {
    this.setData({ apiBase: e.detail.value })
  },

  saveSettings() {
    if (!this.data.apiBase) {
      wx.showToast({ title: '请输入服务器地址', icon: 'none' })
      return
    }
    const settings = {
      apiBase: this.data.apiBase
    }
    wx.setStorageSync('lingShu_settings', settings)
    app.globalData.apiBase = this.data.apiBase
    wx.showToast({ title: '已保存', icon: 'success' })
  },

  reLogin() {
    wx.showLoading({ title: '重新登录中...' })
    // Clear stored auth and re-login
    wx.removeStorageSync('mp_auth')
    app.globalData.token = ''
    app.globalData.userInfo = null
    app.autoLogin()
    wx.hideLoading()
    wx.showToast({ title: '登录中，请稍后' })
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
      header: {},
      success: (res) => {
        wx.hideLoading()
        if (res.statusCode === 200) {
          wx.showToast({ title: '连接成功', icon: 'success' })
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
