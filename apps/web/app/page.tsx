import { Console } from "@/components/console";
import { server } from "@/lib/gateway";
import type { ScenarioSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function InvestigatePage() {
  let scenarios: ScenarioSummary[] = [];
  let gatewayError: string | null = null;
  try {
    scenarios = await server.scenarios();
  } catch (error) {
    gatewayError = (error as Error).message;
  }

  return <Console scenarios={scenarios} gatewayError={gatewayError} />;
}
