"use client";

import { useState } from "react";

import { ManualDemoTest } from "@/components/settings/ManualDemoTest";
import { Button } from "@/components/ui/button";
import { Sheet } from "@/components/ui/sheet";

/** Trades → "New demo order": the BloFin demo order flow (preview → confirm) in a sheet. */
export function NewDemoOrder() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="outline" className="h-11" onClick={() => setOpen(true)}>
        New demo order
      </Button>
      <Sheet open={open} onClose={() => setOpen(false)} title="New demo order" className="overflow-y-auto">
        <ManualDemoTest />
      </Sheet>
    </>
  );
}
