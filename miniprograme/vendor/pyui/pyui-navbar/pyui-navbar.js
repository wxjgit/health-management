// 自绘导航栏：标题来自 globalData.pagesData（路由→标题映射）
// 胶囊布局由 showMenu/showShare/canNavigateBack/notIndex 四个布尔决定，共 5 种分支

// 页面 tint 色 → 导航玻璃底色。
// 白色/默认 = 全透明：玻璃感完全来自 backdrop-filter 对下方内容的 blur 采样，
// 顶部背后是空渐变时与正文无缝（不再"洗白"成一块白板）；
// 品牌 tint（如我的页 #b5fade）= 35% 淡洗，保留色彩身份又不盖住玻璃采样。
function hexToGlass(hex) {
    const m = /^#([0-9a-fA-F]{6})$/.exec(hex || '');
    if (!m || m[1].toLowerCase() === 'ffffff') {
        return 'rgba(255, 255, 255, 0)';
    }
    const n = parseInt(m[1], 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, 0.35)`;
}

function toast(msg) {
    pyui.showToast({ title: msg, icon: 'none', duration: 2000 });
}

const pyui = require('../common/pyui.js');
const metrics = require('../common/metrics.js');
Component({
    options: {
        multipleSlots: false,
        addGlobalClass: true
    },
    properties: {
        showMenu: {
            type: Boolean,
            value: true
        },
        showShare: {
            type: Boolean,
            value: false
        },
        color: {
            type: String,
            value: '#ffffff'
        },
        // 标题覆盖（虚拟页切换宿主页用）：非空时优先于 pagesData 路由映射
        title: {
            type: String,
            value: ''
        }
    },
    data: {
        canNavigateBack: false,
        notIndex: false,
        isTouching: -1,
        pressX: '',   // 手指在按压 seg 内坐标（px 串）；亮斑经 translate3d 合成器通道跟手
        pressY: '',
        navigationHeight: 44,
        marginTop: 0,
        wrapperHeight: 72,
        navBg: 'rgba(255, 255, 255, 0)',
    },
    observers: {
        'color': function (color) {
            // 同上：初始化阶段（attached 前）不 setData，attached 里统一算一次
            if (this._attached) {
                this.setData({ navBg: hexToGlass(color) });
            }
        },
        'title': function (t) {
            // 初始化阶段（attached 前）禁止 setData：渲染层节点树未建，
            // 此时写标题文本节点会触发 "Cannot set properties of undefined (setting 'textContent')" 白屏。
            // 自动标题统一在 attached 计算；运行期 title 被清空才重算。
            if (this._attached && !t) {
                this.findCurrentPage();
            }
        }
    },
    lifetimes: {
        attached() {
            const h = metrics.componentHeights();
            this._pagesData = metrics.hostPagesData();
            this.setData({
                // inner 与微信胶囊同顶同高（胶囊矩形基准，见 app.js getComponentHeight）
                navigationHeight: h.menuHeight || h.navigationHeight,
                marginTop: h.menuTop || h.statusBarHeight,
                wrapperHeight: (h.menuTop || h.statusBarHeight) + (h.menuHeight || h.navigationHeight) + 14, // 14px 羽化渐隐带
                navBg: hexToGlass(this.data.color)
            });
            this._attached = true;
            if (!this.data.title) {
                this.findCurrentPage();
            }
            this.judgePageStatus();
        }
    },
    methods: {
        // 首页路径：宿主 globalData.tabBarData 契约优先；未配置时回落页面栈底（启动页即首页）。
        // 无此回落时 indexPath='' 使 notIndex 恒真——主页自己也显示首页钮且 reLaunch('/') 必失败
        // （用户实测"跳转失败"toast）。已知降级：冷启直达深层页时栈底被误认为首页（少一个首页钮，
        // 优于显示一个必报错的钮）
        _indexPath() {
            let p = metrics.hostIndexPath();
            if (!p) {
                const pages = getCurrentPages();
                if (pages && pages.length) p = pages[0].route;
            }
            return p;
        },

        findCurrentPage() {
            let pages = getCurrentPages();
            if (!pages || !pages.length) return; // 页面栈未就绪（observer 早触发/页面销毁期）防崩溃
            let currentPage = pages[pages.length - 1];
            let currentPath = currentPage.route;
            if (currentPath in this._pagesData) {
                this.setData({ autoTitle: this._pagesData[currentPath] });
            } else {
                // 宿主未配置该路由标题：留空（此前硬编码宿主专属调试文案，第三方宿主会当场漏出）
                this.setData({ autoTitle: '' });
            }
        },

        judgePageStatus() {
            const app = getApp();
            let pages = getCurrentPages();
            if (!pages || !pages.length) return;
            let canNavigateBack = pages.length > 1;
            let currentPage = pages[pages.length - 1];
            let currentPath = currentPage.route;
            let indexPath = this._indexPath();
            this.setData({
                canNavigateBack: canNavigateBack,
                notIndex: currentPath != indexPath
            });
        },

        // 按压物理与 py-button 同款（批 H 终版）：自管 pressing + 亮斑落点/跟手走 translate3d
        handleTouchStart(e) {
            const idx = e.currentTarget.dataset.index;
            const t = (e.touches && e.touches[0]) || null;
            this._segRect = null;
            this.setData({ isTouching: idx, pressX: '', pressY: '' });
            if (!t) return;
            this.createSelectorQuery().select('.seg-' + idx).boundingClientRect((r) => {
                this._segRect = r;
                if (this.data.isTouching === idx && r) {
                    this.setData({
                        pressX: (t.clientX - r.left) + 'px',
                        pressY: (t.clientY - r.top) + 'px'
                    });
                }
            }).exec();
        },

        handleTouchMove(e) {
            if (this.data.isTouching < 0 || !this._segRect) return;
            const t = e.touches && e.touches[0];
            if (!t) return;
            this.setData({
                pressX: (t.clientX - this._segRect.left) + 'px',
                pressY: (t.clientY - this._segRect.top) + 'px'
            });
        },

        handleTouchEnd() {
            this._segRect = null;
            this.setData({ isTouching: -1, pressX: '', pressY: '' });
        },

        navigateBack() {
            wx.vibrateShort({ type: 'light' });
            const app = getApp();
            let pages = getCurrentPages();
            let currentPage = pages[pages.length - 1];
            let currentPath = currentPage.route;
            let indexPath = this._indexPath();
            if (currentPath == indexPath) {
                return;
            } else {
                let redirectPath = '/' + indexPath;
                wx.navigateBack({
                    delta: 1,
                    fail() {
                        wx.reLaunch({
                            url: redirectPath,
                            fail() {
                                toast('跳转失败');
                            }
                        });
                    }
                });
            }
        },

        navigateToIndex() {
            wx.vibrateShort({ type: 'light' });
            const indexPath = this._indexPath();
            if (!indexPath) return;   // 栈空极端时序：不 reLaunch 无效路径
            let redirectPath = '/' + indexPath;
            wx.reLaunch({
                url: redirectPath,
                fail() {
                    toast('跳转失败');
                }
            });
        },

        navigateShare() {
            wx.vibrateShort({ type: 'light' });
        }
    }
});
