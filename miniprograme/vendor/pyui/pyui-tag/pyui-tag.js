// py tag · 玻璃 tint 标签（API 对标 tdesign-miniprogram tag）
Component({
    options: { addGlobalClass: true },
    properties: {
        theme: { type: String, value: 'default' },   // default/primary/warning/danger/success
        variant: { type: String, value: 'light' },   // light/dark/outline/light-outline
        size: { type: String, value: 'medium' },     // small/medium/large
        shape: { type: String, value: 'square' },    // square/round/mark
        closable: { type: Boolean, value: false },
        disabled: { type: Boolean, value: false },
        maxWidth: { type: String, value: '' },
        content: { type: String, value: '' }
    },
    methods: {
        onClick() {
            if (!this.data.disabled) this.triggerEvent('click');
        },
        onClose() {
            if (!this.data.disabled) this.triggerEvent('close');
        }
    }
});
