// py-button — fui-button 的轻量原生替代
// 支持 type 主题色、plain 镂空、open-type 透传（share/getPhoneNumber/chooseAvatar 等）
const TYPE_COLORS = {
    primary: '#015293',
    success: '#09BE4F',
    warning: '#FFB703',
    danger: '#FF2B2B',
    purple: '#6831FF',
    white: '#FFFFFF',
    none: 'transparent'
};

Component({
    options: {
        addGlobalClass: true,
        virtualHost: true
    },
    properties: {
        type: { type: String, value: 'primary' },
        text: { type: String, value: '' },
        size: { type: Number, value: 32 },        // rpx
        height: { type: String, value: '96rpx' },  // 与 fui-button 全局配置默认一致
        width: { type: String, value: '100%' },
        radius: { type: String, value: '' }, // 空=玻璃 pill 默认（wxss class）；传值=内联覆盖
        background: { type: String, value: '' }, // 自定义背景，优先于 type
        color: { type: String, value: '' },      // 自定义文字颜色
        plain: { type: Boolean, value: false },
        disabled: { type: Boolean, value: false },
        loading: { type: Boolean, value: false },
        bold: { type: Boolean, value: false },
        openType: { type: String, value: '' },
        customStyle: { type: String, value: '' }
    },
    data: {
        btnStyle: '',
        pressing: false,   // 按压态自管 class（不用原生 hover-class：setData 重渲染不再有任何重置副作用）
        pressX: '',        // 手指落点/跟随坐标（px 串）；空串回落中心
        pressY: ''
    },
    observers: {
        'type,plain,background,color': function () {
            // 初始化阶段（attached 前）不 setData：渲染层节点树未建，避免写未注册节点；
            // attached 会统一算一次，运行期变更走这里
            if (this._attached) this.updateStyle();
        }
    },
    lifetimes: {
        attached() {
            this._attached = true;
            this.updateStyle();
        }
    },
    methods: {
        updateStyle() {
            const d = this.data;
            // 玻璃化后：type/plain 的配方全部由 wxss 类型 class 承担（G0）；
            // 内联 btnStyle 仅在页面显式传 background/color 时产出——内联优先级高于 class，
            // 保证语义色（如 trackrecord 开始绿/停止红 tint）不被类型配方 !important 吞掉。
            let style = '';
            if (d.background || d.color) {
                if (d.plain) {
                    const c = d.background || TYPE_COLORS[d.type] || TYPE_COLORS.primary;
                    style = `background:transparent;color:${d.color || c};border:1px solid ${c};`;
                } else {
                    style = `background:${d.background || 'transparent'};color:${d.color || '#FFFFFF'};`;
                }
            }
            this.setData({ btnStyle: style });
        },

        handleClick(e) {
            if (this.data.disabled) return;
            this.triggerEvent('click', e.detail || {});
        },

        // 按压态逻辑层自管：start 落 class + 亮斑定格手指落点，end/cancel 抬手才摘。
        // 不跟手（跟手即延时之源：跨层 setData 或 wxs 注册赌局，均实测不可靠）；
        // 首帧零延时放大由 wxss :active 伪类承担。不依赖原生 hover-class（会被重渲染重置）
        _touchOf(e) {
            return (e.touches && e.touches[0]) || (e.changedTouches && e.changedTouches[0]) || null;
        },
        onPressStart(e) {
            if (this.data.disabled) return;
            this._btnRect = null;   // 滚动后 rect 可能失效，按下时重测
            const t = this._touchOf(e);
            if (!t) { this.setData({ pressing: true }); return; }
            const apply = (r) => {
                if (!r) { this.setData({ pressing: true }); return; }
                this.setData({
                    pressing: true,
                    pressX: (t.clientX - r.left) + 'px',
                    pressY: (t.clientY - r.top) + 'px'
                });
            };
            this.createSelectorQuery().select('.py-btn').boundingClientRect((r) => {
                this._btnRect = r;
                apply(r);
            }).exec();
        },
        onPressMove(e) {
            if (!this.data.pressing || !this._btnRect) return;
            const t = this._touchOf(e);
            if (!t) return;
            // 跟手数据源；亮斑节点以 translate3d 走合成器通道渲染（tab-bar 透镜同款原理），
            // 无重绘卡顿；catchtouchmove 吞掉页面滚动
            this.setData({
                pressX: (t.clientX - this._btnRect.left) + 'px',
                pressY: (t.clientY - this._btnRect.top) + 'px'
            });
        },
        onPressEnd() {
            if (!this.data.pressing) return;
            this._btnRect = null;
            // pressing=false 重派发 style 属性，顺带清掉 wxs 写在渲染层的 inline 亮斑
            this.setData({ pressing: false, pressX: '', pressY: '' });
        },

        onGetPhoneNumber(e) {
            this.triggerEvent('getphonenumber', e.detail);
        },

        onGetRealtimePhoneNumber(e) {
            this.triggerEvent('getrealtimephonenumber', e.detail);
        },

        onChooseAvatar(e) {
            this.triggerEvent('chooseavatar', e.detail);
        },

        onContact(e) {
            this.triggerEvent('contact', e.detail);
        },

        onOpenSetting(e) {
            this.triggerEvent('opensetting', e.detail);
        },

        onLaunchApp(e) {
            this.triggerEvent('launchapp', e.detail);
        },

        onError(e) {
            this.triggerEvent('error', e.detail);
        }
    }
});
