import { useEffect, useState } from "react";
import { useDidHide } from "@tarojs/taro";
import { Button, Text, View } from "@tarojs/components";
import type { Health, Profile } from "../../types";
import { api } from "../../api";
import {
  changed,
  mutate,
  useAction,
  usePrivateReset,
  useResource,
} from "../../state";
import {
  DateField,
  Field,
  go,
  Page,
  Select,
  Icon,
} from "../../components/common";

import { AccountPanel } from "../../account";

import FormModal from "../../components/form-modal";
import PlanSection from "../../components/plan-section";

const emptyForm = {
  age: "",
  height_cm: "",
  weight_kg: "",
  goal_weight_kg: "",
  goal_date: "",
  sex: "other",
  activity: "sedentary",
  nutrition_context: "unspecified",
};
const numericFields = [
  ["age", "年龄"],
  ["height_cm", "身高 cm"],
  ["weight_kg", "建档体重 kg"],
  ["goal_weight_kg", "目标体重 kg"],
] as const;
export default function Settings() {
  const resource = useResource(async () => {
    const [profile, health] = await Promise.all([
      api<Profile | null>("/profile"),
      api<Health>("/health"),
    ]);
    return { profile, health };
  });
  const [form, setForm] = useState(emptyForm);
  const [status, setStatus] = useState("");
  const [profileOpen, setProfileOpen] = useState(false);
  useDidHide(() => setProfileOpen(false));
  const action = useAction();
  useEffect(() => {
    const profile = resource.data?.profile;
    setForm(
      profile
        ? {
            ...profile,
            age: String(profile.age),
            height_cm: String(profile.height_cm),
            weight_kg: String(profile.weight_kg),
            goal_weight_kg: String(profile.goal_weight_kg),
            nutrition_context: profile.nutrition_context || "unspecified",
          }
        : emptyForm,
    );
  }, [resource.data]);
  usePrivateReset(() => {
    setForm(emptyForm);
    setStatus("");
    setProfileOpen(false);
  });
  async function save() {
    await action.run(async () => {
      if (
        numericFields.some(
          ([key]) => !form[key].trim() || !Number.isFinite(Number(form[key])),
        ) ||
        !form.goal_date
      )
        throw new Error("请填写完整数值和目标日期。");
      await mutate("/profile", "PUT", {
        ...form,
        age: Number(form.age),
        height_cm: Number(form.height_cm),
        weight_kg: Number(form.weight_kg),
        goal_weight_kg: Number(form.goal_weight_kg),
      });
      setStatus("档案已保存。建档体重与每日称重分开保存。");
      setProfileOpen(false);
    });
  }
  return (
    <Page
      title="我的健康空间"
      error={resource.error || (!profileOpen ? action.error : "")}
      loading={resource.loading}
      refresh={changed}
    >
      <AccountPanel />
      <View className="profile-hero">
        <View className="profile-avatar">
          <Icon name="mine" active />
        </View>
        <View>
          <View className="subtitle">我的健康档案</View>
          <View className="muted">
            {resource.data?.profile
              ? `${resource.data.profile.height_cm} cm · 目标 ${resource.data.profile.goal_weight_kg} kg`
              : "补充个人档案，积累属于你的健康记录"}
          </View>
        </View>
      </View>
      <PlanSection today={resource.data?.health.today} />
      <View className="settings-hub">
        {[
          {
            page: "facts",
            title: "长期信息",
            detail: "偏好、限制与变更历史",
            icon: "record" as const,
          },
          {
            page: "preferences",
            title: "偏好与提醒",
            detail: "定义适合自己的节奏",
            icon: "plans" as const,
          },
          {
            page: "notifications",
            title: "站内消息",
            detail: "复盘与提醒都在这里",
            icon: "history" as const,
          },
          {
            page: "privacy",
            title: "隐私管理",
            detail: "删除范围与清理恢复",
            icon: "mine" as const,
          },
        ].map(({ page, title, detail, icon }) => (
          <Button className="hub-tile" key={page} onClick={() => go(page)}>
            <View className="hub-icon">
              <Icon name={icon} active />
            </View>
            <View className="hub-title">{title}</View>
            <View className="hub-detail">{detail}</View>
            <Text className="hub-arrow">↗</Text>
          </Button>
        ))}
      </View>
      <Button
        className="plan-edit-trigger"
        onClick={() => setProfileOpen(true)}
      >
        个人档案
      </Button>
      {profileOpen && (
        <FormModal
          title="个人档案"
          busy={action.busy}
          onClose={() => setProfileOpen(false)}
          footer={
            <Button
              className="primary"
              disabled={!resource.data || action.busy}
              loading={action.busy}
              onClick={save}
            >
              保存档案
            </Button>
          }
        >
          {action.error && <View className="error">{action.error}</View>}
          {numericFields.map(([key, label]) => (
            <Field
              key={key}
              label={label}
              numeric
              value={form[key]}
              onChange={(value) => setForm({ ...form, [key]: value })}
            />
          ))}
          <Select
            label="性别系数"
            value={form.sex}
            options={{ female: "女性", male: "男性", other: "其他或不说明" }}
            onChange={(sex) => setForm({ ...form, sex })}
          />
          <Select
            label="活动水平"
            value={form.activity}
            options={{
              sedentary: "久坐",
              light: "轻度活动",
              moderate: "中度活动",
              active: "高活动量",
            }}
            onChange={(activity) => setForm({ ...form, activity })}
          />
          <Select
            label="营养适用情况"
            value={form.nutrition_context}
            options={{
              unspecified: "尚未说明",
              general: "一般成人",
              pregnant: "孕期",
              breastfeeding: "哺乳期",
              clinical: "特殊医疗营养需求",
            }}
            onChange={(nutrition_context) =>
              setForm({ ...form, nutrition_context })
            }
          />
          <DateField
            label="目标日期"
            value={form.goal_date}
            onChange={(goal_date) => setForm({ ...form, goal_date })}
          />
        </FormModal>
      )}
      {status && <View className="card">{status}</View>}
    </Page>
  );
}
