import React, { useEffect, useState } from 'react';
import { ChevronRight, Clock3, ExternalLink, TrendingUp, X } from 'lucide-react';
import { EdgeScorePayload, EdgeScoreRecommendation } from '../types';
import { colors } from '../utils/propsmadness_colors';

const COMPONENT_LABELS: Record<string, string> = {
  projection: 'Projection edge',
  recent_form: 'Recent form',
  matchup: 'Matchup fit',
  market: 'Best number',
  line_movement: 'Market move',
  similar_players: 'Comp support',
  head_to_head: 'Opponent history',
  back_to_back: 'Rest context',
};

const AREA_LABELS: Record<string, string> = {
  restricted_area: 'rim',
  paint: 'paint',
  mid_range: 'mid-range',
  left_corner: 'left corner',
  right_corner: 'right corner',
  top_key: 'above the break',
  catch_and_shoot: 'catch-and-shoot',
  pull_up: 'pull-up',
  less_than_10_ft: 'inside 10 ft',
};

function formatGeneratedAt(value?: string | null) {
  if (!value) return 'Waiting for refresh';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'Waiting for refresh';

  return parsed.toLocaleString([], {
    hour: 'numeric',
    minute: '2-digit',
    month: 'short',
    day: 'numeric',
    timeZoneName: 'short',
  });
}

function formatSigned(value: number, digits = 1) {
  const formatted = digits === 0 ? Math.round(value).toString() : value.toFixed(digits);
  return value > 0 ? `+${formatted}` : formatted;
}

function safeNumber(value: unknown) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function renderMarketLabel(recommendation: EdgeScoreRecommendation) {
  return `${recommendation.stat_label} ${recommendation.pick_label} ${recommendation.line.toFixed(1)}`;
}

function getWeightedLeaders(weights: Record<string, any> | undefined, limit = 2) {
  return Object.entries(weights ?? {})
    .map(([area, entry]) => ({
      area,
      label: AREA_LABELS[area] ?? area.replace(/_/g, ' '),
      playerPct: safeNumber(entry?.player_pct ?? entry?.percentage) ?? 0,
      oppRank: safeNumber(entry?.opp_rank ?? entry?.rank),
    }))
    .filter((entry) => entry.playerPct > 0)
    .sort((left, right) => right.playerPct - left.playerPct)
    .slice(0, limit);
}

function getPositiveSignals(recommendation: EdgeScoreRecommendation, limit = 8) {
  return Object.entries(recommendation.component_scores ?? {})
    .filter(([, score]) => Number.isFinite(score) && score > 0.04)
    .sort((left, right) => right[1] - left[1])
    .slice(0, limit);
}

function withAlpha(hex: string, alpha: number) {
  const normalized = hex.replace('#', '');
  if (normalized.length !== 6) {
    return hex;
  }

  const red = Number.parseInt(normalized.slice(0, 2), 16);
  const green = Number.parseInt(normalized.slice(2, 4), 16);
  const blue = Number.parseInt(normalized.slice(4, 6), 16);

  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}

function getCourtScaleColor(baseScore: number) {
  switch (baseScore) {
    case 1: return colors.courtRed;
    case 2: return colors.courtOrange;
    case 3: return colors.courtYellow;
    case 4: return colors.courtLightGreen;
    case 5: return colors.courtGreen;
    default: return colors.courtYellow;
  }
}

function getCourtScaleText(baseScore: number) {
  return baseScore === 3 || baseScore === 4 ? colors.black : colors.fixedWhite;
}

function getCourtTone(baseScore: number) {
  const accent = getCourtScaleColor(baseScore);
  const solidText = getCourtScaleText(baseScore);

  return {
    accent,
    solidStyle: {
      backgroundColor: accent,
      borderColor: accent,
      color: solidText,
    },
    softStyle: {
      backgroundColor: withAlpha(accent, 0.18),
      borderColor: withAlpha(accent, 0.42),
      color: accent,
    },
    cardStyle: {
      backgroundColor: withAlpha(accent, 0.12),
      borderColor: withAlpha(accent, 0.32),
    },
    dotStyle: {
      backgroundColor: accent,
    },
  };
}

function getSignalBaseScore(score: number) {
  if (score >= 82) return 5;
  if (score >= 74) return 4;
  if (score >= 66) return 3;
  if (score >= 58) return 2;
  return 1;
}

function getSignalTone(score: number) {
  return getCourtTone(getSignalBaseScore(score));
}

function getSupportBaseScore(confidence: number) {
  if (confidence >= 88) return 5;
  if (confidence >= 78) return 4;
  if (confidence >= 66) return 3;
  if (confidence >= 54) return 2;
  return 1;
}

function getSupportDepth(confidence: number) {
  const tone = getCourtTone(getSupportBaseScore(confidence));

  if (confidence >= 86) {
    return {
      label: 'A lot is backing this pick',
      detail: 'Projection, recent form, matchup data, and market context are all showing up here.',
      tone,
    };
  }

  if (confidence >= 72) {
    return {
      label: 'Strong data support',
      detail: 'Several different stats are pointing in the same direction.',
      tone,
    };
  }

  if (confidence >= 58) {
    return {
      label: 'Some good support',
      detail: 'There is a solid case here, but fewer data points are available.',
      tone,
    };
  }

  return {
    label: 'Less data behind it',
    detail: 'This pick still ranks, but it is being supported by fewer inputs than the strongest plays.',
    tone,
  };
}

function getSummaryChipTone(kind: 'count' | 'changes' | 'delivery' | 'leader') {
  return 'border-borderMedium bg-bgElevation0 text-fgSubtle';
}

function getDescriptorBaseScore(componentScore: number) {
  if (componentScore >= 0.32) return 5;
  if (componentScore >= 0.2) return 4;
  if (componentScore >= 0.12) return 3;
  if (componentScore >= 0.07) return 2;
  return 2;
}

function getComponentTone(componentScore: number) {
  return getCourtTone(getDescriptorBaseScore(componentScore));
}

function buildMatchupInsight(recommendation: EdgeScoreRecommendation) {
  const matchup = recommendation.inputs?.matchup ?? {};
  const statType = recommendation.stat_type;

  const assistLeaders = getWeightedLeaders(matchup.assist_zones?.weights);
  const shootingLeaders = getWeightedLeaders(matchup.shooting_zones?.weights);
  const threeLeaders = getWeightedLeaders(matchup.three_zones?.weights);
  const shotTypeLeaders = getWeightedLeaders(matchup.shot_type?.weights);
  const paintRank = safeNumber(matchup.paint_rank);
  const playTypeLeaders = getWeightedLeaders(matchup.play_type?.weights);

  if (['AST', 'PTS+AST', 'REB+AST', 'PTS+REB+AST'].includes(statType) && assistLeaders.length) {
    const [primary, secondary] = assistLeaders;
    return `Assist creation is concentrated in ${primary.label} (${primary.playerPct.toFixed(0)}%)${secondary ? ` and ${secondary.label} (${secondary.playerPct.toFixed(0)}%)` : ''}, and the opponent is more vulnerable in those lanes${primary.oppRank ? ` with ranks of ${primary.oppRank.toFixed(0)}` : ''}${secondary?.oppRank ? ` and ${secondary.oppRank.toFixed(0)}` : ''}.`;
  }

  if (statType === 'FG3M' && threeLeaders.length) {
    const [primary, secondary] = threeLeaders;
    return `Three-point volume lives ${primary.label} (${primary.playerPct.toFixed(0)}%)${secondary ? ` and ${secondary.label} (${secondary.playerPct.toFixed(0)}%)` : ''}, which lines up with softer opponent coverage in those zones${primary.oppRank ? ` at ${primary.oppRank.toFixed(0)}` : ''}${secondary?.oppRank ? ` and ${secondary.oppRank.toFixed(0)}` : ''}.`;
  }

  if (statType === 'REB' && paintRank !== null) {
    return `The rebound read is driven mainly by paint context, where the opponent carries a paint rank of ${paintRank.toFixed(0)}.`;
  }

  if (shotTypeLeaders.length) {
    const [primary, secondary] = shotTypeLeaders;
    return `The shot profile leans on ${primary.label} (${primary.playerPct.toFixed(0)}%)${secondary ? ` and ${secondary.label} (${secondary.playerPct.toFixed(0)}%)` : ''}, which is where this matchup is doing work.`;
  }

  if (shootingLeaders.length) {
    const [primary, secondary] = shootingLeaders;
    return `The scoring map is weighted toward ${primary.label} (${primary.playerPct.toFixed(0)}%)${secondary ? ` and ${secondary.label} (${secondary.playerPct.toFixed(0)}%)` : ''}, so the zone matchup matters here.`;
  }

  if (playTypeLeaders.length) {
    const [primary, secondary] = playTypeLeaders;
    return `Play-type usage is centered on ${primary.label} (${primary.playerPct.toFixed(0)}%)${secondary ? ` and ${secondary.label} (${secondary.playerPct.toFixed(0)}%)` : ''}, which supports the matchup read.`;
  }

  return null;
}

function buildMatchupTag(recommendation: EdgeScoreRecommendation) {
  const matchup = recommendation.inputs?.matchup ?? {};
  const statType = recommendation.stat_type;
  const assistLeaders = getWeightedLeaders(matchup.assist_zones?.weights, 1);
  const threeLeaders = getWeightedLeaders(matchup.three_zones?.weights, 1);
  const shootingLeaders = getWeightedLeaders(matchup.shooting_zones?.weights, 1);
  const shotTypeLeaders = getWeightedLeaders(matchup.shot_type?.weights, 1);
  const paintRank = safeNumber(matchup.paint_rank);
  const playTypeLeaders = getWeightedLeaders(matchup.play_type?.weights, 1);

  if (['AST', 'PTS+AST', 'REB+AST', 'PTS+REB+AST'].includes(statType) && assistLeaders.length) {
    return 'Assist lanes fit';
  }

  if (statType === 'FG3M' && (threeLeaders.length || shotTypeLeaders.length)) {
    return '3-point map fits';
  }

  if ((statType === 'REB' || statType === 'REB+AST' || statType === 'PTS+REB' || statType === 'PTS+REB+AST') && paintRank !== null) {
    return 'Interior rebound spot';
  }

  if (shotTypeLeaders.length) {
    return 'Shot profile fits';
  }

  if (shootingLeaders.length) {
    return 'Zone matchup fits';
  }

  if (playTypeLeaders.length) {
    return 'Play-type fit';
  }

  return 'Matchup support';
}

function buildRowReason(componentName: string, recommendation: EdgeScoreRecommendation) {
  const projection = recommendation.inputs?.projection ?? {};
  const recentForm = recommendation.inputs?.recent_form ?? {};
  const market = recommendation.inputs?.market ?? {};
  const movement = recommendation.inputs?.line_movement ?? {};
  const similar = recommendation.inputs?.similar_players ?? {};
  const headToHead = recommendation.inputs?.head_to_head ?? {};
  const backToBack = recommendation.inputs?.back_to_back ?? {};

  if (componentName === 'projection') {
    const baselineProjection = safeNumber(projection.baseline_projection);
    if (baselineProjection !== null) {
      return `Proj ${baselineProjection.toFixed(1)} vs ${recommendation.line.toFixed(1)}`;
    }
  }

  if (componentName === 'recent_form') {
    const recent10HitRate = safeNumber(recentForm.hit_rates?.last_10);
    if (recent10HitRate !== null) {
      return `Last 10: ${recent10HitRate.toFixed(0)}% to ${recommendation.pick}`;
    }
  }

  if (componentName === 'matchup') {
    return buildMatchupTag(recommendation);
  }

  if (componentName === 'market') {
    const lineDelta = safeNumber(market.line_delta_vs_consensus);
    const priceDelta = safeNumber(market.price_delta);
    if (lineDelta !== null && Math.abs(lineDelta) > 0.04) {
      return `${recommendation.sportsbook_label} beats market by ${Math.abs(lineDelta).toFixed(1)}`;
    }
    if (priceDelta !== null && priceDelta > 0.005) {
      return `${recommendation.sportsbook_label} has a better price`;
    }
    return 'Market support';
  }

  if (componentName === 'line_movement') {
    const lineChange = safeNumber(movement.favorable_line_change);
    const priceChange = safeNumber(movement.favorable_price_change);
    if (lineChange !== null && Math.abs(lineChange) > 0.04) {
      return `Moved ${Math.abs(lineChange).toFixed(1)} toward ${recommendation.pick}`;
    }
    if (priceChange !== null && priceChange > 0.005) {
      return 'Price moved this way';
    }
    return 'Market moved this way';
  }

  if (componentName === 'similar_players') {
    const sampleSize = safeNumber(similar.sample_size);
    const averageGap = safeNumber(similar.average_gap_vs_line);
    if (sampleSize !== null && sampleSize > 0 && averageGap !== null) {
      const directionalGap = recommendation.pick === 'over' ? averageGap : -averageGap;
      if (directionalGap > 0.04) {
        return recommendation.pick === 'over'
          ? `${sampleSize.toFixed(0)} comps ${formatSigned(averageGap, 1)} vs line`
          : `${sampleSize.toFixed(0)} comps ${Math.abs(averageGap).toFixed(1)} below line`;
      }
    }
    return 'Comp sample supports it';
  }

  if (componentName === 'head_to_head') {
    const sampleSize = safeNumber(headToHead.sample_size);
    if (sampleSize !== null && sampleSize > 0) {
      return `${sampleSize.toFixed(0)} prior matchup samples`;
    }
    return 'Opponent history helps';
  }

  if (componentName === 'back_to_back' && backToBack.current_is_b2b === true) {
    return 'Back-to-back split matters';
  }

  return null;
}

function buildRowReasons(recommendation: EdgeScoreRecommendation) {
  const used = new Set<string>();
  const reasons: string[] = [];

  getPositiveSignals(recommendation, 5).forEach(([componentName]) => {
    const reason = buildRowReason(componentName, recommendation);
    if (reason && !used.has(reason)) {
      used.add(reason);
      reasons.push(reason);
    }
  });

  recommendation.reasons.forEach((reason) => {
    const compactReason = reason.replace(/\.$/, '');
    if (!used.has(compactReason) && compactReason.length > 0) {
      used.add(compactReason);
      reasons.push(compactReason);
    }
  });

  return reasons.slice(0, 3);
}

function buildSupportDescriptors(recommendation: EdgeScoreRecommendation) {
  return getPositiveSignals(recommendation, 8).map(([componentName, score]) => ({
    componentName,
    score,
    label: COMPONENT_LABELS[componentName] ?? componentName,
    detail: buildRowReason(componentName, recommendation) ?? 'This bucket is helping the spot.',
  }));
}

function buildOverviewNarrative(recommendation: EdgeScoreRecommendation) {
  const projection = recommendation.inputs?.projection ?? {};
  const recentForm = recommendation.inputs?.recent_form ?? {};
  const market = recommendation.inputs?.market ?? {};
  const baselineProjection = safeNumber(projection.baseline_projection);
  const projectionGap = safeNumber(projection.projection_gap);
  const recent10Average = safeNumber(recentForm.averages?.last_10);
  const recent10HitRate = safeNumber(recentForm.hit_rates?.last_10);
  const consensusLine = safeNumber(market.consensus_line);
  const lineDelta = safeNumber(market.line_delta_vs_consensus);
  const priceDelta = safeNumber(market.price_delta);

  const sentences: string[] = [];

  if (baselineProjection !== null && projectionGap !== null) {
    sentences.push(
      `${recommendation.player_name} projects ${projectionGap >= 0 ? 'to clear' : 'to stay below'} this number with a ${baselineProjection.toFixed(1)} baseline against a ${recommendation.line.toFixed(1)} line.`,
    );
  }

  if (recent10Average !== null && recent10HitRate !== null) {
    sentences.push(
      `Recent form adds support with a ${recent10Average.toFixed(1)} average over the last 10 and a ${recent10HitRate.toFixed(0)}% ${recommendation.pick} hit rate.`,
    );
  }

  if (consensusLine !== null && lineDelta !== null && Math.abs(lineDelta) >= 0.1) {
    sentences.push(
      `${recommendation.sportsbook_label} is also offering ${recommendation.line.toFixed(1)} while the broader market sits at ${consensusLine.toFixed(1)}.`,
    );
  } else if (priceDelta !== null && priceDelta > 0.01) {
    sentences.push(
      `${recommendation.sportsbook_label} is also hanging a better price than the market average for this side.`,
    );
  }

  if (!sentences.length) {
    sentences.push('This spot ranks because several context layers line up in the same direction on the current number.');
  }

  return sentences.join(' ');
}

function buildSupportSections(recommendation: EdgeScoreRecommendation) {
  const market = recommendation.inputs?.market ?? {};
  const movement = recommendation.inputs?.line_movement ?? {};
  const similar = recommendation.inputs?.similar_players ?? {};
  const headToHead = recommendation.inputs?.head_to_head ?? {};
  const backToBack = recommendation.inputs?.back_to_back ?? {};

  const sections: Array<{ title: string; body: string }> = [
    {
      title: 'Quick read',
      body: buildOverviewNarrative(recommendation),
    },
  ];

  const matchupInsight = buildMatchupInsight(recommendation);
  if (matchupInsight) {
    sections.push({
      title: 'Matchup lens',
      body: matchupInsight,
    });
  }

  const supportSentences: string[] = [];
  const lineDelta = safeNumber(market.line_delta_vs_consensus);
  const priceDelta = safeNumber(market.price_delta);
  const snapshotsSeen = safeNumber(movement.snapshots_seen);
  const favorableLineChange = safeNumber(movement.favorable_line_change);
  const favorablePriceChange = safeNumber(movement.favorable_price_change);
  const compSample = safeNumber(similar.sample_size);
  const compGap = safeNumber(similar.average_gap_vs_line);
  const h2hSample = safeNumber(headToHead.sample_size);
  const h2hAverage = safeNumber(headToHead.average);
  const directionalCompGap = compGap === null ? null : (recommendation.pick === 'over' ? compGap : -compGap);
  const directionalH2hGap = h2hAverage === null ? null : (recommendation.pick === 'over'
    ? h2hAverage - recommendation.line
    : recommendation.line - h2hAverage);

  if (lineDelta !== null && Math.abs(lineDelta) >= 0.1) {
    supportSentences.push(
      `${recommendation.sportsbook_label} is posting a ${Math.abs(lineDelta).toFixed(1)}-point better number than consensus for this side.`,
    );
  } else if (priceDelta !== null && priceDelta > 0.01) {
    supportSentences.push(
      `${recommendation.sportsbook_label} is offering a better price than the market average for this side.`,
    );
  }

  if (
    snapshotsSeen !== null
    && snapshotsSeen > 1
    && (
      (favorableLineChange !== null && favorableLineChange > 0.04)
      || (favorablePriceChange !== null && favorablePriceChange > 0.005)
    )
  ) {
    supportSentences.push(
      `The market has moved toward this side${favorableLineChange !== null ? ` by ${Math.abs(favorableLineChange).toFixed(1)} on the line` : ''}${favorablePriceChange !== null ? `${favorableLineChange !== null ? ' and' : ''} ${Math.abs(favorablePriceChange * 100).toFixed(0)} implied basis points on price` : ''}.`,
    );
  }

  if (compSample !== null && compSample >= 3 && compGap !== null && directionalCompGap !== null && directionalCompGap > 0.04) {
    supportSentences.push(
      recommendation.pick === 'over'
        ? `Similar-player comps are supportive too: ${compSample.toFixed(0)} close matches averaged ${formatSigned(compGap, 1)} versus their own lines.`
        : `Similar-player comps are supportive too: ${compSample.toFixed(0)} close matches averaged ${Math.abs(compGap).toFixed(1)} below their own lines.`,
    );
  }

  if (h2hSample !== null && h2hSample >= 2 && h2hAverage !== null && directionalH2hGap !== null && directionalH2hGap > 0.04) {
    supportSentences.push(
      `There is also some opponent history here, with ${h2hSample.toFixed(0)} prior meetings averaging ${h2hAverage.toFixed(1)}.`,
    );
  }

  if (backToBack.current_is_b2b === true) {
    supportSentences.push('Rest context is part of the read because this player is on the second night of a back-to-back.');
  }

  if (supportSentences.length) {
    sections.push({
      title: 'What keeps it up the board',
      body: supportSentences.join(' '),
    });
  }

  return sections;
}

interface EdgeBoardPanelProps {
  isOpen: boolean;
  payload?: EdgeScorePayload | null;
  isLoading?: boolean;
  activeRecommendationKey?: string | null;
  onClose: () => void;
  onSelectRecommendation: (recommendation: EdgeScoreRecommendation) => void;
}

export const EdgeBoardPanel: React.FC<EdgeBoardPanelProps> = ({
  isOpen,
  payload,
  isLoading = false,
  activeRecommendationKey = null,
  onClose,
  onSelectRecommendation,
}) => {
  const recommendations = payload?.recommendations ?? [];
  const leader = recommendations[0] ?? null;
  const [selectedRecommendationKey, setSelectedRecommendationKey] = useState<string | null>(null);

  const changeCount = Number(payload?.notification?.change_count ?? 0);
  const discordConfigured = Boolean(payload?.notification?.discord_configured);
  const updatedAtLabel = formatGeneratedAt(payload?.generated_at);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    setSelectedRecommendationKey(activeRecommendationKey ?? null);
  }, [isOpen, activeRecommendationKey]);

  useEffect(() => {
    if (!isOpen) {
      return undefined;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) {
    return null;
  }

  return (
    <>
      <button
        type="button"
        aria-label="Close Top Spots"
        className="fixed inset-0 z-[65] bg-black/50 backdrop-blur-sm"
        onClick={onClose}
      />

      <div className="fixed inset-x-0 top-[68px] z-[70] flex justify-center px-2 sm:top-[72px] sm:px-4">
        <section className="flex max-h-[calc(100vh-84px)] w-full max-w-[1120px] flex-col overflow-hidden rounded-xl border border-borderMedium bg-bgElevation0 shadow-2xl sm:max-h-[calc(100vh-92px)]">
          <div className="flex items-start justify-between gap-4 border-b border-borderMedium px-4 py-4 sm:px-5">
            <div className="min-w-0">
              <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-fgSubtle">
                Top Spots
              </div>
              <h2 className="mt-1 text-lg font-bold text-white sm:text-xl">
                Today&apos;s scouting board
              </h2>
              <p className="mt-1 max-w-2xl text-sm leading-6 text-fgSubtle">
                Signal Score is our quick ranking number. This board helps you find today&apos;s strongest prop spots fast, understand why they stand out, and jump into the full player dashboard when you want more detail.
              </p>
            </div>

            <div className="flex items-center gap-2">
              <div className="hidden items-center gap-1.5 rounded-lg border border-borderMedium bg-bgCanvas px-3 py-2 text-xs text-fgSubtle sm:flex">
                <Clock3 className="h-3.5 w-3.5" />
                {updatedAtLabel}
              </div>
              <button
                type="button"
                onClick={onClose}
                className="flex h-9 w-9 items-center justify-center rounded-lg border border-borderMedium bg-bgCanvas text-gray-400 transition-colors hover:text-white"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto custom-scrollbar p-4 sm:p-5">
            <div className="mb-4 flex flex-wrap gap-2">
              <div className={`rounded-lg border px-3 py-2 text-[11px] ${getSummaryChipTone('count')}`}>
                <span className="font-bold text-white">{recommendations.length || '--'}</span> live picks
              </div>
              {payload?.summary?.active_players && (
                <div className={`rounded-lg border px-3 py-2 text-[11px] ${getSummaryChipTone('count')}`}>
                  <span className="font-bold text-white">{payload.summary.active_players}</span> active players
                </div>
              )}
              <div className={`rounded-lg border px-3 py-2 text-[11px] ${getSummaryChipTone('changes')}`}>
                <span className="font-bold text-white">{changeCount}</span> fresh changes
              </div>
              <div className={`rounded-lg border px-3 py-2 text-[11px] ${getSummaryChipTone('delivery')}`}>
                Delivery: <span className="font-bold text-white">{discordConfigured ? 'Discord webhook' : 'Local artifact only'}</span>
              </div>
              {leader && (
                <div className={`rounded-lg border px-3 py-2 text-[11px] ${getSummaryChipTone('leader')}`}>
                  Leader: <span className="font-bold text-white">{leader.player_name}</span>
                </div>
              )}
            </div>

            {recommendations.length > 0 ? (
              <div className="space-y-2">
                {recommendations.map((recommendation) => {
                  const isSelected = recommendation.recommendation_key === selectedRecommendationKey;
                  const isActive = recommendation.recommendation_key === activeRecommendationKey;
                  const rowReasons = buildRowReasons(recommendation);
                  const supportDepth = getSupportDepth(recommendation.confidence);
                  const signalTone = getSignalTone(recommendation.edge_score);
                  const supportSections = isSelected ? buildSupportSections(recommendation) : [];
                  const supportDescriptors = isSelected ? buildSupportDescriptors(recommendation) : [];

                  return (
                    <div
                      key={recommendation.recommendation_key}
                      className={`w-full rounded-lg border transition-colors ${isSelected
                        ? 'border-blue500/40 bg-bgElevation1'
                        : 'border-borderMedium bg-bgCanvas'
                        }`}
                    >
                      <div
                        role="button"
                        tabIndex={0}
                        onClick={() => setSelectedRecommendationKey((current) => (
                          current === recommendation.recommendation_key ? null : recommendation.recommendation_key
                        ))}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            setSelectedRecommendationKey((current) => (
                              current === recommendation.recommendation_key ? null : recommendation.recommendation_key
                            ));
                          }
                        }}
                        className={`w-full px-4 py-3 text-left ${isSelected ? '' : 'hover:bg-bgElevation1/70'}`}
                      >
                        <div className="flex flex-col gap-3 lg:grid lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1.5fr)_130px_110px] lg:items-center">
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className={`inline-flex min-w-[36px] items-center justify-center rounded-md px-2 py-1 text-xs font-bold ${isSelected
                                ? 'bg-blue500 text-white'
                                : 'bg-bgElevation1 text-fgSubtle'
                                }`}>
                                #{recommendation.rank}
                              </span>
                              {isActive && (
                                <span className="rounded-md border border-borderMedium bg-bgElevation0 px-2 py-1 text-[10px] font-bold uppercase tracking-[0.16em] text-fgSubtle">
                                  On dashboard
                                </span>
                              )}
                            </div>
                            <div className="mt-3 truncate text-sm font-bold text-white">
                              {recommendation.player_name}
                            </div>
                            <div className="truncate text-[11px] uppercase tracking-[0.16em] text-fgSubtle">
                              {recommendation.team}
                              {recommendation.opponent ? ` vs ${recommendation.opponent}` : ''}
                              {recommendation.game_time_et ? ` • ${recommendation.game_time_et}` : ''}
                            </div>
                          </div>

                          <div className="min-w-0">
                            <div className="text-sm font-semibold text-white">
                              {renderMarketLabel(recommendation)}
                            </div>
                            <div className="mt-1 text-[11px] text-fgSubtle">
                              {recommendation.sportsbook_label} • {recommendation.odds_display}
                            </div>
                            {rowReasons.length > 0 && (
                              <div className="mt-2 flex flex-wrap gap-2">
                                {rowReasons.map((reason, index) => (
                                  <span
                                    key={reason}
                                    className={`rounded-full border border-borderMedium bg-bgElevation0 px-2.5 py-1 text-[11px] leading-5 text-fgSubtle ${index === 1 ? 'hidden sm:inline-flex' : ''} ${index === 2 ? 'hidden lg:inline-flex' : ''}`}
                                  >
                                    {reason}
                                  </span>
                                ))}
                              </div>
                            )}
                          </div>

                          <div className="flex items-center justify-between gap-3 lg:block">
                            <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-fgSubtle lg:mb-1">
                              Signal Score
                            </div>
                            <div
                              className="inline-flex rounded-md border px-2.5 py-1 font-chakra text-xl font-bold"
                              style={signalTone.solidStyle}
                            >
                              {recommendation.edge_score.toFixed(1)}
                            </div>
                            <div
                              className="mt-1 inline-flex items-center gap-1.5 rounded-full border border-borderMedium bg-bgElevation0 px-2 py-0.5 text-[10px] font-semibold text-fgSubtle"
                            >
                              <span
                                className="h-1.5 w-1.5 rounded-full"
                                style={supportDepth.tone.dotStyle}
                              />
                              {supportDepth.label}
                            </div>
                          </div>

                          <div className="flex items-center justify-between gap-3 lg:justify-end">
                            <div className="hidden items-center gap-1 text-[10px] font-bold uppercase tracking-[0.14em] text-fgSubtle sm:inline-flex">
                              {isSelected ? 'Hide details' : 'See details'}
                              <ChevronRight className={`h-3.5 w-3.5 transition-transform ${isSelected ? 'rotate-90' : ''}`} />
                            </div>
                            <button
                              type="button"
                              onClick={(event) => {
                                event.stopPropagation();
                                onSelectRecommendation(recommendation);
                              }}
                              className="inline-flex items-center gap-1 rounded-md border border-borderMedium bg-bgElevation0 px-2.5 py-1.5 text-[10px] font-bold uppercase tracking-[0.14em] text-white transition-colors hover:bg-bgElevation1"
                            >
                              Open
                              <ChevronRight className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        </div>
                      </div>

                      {isSelected && (
                        <div className="border-t border-borderMedium bg-bgElevation0/80 px-4 py-4">
                          <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                            <div className="min-w-0">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="rounded-md border border-borderMedium px-2 py-1 text-[10px] font-bold uppercase tracking-[0.16em] text-fgSubtle">
                                  {recommendation.sportsbook_label}
                                </span>
                                {recommendation.recommendation_key === activeRecommendationKey && (
                                  <span className="rounded-md bg-blue500 px-2 py-1 text-[10px] font-bold uppercase tracking-[0.16em] text-white">
                                    On dashboard
                                  </span>
                                )}
                              </div>
                              <div className="mt-3 text-sm font-semibold text-white">
                                {renderMarketLabel(recommendation)}
                              </div>
                              <div className="mt-1 text-[11px] text-fgSubtle">
                                {recommendation.odds_display} on {recommendation.sportsbook_label}
                              </div>
                            </div>

                            <div className="grid min-w-full gap-2 sm:min-w-[280px] lg:min-w-[300px]">
                              <div className="grid grid-cols-2 gap-2">
                                <div className="rounded-lg border border-borderMedium bg-bgElevation0 px-3 py-3">
                                  <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-fgSubtle">
                                    Signal Score
                                  </div>
                                  <div
                                    className="mt-2 inline-flex rounded-md border px-3 py-1 font-chakra text-2xl font-bold"
                                    style={signalTone.solidStyle}
                                  >
                                    {recommendation.edge_score.toFixed(1)}
                                  </div>
                                </div>

                                <div className="rounded-lg border border-borderMedium bg-bgElevation0 px-3 py-3">
                                  <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-fgSubtle">
                                    How Much Backs It
                                  </div>
                                  <div
                                    className="mt-2 inline-flex items-center gap-2 rounded-full border border-borderMedium bg-bgElevation1 px-2 py-0.5 text-sm font-bold text-white"
                                  >
                                    <span
                                      className="h-2 w-2 rounded-full"
                                      style={supportDepth.tone.dotStyle}
                                    />
                                    {supportDepth.label}
                                  </div>
                                  <div className="mt-1 text-[11px] leading-5 text-fgSubtle">
                                    {recommendation.confidence.toFixed(0)}% coverage
                                  </div>
                                </div>
                              </div>

                              <button
                                type="button"
                                onClick={() => onSelectRecommendation(recommendation)}
                                className="inline-flex items-center justify-center gap-2 rounded-lg border border-borderMedium bg-bgElevation0 px-3 py-2 text-xs font-bold uppercase tracking-[0.14em] text-white transition-colors hover:bg-bgElevation1"
                              >
                                Open In Dashboard
                                <ExternalLink className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          </div>

                          <div className="grid gap-4 lg:grid-cols-[1.35fr_0.95fr]">
                            <div>
                              <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.18em] text-fgSubtle">
                                Why this spot stands out
                              </div>
                              <div className="space-y-2">
                                {supportSections.map((section) => (
                                  <div
                                    key={section.title}
                                    className="rounded-lg border border-borderMedium bg-bgElevation0 px-3 py-3"
                                  >
                                    <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-fgSubtle">
                                      {section.title}
                                    </div>
                                    <div className="mt-2 text-sm leading-6 text-fgSubtle">
                                      {section.body}
                                    </div>
                                  </div>
                                ))}
                              </div>
                            </div>

                            <div className="space-y-4">
                              <div className="rounded-lg border border-borderMedium bg-bgElevation0 px-3 py-3">
                                <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-fgSubtle">
                                  What supports it
                                </div>
                                <div className="mt-3 flex flex-wrap gap-2">
                                  {supportDescriptors.length > 0 ? supportDescriptors.map((descriptor) => (
                                    <div
                                      key={descriptor.componentName}
                                      className="inline-flex items-center rounded-full border border-borderMedium bg-bgCanvas px-3 py-1.5 text-[11px] text-fgSubtle"
                                    >
                                      <span
                                        className="mr-2 h-2 w-2 rounded-full"
                                        style={getComponentTone(descriptor.score).dotStyle}
                                      />
                                      <span className="font-semibold text-white">{descriptor.label}</span>
                                      <span className="ml-2">{descriptor.detail}</span>
                                    </div>
                                  )) : (
                                    <div className="rounded-full border border-borderMedium bg-bgCanvas px-3 py-1.5 text-[11px] text-fgSubtle">
                                      Waiting for more support context on this spot.
                                    </div>
                                  )}
                                </div>
                              </div>

                              <div
                                className="rounded-lg border border-borderMedium bg-bgElevation0 px-3 py-3"
                                style={{ boxShadow: `inset 3px 0 0 ${supportDepth.tone.accent}` }}
                              >
                                <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.18em] text-fgSubtle">
                                  <span
                                    className="h-2 w-2 rounded-full"
                                    style={supportDepth.tone.dotStyle}
                                  />
                                  How Much Backs It
                                </div>
                                <div className="mt-2 text-sm font-semibold text-white">
                                  {supportDepth.detail}
                                </div>
                              </div>
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="rounded-lg border border-borderMedium bg-bgCanvas px-4 py-10 text-center text-sm text-fgSubtle">
                {isLoading
                  ? 'Loading current Top Spots slate...'
                  : (payload?.summary?.unavailable_reason as string | undefined) || 'No ranked props are available yet for the active slate.'}
              </div>
            )}

            <div className="mt-4 flex flex-col gap-2 rounded-lg border border-borderMedium bg-bgCanvas px-3 py-3 text-[11px] text-fgSubtle sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-center gap-2">
                <TrendingUp className="h-3.5 w-3.5 text-white" />
                Scan the board first, expand a row where it sits, then use Open to sync it into the dashboard.
              </div>
              <div className="hidden whitespace-nowrap sm:block">
                This board stays separate so the player dashboard stays clean.
              </div>
            </div>
          </div>
        </section>
      </div>
    </>
  );
};
