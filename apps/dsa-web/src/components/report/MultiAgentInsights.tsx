import type React from 'react';
import type { MultiAgentInsights as MultiAgentInsightsData, ReportLanguage } from '../../types/analysis';
import { Card, Tooltip } from '../common';
import { DashboardPanelHeader } from '../dashboard';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';

interface MultiAgentInsightsProps {
  insights?: MultiAgentInsightsData;
  language?: ReportLanguage;
}

type AgentLabelKey = 'agentLabelTechnical' | 'agentLabelIntel' | 'agentLabelMacroIntel' | 'agentLabelRisk';

const AGENT_LABEL_KEY: Record<string, AgentLabelKey> = {
  technical: 'agentLabelTechnical',
  intel: 'agentLabelIntel',
  macro_intel: 'agentLabelMacroIntel',
  risk: 'agentLabelRisk',
};

/** 将 agentName / sourceAgent 转换为可读的多语言标签，找不到映射时回退为原始值 */
const getAgentLabel = (agentName: string, text: Record<AgentLabelKey, string>): string => {
  const labelKey = AGENT_LABEL_KEY[agentName];
  return labelKey ? text[labelKey] : agentName;
};

interface AttributionSegment {
  key: keyof NonNullable<MultiAgentInsightsData['signalAttribution']>;
  labelKey: AgentLabelKey;
  color: string;
}

const ATTRIBUTION_SEGMENTS: AttributionSegment[] = [
  { key: 'technicalIndicators', labelKey: 'agentLabelTechnical', color: '#3b82f6' },
  { key: 'newsSentiment', labelKey: 'agentLabelIntel', color: '#10b981' },
  { key: 'fundamentals', labelKey: 'agentLabelRisk', color: '#f59e0b' },
  { key: 'marketConditions', labelKey: 'agentLabelMacroIntel', color: '#8b5cf6' },
];

/**
 * 多方观点区组件 - 展示技术面/消息面/宏观面/风险面等多智能体的观点、看多看空要点与信号权重占比
 */
export const MultiAgentInsights: React.FC<MultiAgentInsightsProps> = ({ insights, language = 'zh' }) => {
  if (!insights) {
    return null;
  }

  const { opinions, bullishPoints, bearishPoints, signalAttribution } = insights;
  if (opinions.length === 0 && bullishPoints.length === 0 && bearishPoints.length === 0) {
    return null;
  }

  const reportLanguage = normalizeReportLanguage(language);
  const text = getReportText(reportLanguage);

  return (
    <Card variant="bordered" padding="md" className="home-panel-card">
      <DashboardPanelHeader title={text.multiAgentInsightsTitle} />

      {opinions.length > 0 && (
        <div className="flex flex-col gap-1 mb-4">
          {opinions.map((opinion) => {
            const label = getAgentLabel(opinion.agentName, text);
            return (
              <div key={opinion.agentName} className="text-sm">
                <div className="flex items-center justify-between">
                  <span className="text-muted-text">{label}</span>
                  <span className="font-mono">
                    {opinion.signal} · {opinion.confidence.toFixed(2)}
                  </span>
                </div>
                {opinion.reasoning && (
                  <p className="mt-0.5 text-xs leading-5 text-secondary-text">{opinion.reasoning}</p>
                )}
              </div>
            );
          })}
        </div>
      )}

      {signalAttribution && (
        <div className="mb-4">
          <div className="text-xs text-muted-text mb-1">{text.signalAttributionTitle}</div>
          <div className="flex h-2 w-full overflow-hidden rounded-full">
            {ATTRIBUTION_SEGMENTS.map((segment) => {
              const value = signalAttribution[segment.key] ?? 0;
              return (
                <div key={segment.key} style={{ width: `${value}%` }}>
                  {/* Tooltip's trigger defaults to inline-flex; force `flex`
                      here (twMerge drops the conflicting inline-flex) since
                      inline-flex breaks percentage height/width resolution
                      for its nested child in this specific block-level
                      parent chain (reproduced in a real browser: the segment
                      silently fails to paint any color despite every
                      computed style reporting correct values). */}
                  <Tooltip content={`${text[segment.labelKey]}: ${value}%`} className="flex h-full w-full">
                    <div className="h-full w-full" style={{ backgroundColor: segment.color }} />
                  </Tooltip>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {(bullishPoints.length > 0 || bearishPoints.length > 0) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <div className="text-xs font-semibold text-green-600 mb-2">{text.bullishPointsTitle}</div>
            <ul className="list-disc list-inside space-y-1 text-sm">
              {bullishPoints.map((point, idx) => (
                <li key={idx}>
                  {point.text} <span className="text-muted-text">({getAgentLabel(point.sourceAgent, text)})</span>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <div className="text-xs font-semibold text-red-600 mb-2">{text.bearishPointsTitle}</div>
            <ul className="list-disc list-inside space-y-1 text-sm">
              {bearishPoints.map((point, idx) => (
                <li key={idx}>
                  {point.text} <span className="text-muted-text">({getAgentLabel(point.sourceAgent, text)})</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </Card>
  );
};
