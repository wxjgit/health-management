import { Button, Switch, Text, View } from "@tarojs/components";
import { Fold } from "./components/common";

export type Policy = {
  version: string;
  ready: boolean;
  operator: string;
  contact: string;
  model_available: boolean;
  model_provider: string;
  model_name: string;
  privacy: string[];
  model_notice: string[];
  terms: string[];
};
export type Agreement = { privacy: boolean; health: boolean; model: boolean };
export const emptyAgreement: Agreement = {
  privacy: false,
  health: false,
  model: false,
};

export function Agreements({
  policy,
  value,
  onChange,
}: {
  policy: Policy;
  value: Agreement;
  onChange: (next: Agreement) => void;
}) {
  return (
    <View className="card">
      <View>运营方：{policy.operator || "尚未配置"}</View>
      <View>联系邮箱：{policy.contact || "尚未配置"}</View>
      {!policy.ready && (
        <View className="warning">
          服务尚未完成运营信息配置，暂不能建立账号。
        </View>
      )}
      <Fold title="隐私政策与用户协议">
        {[...policy.privacy, ...policy.terms].map((text, index) => (
          <View key={index} className="field">
            {text}
          </View>
        ))}
      </Fold>
      <View className="field">
        <Text>我已阅读并同意隐私政策与用户协议</Text>
        <Switch
          checked={value.privacy}
          onChange={(e) => onChange({ ...value, privacy: e.detail.value })}
        />
      </View>
      <View className="field">
        <Text>我单独同意为记录与复盘处理我自主提交的健康信息</Text>
        <Switch
          checked={value.health}
          onChange={(e) => onChange({ ...value, health: e.detail.value })}
        />
      </View>
      <Fold title="可选：外部模型数据处理说明">
        {policy.model_notice.map((text, index) => (
          <View key={index} className="field">
            {text}
          </View>
        ))}
      </Fold>
      <View className="field">
        <Text>另行同意将必要数据发送给上述外部模型（可随时撤回）</Text>
        <Switch
          checked={value.model}
          disabled={!policy.model_available}
          onChange={(e) => onChange({ ...value, model: e.detail.value })}
        />
      </View>
      <View className="muted">
        不选择模型授权仍可使用基础记录。撤回不影响此前已保存的记录。
      </View>
    </View>
  );
}
