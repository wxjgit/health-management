// py switch · 开关（API 对标 tdesign-miniprogram switch）
Component({
    options: { addGlobalClass: true },
    properties: {
        value: { type: Boolean, value: false },
        loading: { type: Boolean, value: false },
        disabled: { type: Boolean, value: false },
        size: { type: String, value: 'medium' },  // small/medium/large
        customColor: { type: String, value: '' }
    },
    methods: {
        onTap() {
            if (this.data.disabled || this.data.loading) return;
            this.triggerEvent('change', { value: !this.data.value });
        }
    }
});
