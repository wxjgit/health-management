// py-search-bar — fui-search-bar 的轻量替代
// disabled 模式下点击整体触发 click 事件（常用于"点开弹出选择层"）
Component({
    options: { addGlobalClass: true },
    properties: {
        value: { type: String, value: '' },
        placeholder: { type: String, value: '请输入搜索内容' },
        placeholderColor: { type: String, value: '#B2B2B2' },
        type: { type: String, value: 'text' },
        disabled: { type: Boolean, value: false },
        focus: { type: Boolean, value: false },
        maxlength: { type: Number, value: 140 },
        size: { type: Number, value: 28 },
        color: { type: String, value: '' },
        background: { type: String, value: '#FFFFFF' },
        boxBackground: { type: String, value: '#F5F6F7' },
        radius: { type: Number, value: 16 },
        showAction: { type: Boolean, value: false },
        actionText: { type: String, value: '搜索' },
        actionColor: { type: String, value: '#015293' }
    },
    methods: {
        onInput(e) {
            this.triggerEvent('input', e.detail);
        },
        onFocus(e) {
            this.triggerEvent('focus', e.detail);
        },
        onBlur(e) {
            this.triggerEvent('blur', e.detail);
        },
        onConfirm(e) {
            this.triggerEvent('search', e.detail);
        },
        onActionTap() {
            this.triggerEvent('search', { value: this.data.value });
        },
        onClear() {
            this.triggerEvent('input', { value: '' });
            this.triggerEvent('clear');
        },
        onWrapTap() {
            this.triggerEvent('click');
        }
    }
});
