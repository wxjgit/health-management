// pyui 全局配置存储（config-provider 的运行时载体）
// 组件默认文案从这里读；setGlobalConfig 合并覆盖
const defaults = {
    cancel: '取消',
    confirm: '确认',
    loading: '加载中…',
    empty: '暂无数据',
    confirmBtn: '确认',
    cancelBtn: '取消'
};
let current = Object.assign({}, defaults);

function setGlobalConfig(patch) {
    current = Object.assign({}, current, patch || {});
    return current;
}

function getGlobalConfig(key) {
    return current[key];
}

function resetGlobalConfig() {
    current = Object.assign({}, defaults);
}

module.exports = { setGlobalConfig, getGlobalConfig, resetGlobalConfig };
