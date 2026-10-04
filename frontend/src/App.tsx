import { CasesScreen } from "./components/screens/CasesScreen";
import { ControlsScreen } from "./components/screens/ControlsScreen";
import { NewCaseScreen } from "./components/screens/NewCaseScreen";
import { RunScreen } from "./components/screens/RunScreen";
import { Workspace } from "./components/screens/Workspace";
import { TopBar } from "./components/TopBar";
import { useRoute } from "./route";

export function App() {
  const route = useRoute();
  return (
    <>
      <TopBar route={route} />
      {route.screen === "cases" && <CasesScreen />}
      {route.screen === "new" && <NewCaseScreen />}
      {route.screen === "controls" && <ControlsScreen />}
      {route.screen === "run" && <RunScreen key={route.caseId} caseId={route.caseId} />}
      {route.screen === "case" && <Workspace key={route.caseId} caseId={route.caseId} stage={route.stage} />}
    </>
  );
}
