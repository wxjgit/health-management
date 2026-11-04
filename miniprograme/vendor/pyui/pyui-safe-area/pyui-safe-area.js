const metrics = require('../common/metrics.js');

Component({
    options: {
        multipleSlots: false,
        addGlobalClass: true
    },
    properties: {
        color: {
            type: String,
            // 默认透明：露出 page{} 全局渐变底色；无参使用页面不再出白边
            value: 'transparent'
        }
    },
    data: {
        safeareaHeight: 0
    },
    lifetimes: {
        attached() {
                        this.setData({
                safeareaHeight: metrics.componentHeights().safeareaHeight
            });
        }
    }
});
