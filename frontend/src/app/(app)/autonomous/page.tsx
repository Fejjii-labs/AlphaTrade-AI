"use client";

import { BotSettings } from "@/components/autonomous/BotSettings";
import { MyBloFinTrades } from "@/components/autonomous/MyBloFinTrades";
import { SessionScoped } from "@/components/settings/SessionScoped";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";

/** Bot: my own BloFin demo trades, and the autonomous bot. */
export default function AutonomousPage() {
  return (
    <SessionScoped>
      <div className="page-sections" data-testid="autonomous-page">
        <PageHeader title="Bot" />
        <Card id="my-trades" className="scroll-mt-4">
          <CardHeader>
            <CardTitle>My BloFin trades</CardTitle>
          </CardHeader>
          <CardContent>
            <MyBloFinTrades />
          </CardContent>
        </Card>
        <Card id="autonomous" className="scroll-mt-4">
          <CardHeader>
            <CardTitle>Autonomous trading</CardTitle>
          </CardHeader>
          <CardContent>
            <BotSettings />
          </CardContent>
        </Card>
      </div>
    </SessionScoped>
  );
}
