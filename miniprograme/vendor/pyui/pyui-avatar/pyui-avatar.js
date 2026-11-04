// py avatar · 头像（API 对标 tdesign-miniprogram avatar）
Component({
    options: { addGlobalClass: true, virtualHost: true },
    properties: {
        image: { type: String, value: '' },
        alt: { type: String, value: '' },
        size: { type: String, value: 'medium' },  // small/medium/large 或数字 rpx
        shape: { type: String, value: 'circle' }, // circle/round
        icon: { type: String, value: 'user' }
    },
    data: { failed: false, sizePx: '' },
    observers: {
        size: function (size) {
            if (!this._attached) return;
            this._deriveSize();
        },
        // 换图必须清 failed：否则失败过一次后 image 节点永被 wx:if 摘除，新头像只显示 icon 兜底
        image: function () {
            if (!this._attached) return;
            if (this.data.failed) this.setData({ failed: false });
        }
    },
    lifetimes: {
        attached() {
            this._attached = true;
            this._deriveSize();
        }
    },
    methods: {
        // 预派生尺寸：命名档 → rpx 字符串
        _deriveSize() {
            const map = { small: '64rpx', medium: '96rpx', large: '128rpx' };
            const s = this.data.size;
            this.setData({ sizePx: map[s] || (isNaN(Number(s)) ? s : s + 'rpx') });
        },
        onError() {
            this.setData({ failed: true });
            this.triggerEvent('error');
        }
    }
});
