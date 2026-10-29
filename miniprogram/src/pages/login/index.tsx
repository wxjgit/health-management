import { useState } from "react";
import { api } from "../../api";
import { Agreements, emptyAgreement, type Policy } from "../../agreements";
import Taro from "@tarojs/taro";
import { Button, View } from "@tarojs/components";
import { wechatLogin } from "../../account";
import { useAction, useResource } from "../../state";

export default function Login() {
  const action = useAction();
  const resource = useResource(() => api<Policy>("/policies"));
  const [agreement, setAgreement] = useState(emptyAgreement);
  return (
    <View className="card">
      <View className="subtitle">登录轻记</View>
      <View>使用微信身份建立账号，不读取微信昵称、头像或手机号。</View>
      <View className="muted">登录后记录和查看自己的健康数据。</View>
      {resource.error && <View className="error">{resource.error}</View>}
      {resource.data && (
        <Agreements
          policy={resource.data}
          value={agreement}
          onChange={setAgreement}
        />
      )}
      {action.error && <View className="error">{action.error}</View>}
      <Button
        className="primary"
        loading={action.busy}
        disabled={
          action.busy ||
          !resource.data?.ready ||
          !agreement.privacy ||
          !agreement.health
        }
        onClick={() =>
          action.run(async () => {
            await wechatLogin({
              privacy_version: resource.data!.version,
              privacy_consent: true,
              health_consent: true,
              model_consent: agreement.model,
            });
            await Taro.reLaunch({ url: "/pages/settings/index" });
          })
        }
      >
        微信登录
      </Button>
    </View>
  );
}
