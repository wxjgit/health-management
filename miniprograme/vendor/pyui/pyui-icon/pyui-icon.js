// py-icon · SVG 几何图标集（自绘 stroke 路径，data-URI 经 image 渲染，零字体依赖）
// API 对标 tdesign-miniprogram icon：name / size / color / customStyle（前缀差异：无 class 前缀机制）
const PATHS = {
    "close": "M18 6L6 18M6 6l12 12",
    "check": "M20 6L9 17l-5-5",
    "chevron-right": "M9 18l6-6-6-6",
    "chevron-left": "M15 18l-6-6 6-6",
    "chevron-down": "M6 9l6 6 6-6",
    "chevron-up": "M18 15l-6-6-6 6",
    "plus": "M12 5v14M5 12h14",
    "minus": "M5 12h14",
    "search": "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
    "user": "M12 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM4 20c0-4 4-6 8-6s8 2 8 6",
    "home": "M4 11l8-7 8 7M6 10v10h12V10",
    "star": "M12 3l2.7 5.8 6.3.8-4.6 4.3 1.2 6.1-5.6-3-5.6 3 1.2-6.1L3 9.6l6.3-.8z",
    "heart": "M12 20s-7-4.5-9-9c-1.4-3.2.8-7 4.3-7 2 0 3.6 1.2 4.7 3 1.1-1.8 2.7-3 4.7-3 3.5 0 5.7 3.8 4.3 7-2 4.5-9 9-9 9z",
    "share": "M18 8a3 3 0 1 0-3-3M18 8a3 3 0 0 0 0-.01M6 15a3 3 0 1 0 0-.01M18 22a3 3 0 1 0 0-.01M8.6 13.5l6.8 4M15.4 6.5l-6.8 4",
    "download": "M12 4v12M6 12l6 6 6-6M4 20h16",
    "upload": "M12 20V8M6 12l6-6 6 6M4 4h16",
    "camera": "M4 8h3l2-3h6l2 3h3v12H4zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
    "photo": "M4 5h16v14H4zM4 15l4-4 3 3 5-5 4 4",
    "edit": "M4 20h4L20 8l-4-4L4 16zM14 6l4 4",
    "trash": "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6",
    "info": "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 11v6M12 7.5v.5",
    "warning": "M12 4L2 20h20zM12 10v5M12 17.5v.5",
    "error": "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM15 9l-6 6M9 9l6 6",
    "success": "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM8 12l3 3 5-6",
    "scan": "M4 8V4h4M20 8V4h-4M4 16v4h4M20 16v4h-4M4 12h16",
    "location": "M12 21s-7-6-7-11a7 7 0 0 1 14 0c0 5-7 11-7 11zM12 12a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z",
    "phone": "M6 3h4l2 5-3 2c1 3 3 5 5 6l2-3 5 2v4c-9 1-16-6-15-16z",
    "mail": "M4 6h16v12H4zM4 7l8 6 8-6",
    "clock": "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l4 2",
    "calendar": "M4 6h16v14H4zM8 3v5M16 3v5M4 11h16",
    "copy": "M9 9h11v11H9zM5 15V4h11",
    "refresh": "M20 12a8 8 0 1 1-3-6.2M20 4v5h-5",
    "more": "M6 12h.01M12 12h.01M18 12h.01",
    "filter": "M4 5h16l-6 8v6l-4 2v-8z",
    "eye": "M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
    "lock": "M6 11h12v10H6zM9 11V7a3 3 0 0 1 6 0v4",
    "settings": "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM12 2l1.5 3 3.3.6 2.2-1.4 1.8 3-2.3 2.4.4 3.4 2.3 2.4-1.8 3-2.2-1.4-3.3.6L12 22l-1.5-3-3.3-.6-2.2 1.4-1.8-3 2.3-2.4-.4-3.4L2.8 7.2l1.8-3 2.2 1.4 3.3-.6z",
    // ── 轻记健康场景扩展图标（stroke 风格，与内置集一致）──
    "leaf": "M5 20c0-8 6-14 15-15-1 9-7 15-15 15zM5 20c4-6 8-10 13-13",
    "bowl": "M4 12h16a8 8 0 0 1-16 0zM9 8c0-1.5 1-1.5 1-3M14 8c0-1.5 1-1.5 1-3",
    "scale": "M12 3v2M7 5h10l2 14H5L7 5zM12 9v4",
    "moon": "M20 13A8 8 0 1 1 11 4a7 7 0 0 0 9 9z",
    "activity": "M3 12h4l3-7 4 14 3-7h4",
    "flower": "M12 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM12 10a3 3 0 1 1 3-3M12 10a3 3 0 1 0-3-3M12 14a3 3 0 1 1-3 3M12 14a3 3 0 1 0 3 3",
    "chart": "M4 20h16M7 20v-6M12 20V8M17 20v-10",
    "trend": "M3 17l6-6 4 4 7-8M14 7h6v6",
    "list": "M8 4h8v3H8zM6 4H5v17h14V4h-1M9 11h6M9 15h6",
    "bell": "M6 9a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6M10 19a2 2 0 0 0 4 0",
    "archive": "M3 5h18v4H3zM5 9v11h14V9M10 13h4",
    "shield": "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z",
    "logout": "M15 4h5v16h-5M10 8l-4 4 4 4M6 12h9",
    "pin": "M12 21v-6M8 3h8l-1 7 3 3H6l3-3z",
    "ban": "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM6 6l12 12",
    "medical": "M9 3h6v6h6v6h-6v6H9v-6H3V9h6z",
    "target": "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 12h.01",
    "mic": "M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3zM6 11a6 6 0 0 0 12 0M12 18v3M9 21h6"
};

function uri(name, color) {
    const d = PATHS[name];
    if (!d) return '';
    const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="' + color +
        '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="' + d + '"/></svg>';
    return 'data:image/svg+xml,' + encodeURIComponent(svg);
}

Component({
    options: { addGlobalClass: true },
    properties: {
        name: { type: String, value: '' },
        size: { type: String, value: '40rpx' },
        color: { type: String, value: '#0f172a' },
        customStyle: { type: String, value: '' }
    },
    data: { src: '' },
    observers: {
        'name, color': function (name, color) {
            if (!this._attached) return;
            this.setData({ src: uri(name, color) });
        }
    },
    lifetimes: {
        attached() {
            this._attached = true;
            this.setData({ src: uri(this.data.name, this.data.color) });
        }
    }
});
