"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

type SimilarGame = {
  id: number;
  name_zh: string | null;
  name_en: string | null;
  thumbnail_url: string | null;
  genre: string | null;
  similarity: number;
};

export function SimilarGamesCard({ gameId }: { gameId: number }) {
  const [games, setGames] = useState<SimilarGame[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(`/api/games/${gameId}/similar`)
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        setGames(Array.isArray(data) ? data : []);
        setLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setGames([]);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [gameId]);

  return (
    <div className="bg-white rounded-lg shadow p-4">
      <h3 className="font-semibold mb-4">相似游戏</h3>
      {loading ? (
        <p className="text-gray-400 text-sm">加载中...</p>
      ) : games.length === 0 ? (
        <p className="text-gray-400 text-sm">
          暂无相似游戏（尚未生成 embedding）
        </p>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          {games.map((g) => {
            const name = g.name_zh || g.name_en || "Unknown";
            const similarityPct = Math.round((g.similarity ?? 0) * 100);
            return (
              <Link
                key={g.id}
                href={`/games/${g.id}`}
                className="flex items-center gap-2 p-2 rounded hover:bg-gray-50 transition-colors"
              >
                {g.thumbnail_url ? (
                  <img
                    src={g.thumbnail_url}
                    alt=""
                    className="w-10 h-10 rounded shadow-sm object-cover shrink-0"
                  />
                ) : (
                  <div className="w-10 h-10 rounded bg-gray-100 shrink-0" />
                )}
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium truncate">{name}</p>
                  <p className="text-xs text-gray-400 truncate">
                    {g.genre || "-"} · {similarityPct}%
                  </p>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
