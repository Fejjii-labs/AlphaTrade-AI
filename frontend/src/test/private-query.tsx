import { afterEach } from "vitest";
import * as testing from "@testing-library/react";
import { PrivateQueryProvider } from "@/components/query/PrivateQueryProvider";

afterEach(testing.cleanup);

export function PrivateQueryFixture({ children }: { children: React.ReactNode }) {
  return <PrivateQueryProvider organizationId="fixture-org" userId="fixture-user">{children}</PrivateQueryProvider>;
}
export const renderHook: typeof testing.renderHook = (callback, options) =>
  testing.renderHook(callback, { wrapper: PrivateQueryFixture, ...options });
