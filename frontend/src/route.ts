import { useEffect, useState } from "react";
import { STAGE_BY_ID } from "./catalogue";
import type { RailStageId } from "./types";

/** Screens are addressed by the URL hash, so a demo can link straight to a stage. The home screen is a new case. */
export type Route =
  | { screen: "cases" }
  | { screen: "new" }
  | { screen: "controls" }
  | { screen: "run"; caseId: string }
  | { screen: "case"; caseId: string; stage: RailStageId };

/** A case opens at the first stage, so a walkthrough starts at the beginning */
export const DEFAULT_STAGE: RailStageId = "ocr";

export function parse(hash: string): Route {
  const [first, caseId, rest] = hash.replace(/^#\/?/, "").split("/");
  if (first === "controls") return { screen: "controls" };
  if (first === "cases" && caseId) {
    if (rest === "run") return { screen: "run", caseId };
    const stage = rest && rest in STAGE_BY_ID ? (rest as RailStageId) : DEFAULT_STAGE;
    return { screen: "case", caseId, stage };
  }
  if (first === "cases") return { screen: "cases" };
  return { screen: "new" };
}

export function href(route: Route): string {
  switch (route.screen) {
    case "run":
      return `#/cases/${route.caseId}/run`;
    case "case":
      return `#/cases/${route.caseId}/${route.stage}`;
    default:
      return `#/${route.screen}`;
  }
}

export const navigate = (route: Route) => {
  window.location.hash = href(route);
};

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parse(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
