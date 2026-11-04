// py-bottom-popup — iOS 26 sheet 解剖（DESIGN.md §6）：grabber + 顶大圆角玻璃面 + 弹簧上滑
// 带遮罩、安全区；圆角统一 --r-sheet 令牌，不再外露 radius
Component({
    options: { addGlobalClass: true },
    properties: {
        show: { type: Boolean, value: false },
        title: { type: String, value: '' },
        background: { type: String, value: '#FFFFFF' },
        height: { type: String, value: 'auto' },
        maskClosable: { type: Boolean, value: true }
    },
    data: {
        visible: false
    },
    observers: {
        'show': function (show) {
            if (show) {
                this.setData({ visible: true });
            } else if (this.data.visible) {
                setTimeout(() => {
                    if (!this.data.show) this.setData({ visible: false });
                }, 250);
            }
        }
    },
    methods: {
        noop() {},

        onMaskTap() {
            if (this.data.maskClosable) {
                this.onClose();
            }
        },

        onClose() {
            this.triggerEvent('close');
        }
    }
});
