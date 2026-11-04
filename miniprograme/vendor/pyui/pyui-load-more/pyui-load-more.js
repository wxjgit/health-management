// py-loadmore — fui-loadmore 的轻量替代
// status: 'loading' 加载中(带转圈) | 'nodata' 没有更多数据 | 其他值不渲染文字以外内容
Component({
    options: { addGlobalClass: true },
    properties: {
        status: { type: String, value: 'loading' },
        text: { type: String, value: '' },          // 自定义文字，优先
        loadingText: { type: String, value: '正在加载...' },
        nodataText: { type: String, value: '没有更多数据了' },
        size: { type: Number, value: 26 },
        color: { type: String, value: '#B2B2B2' },
        spinnerColor: { type: String, value: '#B2B2B2' }  // 转圈颜色，与 fui-loadmore 默认灰环一致
    },
    data: {
        showText: ''
    },
    observers: {
        'text,loadingText,nodataText,status': function () {
            const d = this.data;
            let showText = d.text;
            if (!showText) {
                showText = d.status === 'loading' ? d.loadingText : d.nodataText;
            }
            this.setData({ showText });
        }
    },
    methods: {
        handleClick() {
            this.triggerEvent('click');
        }
    }
});
