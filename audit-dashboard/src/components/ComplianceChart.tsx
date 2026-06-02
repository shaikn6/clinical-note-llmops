import React from 'react';
import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  Legend,
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
} from 'recharts';
import type { PHICategory } from '../types';

const CHART_COLORS = [
  '#ef4444', '#3b82f6', '#22c55e', '#eab308', '#a855f7',
  '#f97316', '#14b8a6', '#6366f1', '#ec4899', '#f59e0b',
];

interface DistributionEntry {
  type: PHICategory;
  count: number;
  percentage: number;
}

interface ComplianceChartProps {
  distribution: DistributionEntry[];
  dailyVolume?: Array<{ date: string; count: number }>;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ name: string; value: number; payload: DistributionEntry }>;
}

const CustomTooltip: React.FC<CustomTooltipProps> = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  const entry = payload[0].payload;
  return (
    <div className="chart-tooltip">
      <p className="tooltip-label">{entry.type}</p>
      <p>Count: <strong>{entry.count.toLocaleString()}</strong></p>
      <p>Share: <strong>{entry.percentage.toFixed(1)}%</strong></p>
    </div>
  );
};

export const ComplianceChart: React.FC<ComplianceChartProps> = ({
  distribution,
  dailyVolume = [],
}) => {
  const pieData = distribution.map((d) => ({ ...d, name: d.type }));

  return (
    <div className="compliance-charts">
      <section className="chart-section" aria-label="PHI entity type distribution">
        <h3 className="chart-title">PHI Entity Distribution</h3>
        <ResponsiveContainer width="100%" height={320}>
          <PieChart>
            <Pie
              data={pieData}
              dataKey="count"
              nameKey="name"
              cx="50%"
              cy="50%"
              outerRadius={110}
              innerRadius={55}
              paddingAngle={2}
            >
              {pieData.map((_, index) => (
                <Cell key={`cell-${index}`} fill={CHART_COLORS[index % CHART_COLORS.length]} />
              ))}
            </Pie>
            <Tooltip content={<CustomTooltip />} />
            <Legend
              layout="vertical"
              align="right"
              verticalAlign="middle"
              formatter={(value) => (
                <span style={{ color: '#cbd5e1', fontSize: '0.8rem' }}>{value}</span>
              )}
            />
          </PieChart>
        </ResponsiveContainer>
      </section>

      {dailyVolume.length > 0 && (
        <section className="chart-section" aria-label="Daily processing volume">
          <h3 className="chart-title">Daily Processing Volume</h3>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={dailyVolume} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis
                dataKey="date"
                tick={{ fill: '#94a3b8', fontSize: 11 }}
                tickFormatter={(v: string) => v.slice(5)}
              />
              <YAxis tick={{ fill: '#94a3b8', fontSize: 11 }} />
              <Tooltip
                contentStyle={{ background: '#0f172a', border: '1px solid #1e3a5f', color: '#e2e8f0' }}
                labelStyle={{ color: '#38bdf8' }}
              />
              <Bar dataKey="count" fill="#22c55e" radius={[3, 3, 0, 0]} name="Notes processed" />
            </BarChart>
          </ResponsiveContainer>
        </section>
      )}
    </div>
  );
};

export default ComplianceChart;
