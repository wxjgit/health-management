const { call } = require("./platform");
const labels = {
  meal: "饮食",
  weight: "体重",
  sleep: "睡眠",
  exercise: "运动",
  cycle: "周期",
  kcal: "热量",
  protein_g: "蛋白质",
  carbs_g: "碳水",
  fat_g: "脂肪",
  fiber_g: "膳食纤维",
  exercise_minutes: "每周运动",
  energy: "热量适配",
  macros: "营养适配",
  satiety: "饱腹感",
  adherence: "可坚持性",
  snacking: "加餐风险",
  breakfast: "早餐",
  lunch: "午餐",
  dinner: "晚餐",
};
const numberText = (value, digits = 1) =>
  value == null ? "未知" : String(Number(Number(value).toFixed(digits)));
const metrics = (targets) =>
  Object.entries(targets || {})
    .filter(([key]) =>
      [
        "kcal",
        "protein_g",
        "carbs_g",
        "fat_g",
        "exercise_minutes",
        "fiber_g",
      ].includes(key),
    )
    .map(([key, value]) => ({
      key,
      label: labels[key],
      text: numberText(value),
      unit:
        key === "kcal" ? "kcal" : key === "exercise_minutes" ? "分钟/周" : "g",
    }));
const confirmAction = async (title, content) =>
  (
    await call("showModal", {
      title,
      content,
      confirmText: "确认",
      cancelText: "取消",
    })
  ).confirm;
const tabs = ["chat", "history", "insights", "settings"];
function go(page) {
  const [name, query] = page.split("?");
  return call(tabs.includes(name) ? "switchTab" : "navigateTo", {
    url: `/pages/${name}/index${query ? "?" + query : ""}`,
  });
}
const fieldRows = (values, config) =>
  Object.entries(config).map(([key, setting]) => ({
    key,
    value: values[key] ?? "",
    ...(typeof setting === "string" ? { label: setting } : setting),
  }));
const schema = {
  profile: {
    age: { label: "年龄", type: "digit" },
    height_cm: { label: "身高 cm", type: "digit" },
    weight_kg: { label: "建档体重 kg", type: "digit" },
    goal_weight_kg: { label: "目标体重 kg", type: "digit" },
    sex: {
      label: "性别系数",
      type: "select",
      options: [
        { value: "female", label: "女性" },
        { value: "male", label: "男性" },
        { value: "other", label: "其他或不说明" },
      ],
    },
    activity: {
      label: "活动水平",
      type: "select",
      options: [
        { value: "sedentary", label: "久坐" },
        { value: "light", label: "轻度活动" },
        { value: "moderate", label: "中度活动" },
        { value: "active", label: "高活动量" },
      ],
    },
    nutrition_context: {
      label: "营养适用情况",
      type: "select",
      options: [
        { value: "unspecified", label: "尚未说明" },
        { value: "general", label: "一般成人" },
        { value: "pregnant", label: "孕期" },
        { value: "breastfeeding", label: "哺乳期" },
        { value: "clinical", label: "特殊医疗营养需求" },
      ],
    },
    goal_date: { label: "目标日期", type: "date" },
  },
};
module.exports = {
  labels,
  numberText,
  metrics,
  confirmAction,
  go,
  fieldRows,
  schema,
};
