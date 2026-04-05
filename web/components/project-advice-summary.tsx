type Props = {
  advice: {
    recommendation: "pursue" | "monitor" | "pass";
    reasoning: string;
    strengths: string[];
    weaknesses: string[];
    resource_estimate_weeks: number;
    risk_factors: string[];
    confidence: number;
    similar_shipped_projects?: string[];
  };
  gameName: string;
  gameId: number;
};

const META: Record<
  "pursue" | "monitor" | "pass",
  { label: string; bg: string; text: string }
> = {
  pursue: { label: "推荐立项", bg: "bg-green-500", text: "text-white" },
  monitor: { label: "建议观察", bg: "bg-yellow-500", text: "text-white" },
  pass: { label: "建议放弃", bg: "bg-gray-500", text: "text-white" },
};

export function ProjectAdviceSummary({ advice }: Props) {
  const meta = META[advice.recommendation] ?? META.monitor;
  const strengths = advice.strengths ?? [];
  const risks = advice.risk_factors ?? [];
  const similarCount = advice.similar_shipped_projects?.length ?? 0;

  return (
    <div className="bg-white rounded-lg shadow p-4">
      <div className="flex items-center justify-between mb-3">
        <div
          className={`${meta.bg} ${meta.text} px-3 py-1 rounded-full text-sm font-bold`}
        >
          {meta.label}
        </div>
        <span className="text-xs text-gray-500">
          置信度 {Math.round((advice.confidence ?? 0) * 100)}%
        </span>
      </div>

      <p className="text-sm text-gray-700 mb-3 whitespace-pre-wrap">
        {advice.reasoning}
      </p>

      <div className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <h4 className="font-semibold text-green-700 mb-1">优势</h4>
          <ul className="space-y-0.5 text-gray-700">
            {strengths.length === 0 ? (
              <li className="text-gray-400">-</li>
            ) : (
              strengths.map((s, i) => <li key={i}>• {s}</li>)
            )}
          </ul>
        </div>
        <div>
          <h4 className="font-semibold text-red-700 mb-1">风险</h4>
          <ul className="space-y-0.5 text-gray-700">
            {risks.length === 0 ? (
              <li className="text-gray-400">-</li>
            ) : (
              risks.map((r, i) => <li key={i}>• {r}</li>)
            )}
          </ul>
        </div>
      </div>

      <div className="flex items-center justify-between text-xs text-gray-500 mt-3 pt-3 border-t">
        <span>预估 {advice.resource_estimate_weeks} 周</span>
        <span>{similarCount} 个相似项目</span>
      </div>
    </div>
  );
}
