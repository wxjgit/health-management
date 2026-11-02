/**
 * 悬浮液态玻璃 TabBar（常驻组件，透镜位置由组件内部单一数据源持有）。
 *
 * 1:1 移植自 chengshichuxing/miniprogram/components/glass-tab-bar（ts → js 机械转换，去类型注解）。
 *
 * 关键设计：progress 不再由父页面回传（异步回传会导致透镜闪回旧槽位），
 * 组件内部维护 progress/dragging/pressing；父页面只通过 bind:change / bind:settle 切面板、
 * 通过 tab 属性在外部跳转时驱动透镜动画（observer）。
 * 组件自身不做任何路由（不 reLaunch），目标切换由宿主页决定。
 *
 * N 槽位通用（源为 5 槽写死，本项目 N=2）：槽位 i == tab i，无模式槽。
 * 裁剪：模式切换气泡（bubble-scrim / mode-bubble-* / 260ms 长按）与 AI 槽业务（停车/地铁专属）。
 * 保留：滑动透镜帧循环动画、按压 pressScale 等比放大、拖拽舌片 tongue（左右 scaleX 拉伸）、
 *       越界阻尼 + 沉底隐藏 tabbar-hidden、透镜折射环/亮暗态、pill-base/pill-lit 裁剪窗反移数学。
 */

/** 落位回弹：轻过冲弹簧，模拟液态"啪嗒" */
function easeOutBack(x) {
  const c1 = 1.2;
  const c3 = c1 + 1;
  const t = x - 1;
  return 1 + c3 * t * t * t + c1 * t * t;
}

Component({
  options: {
    styleIsolation: "apply-shared",
  },

  properties: {
    /** 已落定的 tab 索引（0 ~ N-1）。外部变化时透镜动画滑到对应槽位 */
    tab: {
      type: Number,
      value: 0,
      observer(newTab) {
        if (this.data.dragging) return;
        const slot = Math.max(0, Math.min(this.data.slotCount - 1, newTab));
        if (slot !== this.data.progress) this.animateProgress(slot, 450, true);
      },
    },
    /** 槽位数组 [{ iconName, selectedIconName, text, pagePath }]，长度 N 决定槽位数 */
    slots: {
      type: Array,
      value: [],
      observer(newSlots) {
        const n = Math.max(1, (newSlots && newSlots.length) || 0);
        this.setData({ slotCount: n });
        // 槽宽 = (bar 内容宽)/N，N 变化后需重量
        this.initBarRect();
        // N 就绪后把透镜对齐到当前 tab（避免 tab observer 早于 slots 到达时被钳到 0）
        if (!this.data.dragging) {
          const slot = Math.max(0, Math.min(n - 1, this.data.tab));
          if (slot !== this.data.progress) this.animateProgress(slot, 450, true);
        }
      },
    },
    /** 定位模式：fixed 悬浮（默认）/ static 内嵌（demo 同页展示用） */
    fixed: {
      type: Boolean,
      value: true,
    },
    /** 沉底隐藏（抽屉展开等让位；CSS 弹簧曲线顺滑动 translateY 沉出视野） */
    hidden: {
      type: Boolean,
      value: false,
      observer(newHidden) {
        // 可选事件：沉底隐藏状态变化通知宿主
        this.triggerEvent("dragstate", { hidden: !!newHidden });
      },
    },
  },

  data: {
    /** 透镜连续位置（slot 单位 0 ~ N-1），组件内部单一数据源 */
    progress: 0,
    dragging: false,
    pressing: false,
    /** 越界拉伸系数（1 = 原形，作用于 lens-body scaleX） */
    stretch: 1,
    /** 左右舌片拉伸系数（0 = 收起，作用于 tongue scaleX） */
    stretchL: 0,
    stretchR: 0,
    /** 按压缩放系数（JS 帧循环驱动，1.16 ≈ 透镜上下接近贴满 bar） */
    pressScale: 1,
    /** 槽位数 N（由 slots 属性驱动，本项目 N=2） */
    slotCount: 1,
  },

  lifetimes: {
    attached() {
      // Page navigation creates a fresh native tab bar; start at the actual route.
      this.stopProgressAnim();
      this.setData({ progress: Math.max(0, Math.min(this.data.slotCount - 1, this.data.tab)) });
      this.initBarRect();
    },
    detached() {
      this.stopProgressAnim();
      if (this.scaleTimer) clearInterval(this.scaleTimer);
    },
  },

  methods: {
    /** The host page is authoritative when navigation completes or a change is rejected. */
    syncSelection(tab) {
      this.stopProgressAnim();
      if (this.scaleTimer) clearInterval(this.scaleTimer);
      this.scaleTimer = null;
      const slot = Math.max(0, Math.min(this.data.slotCount - 1, tab));
      this.lastP = slot;
      this.setData({ progress: slot, dragging: false, pressing: false,
        pressScale: 1, stretch: 1, stretchL: 0, stretchR: 0 });
    },
    initBarRect() {
      this.createSelectorQuery()
        .select(".tabbar")
        .boundingClientRect((rect) => {
          if (!rect) return;
          this.barLeft = rect.left;
          // 与 .lens 的 calc((100% - 12px)/N) 同基准：100% = padding-box 宽 = rect - 2*border
          // 14 = 2*border(1px) + 2*padding(6px)
          this.slotW = (rect.width - 14) / this.data.slotCount;
        })
        .exec();
    },

    noop() {},

    stopProgressAnim() {
      if (this.progTimer) {
        clearInterval(this.progTimer);
        this.progTimer = null;
      }
    },

    /** 按压缩放动画：与 progress 同一套帧循环，裁剪窗/亮 pill 同帧取同一 pressScale 逐帧精确相消 */
    animateScale(target, dur) {
      if (this.scaleTimer) clearInterval(this.scaleTimer);
      const from = this.data.pressScale;
      if (from === target) return;
      const t0 = Date.now();
      this.scaleTimer = setInterval(() => {
        const x = Math.min(1, (Date.now() - t0) / dur);
        const e = 1 - Math.pow(1 - x, 3);
        if (x >= 1) {
          clearInterval(this.scaleTimer);
          this.scaleTimer = null;
          this.setData({ pressScale: target });
        } else {
          this.setData({ pressScale: from + (target - from) * e });
        }
      }, 16);
    },

    /** JS 帧循环驱动 progress：透镜/裁剪窗/反移同帧取同一值，transform 相消逐帧精确，
     *  杜绝"两条 CSS 过渡插值不重合 → 蓝层先跟走后回位" */
    animateProgress(target, dur, overshoot) {
      this.stopProgressAnim();
      const from = this.data.progress;
      if (from === target) return;
      const t0 = Date.now();
      this.progTimer = setInterval(() => {
        const x = Math.min(1, (Date.now() - t0) / dur);
        const e = overshoot ? easeOutBack(x) : 1 - Math.pow(1 - x, 3);
        if (x >= 1) {
          this.stopProgressAnim();
          this.setData({ progress: target });
        } else {
          this.setData({ progress: from + (target - from) * e });
        }
      }, 16);
    },

    onTouchStart(e) {
      const t = e.touches && e.touches[0];
      const slotW = this.slotW;
      const barLeft = this.barLeft;
      if (!t || !slotW) return;
      const max = this.data.slotCount - 1;
      this.x0 = t.clientX;
      this.moved = false;
      const f = (t.clientX - barLeft - 7) / slotW;
      const p = Math.max(0, Math.min(max, f - 0.5));
      this.lastP = p;
      // 按压：JS 短动画抓位（160ms 跟位曲线），拖动后由 move 逐帧接管
      this.stopProgressAnim();
      this.setData({ pressing: true });
      this.animateProgress(p, 180, false);
      this.animateScale(1.16, 180);
    },

    onTouchMove(e) {
      const t = e.touches && e.touches[0];
      const slotW = this.slotW;
      const barLeft = this.barLeft;
      if (!t || !slotW) return;
      // 零死区：首个 move 事件即进入跟手拖动
      if (!this.moved) {
        this.moved = true;
      }
      if (!this.data.dragging) this.setData({ dragging: true });
      this.stopProgressAnim();
      const max = this.data.slotCount - 1;
      const f = (t.clientX - barLeft - 7) / slotW;
      // 越界钳制 0.6 槽 + 阻尼 0.2 → 最大拖出约 0.12 槽，只留"顶到边"的手感
      const raw = Math.max(-0.6, Math.min(max + 0.6, f - 0.5));
      this.lastP = raw;
      const over = raw < 0 ? -raw : raw > max ? raw - max : 0;
      const damped = raw < 0 ? -over * 0.2 : raw > max ? max + (raw - max) * 0.2 : raw;
      const stretch = 1 + Math.min(over, 0.6) * 0.05;
      // 舌片：越界量 → 饱和曲线 k(o)=o/(o+1)，左右独立 scaleX 拉伸（bar 本体零 transform）
      const k = (o) => o / (o + 1);
      this.setData({
        progress: damped,
        stretch,
        stretchL: raw < 0 ? k(-raw) : 0,
        stretchR: raw > max ? k(raw - max) : 0,
      });
    },

    onTouchEnd() {
      const max = this.data.slotCount - 1;
      const settleSlot = Math.max(0, Math.min(max, Math.round(this.lastP ?? this.data.progress)));
      this.setData({ pressing: false, dragging: false, stretch: 1, stretchL: 0, stretchR: 0 });
      this.animateScale(1, 220);
      this.animateProgress(settleSlot, 450, true);
      // 源行为对齐：抬手瞬间即触发事件（与 450ms 落位动画并发），而非动画结束后；
      // change 仅在目标真正变化时触发，宿主页据此切面板/路由（组件不 reLaunch）
      if (settleSlot !== this.data.tab) {
        this.triggerEvent("change", { index: settleSlot });
      }
      this.triggerEvent("settle", { index: settleSlot });
    },

    onTouchCancel() {
      this.setData({ pressing: false, dragging: false, stretch: 1, stretchL: 0, stretchR: 0 });
      this.animateScale(1, 220);
      this.animateProgress(Math.max(0, Math.min(this.data.slotCount - 1, this.data.tab)), 300, false);
    },
  },
});
