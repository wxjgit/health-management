// py notice-bar · 公告栏（API 对标 tdesign-miniprogram notice-bar 核心）
Component({
    options: { addGlobalClass: true },
    properties: {
        content: { type: String, value: '' },
        theme: { type: String, value: 'info' },   // info/success/warning/error
        prefixIcon: { type: String, value: '' },  // 空=按 theme 默认
        closable: { type: Boolean, value: false },
        marquee: { type: Boolean, value: false }  // 跑马灯暂不实现，声明保留
    },
    data: { icon: 'info', closed: false },
    observers: {
        'theme, prefixIcon': function () {
            if (!this._attached) return;
            this._derive();
        },
        // 换新文案视为重新展示（close 后无需重建组件）
        content: function () {
            if (!this._attached) return;
            if (this.data.closed) this.setData({ closed: false });
        }
    },
    lifetimes: { attached() { this._attached = true; this._derive(); } },
    methods: {
        _derive() {
            const def = { info: 'info', success: 'success', warning: 'warning', error: 'error' };
            this.setData({ icon: this.data.prefixIcon || def[this.data.theme] || 'info' });
        },
        onClick() { if (!this.data.closed) this.triggerEvent('click'); },
        // 内部收起：此前只抛事件不落视觉，点关闭按钮组件纹丝不动
        onClose() {
            this.setData({ closed: true });
            this.triggerEvent('close');
        }
    }
});
