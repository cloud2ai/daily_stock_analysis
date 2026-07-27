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

    // Every bullish/bearish point must be attributed to its source agent, not shown anonymously.
    const bullishItem = screen.getByText('政策支持').closest('li');
    const bearishItem = screen.getByText('关税压力').closest('li');
    expect(bullishItem).toHaveTextContent('宏观政策');
    expect(bearishItem).toHaveTextContent('宏观政策');
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

  it('renders bullish column even when bearishPoints is empty', () => {
    render(
      <MultiAgentInsights
        insights={{ ...baseInsights, bearishPoints: [] }}
        language="zh"
      />,
    );
    expect(screen.getByText('政策支持')).toBeInTheDocument();
    expect(screen.queryByText('关税压力')).not.toBeInTheDocument();
  });

  it('renders bearish column even when bullishPoints is empty', () => {
    render(
      <MultiAgentInsights
        insights={{ ...baseInsights, bullishPoints: [] }}
        language="zh"
      />,
    );
    expect(screen.getByText('关税压力')).toBeInTheDocument();
    expect(screen.queryByText('政策支持')).not.toBeInTheDocument();
  });

  it('renders the signal attribution stacked bar with correct widths and tooltip labels', () => {
    const { container } = render(<MultiAgentInsights insights={baseInsights} language="zh" />);

    expect(screen.getByText('权重占比')).toBeInTheDocument();

    const segments = container.querySelectorAll('[title$="%"]');
    expect(segments).toHaveLength(4);

    const titles = Array.from(segments).map((segment) => segment.getAttribute('title'));
    expect(titles).toEqual([
      '技术面: 35%',
      '个股消息面: 20%',
      '风险面: 25%',
      '宏观政策: 20%',
    ]);

    const widths = Array.from(segments).map((segment) => (segment as HTMLElement).style.width);
    expect(widths).toEqual(['35%', '20%', '25%', '20%']);
  });

  it('does not render the signal attribution bar when signalAttribution is absent', () => {
    render(
      <MultiAgentInsights
        insights={{ ...baseInsights, signalAttribution: undefined }}
        language="zh"
      />,
    );
    expect(screen.queryByText('权重占比')).not.toBeInTheDocument();
  });
});
