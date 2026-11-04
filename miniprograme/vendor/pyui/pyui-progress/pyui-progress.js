// py progress · 进度条（API 对标 tdesign-miniprogram progress）
Component({
    options: { addGlobalClass: true },
    properties: {
        percentage: { type: Number, value: 0 },
        theme: { type: String, value: 'line' },   // line/plump
        color: { type: String, value: '' },
        trackColor: { type: String, value: '' },
        strokeWidth: { type: Number, value: 8 },  // rpx
        label: { type: Boolean, value: false },
        status: { type: String, value: '' }       // ''/success/warning/error
    },
    data: { pct: 0, barColor: '' },
    observers: {
        'percentage, status, color': function () {
            if (!this._attached) return;
            this._derive();
        }
    },
    lifetimes: {
        attached() {
            this._attached = true;
            this._derive();
        }
    },
    methods: {
        // 预派生：宽度钳位 + status 语义色（模板不做比较）
        _derive() {
            const p = Math.max(0, Math.min(100, Number(this.data.percentage) || 0));
            const statusColor = {
                success: '#09BE4F',
                warning: '#FFB703',
                error: '#FF2B2B'
            }[this.data.status] || '';
            this.setData({ pct: p, barColor: this.data.color || statusColor });
        }
    }
});
