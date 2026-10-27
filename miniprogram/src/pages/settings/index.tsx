import { useEffect, useState } from "react";
import Taro from "@tarojs/taro";
import { Button, Text, View } from "@tarojs/components";
import type { Health, Profile } from "../../types";
import { api, baseUrl } from "../../api";
import {
  changed,
  clearPrivateState,
  exclusive,
  mutate,
  pendingOperation,
  privacyGeneration,
  useAction,
  usePrivateReset,
  useResource,
} from "../../state";
import { deletionState, exportPaths, removeExports } from "../../privacy";
import {
  confirm,
  DateField,
  Field,
  Fold,
  go,
  Page,
  Select,
  Icon,
} from "../../components/common";

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
  const [url, setUrl] = useState(baseUrl());
  const [status, setStatus] = useState("");
  const [files, setFiles] = useState(exportPaths);
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
    setFiles([]);
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
    });
  }
  async function exportData() {
    await action.run(() =>
      exclusive(async () => {
        if (pendingOperation() || deletionState()?.result?.state === "pending")
          throw new Error("请先完成待处理请求和隐私清理。");
        if (
          !(await confirm(
            "导出健康数据",
            "将生成含个人健康信息的 JSON 文件。分享或保存到其他位置的副本不受应用隐私删除控制。",
          ))
        )
          return;
        const epoch = privacyGeneration();
        const response = await Taro.downloadFile({
          url: `${baseUrl()}/api/data/export`,
          timeout: 125000,
        });
        try {
          if (response.statusCode !== 200)
            throw new Error("导出失败，请检查服务状态。");
          if (epoch !== privacyGeneration())
            throw new Error("隐私状态已变化，请重新导出。");
          const path = `${Taro.env.USER_DATA_PATH}/qingji-export-${Date.now()}.json`;
          const next = [...exportPaths(), path];
          Taro.setStorageSync("export-files", next);
          await Taro.getFileSystemManager().copyFile({
            srcPath: response.tempFilePath,
            destPath: path,
          });
          setFiles(next);
          setStatus("导出成功，可以分享文件或清理应用内副本。");
        } finally {
          await Taro.getFileSystemManager().unlink({
            filePath: response.tempFilePath,
          });
        }
      }),
    );
  }
  return (
    <Page
      title="我的健康空间"
      error={action.error || resource.error}
      loading={resource.loading}
      refresh={resource.refresh}
    >
      <View className="profile-hero">
        <View className="profile-avatar">
          <Icon name="mine" active />
        </View>
        <View>
          <View className="section-kicker">MY HEALTH JOURNAL</View>
          <View className="subtitle">照顾自己，从了解自己开始</View>
          <View className="muted">
            {resource.data?.profile
              ? `${resource.data.profile.height_cm} cm · 目标 ${resource.data.profile.goal_weight_kg} kg`
              : "补充个人档案，积累属于你的健康记录"}
          </View>
        </View>
      </View>
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
            title: "权重与提醒",
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
      <Fold title="个人档案">
        <View className="card">
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
          <Button
            className="primary"
            disabled={!resource.data || action.busy}
            loading={action.busy}
            onClick={save}
          >
            保存档案
          </Button>
        </View>
      </Fold>
      <View className="card">
        <View className="subtitle">数据属于你</View>
        <View className="muted">
          原始记录保留一年，更早按月归档；导出不含模型密钥。
        </View>
        <Button disabled={action.busy} onClick={exportData}>
          导出数据 JSON
        </Button>
        {files.map((path) => (
          <Button
            key={path}
            disabled={action.busy}
            onClick={() =>
              action.run(() =>
                Taro.shareFileMessage({
                  filePath: path,
                  fileName: "轻记健康记录.json",
                }),
              )
            }
          >
            分享 {path.split("/").pop()}
          </Button>
        ))}
        {!!files.length && (
          <Button
            disabled={action.busy}
            onClick={() =>
              action.run(async () => {
                await removeExports();
                setFiles([]);
                setStatus("应用内导出副本已清理。");
              })
            }
          >
            清理应用内导出副本
          </Button>
        )}
      </View>
      <Fold title="开发服务配置">
        <View className="card">
          <Field label="API 地址（不带 /api）" value={url} onChange={setUrl} />
          <View className="muted">
            当前是单用户开发版，后端尚未接入登录鉴权。仅在可信网络中联调。
          </View>
          <Button
            disabled={action.busy}
            onClick={() =>
              action.run(async () => {
                const next = url.trim().replace(/\/+$/, "");
                if (!/^https?:\/\/[^/\s?#]+$/.test(next))
                  throw new Error("请输入不含路径的 HTTP 或 HTTPS 地址。");
                if (
                  pendingOperation() ||
                  deletionState()?.result?.state === "pending"
                )
                  throw new Error("请先在原服务完成待处理请求和隐私清理。");
                await removeExports();
                clearPrivateState();
                Taro.removeStorageSync("deletion-state");
                Taro.setStorageSync("api-base", next);
                changed();
                setStatus("地址已保存，正在读取新服务。");
              })
            }
          >
            保存地址
          </Button>
          <View className="muted">
            运行模式：
            {resource.data?.health.local_only
              ? "服务器强制纯本地"
              : resource.data?.health.mode === "model"
                ? `模型 ${resource.data.health.model_name}`
                : "演示服务"}
            。模型密钥仅由服务端管理。
          </View>
        </View>
      </Fold>
      {status && <View className="card">{status}</View>}
    </Page>
  );
}
