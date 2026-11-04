// 布局度量自给模块：库脱离宿主 app 也能算出胶囊/状态栏/安全区高度
// 宿主若在 globalData.componentHeights 提供覆盖值，优先宿主（老 app 切换零改动）
let cache = null;

function win() {
    if (wx.getWindowInfo) return wx.getWindowInfo();
    return wx.getSystemInfoSync();
}

function getMetrics() {
    if (cache) return cache;
    let menuTop = 24, menuHeight = 32, statusBarHeight = 20, safeareaHeight = 0;
    try {
        const r = wx.getMenuButtonBoundingClientRect();
        if (r && r.top) {
            menuTop = r.top;
            menuHeight = r.height;
        }
        const wi = win();
        statusBarHeight = wi.statusBarHeight || 20;
        safeareaHeight = wi.safeArea ? Math.max(0, wi.screenHeight - wi.safeArea.bottom) : 0;
    } catch (e) { /* 极端环境回落默认值 */ }
    cache = {
        menuTop: menuTop,
        menuHeight: menuHeight,
        statusBarHeight: statusBarHeight,
        safeareaHeight: safeareaHeight,
        navigationHeight: (menuTop - statusBarHeight) * 2 + menuHeight
    };
    return cache;
}

function hostHeights() {
    try {
        const app = getApp();
        return (app && app.globalData && app.globalData.componentHeights) || null;
    } catch (e) {
        return null;
    }
}

function componentHeights() {
    return Object.assign({}, getMetrics(), hostHeights() || {});
}

function hostIndexPath() {
    try {
        const app = getApp();
        const t = app && app.globalData && app.globalData.tabBarData;
        return (t && t.tabBar && t.tabBar[0] && t.tabBar[0].pagePath) || '';
    } catch (e) {
        return '';
    }
}

function hostPagesData() {
    try {
        const app = getApp();
        return (app && app.globalData && app.globalData.pagesData) || {};
    } catch (e) {
        return {};
    }
}

module.exports = { componentHeights: componentHeights, hostIndexPath: hostIndexPath, hostPagesData: hostPagesData };
