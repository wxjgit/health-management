// py skeleton · 骨架屏（API 对标 tdesign-miniprogram skeleton）
Component({
    options: { addGlobalClass: true },
    properties: {
        loading: { type: Boolean, value: true },
        theme: { type: String, value: 'text' },  // text/paragraph/avatar/avatar-text/paragraph-avatar
        row: { type: Number, value: 3 },
        col: { type: Number, value: 1 },
        animation: { type: String, value: 'gradient' }  // gradient/flush/none
    },
    data: { rows: [], cols: [], showAvatar: false, showText: true },
    observers: {
        'theme, row, col': function () {
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
        // 预派生数组供 wx:for（模板不做下标/比较）
        _derive() {
            const t = this.data.theme;
            this.setData({
                rows: Array.from({ length: Math.max(0, this.data.row | 0) }, function (_, i) { return i; }),
                cols: Array.from({ length: Math.max(1, this.data.col | 0) }, function (_, i) { return i; }),
                showAvatar: t === 'avatar' || t === 'avatar-text' || t === 'paragraph-avatar',
                showText: t !== 'paragraph'
            });
        }
    }
});
