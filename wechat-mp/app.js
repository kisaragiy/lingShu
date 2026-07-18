// app.js
// Generate or recover a stable device_id for auto-login
const deviceId = wx.getStorageSync('device_id') || (function(){
  const id = 'mp_' + Date.now() + '_' + Math.random().toString(36).slice(2, 10)
  wx.setStorageSync('device_id', id)
  return id
})()

App({
  globalData: {
    apiBase: 'http://127.0.0.1:8788',  // 主服务端口，真机调试改为电脑局域网 IP
    token: '',
    deviceId: deviceId,
    userInfo: null,
    chatMode: 'normal'  // normal | research | cs
  },

  onLaunch() {
    // Step 1: try stored login from previous session
    const stored = wx.getStorageSync('mp_auth')
    if (stored && stored.token) {
      this.globalData.token = stored.token
      this.globalData.userInfo = stored.userInfo || null
      console.log('MP auth: restored stored token')
      return
    }
    // Step 2: no stored token — auto-login
    console.log('MP auth: no stored token, starting auto-login')
    this.autoLogin()
  },

  autoLogin() {
    wx.login({
      success: (res) => {
        // wx.login() gives us a code — send it along with device_id
        wx.request({
          url: this.globalData.apiBase + '/v1/auth/mp-login',
          method: 'POST',
          timeout: 10000,
          data: { code: res.code, device_id: this.globalData.deviceId },
          success: (r) => {
            if (r.data && r.data.token) {
              this.globalData.token = r.data.token
              this.globalData.userInfo = { userId: r.data.user_id, username: r.data.username }
              wx.setStorageSync('mp_auth', { token: r.data.token, userInfo: this.globalData.userInfo })
              console.log('MP auth: login success, token stored')
            } else {
              console.error('MP auth: login failed', r.data)
            }
          },
          fail: (err) => {
            console.error('MP auth: network error', err)
          }
        })
      },
      fail: (err) => {
        console.error('MP auth: wx.login failed', err)
      }
    })
  }
})
