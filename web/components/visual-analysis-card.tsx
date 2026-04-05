"use client";
import { useEffect, useState } from "react";

type Analysis = {
  id: number;
  assetUrl: string;
  analysisType: string;
  result: any;
  confidence: number | null;
  analyzedAt: string;
};

const TYPE_META: Record<string, { label: string; emoji: string }> = {
  scene_description: { label: "场景", emoji: "🎬" },
  color_palette: { label: "配色", emoji: "🎨" },
  ui_layout: { label: "UI 布局", emoji: "📱" },
  text_ocr: { label: "文字识别", emoji: "🔤" },
};

export function VisualAnalysisCard({ gameId }: { gameId: number }) {
  const [analyses, setAnalyses] = useState<Analysis[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`/api/games/${gameId}/visuals`)
      .then((r) => r.json())
      .then((d) => {
        setAnalyses(d);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [gameId]);

  if (loading) return null;
  if (analyses.length === 0) return null;

  // Group by assetUrl
  const byUrl: Record<string, Analysis[]> = {};
  for (const a of analyses) {
    if (!byUrl[a.assetUrl]) byUrl[a.assetUrl] = [];
    byUrl[a.assetUrl].push(a);
  }

  return (
    <div className="bg-white rounded-lg shadow p-4 col-span-2">
      <h3 className="font-semibold mb-4">视觉分析 (AI 识别)</h3>
      <div className="space-y-4">
        {Object.entries(byUrl)
          .slice(0, 3)
          .map(([url, items]) => (
            <div key={url} className="flex gap-4 items-start">
              <img
                src={url}
                alt=""
                className="w-32 h-auto rounded shrink-0 object-cover"
              />
              <div className="flex-1 min-w-0 grid grid-cols-2 gap-2">
                {items.map((a) => {
                  const meta = TYPE_META[a.analysisType] || {
                    label: a.analysisType,
                    emoji: "📄",
                  };
                  return (
                    <div
                      key={a.id}
                      className="text-xs bg-gray-50 rounded p-2"
                    >
                      <div className="font-semibold mb-1">
                        {meta.emoji} {meta.label}
                      </div>
                      <RenderResult
                        type={a.analysisType}
                        result={a.result}
                      />
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
      </div>
    </div>
  );
}

function RenderResult({ type, result }: { type: string; result: any }) {
  if (type === "scene_description") {
    return (
      <div>
        <p>{result.description}</p>
        <div className="mt-1 flex flex-wrap gap-1">
          {(result.art_style_tags || []).map((t: string) => (
            <span key={t} className="bg-purple-100 px-1.5 rounded">
              {t}
            </span>
          ))}
        </div>
      </div>
    );
  }
  if (type === "color_palette") {
    return (
      <div>
        <div className="flex gap-1 mb-1">
          {(result.dominant_colors || []).map((c: string) => (
            <span
              key={c}
              style={{ background: c }}
              className="w-4 h-4 rounded border"
              title={c}
            />
          ))}
        </div>
        <span>
          {result.mood} · {result.contrast}
        </span>
      </div>
    );
  }
  if (type === "ui_layout") {
    return (
      <div>
        <div>
          {result.layout_type} · {result.hud_density}
        </div>
        <div className="text-gray-500">{result.navigation_pattern}</div>
      </div>
    );
  }
  if (type === "text_ocr") {
    const texts = result.visible_text || [];
    return <div>{texts.slice(0, 3).join(" / ")}</div>;
  }
  return (
    <pre className="text-xs">
      {JSON.stringify(result, null, 2).slice(0, 200)}
    </pre>
  );
}
