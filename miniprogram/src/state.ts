import { useCallback, useEffect, useRef, useState } from "react";
import Taro, { useDidHide, useDidShow } from "@tarojs/taro";
import { api, ApiError, baseUrl, errorText, type Method } from "./api";
import { deletionState, removeExports, saveDeletionState } from "./privacy";
import type { DeletionResult } from "./types";

type Listener = () => void;
const listeners = new Set<Listener>();
const privateListeners = new Set<Listener>();
let generation = 0;
let writing = false;
export const changed = () => listeners.forEach((listener) => listener());
export function clearPrivateState() {
  generation++;
  Taro.removeStorageSync("correction-event");
  Taro.removeStorageSync("evidence-event");
  privateListeners.forEach((listener) => listener());
}
export const privacyGeneration = () => generation;

export function privacyBlocked() {
  const deletion = deletionState();
  return (
    (deletion?.base === baseUrl() && deletion.result?.state === "pending") ||
    pendingOperation()?.path === "/deletions"
  );
}

export function useChange(callback: Listener) {
  const current = useRef(callback);
  current.current = callback;
  useEffect(() => {
    const listener = () => current.current();
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);
}

export function usePrivateReset(reset: Listener) {
  const callback = useRef(reset);
  callback.current = reset;
  useEffect(() => {
    const listener = () => callback.current();
    privateListeners.add(listener);
    return () => {
      privateListeners.delete(listener);
    };
  }, []);
}

export function useResource<Result>(load: () => Promise<Result>) {
  const [data, setData] = useState<Result | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const loader = useRef(load);
  loader.current = load;
  const sequence = useRef(0);
  const visible = useRef(false);
  const refresh = useCallback(async () => {
    const current = ++sequence.current;
    const epoch = generation;
    if (privacyBlocked()) {
      setData(null);
      setError("隐私清理结果尚待核对，请在我的 → 隐私清理中恢复。");
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const result = await loader.current();
      if (current === sequence.current && epoch === generation) {
        setData(result);
        setError("");
      }
    } catch (reason) {
      if (current === sequence.current && epoch === generation) {
        setData(null);
        setError(errorText(reason));
      }
    } finally {
      if (current === sequence.current) setLoading(false);
    }
  }, []);
  useDidShow(() => {
    visible.current = true;
    void refresh();
  });
  useDidHide(() => {
    visible.current = false;
    sequence.current++;
    setLoading(false);
  });
  useEffect(() => {
    const update = () => {
      if (visible.current) void refresh();
    };
    listeners.add(update);
    return () => {
      listeners.delete(update);
      sequence.current++;
    };
  }, [refresh]);
  usePrivateReset(() => {
    sequence.current++;
    setData(null);
    setError("");
    setLoading(false);
  });
  return { data, error, loading, refresh };
}

export type Operation = {
  base: string;
  path: string;
  body: Record<string, unknown>;
  label: string;
};
const operationKey = "pending-operation";
export const pendingOperation = (): Operation | null => {
  const pending = Taro.getStorageSync<Operation>(operationKey);
  if (pending) return pending;
  const previous = Taro.getStorageSync<{
    path: string;
    body: Record<string, unknown>;
  }>("pending-submission");
  if (!previous) return null;
  const migrated = {
    base: baseUrl(),
    ...previous,
    label: "首期版本待核对记录",
  };
  Taro.setStorageSync(operationKey, migrated);
  Taro.removeStorageSync("pending-submission");
  return migrated;
};
export const operation = (
  path: string,
  body: Record<string, unknown>,
  label: string,
): Operation => ({ base: baseUrl(), path, body, label });
export async function executeOperation<Result>(
  next: Operation,
): Promise<Result> {
  if (writing) throw new Error("已有操作正在执行，请稍候。");
  if (privacyBlocked() && next.path !== "/deletions")
    throw new Error("请先完成隐私清理。");
  const previous = pendingOperation();
  if (previous && JSON.stringify(previous) !== JSON.stringify(next))
    throw new Error("请先处理待核对的原请求。");
  if (next.base !== baseUrl())
    throw new Error("待重试请求属于另一服务，请切回原地址。");
  writing = true;
  try {
    if (next.body.processing_mode === "model_allowed") {
      if (Taro.getStorageSync("local-only") !== false)
        throw new Error(
          "原请求使用模型模式。请先核对原请求结果；如需重试，需主动允许模型处理。",
        );
      const health = await api<{ local_only: boolean }>("/health");
      if (health.local_only)
        throw new Error(
          "服务已强制纯本地模式，不能重发原模型请求。请核对服务端结果。",
        );
    }
    Taro.setStorageSync(operationKey, next);
    if (next.path === "/deletions") {
      await removeExports();
      clearPrivateState();
    }
    changed();
    const result = await api<Result>(next.path, "POST", next.body);
    if (next.path === "/deletions") {
      const state = deletionState();
      if (state)
        saveDeletionState({ ...state, result: result as DeletionResult });
      clearPrivateState();
    }
    Taro.removeStorageSync(operationKey);
    return result;
  } catch (reason) {
    if (next.path === "/deletions") clearPrivateState();
    if (reason instanceof ApiError && reason.preview) {
      const state = deletionState();
      if (state)
        saveDeletionState({
          ...state,
          preview: reason.preview,
          requestId: "",
          result: undefined,
        });
      Taro.removeStorageSync(operationKey);
    }
    if (
      reason instanceof ApiError &&
      [400, 404, 410, 413, 415, 422, 428].includes(reason.status)
    )
      Taro.removeStorageSync(operationKey);
    throw reason;
  } finally {
    writing = false;
    changed();
  }
}

export async function mutate<Result>(
  path: string,
  method: Method,
  body?: object,
): Promise<Result> {
  return exclusive(
    () => api<Result>(path, method, body),
    path.startsWith("/deletions/"),
  );
}

export async function exclusive<Result>(
  task: () => Promise<Result>,
  privacyRecovery = false,
): Promise<Result> {
  if (writing || pendingOperation())
    throw new Error("请先完成或核对待处理请求。");
  if (privacyBlocked() && !privacyRecovery)
    throw new Error("请先完成隐私清理。");
  writing = true;
  try {
    return await task();
  } finally {
    writing = false;
    changed();
  }
}

export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const lock = useRef(false);
  async function run(task: () => Promise<unknown>): Promise<boolean> {
    if (lock.current) return false;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      await task();
      return true;
    } catch (reason) {
      setError(errorText(reason));
      return false;
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  usePrivateReset(() => setError(""));
  return { busy, error, run, setError };
}

export function useOperation() {
  const [pending, setPending] = useState(pendingOperation);
  useEffect(() => {
    const listener = () => setPending(pendingOperation());
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);
  useDidShow(() => setPending(pendingOperation()));
  return pending;
}

export function acknowledgeOperation() {
  if (writing) throw new Error("操作仍在执行，请稍候。");
  if (pendingOperation()?.path === "/deletions")
    throw new Error("删除请求需要在隐私清理中核对结果。");
  Taro.removeStorageSync(operationKey);
  changed();
}

export function useForegroundPoll(
  enabled: boolean,
  refresh: () => Promise<void>,
  interval = 2000,
) {
  const visible = useRef(true);
  const running = useRef(false);
  useDidShow(() => {
    visible.current = true;
  });
  useDidHide(() => {
    visible.current = false;
  });
  useEffect(() => {
    if (!enabled) return;
    const timer = setInterval(async () => {
      if (!visible.current || running.current) return;
      running.current = true;
      try {
        await refresh();
      } finally {
        running.current = false;
      }
    }, interval);
    return () => clearInterval(timer);
  }, [enabled, refresh, interval]);
}
