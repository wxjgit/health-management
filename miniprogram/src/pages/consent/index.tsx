import { useEffect, useState } from "react";
import { Button, View } from "@tarojs/components";
import { api } from "../../api";
import { Agreements, emptyAgreement, type Policy } from "../../agreements";
import { Page } from "../../components/common";
import { changed, useAction, useResource } from "../../state";

export default function Consent() {
  const resource = useResource(async () => {
    const [policy, consent] = await Promise.all([
      api<Policy>("/policies"),
      api<{ privacy_accepted: boolean; model_allowed: boolean }>(
        "/auth/consent",
      ),
    ]);
    return { policy, consent };
  });
  const [agreement, setAgreement] = useState(emptyAgreement);
  const [saved, setSaved] = useState(false);
  const action = useAction();
  useEffect(() => {
    if (resource.data)
      setAgreement({
        privacy: resource.data.consent.privacy_accepted,
        health: resource.data.consent.privacy_accepted,
        model: resource.data.consent.model_allowed,
      });
  }, [resource.data]);
  return (
    <Page
      title="隐私与模型授权"
      error={resource.error || action.error}
      loading={resource.loading}
    >
      {resource.data && (
        <>
          <Agreements
            policy={resource.data.policy}
            value={agreement}
            onChange={(v) => {
              setAgreement(v);
              setSaved(false);
            }}
          />
          <Button
            className="primary"
            disabled={
              !agreement.privacy ||
              !agreement.health ||
              !resource.data.policy.ready ||
              action.busy
            }
            onClick={() =>
              action.run(async () => {
                await api("/auth/consent", "PUT", {
                  privacy_version: resource.data!.policy.version,
                  privacy_consent: true,
                  health_consent: true,
                  model_consent: agreement.model,
                });
                setSaved(true);
                changed();
              })
            }
          >
            保存授权选择
          </Button>
          <View className="muted">
            如不再同意本服务处理健康信息，可在“我的”注销账号并清理数据。
          </View>
          {saved && <View>授权选择已保存。</View>}
        </>
      )}
    </Page>
  );
}
