// py-empty — fui-empty 的轻量替代：图标 + 提示文字
Component({
    options: { addGlobalClass: true },
    properties: {
        text: { type: String, value: '暂无数据' },
        src: { type: String, value: '' },
        size: { type: Number, value: 240 },     // 图标尺寸 rpx
        textSize: { type: Number, value: 28 },
        color: { type: String, value: '#B2B2B2' }
    }
});
