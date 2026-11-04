// py slider · 滑杆（API 对标 tdesign-miniprogram slider 核心子集）
// 手势：touchmove 零 setData 直改 knob 位移？——保守起见每 move 一次 setData（单值轻量）
Component({
    options: { addGlobalClass: true },
    properties: {
        value: { type: Number, value: 0 },
        min: { type: Number, value: 0 },
        max: { type: Number, value: 100 },
        step: { type: Number, value: 1 },
        disabled: { type: Boolean, value: false },
        label: { type: Boolean, value: false },
        range: { type: Boolean, value: false }   // 区间模式暂不实现，保留 API 位
    },
    data: { pct: 0, dragging: false },
    observers: {
        'value, min, max': function () {
            if (!this._attached) return;
            this._derive();
        }
    },
    lifetimes: {
        attached() { this._attached = true; this._derive(); }
    },
    methods: {
        _derive() {
            const { value, min, max } = this.data;
            const span = max - min || 1;
            this.setData({ pct: Math.max(0, Math.min(100, ((value - min) / span) * 100)) });
        },
        _rect(cb) {
            this.createSelectorQuery().select('.py-slider-track').boundingClientRect((r) => cb(r)).exec();
        },
        onTouchStart(e) {
            if (this.data.disabled) return;
            this._dragging = true;
            this.setData({ dragging: true });
            this._rect((r) => { this._track = r; this._move(e); });
        },
        onTouchMove(e) {
            if (this._dragging) this._move(e);
        },
        onTouchEnd() {
            this._dragging = false;
            this.setData({ dragging: false });
        },
        _move(e) {
            if (!this._track) return;
            const { min, max, step } = this.data;
            const x = e.touches[0].clientX - this._track.left;
            const ratio = Math.max(0, Math.min(1, x / this._track.width));
            const span = max - min;
            let v = min + ratio * span;
            v = Math.round(v / step) * step;
            v = Math.max(min, Math.min(max, Number(v.toFixed(2))));
            if (v !== this.data.value) this.triggerEvent('change', { value: v });
        }
    }
});
