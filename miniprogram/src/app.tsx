import { useEffect, type PropsWithChildren } from "react";
import Taro from "@tarojs/taro";
import { clearPrivateState } from "./state";
import "./app.css";

export default function App({ children }: PropsWithChildren) {
  useEffect(() => {
    Taro.eventCenter.on("auth-cleared", clearPrivateState);
    return () => {
      Taro.eventCenter.off("auth-cleared", clearPrivateState);
    };
  }, []);
  return children;
}
