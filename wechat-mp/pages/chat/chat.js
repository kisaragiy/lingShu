// pages/chat/chat.js
const app = getApp()

Page({
  data: {
    messages: [],
    inputValue: '',
    loading: false,
    mode: 'normal',
    modeName: 'AI 对话',
    sessionId: '',
    scrollTop: 999999,
    quickReplies: []
  },

  onLoad(options) {
    // 检查是否有模式切换
    const mode = options.mode || app.globalData.chatMode || 'normal'
    const scene = options.scene || ''
    this.initChat(mode, scene)
  },

  onShow() {
    // 如果 globalData 中的 chatMode 变了，同步切换
    const mode = app.globalData.chatMode || 'normal'
    if (mode !== this.data.mode) {
      this.initChat(mode, '')
    }
  },

  initChat(mode, scene) {
    const modeNames = {
      normal: 'AI 对话',
      research: '调研助手',
      cs: '智能客服'
    }
    this.setData({
      mode: mode,
      modeName: modeNames[mode] || 'AI 对话'
    })

    // 加载历史消息
    const historyKey = 'chat_history_' + mode
    const history = wx.getStorageSync(historyKey) || []

    if (history.length > 0) {
      this.setData({ messages: history })
    } else {
      // 根据模式显示不同的欢迎语
      const welcomeMessages = {
        normal: {
          role: 'assistant',
          content: '你好！我是灵枢，你的 AI 助手。有什么我可以帮你的吗？'
        },
        research: {
          role: 'assistant',
          content: '你好！我是灵枢调研助手。告诉我你想研究的话题，我会自动搜索、分析并生成完整报告。'
        },
        cs: {
          role: 'assistant',
          content: '你好！我是灵枢智能客服。请告诉我你需要什么帮助，我可以查询订单、物流、优惠信息等。'
        }
      }
      this.setData({
        messages: [welcomeMessages[mode] || welcomeMessages.normal]
      })
    }

    // 客服模式如果有场景参数，自动发送预设消息
    if (mode === 'cs' && scene) {
      const sceneMessages = {
        presales: '我想了解产品信息',
        order: '帮我查一下我的订单',
        coupon: '最近有什么优惠活动吗',
        aftersale: '我要申请售后',
        address: '我要修改收货地址',
        human: '请帮我转接人工客服'
      }
      const msg = sceneMessages[scene]
      if (msg) {
        // 延迟一下让页面渲染完成再发送
        setTimeout(() => {
          this.sendText(msg)
        }, 500)
      }
    }
  },

  onUnload() {
    // 保存消息到本地
    this.saveHistory()
  },

  saveHistory() {
    const historyKey = 'chat_history_' + this.data.mode
    const msgs = this.data.messages
    if (msgs.length > 1) {
      wx.setStorageSync(historyKey, msgs)
    }
  },

  onInput(e) {
    this.setData({ inputValue: e.detail.value })
  },

  sendMessage() {
    const text = this.data.inputValue.trim()
    this.sendText(text)
  },

  sendText(text) {
    if (!text || this.data.loading) return

    // 添加用户消息
    const userMsg = { role: 'user', content: text }
    const msgList = [...this.data.messages, userMsg]
    this.setData({
      messages: msgList,
      inputValue: '',
      loading: true,
      quickReplies: []
    })

    // 滚动到底部
    this.scrollToBottom()

    // 调 API
    this.callAPI(text)
  },

  callAPI(text) {
    const mode = this.data.mode

    if (mode === 'cs') {
      this.callCS(text)
    } else {
      this.callChatCompletions(text)
    }
  },

  callChatCompletions(text) {
    const baseUrl = app.globalData.apiBase
    const mode = this.data.mode

    // 构造带历史的消息列表
    const historyMessages = []
    // 跳过第一条欢迎消息，加上系统提示
    const systemPrompt = mode === 'research'
      ? '你是一个调研助手。请用中文回复，帮助用户搜索和分析信息。'
      : '你是一个有用的AI助手。请用中文回复。'

    // 从messages中提取用户的对话历史（不包括最后一条占位消息和欢迎消息）
    const msgs = this.data.messages
    const conversationMsgs = msgs.slice(1, -1) // 跳过欢迎和占位
      .filter(m => m.content && m.content !== '')
      .map(m => ({ role: m.role, content: m.content }))

    wx.request({
      url: `${baseUrl}/v1/chat/completions`,
      method: 'POST',
      timeout: 30000,
      header: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${app.globalData.apiToken}`,
        'X-API-Token': app.globalData.apiToken
      },
      data: {
        model: 'default',
        messages: [
          { role: 'system', content: systemPrompt },
          ...conversationMsgs,
          { role: 'user', content: text }
        ],
        stream: false
      },
      success: (res) => {
        if (res.data && res.data.choices && res.data.choices[0]) {
          const reply = res.data.choices[0].message.content
          this.appendAssistantReply(reply)
        } else if (res.data && res.data.reply) {
          this.appendAssistantReply(res.data.reply)
        } else {
          this.appendAssistantReply('抱歉，我没有理解你的问题，请重新描述一下。')
        }
      },
      fail: (err) => {
        console.error('API call failed:', err)
        this.appendAssistantReply('连接失败，请检查网络或到设置页确认 API 地址是否正确。')
      },
      complete: () => {
        this.setData({ loading: false })
        this.saveHistory()
      }
    })
  },

  callCS(text) {
    const baseUrl = app.globalData.apiBase

    wx.request({
      url: `${baseUrl}/v1/cs/chat`,
      method: 'POST',
      timeout: 30000,
      header: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${app.globalData.apiToken}`,
        'X-API-Token': app.globalData.apiToken
      },
      data: {
        message: text,
        history: this.data.messages
          .filter(m => m.role !== 'system')
          .slice(1, -1)
          .map(m => ({ role: m.role, content: m.content }))
      },
      success: (res) => {
        if (res.data && res.data.reply) {
          this.appendAssistantReply(res.data.reply)

          // 如果有 quick_replies，显示为快捷按钮
          if (res.data.quick_replies && res.data.quick_replies.length > 0) {
            this.setData({ quickReplies: res.data.quick_replies })
          }

          // 如果有 intent，记录
          if (res.data.intent) {
            console.log('CS intent:', res.data.intent)
          }
        } else {
          this.appendAssistantReply('抱歉，客服系统暂时无法回复，请稍后再试。')
        }
      },
      fail: (err) => {
        console.error('CS API call failed:', err)
        this.appendAssistantReply('连接失败，请检查网络或到设置页确认 API 地址是否正确。')
      },
      complete: () => {
        this.setData({ loading: false })
        this.saveHistory()
      }
    })
  },

  appendAssistantReply(content) {
    const msgs = this.data.messages
    // 替换最后的占位消息或追加新消息
    const lastMsg = msgs[msgs.length - 1]
    if (lastMsg && lastMsg.role === 'assistant' && lastMsg.content === '') {
      msgs[msgs.length - 1] = { role: 'assistant', content: content }
    } else {
      msgs.push({ role: 'assistant', content: content })
    }
    this.setData({ messages: msgs })
    this.scrollToBottom()
  },

  onQuickReply(e) {
    const text = e.currentTarget.dataset.text
    if (text) {
      this.sendText(text)
    }
  },

  scrollToBottom() {
    setTimeout(() => {
      this.setData({ scrollTop: 999999 })
    }, 100)
  },

  clearHistory() {
    wx.showModal({
      title: '清空对话',
      content: '确认清空当前对话记录？',
      success: (res) => {
        if (res.confirm) {
          const mode = this.data.mode
          const welcomeMessages = {
            normal: { role: 'assistant', content: '你好！我是灵枢，你的 AI 助手。有什么我可以帮你的吗？' },
            research: { role: 'assistant', content: '你好！我是灵枢调研助手。告诉我你想研究的话题，我会自动搜索、分析并生成完整报告。' },
            cs: { role: 'assistant', content: '你好！我是灵枢智能客服。请告诉我你需要什么帮助。' }
          }
          this.setData({
            messages: [welcomeMessages[mode] || welcomeMessages.normal],
            quickReplies: []
          })
          wx.removeStorageSync('chat_history_' + mode)
          wx.showToast({ title: '已清空', icon: 'success' })
        }
      }
    })
  }
})
