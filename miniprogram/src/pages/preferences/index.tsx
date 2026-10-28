import { useEffect, useState } from "react";
import { Button, Switch, View } from "@tarojs/components";
import type { Preferences } from "../../types";
import { api } from "../../api";
import { mutate, useAction, usePrivateReset, useResource } from "../../state";
import {
  DateField,
  Field,
  Fold,
  labels,
  Page,
  Select,
} from "../../components/common";

type Suggestion = {
  weights: Record<string, number>;
  reason: string;
  limitations: string;
};
export default function PreferencesPage() {
  const resource = useResource(async () => {
    const [preferences, suggestion] = await Promise.all([
      api<Preferences>("/preferences"),
      api<Suggestion | null>("/preferences/suggestion"),
    ]);
    return { preferences, suggestion };
  });
  const [draft, setDraft] = useState<Preferences | null>(null);
  const [weights, setWeights] = useState<Record<string, string>>({});
  const action = useAction();
  useEffect(() => {
    if (!resource.data) return;
    setDraft(resource.data.preferences);
    setWeights(
      Object.fromEntries(
        Object.entries(resource.data.preferences.weights).map(
          ([key, value]) => [key, String(value)],
        ),
      ),
    );
  }, [resource.data]);
  usePrivateReset(() => {
    setDraft(null);
    setWeights({});
  });
  return (
    <Page
      title="权重与提醒"
      loading={resource.loading}
      error={action.error || resource.error}
      refresh={resource.refresh}
    >
      {draft && (
        <>
          <View className="card compact-fold-card">
            <Fold title="调整健康评分权重">
            <View className="muted">
              总和必须为100%。缺少个人数据的维度保持未知。
            </View>
            {Object.keys(draft.weights).map((key) => (
              <Field
                key={key}
                label={`${labels[key]} %`}
                value={weights[key] || ""}
                numeric
                onChange={(value) => setWeights({ ...weights, [key]: value })}
              />
            ))}
            <View>
              当前合计：
              {Object.values(weights).reduce(
                (total, value) => total + Number(value || 0),
                0,
              )}
              %
            </View>
            {resource.data?.suggestion && (
              <View className="inset">
                <View>{resource.data.suggestion.reason}</View>
                <View className="muted">
                  {resource.data.suggestion.limitations}
                </View>
                <Button
                  onClick={() => {
                    const suggestion = resource.data?.suggestion;
                    if (suggestion)
                      setWeights(
                        Object.fromEntries(
                          Object.entries(suggestion.weights).map(
                            ([key, value]) => [key, String(value)],
                          ),
                        ),
                      );
                  }}
                >
                  填入权重建议（保存后生效）
                </Button>
              </View>
            )}
            </Fold>
          </View>
          <View className="card">
            <View className="subtitle">提醒与复盘</View>
            <View>
              开启站内提醒{" "}
              <Switch
                checked={draft.notifications_enabled}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    notifications_enabled: event.detail.value,
                  })
                }
              />
            </View>
            {Object.entries(draft.meal_times).map(([key, value]) => (
              <DateField
                mode="time"
                key={key}
                label={`${labels[key]}时间`}
                value={value}
                onChange={(next) =>
                  setDraft({
                    ...draft,
                    meal_times: { ...draft.meal_times, [key]: next },
                  })
                }
              />
            ))}
            <DateField
              mode="time"
              label="身体状态提醒"
              value={draft.checkin_time}
              onChange={(checkin_time) => setDraft({ ...draft, checkin_time })}
            />
            <DateField
              mode="time"
              label="每日总结"
              value={draft.summary_time}
              onChange={(summary_time) => setDraft({ ...draft, summary_time })}
            />
            <Select
              label="每周复盘"
              value={String(draft.weekly_day)}
              options={{
                "0": "周一",
                "1": "周二",
                "2": "周三",
                "3": "周四",
                "4": "周五",
                "5": "周六",
                "6": "周日",
              }}
              onChange={(value) =>
                setDraft({ ...draft, weekly_day: Number(value) })
              }
            />
            <View className="muted">
              时区
              Asia/Shanghai。服务运行时生成站内消息，关闭小程序不等于微信订阅推送。
            </View>
          </View>
          <Button
            className="primary"
            loading={action.busy}
            disabled={action.busy}
            onClick={() =>
              action.run(async () => {
                if (
                  Object.values(weights).some(
                    (value) =>
                      !value.trim() ||
                      !Number.isFinite(Number(value)) ||
                      Number(value) < 0 ||
                      Number(value) > 100,
                  )
                )
                  throw new Error("权重必须是0到100的数值。");
                const values = Object.fromEntries(
                  Object.entries(weights).map(([key, value]) => [
                    key,
                    Number(value),
                  ]),
                );
                if (
                  Math.abs(
                    Object.values(values).reduce(
                      (total, value) => total + value,
                      0,
                    ) - 100,
                  ) > 0.01
                )
                  throw new Error("权重合计必须为100%。");
                await mutate("/preferences", "PUT", {
                  ...draft,
                  weights: values,
                });
              })
            }
          >
            保存设置
          </Button>
        </>
      )}
    </Page>
  );
}
