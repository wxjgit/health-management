import Taro from "@tarojs/taro";
import { Button, View } from "@tarojs/components";
import { api, baseUrl } from "./api";
import { clearPrivateState, useAction, useResource } from "./state";
import { confirm, go } from "./components/common";
import { exportMyData } from "./export";
import { removeExports } from "./privacy";

export type Session = {
  access_token: string;
  expires_at: number;
  user: { id: string };
};

export async function clearAccountData() {
  await removeExports();
  for (const key of [
    "auth-session",
    "pending-operation",
    "pending-submission",
    "deletion-state",
  ])
    Taro.removeStorageSync(key);
  clearPrivateState();
}

export async function wechatLogin(consent: {
  privacy_version: string;
  privacy_consent: true;
  health_consent: true;
  model_consent: boolean;
}) {
  await clearAccountData();
  const origin = baseUrl();
  const result = await Taro.login();
  if (!result.code) throw new Error("未取得微信登录凭证，请重试。");
  const session = await api<Session>("/auth/wechat/login", "POST", {
    code: result.code,
    ...consent,
  });
  if (baseUrl() !== origin) throw new Error("服务地址已变化，请重新登录。");
  Taro.setStorageSync("auth-session", {
    base: origin,
    token: session.access_token,
    user: session.user,
    expiresAt: session.expires_at,
  });
}

export function AccountPanel() {
  const resource = useResource(() => api<{ user: { id: string } }>("/auth/me"));
  const action = useAction();
  if (!resource.data?.user) return null;
  return (
    <View className="card">
      <View>账号：{resource.data.user.id}</View>
      <View className="muted">健康记录仅保存在当前账号下。</View>
      <Button onClick={() => go("consent")}>隐私政策与模型授权</Button>
      <Button disabled={action.busy} onClick={() => action.run(exportMyData)}>
        导出我的数据
      </Button>
      <Button
        className="danger"
        disabled={action.busy}
        onClick={() =>
          action.run(async () => {
            if (
              !(await confirm(
                "注销账号",
                "将撤销全部登录并清理本账号的档案、记录、聊天、计划和后台任务，无法撤销。运行中的操作结束后完成清理。备份按保留周期清理，自行分享的文件需自行删除。",
              ))
            )
              return;
            await removeExports();
            const result = await api<{ state: string }>(
              "/auth/account",
              "DELETE",
              { confirmation: "注销账号" },
            );
            await clearAccountData();
            await Taro.showModal({
              title: result.state === "completed" ? "账号已注销" : "注销已受理",
              content:
                result.state === "completed"
                  ? "服务端账号数据已清理。"
                  : "全部登录已撤销，后台会在运行中的操作结束后继续清理。",
              showCancel: false,
            });
            await Taro.reLaunch({ url: "/pages/login/index" });
          })
        }
      >
        注销账号并清理数据
      </Button>
      {action.error && <View className="error">{action.error}</View>}
      <Button
        disabled={action.busy}
        onClick={() =>
          action.run(async () => {
            await api("/auth/logout", "POST");
            await clearAccountData();
            await Taro.reLaunch({ url: "/pages/login/index" });
          })
        }
      >
        退出登录
      </Button>
    </View>
  );
}
