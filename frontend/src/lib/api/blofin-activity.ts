import { blofinActivity } from "./generated/client";
import type { components } from "./generated/types";

export type NativeActivityPage = components["schemas"]["ActivityPage"];
export type NativeActivityItem = components["schemas"]["ActivityItem"];
export type NativeActivityKind = NativeActivityItem["kind"];
export const readNativeActivity = blofinActivity;
