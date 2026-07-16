// pages/report-detail/report-detail.js
const app = getApp()

Page({
  data: {
    report: null,
    loading: true,
    error: false,
    id: ''
  },

  onLoad(options) {
    const id = options.id
    if (!id) {
      this.setData({ loading: false, error: true })
      return
    }
    this.setData({ id: id })
    this.loadReport(id)
  },

  loadReport(id) {
    this.setData({ loading: true, error: false })
    wx.request({
      url: app.globalData.apiBase + '/v1/reports/' + id,
      header: {
        'Authorization': 'Bearer ' + app.globalData.apiToken,
        'X-API-Token': app.globalData.apiToken
      },
      success: (res) => {
        let report = null
        if (res.data && res.data.data) {
          report = res.data.data
        } else if (res.data && (res.data.title || res.data.content)) {
          report = res.data
        }
        if (report) {
          this.setData({ report: report, loading: false })
        } else {
          this.setData({ loading: false, error: true })
        }
      },
      fail: () => {
        this.setData({ loading: false, error: true })
      }
    })
  },

  onShareAppMessage() {
    if (this.data.report) {
      return {
        title: this.data.report.title || '灵枢调研报告',
        path: '/pages/report-detail/report-detail?id=' + this.data.id
      }
    }
  },

  shareReport() {
    wx.shareAppMessage({
      title: this.data.report.title || '灵枢调研报告'
    })
  }
})
