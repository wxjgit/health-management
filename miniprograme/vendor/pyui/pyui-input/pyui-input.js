// py-input — fui-input 的轻量替代（单行输入，支持 clearable 清空按钮）
// type 支持 text/number/idcard/digit/safePassword/nickname（原生 input 类型）
Component({
    options: { addGlobalClass: true },
    properties: {
        value: { type: String, value: '' },
        clearable: { type: Boolean, value: false },
        label: { type: String, value: '' },
        labelColor: { type: String, value: '' },
        labelSize: { type: Number, value: 28 },
        type: { type: String, value: 'text' },
        password: { type: Boolean, value: false },
        placeholder: { type: String, value: '' },
        placeholderColor: { type: String, value: '#B2B2B2' },
        disabled: { type: Boolean, value: false },
        focus: { type: Boolean, value: false },
        maxlength: { type: Number, value: 140 },
        align: { type: String, value: 'left' },
        size: { type: Number, value: 28 },
        color: { type: String, value: '' }, // 空=令牌色（wxss）；传值=内联覆盖
        radius: { type: Number, value: 12 },
        background: { type: String, value: '' }, // 空=玻璃域默认（glass-field）；传值=内联覆盖
        padding: { type: String, value: '16rpx 24rpx' },
        border: { type: String, value: 'none' },
        confirmType: { type: String, value: 'done' }
    },
    data: {
        val: '',
        formDisabled: false   // form 级禁用（经 form-item 下发，不覆盖自身 disabled 属性）
    },
    observers: {
        'value': function (v) {
            // null/undefined 强制空串：渲染层 input 收到 null 会触发 textContent 崩溃白屏
            const safe = (v === null || v === undefined) ? '' : String(v);
            if (safe !== this.data.val) this.setData({ val: safe });
        }
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
            this.setData({ val: e.detail.value });
            this.triggerEvent('input', e.detail);
        },
        onClear() {
            this.setData({ val: '' });
            this.triggerEvent('input', { value: '' });
            this.triggerEvent('clear');
        },
        onFocus(e) {
            this.triggerEvent('focus', e.detail);
        },
        onBlur(e) {
            this.triggerEvent('blur', e.detail);
        },
        onConfirm(e) {
            this.triggerEvent('confirm', e.detail);
        }
    }
});
