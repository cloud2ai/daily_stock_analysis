import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MultiAgentInsights } from '../MultiAgentInsights';
import type { MultiAgentInsights as MultiAgentInsightsData } from '../../../types/analysis';

const baseInsights: MultiAgentInsightsData = {
  opinions: [
    { agentName: 'technical', signal: 'buy', confidence: 0.72, reasoning: 'MA金叉' },
    { agentName: 'macro_intel', signal: 'hold', confidence: 0.6, reasoning: '宏观缺乏强催化' },
  ],
  bullishPoints: [{ text: '政策支持', sourceAgent: 'macro_intel' }],
  bearishPoints: [{ text: '关税压力', sourceAgent: 'macro_intel' }],
  signalAttribution: {
    technicalIndicators: 35,
    newsSentiment: 20,
    fundamentals: 25,
    marketConditions: 20,
  },
};

describe('MultiAgentInsights', () => {
  it('renders agent opinions, bullish/bearish points, and signal attribution', () => {
    render(<MultiAgentInsights insights={baseInsights} language="zh" />);

    expect(screen.getByText('多方观点')).toBeInTheDocument();
    expect(screen.getByText('MA金叉')).toBeInTheDocument();
    expect(screen.getByText('政策支持')).toBeInTheDocument();
    expect(screen.getByText('关税压力')).toBeInTheDocument();
    expect(screen.getByText('看多')).toBeInTheDocument();
    expect(screen.getByText('看空')).toBeInTheDocument();
  });

  it('renders English labels when language is en', () => {
    render(<MultiAgentInsights insights={baseInsights} language="en" />);
    expect(screen.getByText('Multi-Agent Views')).toBeInTheDocument();
    expect(screen.getByText('Bullish')).toBeInTheDocument();
    expect(screen.getByText('Bearish')).toBeInTheDocument();
  });

  it('returns null when insights is undefined', () => {
    const { container } = render(<MultiAgentInsights insights={undefined} language="zh" />);
    expect(container).toBeEmptyDOMElement();
  });

  it('returns null when opinions, bullishPoints, and bearishPoints are all empty', () => {
    const { container } = render(
      <MultiAgentInsights
        insights={{ opinions: [], bullishPoints: [], bearishPoints: [] }}
        language="zh"
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders bullish column even when bearishPoints is empty, and vice versa', () => {
    render(
      <MultiAgentInsights
        insights={{ ...baseInsights, bearishPoints: [] }}
        language="zh"
      />,
    );
    expect(screen.getByText('政策支持')).toBeInTheDocument();
  });
});
