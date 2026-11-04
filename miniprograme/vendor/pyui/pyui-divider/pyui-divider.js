// py-divider — fui-divider 的轻量替代：分隔线（可带居中文字）
Component({
    options: { addGlobalClass: true },
    properties: {
        text: { type: String, value: '' },
        size: { type: Number, value: 26 },
        color: { type: String, value: '#B2B2B2' },
        lineColor: { type: String, value: '#EEEEEE' },  // 支持纯色或 linear-gradient(...)
        padding: { type: Number, value: 24 }
    }
});
