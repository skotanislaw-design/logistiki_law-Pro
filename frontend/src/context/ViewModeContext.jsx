import { createContext, useContext, useState } from "react";

const ViewModeContext = createContext(null);

export function ViewModeProvider({ children }) {
  const [oneScreen, setOneScreen] = useState(() => localStorage.getItem("nomos_view_mode") === "one");
  const toggle = () =>
    setOneScreen((v) => {
      localStorage.setItem("nomos_view_mode", v ? "classic" : "one");
      return !v;
    });
  return <ViewModeContext.Provider value={{ oneScreen, toggle }}>{children}</ViewModeContext.Provider>;
}

export const useViewMode = () => useContext(ViewModeContext);
