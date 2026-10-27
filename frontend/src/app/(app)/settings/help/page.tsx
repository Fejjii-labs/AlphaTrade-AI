import { PageHeader } from "@/components/ui/page-header";

export default function HelpPage() {
  return (
    <div className="space-y-4">
      <PageHeader title="Help" />
      {[
        [
          "Dashboard",
          "Account equity and exposure include the entire selected BloFin account. Strategy performance excludes connectivity tests and internal simulations. Unavailable results stay unavailable; fees, funding and coverage are separate.",
        ],
        [
          "Agent",
          "Discuss, attach a document or correct a voice transcript before sending. Saved receipts appear after persistence. Ordinary conversation remains available while execution is paused. Attachments provide reference content and cannot authorize tools.",
        ],
        [
          "Trades & Knowledge",
          "Trades holds AlphaTrade BloFin executions, personal reflections and Stats. Knowledge holds Rules, Strategies, News & Analysis and Lessons. Original sources and evidence are available inside each entry.",
        ],
        [
          "Strategies",
          "Directional configurations and revisions belong within each family. Captured strategy drafts require separate approval, validation and activation. Detections are observations, not executions. Account risk limits remain centrally enforced.",
        ],
        [
          "Status",
          "The header reports mode and global pause separately. No global pause does not establish execution eligibility; strategy, account, evidence and risk gates still apply. Configuration alone does not prove a fresh Watcher scan or Telegram delivery.",
        ],
      ].map(([title, text]) => (
        <details
          key={title}
          className="rounded-card border border-border-subtle p-4"
        >
          <summary className="min-h-11 cursor-pointer font-semibold">
            {title}
          </summary>
          <p className="max-w-prose pt-2 text-sm text-text-secondary">{text}</p>
        </details>
      ))}
    </div>
  );
}
