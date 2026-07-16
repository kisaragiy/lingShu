// pages/about/about.js
const app = getApp()

Page({
  data: {
    version: '1.0.0',
    developerName: 'kisaragiy',
    githubUrl: 'https://github.com/kisaragiy/lingShu',
    wechatId: 'RT2965783750'
  },

  copyText(e) {
    const text = e.currentTarget.dataset.text
    wx.setClipboardData({
      data: text,
      success: () => {
        wx.showToast({ title: '已复制', icon: 'success' })
      }
    })
  },

  onShareAppMessage() {
    return {
      title: '灵枢 - AI 应用开发助手',
      path: '/pages/home/home'
    }
  }
})
