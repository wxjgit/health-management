// py-textarea — fui-textarea 的轻量替代（多行输入）
Component({
    options: { addGlobalClass: true },
    properties: {
        value: { type: String, value: '' },
        placeholder: { type: String, value: '' },
        placeholderColor: { type: String, value: '#B2B2B2' },
        disabled: { type: Boolean, value: false },
        focus: { type: Boolean, value: false },
        maxlength: { type: Number, value: 140 },
        autoHeight: { type: Boolean, value: false },
        height: { type: Number, value: 200 },   // rpx，autoHeight 时失效
        size: { type: Number, value: 28 },
        color: { type: String, value: '#333333' },
        radius: { type: Number, value: 12 },
        background: { type: String, value: '#F8F8F8' },
        padding: { type: String, value: '16rpx 24rpx' },
        border: { type: String, value: 'none' }
    },
    observers: {
        'value': function (v) {
            if (v === null || v === undefined) this.setData({ value: '' });
        }
    },
    data: {
        formDisabled: false   // form 级禁用（经 form-item 下发，不覆盖自身 disabled 属性）
    },
    methods: {
        // form-item 经 relations 注入：input 事件同步上报值供校验
        bindFormItem(item) {
            this._formItem = item;
        },
        setFormDisabled(disabled) {
            this.setData({ formDisabled: !!disabled });
        },
        _reportToFormItem(value) {
            if (this._formItem && this._formItem.onFieldChange) this._formItem.onFieldChange(value);
        },

        onInput(e) {
            this._reportToFormItem(e.detail.value);
            this.triggerEvent('input', e.detail);
        },
        onFocus(e) {
            this.triggerEvent('focus', e.detail);
        },
        onBlur(e) {
            this.triggerEvent('blur', e.detail);
        },
        onConfirm(e) {
            this.triggerEvent('confirm', e.detail);
        },
        onLineChange(e) {
            this.triggerEvent('linechange', e.detail);
        }
    }
});
