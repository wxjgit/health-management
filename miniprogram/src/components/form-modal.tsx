import { useEffect, type PropsWithChildren, type ReactNode } from "react";
import Taro from "@tarojs/taro";
import { Button, RootPortal, ScrollView, View } from "@tarojs/components";

export default function FormModal({
  title,
  busy = false,
  onClose,
  footer,
  className = "",
  children,
}: PropsWithChildren<{
  title: string;
  busy?: boolean;
  onClose: () => void;
  footer: ReactNode;
  className?: string;
}>) {
  const page = Taro.getCurrentInstance().page;
  useEffect(() => {
    const tabBar = page?.getTabBar?.();
    tabBar?.setData?.({ hidden: true });
    return () => tabBar?.setData?.({ hidden: false });
  }, [page]);
  return (
    <RootPortal>
      <View className="plan-modal" catchMove>
        <View
          className="plan-modal-backdrop"
          onClick={() => {
            if (!busy) onClose();
          }}
        />
        <View
          className={`plan-modal-panel ${className}`}
          role="dialog"
          aria-label={title}
        >
          <View className="plan-modal-header">
            <View className="subtitle">{title}</View>
            <Button
              className="plan-modal-close"
              size="mini"
              disabled={busy}
              onClick={onClose}
            >
              关闭
            </Button>
          </View>
          <ScrollView scrollY className="plan-modal-body">
            {children}
          </ScrollView>
          <View className="plan-modal-footer">{footer}</View>
        </View>
      </View>
    </RootPortal>
  );
}
