// runtime/voice.ts — 语音输入封装（微信同声传译插件 WechatSI）
// 录音识别在微信侧完成（免费、官方维护）；本模块只做单例管理器、权限与错误映射。
// 用法：voice.start({onPartial, onEnd, onError}) → voice.stop() / voice.cancel()
import { confirm } from './feedback';

let manager: any = null;
let bound = false;
let recording = false;

interface Handlers {
  onPartial?: (text: string) => void;
  onEnd?: (text: string) => void;
  onError?: (msg: string) => void;
}
let H: Handlers = {};

// 插件错误码 → 可读提示（官方文档错误码表）
function mapErr(code: number): string {
  const m: Record<number, string> = {
    [-30001]: '录音失败，请检查麦克风权限',
    [-30002]: '录音被中断，请重试',
    [-30003]: '录音数据传输失败，请重试',
    [-30004]: '网络异常，识别未完成',
    [-30005]: '识别服务繁忙，请稍后重试',
    [-30006]: '识别超时，请重试',
    [-30007]: '语音启动参数错误',
    [-30008]: '查询识别结果时网络失败',
    [-30011]: '上一次识别尚未结束',
    [-40001]: '识别调用过于频繁，请稍后再试',
  };
  return m[code] || ('语音识别失败（' + code + '）');
}

function mgr(): any {
  if (!manager) {
    const plugin = requirePlugin('WechatSI');
    manager = plugin.getRecordRecognitionManager();
  }
  if (!bound) {
    bound = true;
    manager.onStart = () => { recording = true; };
    manager.onRecognize = (res: any) => { if (H.onPartial) H.onPartial(res.result || ''); };
    manager.onStop = (res: any) => {
      recording = false;
      const text = ((res && res.result) || '').trim();
      const end = H.onEnd;
      H = {};
      if (end) end(text);
    };
    manager.onError = (res: any) => {
      recording = false;
      const err = H.onError;
      H = {};
      if (err) err(mapErr(res && res.retcode));
    };
  }
  return manager;
}

// 麦克风权限：未申请过则 authorize；曾拒绝则引导去设置页（走 PyUI modal，不用原生）
function ensurePermission(): Promise<boolean> {
  return new Promise((resolve) => {
    wx.getSetting({
      success: (res: any) => {
        const granted = res.authSetting && res.authSetting['scope.record'];
        if (granted) { resolve(true); return; }
        if (granted === false) {
          confirm({
            title: '需要麦克风权限',
            content: '语音输入需要使用麦克风。你之前拒绝过授权，可在设置中重新开启。',
            confirmText: '去设置',
          }).then((ok) => {
            if (!ok) { resolve(false); return; }
            wx.openSetting({
              success: (s: any) => resolve(!!(s.authSetting && s.authSetting['scope.record'])),
              fail: () => resolve(false),
            });
          });
          return;
        }
        wx.authorize({
          scope: 'scope.record',
          success: () => resolve(true),
          fail: () => resolve(false),
        });
      },
      fail: () => resolve(false),
    });
  });
}

export function isRecording(): boolean { return recording; }

// 开始录音识别；返回是否成功启动（权限被拒/已在录音时为 false）
export async function start(handlers: Handlers): Promise<boolean> {
  if (recording) return false;
  const ok = await ensurePermission();
  if (!ok) {
    if (handlers.onError) handlers.onError('未获得麦克风权限');
    return false;
  }
  H = handlers;
  try {
    mgr().start({ lang: 'zh_CN', duration: 60000 });
  } catch (e) {
    H = {};
    if (handlers.onError) handlers.onError('语音插件不可用，请检查插件配置');
    return false;
  }
  return true;
}

// 正常结束：onStop 回调带回最终识别文本
export function stop(): void {
  if (recording) mgr().stop();
}

// 取消：清空回调后再 stop，onEnd 不会触发
export function cancel(): void {
  H = {};
  if (recording) mgr().stop();
}
