// pages/cs/cs.js
const app = getApp()

Page({
  data: {
    scenarios: [
      { id: 'presales', icon: '🛒', name: '售前咨询', desc: '产品信息 · 功能说明 · 价格查询' },
      { id: 'order', icon: '📦', name: '查订单', desc: '订单状态 · 发货进度 · 预计到达' },
      { id: 'logistics', icon: '🚚', name: '查物流', desc: '物流轨迹 · 配送进度 · 签收情况' },
      { id: 'coupon', icon: '💰', name: '查优惠', desc: '优惠活动 · 优惠券 · 满减信息' },
      { id: 'aftersale', icon: '🔄', name: '退换货', desc: '退货流程 · 换货申请 · 退款进度' },
      { id: 'human', icon: '👤', name: '转人工', desc: '转接人工客服 · 投诉建议' }
    ]
  },

  startCS(e) {
    const id = e.currentTarget.dataset.id
    // Store pending CS scene in globalData before switching tab
    app.globalData.chatMode = 'cs'
    app.globalData.csScene = id
    wx.switchTab({
      url: '/pages/chat/chat'
    })
  }
})
