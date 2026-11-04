// py-list-cell — fui-list-cell 的轻量替代：列表行（标题 + 值 + 箭头 + 插槽）
Component({
    options: { addGlobalClass: true },
    properties: {
        title: { type: String, value: '' },
        titleColor: { type: String, value: '#333333' },
        titleSize: { type: Number, value: 28 },
        desc: { type: String, value: '' },
        value: { type: String, value: '' },
        valueColor: { type: String, value: '#7F7F7F' },
        arrow: { type: Boolean, value: false },
        last: { type: Boolean, value: false },
        disabled: { type: Boolean, value: false },
        background: { type: String, value: '#FFFFFF' },
        padding: { type: String, value: '24rpx' },
        index: { type: null, value: '' }
    },
    methods: {
        handleClick() {
            if (this.data.disabled) return;
            this.triggerEvent('click', { index: this.data.index });
        }
    }
});
