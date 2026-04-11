"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";

const GRADES = ["", "S", "A", "B", "C", "D"] as const;

export type EditGameFormInitial = {
  nameZh: string;
  nameEn: string;
  developer: string;
  genre: string;
  iaaSuitability: number;
  iaaGrade: string;
  gameplayTags: string; // comma-separated
  positioning: string;
  coreLoop: string;
};

export function EditGameForm({
  gameId,
  initial,
}: {
  gameId: number;
  initial: EditGameFormInitial;
}) {
  const router = useRouter();
  const [form, setForm] = useState<EditGameFormInitial>(initial);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [pending, startTransition] = useTransition();

  function update<K extends keyof EditGameFormInitial>(
    key: K,
    value: EditGameFormInitial[K],
  ) {
    setForm((f) => ({ ...f, [key]: value }));
    setSuccess(false);
    setError(null);
  }

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setSuccess(false);

    const iaaSuitability = Number(form.iaaSuitability);
    if (
      !Number.isFinite(iaaSuitability) ||
      iaaSuitability < 0 ||
      iaaSuitability > 100
    ) {
      setError("IAA 适配必须是 0-100 的整数");
      return;
    }
    if (form.iaaGrade && !["S", "A", "B", "C", "D"].includes(form.iaaGrade)) {
      setError("IAA 等级必须是 S/A/B/C/D 之一");
      return;
    }

    const body = {
      nameZh: form.nameZh || null,
      nameEn: form.nameEn || null,
      developer: form.developer || null,
      genre: form.genre || null,
      iaaSuitability,
      iaaGrade: form.iaaGrade || null,
      gameplayTags: form.gameplayTags
        .split(",")
        .map((s) => s.trim())
        .filter((s) => s.length > 0),
      positioning: form.positioning || null,
      coreLoop: form.coreLoop || null,
    };

    try {
      const res = await fetch(`/api/admin/games/${gameId}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        setError(j?.error ?? `请求失败 (${res.status})`);
        return;
      }
      setSuccess(true);
      startTransition(() => router.refresh());
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Field label="中文名">
          <input
            type="text"
            value={form.nameZh}
            onChange={(e) => update("nameZh", e.target.value)}
            className="text-sm border border-gray-300 rounded px-2 py-1.5 w-full"
          />
        </Field>
        <Field label="英文名">
          <input
            type="text"
            value={form.nameEn}
            onChange={(e) => update("nameEn", e.target.value)}
            className="text-sm border border-gray-300 rounded px-2 py-1.5 w-full"
          />
        </Field>
        <Field label="开发商">
          <input
            type="text"
            value={form.developer}
            onChange={(e) => update("developer", e.target.value)}
            className="text-sm border border-gray-300 rounded px-2 py-1.5 w-full"
          />
        </Field>
        <Field label="品类">
          <input
            type="text"
            value={form.genre}
            onChange={(e) => update("genre", e.target.value)}
            placeholder="如 idle / match3 / puzzle"
            className="text-sm border border-gray-300 rounded px-2 py-1.5 w-full"
          />
        </Field>
        <Field label="IAA 适配度 (0-100)">
          <input
            type="number"
            min={0}
            max={100}
            value={form.iaaSuitability}
            onChange={(e) =>
              update("iaaSuitability", Number(e.target.value) as number)
            }
            className="text-sm border border-gray-300 rounded px-2 py-1.5 w-full"
          />
        </Field>
        <Field label="IAA 等级">
          <select
            value={form.iaaGrade}
            onChange={(e) => update("iaaGrade", e.target.value)}
            className="text-sm border border-gray-300 rounded px-2 py-1.5 w-full bg-white"
          >
            {GRADES.map((g) => (
              <option key={g || "none"} value={g}>
                {g || "(未设置)"}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <Field label="玩法标签 (逗号分隔)">
        <input
          type="text"
          value={form.gameplayTags}
          onChange={(e) => update("gameplayTags", e.target.value)}
          placeholder="如 idle, merge, sandbox"
          className="text-sm border border-gray-300 rounded px-2 py-1.5 w-full"
        />
      </Field>

      <Field label="定位">
        <textarea
          value={form.positioning}
          onChange={(e) => update("positioning", e.target.value)}
          rows={2}
          className="text-sm border border-gray-300 rounded px-2 py-1.5 w-full"
        />
      </Field>

      <Field label="核心循环 (Core Loop)">
        <textarea
          value={form.coreLoop}
          onChange={(e) => update("coreLoop", e.target.value)}
          rows={3}
          className="text-sm border border-gray-300 rounded px-2 py-1.5 w-full"
        />
      </Field>

      {error && (
        <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">
          {error}
        </div>
      )}
      {success && (
        <div className="text-sm text-green-700 bg-green-50 border border-green-200 rounded px-3 py-2">
          保存成功
        </div>
      )}

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={pending}
          className="text-sm bg-blue-600 hover:bg-blue-700 text-white rounded px-4 py-1.5 disabled:opacity-50"
        >
          {pending ? "保存中..." : "保存修改"}
        </button>
      </div>
    </form>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col">
      <label className="text-xs text-gray-500 mb-1">{label}</label>
      {children}
    </div>
  );
}
