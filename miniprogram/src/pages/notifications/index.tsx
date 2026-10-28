import { Button, View } from "@tarojs/components";
import type { Notice } from "../../types";
import { api } from "../../api";
import { mutate, useAction, useForegroundPoll, useResource } from "../../state";
import { Page } from "../../components/common";

export default function Notifications() {
  const resource = useResource(() => api<Notice[]>("/notifications"));
  const action = useAction();
  useForegroundPoll(true, resource.refresh, 60000);
  return (
    <Page
      title="站内消息"
      error={action.error || resource.error}
      loading={resource.loading}
      refresh={resource.refresh}
    >
      <View className="muted">
        在这里查看提醒和计划更新。
      </View>
      {resource.data?.map((notice) => (
        <View className="card" key={notice.id}>
          <View className="subtitle">
            {!notice.read && "● "}
            {notice.title}
          </View>
          <View className="message">{notice.body}</View>
          <View className="muted">{notice.created_at}</View>
          {!notice.read && (
            <Button
              disabled={action.busy}
              onClick={() =>
                action.run(() => mutate(`/notifications/${notice.id}`, "PATCH"))
              }
            >
              标为已读
            </Button>
          )}
        </View>
      ))}
      {resource.data?.length === 0 && <View className="card">暂无消息。</View>}
    </Page>
  );
}
