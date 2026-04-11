"use client";

import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Suspense, useEffect, useState } from "react";

// Inlined from /shared/experiment_templates.json — keep in sync when that
// file is updated (Next.js include scope doesn't reach outside web/).
type Template = {
  id: string;
  label_zh: string;
  hypothesis: string;
  variant_a: Record<string, unknown>;
  variant_b: Record<string, unknown>;
  success_metric: string;
  expected_lift_pct: number;
  sample_size: number;
  priority: number;
  applicable_genres: string[];
};

const TEMPLATES: Template[] = [
  {
    id: "rewarded_revive",
    label_zh: "复活激励视频",
    hypothesis:
      "允许玩家在失败后观看激励视频复活，提高留存和单日会话长度",
    variant_a: { placement: "none", description: "无广告复活" },
    variant_b: {
      placement: "session_fail_screen",
      ad_type: "rewarded_video",
      reward: "instant_revive",
    },
    success_metric: "day1_retention",
    expected_lift_pct: 5.0,
    sample_size: 2000,
    priority: 1,
    applicable_genres: [
      "casual_action",
      "runner",
      "arcade",
      "puzzle",
      "tower_defense",
    ],
  },
  {
    id: "double_reward",
    label_zh: "奖励翻倍视频",
    hypothesis: "通关后提供观看广告翻倍奖励，提高 ARPDAU 且不伤留存",
    variant_a: { placement: "level_complete", reward: "base" },
    variant_b: {
      placement: "level_complete",
      ad_type: "rewarded_video",
      reward: "2x",
    },
    success_metric: "arpdau",
    expected_lift_pct: 8.0,
    sample_size: 1500,
    priority: 2,
    applicable_genres: ["*"],
  },
  {
    id: "session_end_interstitial",
    label_zh: "退出插屏广告",
    hypothesis: "用户返回主页时插屏，对留存影响最小",
    variant_a: { placement: "none" },
    variant_b: {
      placement: "session_return_to_menu",
      ad_type: "interstitial",
      frequency_cap: "1_per_session",
    },
    success_metric: "ad_arpdau",
    expected_lift_pct: 12.0,
    sample_size: 2000,
    priority: 3,
    applicable_genres: ["*"],
  },
  {
    id: "offline_income_boost",
    label_zh: "离线收益翻倍视频",
    hypothesis: "登录后观看广告翻倍离线收益，提升放置类游戏 day3 留存",
    variant_a: { placement: "none", reward: "base" },
    variant_b: {
      placement: "login_offline_reward",
      ad_type: "rewarded_video",
      reward: "2x_offline",
    },
    success_metric: "day3_retention",
    expected_lift_pct: 6.5,
    sample_size: 1500,
    priority: 1,
    applicable_genres: ["idle", "simulation", "tycoon"],
  },
  {
    id: "energy_refill",
    label_zh: "体力刷新激励",
    hypothesis: "体力耗尽时提供广告换体力，增加会话数",
    variant_a: { placement: "none", energy: "timer_only" },
    variant_b: {
      placement: "energy_depleted",
      ad_type: "rewarded_video",
      reward: "full_energy",
    },
    success_metric: "sessions_per_dau",
    expected_lift_pct: 9.0,
    sample_size: 1500,
    priority: 2,
    applicable_genres: ["match3", "puzzle", "casual_action"],
  },
  {
    id: "remove_ads_iap",
    label_zh: "去广告付费包",
    hypothesis: "提供 $2.99 去广告包，吸引付费用户",
    variant_a: { iap: "no_remove_ads" },
    variant_b: { iap: "remove_ads_299", trigger: "after_3rd_interstitial" },
    success_metric: "iap_arpdau",
    expected_lift_pct: 3.0,
    sample_size: 3000,
    priority: 4,
    applicable_genres: ["*"],
  },
  {
    id: "rare_character_trial",
    label_zh: "稀有角色试玩激励",
    hypothesis: "观看广告解锁稀有角色 1 局试玩，提高 day7 留存和后续付费转化",
    variant_a: { placement: "none" },
    variant_b: {
      placement: "character_pick_screen",
      ad_type: "rewarded_video",
      reward: "1_battle_rare_char",
    },
    success_metric: "day7_retention",
    expected_lift_pct: 7.0,
    sample_size: 2000,
    priority: 2,
    applicable_genres: ["card", "rpg", "strategy"],
  },
];

const METRIC_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "day1_retention", label: "次日留存 (day1_retention)" },
  { value: "day3_retention", label: "3日留存 (day3_retention)" },
  { value: "day7_retention", label: "7日留存 (day7_retention)" },
  { value: "arpdau", label: "ARPDAU" },
  { value: "ad_arpdau", label: "广告 ARPDAU (ad_arpdau)" },
  { value: "iap_arpdau", label: "付费 ARPDAU (iap_arpdau)" },
  { value: "sessions_per_dau", label: "人均会话数 (sessions_per_dau)" },
  { value: "session_length", label: "会话时长 (session_length)" },
];

const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "draft", label: "草稿" },
  { value: "planned", label: "已计划" },
  { value: "running", label: "运行中" },
];

const PRIORITY_OPTIONS = [
  { value: 1, label: "P1 最高" },
  { value: 2, label: "P2 高" },
  { value: 3, label: "P3 中" },
  { value: 4, label: "P4 低" },
  { value: 5, label: "P5 最低" },
];

export default function NewExperimentPage() {
  // useSearchParams() needs a Suspense boundary for static rendering —
  // Next.js App Router requires it at the component that reads the URL.
  return (
    <Suspense fallback={<div className="text-sm text-gray-400">加载中…</div>}>
      <NewExperimentForm />
    </Suspense>
  );
}

function NewExperimentForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const prefillGameId = searchParams.get("gameId") || "";

  // Form state
  const [gameId, setGameId] = useState(prefillGameId);
  const [templateId, setTemplateId] = useState("");
  const [name, setName] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [successMetric, setSuccessMetric] = useState("day1_retention");
  const [sampleSize, setSampleSize] = useState("2000");
  const [priority, setPriority] = useState("2");
  const [expectedLift, setExpectedLift] = useState("");
  const [status, setStatus] = useState("draft");
  const [variantA, setVariantA] = useState("{}");
  const [variantB, setVariantB] = useState("{}");
  const [notes, setNotes] = useState("");

  // Submit state
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  // JSON validation state
  const [variantAError, setVariantAError] = useState("");
  const [variantBError, setVariantBError] = useState("");

  // Prefill fields when a template is selected
  useEffect(() => {
    if (!templateId) return;
    const tpl = TEMPLATES.find((t) => t.id === templateId);
    if (!tpl) return;
    setName(tpl.label_zh);
    setHypothesis(tpl.hypothesis);
    setSuccessMetric(tpl.success_metric);
    setSampleSize(String(tpl.sample_size));
    setPriority(String(tpl.priority));
    setExpectedLift(String(tpl.expected_lift_pct));
    setVariantA(JSON.stringify(tpl.variant_a, null, 2));
    setVariantB(JSON.stringify(tpl.variant_b, null, 2));
    setVariantAError("");
    setVariantBError("");
  }, [templateId]);

  // Validate JSON on change
  useEffect(() => {
    try {
      JSON.parse(variantA);
      setVariantAError("");
    } catch {
      setVariantAError("JSON 格式无效");
    }
  }, [variantA]);

  useEffect(() => {
    try {
      JSON.parse(variantB);
      setVariantBError("");
    } catch {
      setVariantBError("JSON 格式无效");
    }
  }, [variantB]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErrorMsg("");

    if (!gameId) {
      setErrorMsg("请填写 Game ID");
      return;
    }
    if (!name.trim() || !hypothesis.trim()) {
      setErrorMsg("名称与假设为必填");
      return;
    }
    if (variantAError || variantBError) {
      setErrorMsg("请修正 Variant JSON 格式");
      return;
    }

    let parsedA: unknown = {};
    let parsedB: unknown = {};
    try {
      parsedA = JSON.parse(variantA);
      parsedB = JSON.parse(variantB);
    } catch {
      setErrorMsg("Variant JSON 解析失败");
      return;
    }

    setSubmitting(true);
    try {
      const res = await fetch("/api/experiments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          gameId: parseInt(gameId),
          name: name.trim(),
          hypothesis: hypothesis.trim(),
          variantA: parsedA,
          variantB: parsedB,
          successMetric,
          sampleSize: sampleSize ? parseInt(sampleSize) : null,
          priority: parseInt(priority),
          expectedLift: expectedLift ? Number(expectedLift) : null,
          status,
          notes: notes.trim() || null,
        }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setErrorMsg(data.error || "创建失败");
        setSubmitting(false);
        return;
      }

      const created = await res.json();
      router.push(`/experiments/${created.id}`);
    } catch (err) {
      console.error(err);
      setErrorMsg("网络错误，请重试");
      setSubmitting(false);
    }
  }

  return (
    <div>
      {/* Breadcrumb */}
      <div className="text-sm text-gray-500 mb-4">
        <Link href="/experiments" className="hover:underline">
          商业化实验
        </Link>
        <span className="mx-2">/</span>
        <span className="text-gray-700">新建实验</span>
      </div>

      <h2 className="text-2xl font-bold mb-6">新建实验</h2>

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Template picker */}
        <div className="bg-white rounded-lg shadow p-5">
          <h3 className="font-semibold text-sm mb-3">模板 (可选)</h3>
          <p className="text-xs text-gray-500 mb-3">
            选择一个常用模板自动填充以下字段，然后再根据需要调整
          </p>
          <select
            value={templateId}
            onChange={(e) => setTemplateId(e.target.value)}
            className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
          >
            <option value="">-- 不使用模板（自定义）--</option>
            {TEMPLATES.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label_zh} ({t.success_metric}, {t.expected_lift_pct}%)
              </option>
            ))}
          </select>
        </div>

        {/* Core fields */}
        <div className="bg-white rounded-lg shadow p-5 space-y-4">
          <h3 className="font-semibold text-sm">基本信息</h3>
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Game ID <span className="text-red-500">*</span>
            </label>
            <input
              type="number"
              value={gameId}
              onChange={(e) => setGameId(e.target.value)}
              required
              placeholder="如 123"
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
            />
            <p className="text-xs text-gray-400 mt-1">
              可从游戏详情页 URL (/games/&lt;id&gt;) 中复制
            </p>
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              实验名称 <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              maxLength={100}
              placeholder="如 复活激励视频 v1"
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              假设 <span className="text-red-500">*</span>
            </label>
            <textarea
              value={hypothesis}
              onChange={(e) => setHypothesis(e.target.value)}
              required
              rows={3}
              placeholder="说明为什么这个变更会带来收益提升 / 留存改善..."
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
            />
          </div>
        </div>

        {/* Variants */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="bg-white rounded-lg shadow p-5">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <span className="w-6 h-6 rounded-full bg-gray-200 flex items-center justify-center text-xs font-bold">
                  A
                </span>
                <h3 className="font-semibold text-sm">对照组 Variant A</h3>
              </div>
              {variantAError && (
                <span className="text-xs text-red-600">{variantAError}</span>
              )}
            </div>
            <textarea
              value={variantA}
              onChange={(e) => setVariantA(e.target.value)}
              rows={8}
              spellCheck={false}
              className={`w-full border rounded px-3 py-2 text-xs font-mono ${
                variantAError ? "border-red-400" : "border-gray-300"
              }`}
              placeholder='{"placement": "none"}'
            />
          </div>
          <div className="bg-white rounded-lg shadow p-5">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <span className="w-6 h-6 rounded-full bg-green-200 flex items-center justify-center text-xs font-bold">
                  B
                </span>
                <h3 className="font-semibold text-sm">实验组 Variant B</h3>
              </div>
              {variantBError && (
                <span className="text-xs text-red-600">{variantBError}</span>
              )}
            </div>
            <textarea
              value={variantB}
              onChange={(e) => setVariantB(e.target.value)}
              rows={8}
              spellCheck={false}
              className={`w-full border rounded px-3 py-2 text-xs font-mono ${
                variantBError ? "border-red-400" : "border-gray-300"
              }`}
              placeholder='{"placement": "session_fail_screen", "ad_type": "rewarded_video"}'
            />
          </div>
        </div>

        {/* Metrics row */}
        <div className="bg-white rounded-lg shadow p-5">
          <h3 className="font-semibold text-sm mb-4">指标与参数</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">
                成功指标 <span className="text-red-500">*</span>
              </label>
              <select
                value={successMetric}
                onChange={(e) => setSuccessMetric(e.target.value)}
                required
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              >
                {METRIC_OPTIONS.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">
                样本量 (DAU)
              </label>
              <input
                type="number"
                value={sampleSize}
                onChange={(e) => setSampleSize(e.target.value)}
                min={500}
                max={10000}
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              />
              <p className="text-xs text-gray-400 mt-1">建议 500 - 10000</p>
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">
                优先级
              </label>
              <select
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              >
                {PRIORITY_OPTIONS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">
                预期提升 (%)
              </label>
              <input
                type="number"
                value={expectedLift}
                onChange={(e) => setExpectedLift(e.target.value)}
                step="0.1"
                placeholder="如 5.0"
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">
                初始状态
              </label>
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              >
                {STATUS_OPTIONS.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>

        {/* Notes */}
        <div className="bg-white rounded-lg shadow p-5">
          <label className="block text-xs font-medium text-gray-700 mb-1">
            备注 / 笔记
          </label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            placeholder="实验背景、相关链接、团队成员..."
            className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
          />
        </div>

        {/* Actions */}
        {errorMsg && (
          <div className="bg-red-50 border-l-4 border-red-400 px-4 py-2 rounded text-sm text-red-700">
            {errorMsg}
          </div>
        )}

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={submitting}
            className="bg-gray-900 text-white px-5 py-2 rounded text-sm hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? "创建中..." : "创建实验"}
          </button>
          <Link
            href="/experiments"
            className="text-sm text-gray-600 hover:text-gray-900"
          >
            取消
          </Link>
        </div>
      </form>
    </div>
  );
}
