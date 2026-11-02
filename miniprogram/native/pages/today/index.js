const { api } = require("../../native-runtime/api");
const { mutate } = require("../../native-runtime/state");
const { registerPage } = require("../../native-runtime/page");
const { numberText, labels } = require("../../native-runtime/ui");
registerPage({
  data: { daily: null },
  async load() {
    const daily = await api("/dashboard");
    return {
      daily: {
        ...daily,
        weightText: numberText(daily.weight_kg),
        sleepText: numberText(daily.sleep_minutes),
        events: (daily.events || []).map((e) => ({
          ...e,
          label: labels[e.kind],
        })),
      },
    };
  },
  onComplete() {
    if (!this.data.daily) return;
    return this.run(() =>
      mutate(`/days/${this.data.daily.date}/complete`, "PUT", {
        complete: !this.data.daily.diet_complete,
      }),
    );
  },
});
