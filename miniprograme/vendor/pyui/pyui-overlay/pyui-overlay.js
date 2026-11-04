// py-overlay · 全局自定义 toast/loading/modal 实体（注册进 utils/pyui.js 实例栈）
const pyui = require('../common/pyui.js');

Component({
    data: {
        toast: { show: false, leaving: false, title: '', icon: 'none' },
        loading: { show: false, leaving: false, title: '' },
        modal: {
            show: false, leaving: false, title: '', content: '',
            showCancel: true, cancelText: '取消', confirmText: '确认', destructive: false
        }
    },

    lifetimes: {
        attached() {
            pyui.register(this);
        },
        detached() {
            pyui.unregister(this);
            if (this._toastTimer) clearTimeout(this._toastTimer);
        }
    },

    methods: {
        noop() {},

        showToast(o) {
            // 顺带清系统 toast（wx.setClipboardData 等 API 的成功系统提示），防双弹
            wx.hideToast();
            if (this._toastTimer) clearTimeout(this._toastTimer);
            if (this._toastLeave) clearTimeout(this._toastLeave);
            this.setData({
                toast: { show: true, leaving: false, title: o.title || '', icon: o.icon || 'none' }
            });
            this._toastTimer = setTimeout(() => this.hideToast(), o.duration || 2000);
        },

        // 退场两段式：先播 180ms 退场动画再摘节点（入场有弹簧、退场不能硬切）
        hideToast() {
            if (this._toastTimer) clearTimeout(this._toastTimer);
            if (!this.data.toast.show || this.data.toast.leaving) return;
            this.setData({ 'toast.leaving': true });
            this._toastLeave = setTimeout(() => {
                this.setData({ toast: { show: false, leaving: false, title: '', icon: 'none' } });
            }, 180);
        },

        showLoading(o) {
            if (this._loadLeave) clearTimeout(this._loadLeave);
            this.setData({ loading: { show: true, leaving: false, title: (o && o.title) || '' } });
        },

        hideLoading() {
            if (!this.data.loading.show || this.data.loading.leaving) return;
            this.setData({ 'loading.leaving': true });
            this._loadLeave = setTimeout(() => {
                this.setData({ loading: { show: false, leaving: false, title: '' } });
            }, 180);
        },

        showModal(o) {
            if (this._modalLeave) clearTimeout(this._modalLeave);
            this._modalOpts = o;
            this.setData({
                modal: {
                    show: true,
                    leaving: false,
                    title: o.title || '',
                    content: o.content || '',
                    showCancel: o.showCancel !== false,
                    cancelText: o.cancelText || '取消',
                    confirmText: o.confirmText || '确认',
                    destructive: !!o.destructive
                }
            });
        },

        _closeModal(res) {
            const o = this._modalOpts || {};
            this._modalOpts = null;
            // 回调先于退场动画触发（业务不等动画）；遮罩/卡片各自播 180ms 退场
            if (typeof o.success === 'function') o.success(res);
            if (typeof o.complete === 'function') o.complete(res);
            if (!this.data.modal.show || this.data.modal.leaving) return;
            this.setData({ 'modal.leaving': true });
            this._modalLeave = setTimeout(() => {
                this.setData({
                    modal: {
                        show: false, leaving: false, title: '', content: '',
                        showCancel: true, cancelText: '取消', confirmText: '确认', destructive: false
                    }
                });
            }, 180);
        },

        onConfirm() {
            this._closeModal({ confirm: true, cancel: false });
        },

        onCancel() {
            this._closeModal({ confirm: false, cancel: true });
        }
    }
});
