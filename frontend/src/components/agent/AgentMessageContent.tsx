import Link from "next/link";
import type { ConversationMessageRecord } from "@/lib/api/types";

const DOCUMENT_GUIDANCE =
  "Document guidance is reference data; these passages do not establish approved settings.";

/** The backend appends this limitation to visible prose. Show it under the reply. */
function withoutDocumentGuidance(text: string): { text: string; guidance: boolean } {
  if (!text.includes(DOCUMENT_GUIDANCE)) return { text, guidance: false };
  return {
    guidance: true,
    text: text
      .split(DOCUMENT_GUIDANCE)
      .join("")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .replace(/[ \t]{2,}/g, " ")
      .trim(),
  };
}

export function AgentMessageContent({
  message,
}: {
  message: Pick<ConversationMessageRecord, "role" | "content" | "payload">;
}) {
  const marker = "\n\nRecorded facts (not a confirmation):\n";
  const boundary =
    message.role === "assistant" ? message.content.indexOf(marker) : -1;
  const metadata = message.payload?.interactive_agent;
  const stored =
    metadata && typeof metadata === "object" && "recorded_evidence" in metadata
      ? metadata.recorded_evidence
      : null;
  const fullReply =
    metadata && typeof metadata === "object" && "full_reply" in metadata
      ? metadata.full_reply
      : null;
  const usage =
    metadata &&
    typeof metadata === "object" &&
    "model_usage" in metadata &&
    Array.isArray(metadata.model_usage)
      ? metadata.model_usage
      : [];
  const sources =
    metadata &&
    typeof metadata === "object" &&
    "sources" in metadata &&
    Array.isArray(metadata.sources)
      ? metadata.sources
      : [];
  const evidence =
    message.role === "assistant"
      ? typeof stored === "string"
        ? stored
        : boundary >= 0
          ? message.content.slice(boundary + marker.length)
          : null
      : null;
  const explanation =
    message.role === "assistant" &&
    typeof fullReply === "string" &&
    fullReply !== message.content.split(marker, 1)[0]
      ? fullReply
      : null;
  const journalAction = sources.find(
    (source) =>
      source &&
      typeof source === "object" &&
      "artifact_kind" in source &&
      source.artifact_kind === "journal_entry" &&
      "relation" in source &&
      source.relation === "recorded trade lineage" &&
      "record_id" in source &&
      typeof source.record_id === "string",
  );
  const manualAction = sources.find(
    (source) =>
      source &&
      typeof source === "object" &&
      "relation" in source &&
      source.relation === "manual demo command" &&
      "record_id" in source &&
      typeof source.record_id === "string",
  );
  const action = journalAction ?? manualAction;
  const rawVisible =
    boundary >= 0 ? message.content.slice(0, boundary) : message.content;
  const peeled =
    message.role === "assistant"
      ? withoutDocumentGuidance(rawVisible)
      : { text: rawVisible, guidance: false };
  return (
    <>
      {peeled.text ? (
        <p className="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
          {peeled.text}
        </p>
      ) : null}
      {peeled.guidance ? (
        <p className="mt-2 text-xs text-text-muted">{DOCUMENT_GUIDANCE}</p>
      ) : null}
      {sources.some(
        (source) =>
          source &&
          typeof source === "object" &&
          "relation" in source &&
          source.relation === "manual demo choice",
      ) && (
        <ul
          aria-label="Matching manual demo attempts"
          className="mt-2 space-y-2"
        >
          {sources.map((source, index) => {
            if (
              !source ||
              typeof source !== "object" ||
              !("relation" in source) ||
              source.relation !== "manual demo choice" ||
              !("record_id" in source) ||
              typeof source.record_id !== "string"
            )
              return null;
            const title =
              "title" in source && typeof source.title === "string"
                ? source.title
                : "Open matching attempt";
            return (
              <li key={`${source.record_id}-${index}`}>
                <Link
                  className="underline"
                  href={`/execution/manual-demo/${encodeURIComponent(source.record_id)}`}
                >
                  {title}
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      {action &&
      typeof action === "object" &&
      "record_id" in action &&
      typeof action.record_id === "string" ? (
        <Link
          className="mt-2 inline-flex min-h-11 items-center text-sm text-accent underline"
          href={
            journalAction
              ? `/journal?trade_id=${encodeURIComponent(action.record_id)}`
              : `/execution/manual-demo/${encodeURIComponent(action.record_id)}`
          }
        >
          {journalAction ? "Open Journal detail" : "Open this exact attempt"}
        </Link>
      ) : null}
      {evidence !== null ||
      explanation !== null ||
      sources.length > 0 ||
      usage.length > 0 ? (
        <details className="mt-3 border-t border-border-subtle pt-2">
          <summary className="cursor-pointer rounded-control text-xs text-text-secondary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2">
            Stored evidence
          </summary>
          {explanation ? (
            <p className="mt-2 whitespace-pre-wrap break-words text-xs [overflow-wrap:anywhere]">
              Full explanation (not execution authority):{"\n"}
              {explanation}
            </p>
          ) : null}
          {evidence !== null ? (
            <p className="mt-2 whitespace-pre-wrap break-words text-xs [overflow-wrap:anywhere]">
              {evidence}
            </p>
          ) : null}
          {sources.length > 0 ? (
            <ul
              aria-label="Source references"
              className="mt-2 space-y-1 text-xs [overflow-wrap:anywhere]"
            >
              {sources.map((source, index) => {
                if (!source || typeof source !== "object") return null;
                const title =
                  "title" in source && typeof source.title === "string"
                    ? source.title
                    : "Stored record";
                const identity =
                  "record_id" in source && typeof source.record_id === "string"
                    ? source.record_id
                    : "unavailable";
                const manualCommand =
                  "relation" in source &&
                  (source.relation === "manual demo choice" ||
                    source.relation === "manual demo command");
                return (
                  <li key={`${identity}-${index}`}>
                    {"relation" in source &&
                    source.relation === "saved user note" ? (
                      <Link
                        href={`/journal?saved=${encodeURIComponent(identity)}`}
                        className="underline"
                      >
                        {title}: {identity}
                      </Link>
                    ) : manualCommand ? (
                      <Link
                        className="underline"
                        href={`/execution/manual-demo/${encodeURIComponent(identity)}`}
                      >
                        {title}: {identity}
                      </Link>
                    ) : (
                      <>
                        {title}: {identity}
                      </>
                    )}
                  </li>
                );
              })}
            </ul>
          ) : null}
          {usage.length ? (
            <pre className="mt-2 overflow-auto whitespace-pre-wrap text-xs">
              Model usage · cost unavailable unless reported or explicitly
              estimated{"\n"}
              {JSON.stringify(usage, null, 2)}
            </pre>
          ) : null}
        </details>
      ) : null}
    </>
  );
}
