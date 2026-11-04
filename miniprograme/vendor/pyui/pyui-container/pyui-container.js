// 页面内容容器（v3 原生滚动版）
// 设计变更：弃用 scroll-view + refresher（性能），页面滚动/下拉刷新/触底改用微信原生能力：
//   - 页面 json: "enablePullDownRefresh": true
//   - 页面 js:   onPullDownRefresh() { this.pullDownFunc(); setTimeout(() => wx.stopPullDownRefresh(), 1000); }
//   - 触底加载:  onReachBottom() { this.scrollToLowerFunc && this.scrollToLowerFunc(); }
// 本组件只负责：全屏文档流容器 + 顶部让位磨砂导航（padding-top）+ 底部让位悬浮 tabbar（type=tabbar）。
const metrics = require('../common/metrics.js');

Component({
    options: {
        multipleSlots: false,
        addGlobalClass: true
    },
    properties: {
        type: { type: String, value: 'default' },    // default | tabbar（底部让位悬浮 tabbar）
        color: { type: String, value: 'transparent' },
        fullscreen: { type: Boolean, value: false }  // map 等满屏页：不让位、不滚动
    },
    data: {
        padTop: 0,
        padBottom: 12
    },
    observers: {
        'type': function (type) {
            // 初始化阶段（attached 前）不 setData；attached 会统一算一次
            if (this._attached) this.updateGeometry(type);
        }
    },
    lifetimes: {
        attached() {
            this._attached = true;
                        const h = metrics.componentHeights();
            this.setData({
                padTop: h.statusBarHeight + h.navigationHeight
            });
            this.updateGeometry(this.data.type);
        }
    },
    methods: {
        updateGeometry(type) {
                        const h = metrics.componentHeights();
            this.setData({
                // 悬浮 tabbar 实际占位 = 自身高度 + 底偏移(10px + safe-area) + 呼吸 gap；
                // 旧公式漏 safe-area 段，末行内容被透镜 bar 压住（2026-10-13 修）
                // tabbarHeight 兜底 56：宿主 globalData.componentHeights 未提供时防止 NaN 让位全失
                padBottom: type === 'tabbar' ? (h.tabbarHeight || 56) + (h.safeareaHeight || 0) + 24 : 12
            });
        }
    }
});
