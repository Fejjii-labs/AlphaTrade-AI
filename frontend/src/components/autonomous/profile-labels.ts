/** Risk-policy profile kinds shown on the Autonomous Trading page. */
export const PROFILE_KINDS = ["internal_paper", "demo"] as const;

export const PROFILE_LABEL: Record<string, string> = {
  internal_paper: "Internal paper",
  demo: "BloFin demo",
};
