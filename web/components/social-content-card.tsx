"use client";

// TODO(integration-agent): this component calls `/api/games/[id]/social-content`.
// The endpoint must return `SocialContentSample[]` (as JSON) ordered by
// viewCount DESC, limited to 20 rows. Each row should be the shape described
// by the `SocialContent` type below (BigInt view_count should be serialized
// to string). Route file: web/app/api/games/[id]/social-content/route.ts.

import { useEffect, useState } from "react";

type SocialContent = {
  id: number;
  platform: string;
  contentType: string;
  title: string;
  hookPhrase: string | null;
  authorName: string | null;
  hashtags: string[];
  viewCount: string; // BigInt serialized to string over JSON
  likeCount: string | null;
  url: string | null;
  postedAt: string;
};

const PLATFORM_EMOJI: Record<string, string> = {
  douyin: "🎵",
  tiktok: "🎬",
  youtube: "📺",
  bilibili: "📼",
};

const PLATFORM_LABELS: Record<string, string> = {
  douyin: "抖音",
  tiktok: "TikTok",
  youtube: "YouTube",
  bilibili: "B站",
};

function formatCount(value: string | null): string {
  if (!value) return "-";
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

export function SocialContentCard({ gameId }: { gameId: number }) {
  const [items, setItems] = useState<SocialContent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);

    fetch(`/api/games/${gameId}/social-content`)
      .then((r) => {
        if (!r.ok) throw new Error(`status ${r.status}`);
        return r.json();
      })
      .then((data: SocialContent[] | { items: SocialContent[] }) => {
        if (cancelled) return;
        const list = Array.isArray(data) ? data : data?.items || [];
        setItems(list);
        setLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setError(true);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [gameId]);

  return (
    <div className="bg-white rounded-lg shadow p-4">
      <h3 className="font-semibold mb-4 flex items-center gap-2">
        <span>传播内容样本</span>
        {items.length > 0 && (
          <span className="text-xs text-gray-400 font-normal">
            Top {Math.min(items.length, 8)}
          </span>
        )}
      </h3>

      {loading ? (
        <p className="text-gray-400 text-sm">加载中...</p>
      ) : error ? (
        <p className="text-gray-400 text-sm">加载失败</p>
      ) : items.length === 0 ? (
        <p className="text-gray-400 text-sm">暂无社媒数据</p>
      ) : (
        <div className="space-y-3">
          {items.slice(0, 8).map((c) => (
            <div
              key={c.id}
              className="flex items-start gap-3 pb-3 border-b border-gray-100 last:border-0 last:pb-0"
            >
              <span
                className="text-lg flex-shrink-0"
                title={PLATFORM_LABELS[c.platform] || c.platform}
              >
                {PLATFORM_EMOJI[c.platform] || "🔗"}
              </span>
              <div className="min-w-0 flex-1">
                {c.url ? (
                  <a
                    href={c.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm font-medium block hover:text-blue-600 line-clamp-2"
                  >
                    {c.title}
                  </a>
                ) : (
                  <p className="text-sm font-medium line-clamp-2">{c.title}</p>
                )}
                {c.hookPhrase && (
                  <p className="text-xs text-purple-600 mt-1 flex items-start gap-1">
                    <span>💡</span>
                    <span>{c.hookPhrase}</span>
                  </p>
                )}
                <div className="text-xs text-gray-400 mt-1 flex gap-3 flex-wrap">
                  {c.authorName && (
                    <span className="truncate max-w-[140px]">
                      {c.authorName}
                    </span>
                  )}
                  <span>{formatCount(c.viewCount)} 播放</span>
                  {c.likeCount && (
                    <span>{formatCount(c.likeCount)} 赞</span>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
