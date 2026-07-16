// pages/reports/reports.js
const app = getApp()

Page({
  data: {
    reports: [],
    loading: true,
    error: false
  },

  onShow() {
    this.loadReports()
  },

  loadReports() {
    this.setData({ loading: true, error: false })
    wx.request({
      url: app.globalData.apiBase + '/v1/reports',
      header: {
        'Authorization': 'Bearer ' + app.globalData.apiToken,
        'X-API-Token': app.globalData.apiToken
      },
      success: (res) => {
        let reports = []
        if (res.data && res.data.reports) {
          reports = res.data.reports
        } else if (res.data && res.data.data) {
          reports = res.data.data
        } else if (Array.isArray(res.data)) {
          reports = res.data
        }
        this.setData({ reports: reports, loading: false })
      },
      fail: () => {
        this.setData({ loading: false, error: true })
      }
    })
  },

  viewDetail(e) {
    const id = e.currentTarget.dataset.id
    wx.navigateTo({ url: '/pages/report-detail/report-detail?id=' + id })
  },

  goChat() {
    app.globalData.chatMode = 'research'
    wx.switchTab({ url: '/pages/chat/chat' })
  }
})
