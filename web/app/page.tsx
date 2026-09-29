import { Console } from "@/components/console";
import { safeHealth, server } from "@/lib/gateway-server";
import type { ScenarioSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function InvestigatePage() {
  // Concurrent: sequentially, an unreachable gateway costs two timeouts.
  const [scenarioResult, health] = await Promise.all([
    server.scenarios().then(
      (value) => ({ ok: true as const, value }),
      (error: unknown) => ({ ok: false as const, message: (error as Error).message }),
    ),
    // Ask what the gateway can serve rather than offering a choice that falls back.
    safeHealth(),
  ]);

  const scenarios: ScenarioSummary[] = scenarioResult.ok ? scenarioResult.value : [];
  const gatewayError = scenarioResult.ok ? null : scenarioResult.message;
  const liveAllowed = health?.live_agents_available ?? false;

  return (
    <Console scenarios={scenarios} gatewayError={gatewayError} liveAllowed={liveAllowed} />
  );
}
