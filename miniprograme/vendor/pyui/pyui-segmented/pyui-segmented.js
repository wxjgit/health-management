// py-segmented · Q 弹分段选择器（透镜跟手 + 裁剪窗渐亮 + 按压缩放统一数据源）
// 移植 glass-tab-bar 交互灵魂：连续 progress 透镜（拖拽跟手 + 越界阻尼 + 松手吸附）、
// easeOutBack 落位回弹、pressScale 按压缩放（JS 帧循环 lerp，与裁剪窗数学共用同一
// 数据源，避免视觉放大与亮区窗口错位）、裁剪窗渐亮（透镜罩住哪部分文字哪部分亮）。
// 裁剪：浮 bar 专属的舌片/沉底/双层图标。
// items: [{key,name}]；current: 选中 key；bind:change → detail {key}
// 交互：点按槽 = 切换；按下透镜即弹向按压槽；横拖跟手，松手吸附最近槽。

/** 落位回弹：轻过冲弹簧（与 glass-tab-bar 同源） */
function easeOutBack(x) {
    const c1 = 1.2;
    const c3 = c1 + 1;
    const t = x - 1;
    return 1 + c3 * t * t * t + c1 * t * t;
}

const OVERDAMP = 0.35;   // 越界阻尼衰减系数
const OVERMAX = 0.18;    // 越界硬上限（slot 单位）：橡皮感最多溢出 18% 槽宽，拖再远也不出 groove
const TAP_SLOP = 8;      // px，位移小于此值判为点按
const PRESS_SCALE = 1.1; // 按压/拖拽中透镜放大系数（2026-10-29 由 1.06 调大：用户反馈缩放感不足）

// cubic-bezier 采样器（Newton 迭代）：按压缩放改贝塞尔时序，与 wxss --ease-spring 同源
function cubicBezier(x1, y1, x2, y2) {
    const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
    const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
    const sampleX = (t) => ((ax * t + bx) * t + cx) * t;
    const sampleY = (t) => ((ay * t + by) * t + cy) * t;
    const sampleDX = (t) => (3 * ax * t + 2 * bx) * t + cx;
    return function (x) {
        let t = x;
        for (let i = 0; i < 5; i++) {
            const dx = sampleX(t) - x;
            if (Math.abs(dx) < 1e-4) break;
            const d = sampleDX(t);
            if (Math.abs(d) < 1e-6) break;
            t -= dx / d;
        }
        return sampleY(Math.min(1, Math.max(0, t)));
    };
}
const EASE_PRESS_IN = cubicBezier(0.2, 0, 0, 1);          // 按下：120ms 快收
const EASE_PRESS_OUT = cubicBezier(0.32, 0.72, 0.28, 1.1); // 松手：450ms 弹簧（= --ease-spring）

Component({
    properties: {
        items: {
            type: Array,
            value: []
        },
        current: {
            type: String,
            value: ''
        }
    },

    data: {
        progress: 0,     // 透镜连续位置（slot 单位，可为小数）
        pressScale: 1,   // 按压缩放（JS 驱动，裁剪窗数学同用）
        dragging: false
    },

    observers: {
        'current, items': function (current, items) {
            const idx = Math.max(0, (items || []).findIndex((it) => it.key === current));
            if (!this.data.dragging && idx !== this.data.progress) {
                this.animateTo(idx);
            }
        }
    },

    lifetimes: {
        attached() {
            this.initRect();
        },
        detached() {
            this.stopAnim();
            this.stopScale();
        }
    },

    methods: {
        initRect() {
            const padPx = 8 * wx.getWindowInfo().windowWidth / 750; // .seg padding 8rpx
            this.createSelectorQuery()
                .select('.seg')
                .boundingClientRect((rect) => {
                    // 轻记本地修改：hidden（display:none）容器内测量会得到 0 宽，
                    // 负 slotW 会使点按/拖拽映射方向反转；无效测量直接丢弃，等可见后重测
                    if (!rect || rect.width <= 2 * padPx) return;
                    this.segLeft = rect.left;
                    this.padPx = padPx;
                    this.slotW = (rect.width - 2 * padPx) / Math.max(1, this.data.items.length);
                })
                .exec();
        },

        stopAnim() {
            if (this._raf) {
                clearInterval(this._raf);
                this._raf = null;
            }
        },

        /** 落位弹簧：16ms 帧循环推进 progress（easeOutBack 过冲） */
        animateTo(target, duration) {
            this.stopAnim();
            const from = this.data.progress;
            const dur = duration || 380;
            const t0 = Date.now();
            this._raf = setInterval(() => {
                const t = Math.min(1, (Date.now() - t0) / dur);
                const p = from + (target - from) * easeOutBack(t);
                this.setData({ progress: p });
                if (t >= 1) {
                    this.stopAnim();
                    this.setData({ progress: target });
                }
            }, 16);
        },

        /** 按压缩放：贝塞尔时序帧循环（按下 120ms 快收 / 松手 450ms 弹簧回弹） */
        startScale(target) {
            this.stopScale();
            const from = this.data.pressScale;
            const dur = target > from ? 120 : 450;
            const ease = target > from ? EASE_PRESS_IN : EASE_PRESS_OUT;
            const t0 = Date.now();
            this._scaleTimer = setInterval(() => {
                const t = Math.min(1, (Date.now() - t0) / dur);
                this.setData({ pressScale: t >= 1 ? target : from + (target - from) * ease(t) });
                if (t >= 1) this.stopScale();
            }, 16);
        },

        stopScale() {
            if (this._scaleTimer) {
                clearInterval(this._scaleTimer);
                this._scaleTimer = null;
            }
        },

        /** 轻记本地修改·公共方法：宿主从 hidden 切为可见后重测槽宽（hidden 内测量为 0 宽） */
        remeasure() {
            this.slotW = 0;
            this.segLeft = undefined;
            this.initRect();
        },

        onTouchStart(e) {
            this.stopAnim();
            // 轻记本地修改：hidden 容器内测量无效（slotW<=0）时先重测并跳过本次手势，
            // 避免 NaN/负宽导致透镜与手指方向相反
            if (!this.slotW || this.slotW <= 0) { this.initRect(); return; }
            const touch = e.touches[0];
            this._x0 = touch.clientX;
            this._moved = false;
            this._pressIdx = Math.max(0, Math.min(this.data.items.length - 1,
                Math.floor((touch.clientX - this.segLeft - this.padPx) / this.slotW)));
            this.setData({ dragging: true });
            this.startScale(PRESS_SCALE);
            // 轻记本地修改（对齐 pyui-tab-bar 手感）：透镜中心 = 手指位置的绝对映射，
            // 按下以 180ms 抓位动画跟过去，拖动后由 move 逐帧接管（不再是相对位移）
            const max = this.data.items.length - 1;
            const p = Math.max(0, Math.min(max,
                (touch.clientX - this.segLeft - this.padPx) / this.slotW - 0.5));
            this.animateTo(p, 180);
        },

        onTouchMove(e) {
            if (this._x0 === undefined || !this.slotW || this.slotW <= 0) return;
            const touch = e.touches[0];
            const dx = touch.clientX - this._x0;
            if (Math.abs(dx) > TAP_SLOP) this._moved = true;
            this.stopAnim();
            const max = this.data.items.length - 1;
            // 绝对映射：透镜中心跟随手指（与 tabbar 同式），越界阻尼 + 硬上限保留橡皮感
            const raw = (touch.clientX - this.segLeft - this.padPx) / this.slotW - 0.5;
            let p;
            if (raw < 0) p = Math.max(-OVERMAX, raw * OVERDAMP);
            else if (raw > max) p = Math.min(max + OVERMAX, max + (raw - max) * OVERDAMP);
            else p = raw;
            this.setData({ progress: p });
        },

        onTouchEnd() {
            this.setData({ dragging: false });
            this.startScale(1);
            const max = this.data.items.length - 1;
            let target;
            if (!this._moved) {
                target = this._pressIdx;                 // 点按：去按下的槽
            } else {
                target = Math.max(0, Math.min(max, Math.round(this.data.progress))); // 拖拽：吸附最近槽
            }
            this._moved = false;
            this._dragBase = undefined;
            if (target !== this.data.items.findIndex((it) => it.key === this.properties.current)) {
                this.triggerEvent('change', { key: this.data.items[target].key });
            }
            this.animateTo(target);
        }
    }
});
