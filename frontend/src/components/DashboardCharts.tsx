import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { SenteroTrendPoint, SenteroTrendSeries } from '@shared/api/client';

type ChartPoint = {
  timestamp?: string;
  label: string;
  dayLabel?: string;
  value: number | null;
  hasData: boolean;
  anomalyCount?: number;
  comparisonStatus?: string;
};

export function MainActivityTrendChart({
  series,
  points,
  selectedDate,
  onSelectDate,
}: {
  series?: SenteroTrendSeries;
  points: SenteroTrendPoint[];
  selectedDate: string;
  onSelectDate: (date: string) => void;
}) {
  if (!series) return <TrendEmptyState />;

  const data = chartData(series).map((point) => {
    const day = points.find((item) => item.date === point.timestamp);
    return {
      ...point,
      anomalyCount: day?.anomaly_score ? 1 : 0,
      comparisonStatus: trendComparisonStatus(point.value, point.hasData, series),
    };
  });

  const visible = data.filter((point) => point.hasData);
  if (visible.length < 2) return <TrendEmptyState />;

  const selectedPoint = data.find((point) => point.timestamp === selectedDate);
  const yTicks = chartTicks(visible, series);

  return (
    <div
      className="sc-main-chart"
      role="img"
      aria-label={`Aktivitätsverlauf: ${series.interpretation}`}
      tabIndex={0}
    >
      <div className="sc-main-chart-plot">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={data}
            margin={{ top: 10, right: 8, bottom: 2, left: 0 }}
            onClick={(state) => {
              const date = activeDateFromChartState(state, data);
              if (date) onSelectDate(date);
            }}
          >
            <CartesianGrid
              vertical={false}
              stroke="#dfeae4"
              strokeOpacity={0.5}
              strokeDasharray="2 10"
            />

            {baselineReady(series) && (
              <ReferenceArea
                y1={series.baseline.lower ?? undefined}
                y2={series.baseline.upper ?? undefined}
                fill="#dcefe7"
                fillOpacity={0.58}
                strokeOpacity={0}
              />
            )}

            {selectedPoint?.hasData && (
              <ReferenceLine
                x={selectedPoint.label}
                stroke="#8bbba7"
                strokeWidth={1}
                strokeDasharray="3 7"
                strokeOpacity={0.55}
              />
            )}

            <XAxis
              dataKey="label"
              tickLine={false}
              axisLine={false}
              interval={xAxisInterval(data.length)}
              minTickGap={34}
              tick={(props) => (
                <SelectableDayTick
                  {...props}
                  selectedDate={selectedDate}
                  onSelectDate={onSelectDate}
                />
              )}
            />

            <YAxis
              tickLine={false}
              axisLine={false}
              width={40}
              ticks={yTicks}
              tickFormatter={(value) => formatAxisValue(Number(value), series.unit)}
            />

            <Tooltip
              content={<MainChartTooltip unit={series.unit} />}
              cursor={{ stroke: '#8bbba7', strokeWidth: 1, strokeDasharray: '3 6' }}
            />

            <Line
              type="linear"
              dataKey="value"
              stroke="#2c8663"
              strokeWidth={2.6}
              strokeLinecap="round"
              strokeLinejoin="round"
              dot={(props) => (
                <ActivityDot
                  {...props}
                  selectedDate={selectedDate}
                  onSelectDate={onSelectDate}
                />
              )}
              activeDot={(props) => <ActiveActivityDot {...props} />}
              connectNulls={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function TrendSparkline({ series }: { series: SenteroTrendSeries }) {
  const data = chartData(series).filter((point) => point.hasData);
  if (data.length < 2) {
    return <div className="sc-sparkline-empty" aria-hidden="true" />;
  }

  return (
    <div className="sc-sparkline" aria-hidden="true">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 6, right: 2, bottom: 4, left: 2 }}>
          {baselineReady(series) && (
            <ReferenceArea
              y1={series.baseline.lower ?? undefined}
              y2={series.baseline.upper ?? undefined}
              fill="#dff3ea"
              fillOpacity={0.75}
              strokeOpacity={0}
            />
          )}
          <Area
            type="linear"
            dataKey="value"
            stroke="#2f7d5f"
            fill="transparent"
            strokeWidth={2.2}
            dot={false}
            activeDot={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function TrendLineChart({ series }: { series: SenteroTrendSeries }) {
  const data = chartData(series);
  const visible = data.filter((point) => point.hasData);
  if (visible.length < 2) return <TrendEmptyState />;

  return (
    <div className="sc-trend-chart" role="img" aria-label={`${series.label}: ${series.interpretation}`}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 18, right: 10, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="#dfeae4" strokeOpacity={0.5} strokeDasharray="2 10" />
          {baselineReady(series) && (
            <ReferenceArea
              y1={series.baseline.lower ?? undefined}
              y2={series.baseline.upper ?? undefined}
              fill="#dff3ea"
              fillOpacity={0.72}
              strokeOpacity={0}
            />
          )}
          <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={20} />
          <YAxis tickLine={false} axisLine={false} width={unitWidth(series.unit)} tickFormatter={(value) => formatValue(Number(value), series.unit)} />
          <Tooltip content={<ChartTooltip unit={series.unit} />} trigger="click" />
          <Line
            type="linear"
            dataKey="value"
            stroke="#2f7d5f"
            strokeWidth={2.6}
            strokeLinecap="round"
            strokeLinejoin="round"
            dot={{ r: 3 }}
            activeDot={{ r: 6 }}
            connectNulls={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function TrendBarChart({ series }: { series: SenteroTrendSeries }) {
  const data = chartData(series);
  const visible = data.filter((point) => point.hasData);
  if (visible.length < 1) return <TrendEmptyState />;

  return (
    <div className="sc-trend-chart" role="img" aria-label={`${series.label}: ${series.interpretation}`}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 18, right: 10, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="#dfeae4" strokeOpacity={0.5} strokeDasharray="2 10" />
          {baselineReady(series) && (
            <ReferenceArea
              y1={series.baseline.lower ?? undefined}
              y2={series.baseline.upper ?? undefined}
              fill="#dff3ea"
              fillOpacity={0.72}
              strokeOpacity={0}
            />
          )}
          <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={20} />
          <YAxis tickLine={false} axisLine={false} width={unitWidth(series.unit)} tickFormatter={(value) => formatValue(Number(value), series.unit)} />
          <Tooltip content={<ChartTooltip unit={series.unit} />} trigger="click" />
          <Bar dataKey="value" fill="#2f7d5f" radius={[6, 6, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function RangeSelector({ value, onChange }: { value: number; onChange: (value: 7 | 14 | 30) => void }) {
  return (
    <nav className="sc-range-tabs" aria-label="Zeitraum wählen">
      {[7, 14, 30].map((item) => (
        <button key={item} className={value === item ? 'active' : ''} type="button" onClick={() => onChange(item as 7 | 14 | 30)}>
          {item} Tage
        </button>
      ))}
    </nav>
  );
}

export function formatTrendValue(value: number | null | undefined, unit: string) {
  if (value == null) return 'keine Daten';
  return formatValue(value, unit);
}

function trendComparisonStatus(
  value: number | null,
  hasData: boolean,
  series: SenteroTrendSeries,
) {
  if (!hasData || value == null) return 'Keine Daten';
  if (!baselineReady(series)) return 'Persönlicher Bereich wird noch gelernt';

  const lower = Number(series.baseline.lower);
  const upper = Number(series.baseline.upper);

  if (value < lower) return 'Unter dem persönlichen Bereich';
  if (value > upper) return 'Über dem persönlichen Bereich';
  return 'Im persönlichen Bereich';
}

function ChartTooltip({ active, payload, label, unit }: { active?: boolean; payload?: Array<{ value?: number }>; label?: string; unit: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="sc-chart-tooltip">
      <strong>{label}</strong>
      <span>{formatTrendValue(payload[0].value, unit)}</span>
    </div>
  );
}

function MainChartTooltip({
  active,
  payload,
  label,
  unit,
}: {
  active?: boolean;
  payload?: Array<{
    value?: number;
    payload?: {
      comparisonStatus?: string;
      anomalyCount?: number;
      dayLabel?: string;
    };
  }>;
  label?: string;
  unit: string;
}) {
  if (!active || !payload?.length) return null;
  const item = payload[0].payload;

  return (
    <div className="sc-chart-tooltip">
      <strong>{item?.dayLabel || label}</strong>
      <span>Aktivität: {formatTrendValue(payload[0].value, unit)}</span>
      <span>{item?.comparisonStatus || 'Tageswert'}</span>
      {item?.anomalyCount ? (
        <span>
          {item.anomalyCount === 1
            ? '1 Auffälligkeit erkannt'
            : `${item.anomalyCount} Auffälligkeiten erkannt`}
        </span>
      ) : null}
    </div>
  );
}

function ActivityDot(props: { cx?: number; cy?: number; payload?: { timestamp?: string; hasData?: boolean }; selectedDate: string; onSelectDate: (date: string) => void }) {
  const { cx, cy, payload, selectedDate, onSelectDate } = props;
  if (cx == null || cy == null || !payload?.hasData || !payload.timestamp) return <g />;

  const selected = payload.timestamp === selectedDate;
  return (
    <g
      role="button"
      aria-label={`${shortDate(payload.timestamp)} auswählen`}
      tabIndex={0}
      onClick={(event) => {
        event.stopPropagation();
        onSelectDate(payload.timestamp || '');
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onSelectDate(payload.timestamp || '');
        }
      }}
    >
      <circle cx={cx} cy={cy} r={14} fill="transparent" />
      {selected && <circle cx={cx} cy={cy} r={7} fill="#23664d" stroke="#fffefa" strokeWidth={3} />}
    </g>
  );
}

function ActiveActivityDot(props: { cx?: number; cy?: number }) {
  const { cx, cy } = props;
  if (cx == null || cy == null) return <g />;

  return (
    <g>
      <circle cx={cx} cy={cy} r={12} fill="#e4f3ec" opacity={0.95} />
      <circle cx={cx} cy={cy} r={6} fill="#23664d" stroke="#fffefa" strokeWidth={3} />
    </g>
  );
}

function SelectableDayTick(props: { x?: number | string; y?: number | string; payload?: { value?: string; payload?: ChartPoint }; selectedDate: string; onSelectDate: (date: string) => void }) {
  const { payload, selectedDate, onSelectDate } = props;
  const x = Number(props.x || 0);
  const y = Number(props.y || 0);
  const date = payload?.payload?.timestamp || '';
  const selected = date === selectedDate;
  const disabled = !payload?.payload?.hasData;
  const label = payload?.payload?.dayLabel || String(payload?.value || '');

  return (
    <g
      className={`sc-chart-day-tick ${selected ? 'selected' : ''} ${disabled ? 'disabled' : ''}`}
      role="button"
      aria-label={`${label} auswählen`}
      tabIndex={disabled ? -1 : 0}
      onClick={() => {
        if (!disabled && date) onSelectDate(date);
      }}
      onKeyDown={(event) => {
        if (!disabled && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault();
          onSelectDate(date);
        }
      }}
    >
      {selected && <rect x={x - 25} y={y - 12} width={50} height={30} rx={8} fill="#e4f3ec" stroke="#9ccab8" />}
      <text x={x} y={y + 8} textAnchor="middle">{label}</text>
    </g>
  );
}

function activeDateFromChartState(state: unknown, data: ChartPoint[]) {
  const chartState = state as {
    activePayload?: Array<{ payload?: { timestamp?: string } }>;
    activeLabel?: string;
  } | null;

  const payloadDate = chartState?.activePayload?.[0]?.payload?.timestamp;
  if (payloadDate) return String(payloadDate);

  const activeLabel = chartState?.activeLabel;
  if (!activeLabel) return '';

  return data.find((point) => point.label === activeLabel || point.dayLabel === activeLabel)?.timestamp || '';
}

function TrendEmptyState() {
  return (
    <div className="sc-chart-empty">
      <strong>Noch nicht genügend Verlaufsdaten</strong>
      <p>Sentero benötigt mehrere verwertbare Tage, um diesen Verlauf darzustellen.</p>
    </div>
  );
}

function chartData(series: SenteroTrendSeries): ChartPoint[] {
  return series.points.map((point) => ({
    timestamp: point.timestamp,
    label: shortDate(point.timestamp),
    dayLabel: dayAxisLabel(point.timestamp),
    value: point.has_data === false || point.value == null ? null : Number(point.value),
    hasData: point.has_data !== false && point.value != null,
  }));
}

function baselineReady(series: SenteroTrendSeries) {
  return series.baseline.lower != null && series.baseline.upper != null;
}

function chartTicks(points: ChartPoint[], series: SenteroTrendSeries) {
  const values = points.map((point) => Number(point.value)).filter((value) => Number.isFinite(value));
  const baselineValues = baselineReady(series) ? [Number(series.baseline.lower), Number(series.baseline.upper)] : [];
  const all = [...values, ...baselineValues].filter((value) => Number.isFinite(value));
  if (!all.length) return undefined;

  const max = Math.max(...all);

  if (series.unit === 'minutes') {
    const top = Math.max(60, Math.ceil(max / 60) * 60);
    return [0, Math.round(top / 2), top];
  }

  if (series.unit === 'time') {
    const min = Math.min(...all);
    return [Math.floor(min / 60) * 60, Math.round((min + max) / 2), Math.ceil(max / 60) * 60];
  }

  const top = Math.max(2, Math.ceil(max));
  return [0, Math.round(top / 2), top];
}

function xAxisInterval(length: number) {
  if (length > 21) return 4;
  if (length > 10) return 2;
  return 0;
}

function shortDate(value: string) {
  const [year, month, day] = value.split('-').map(Number);
  const date = new Date(year, (month || 1) - 1, day || 1);
  return new Intl.DateTimeFormat('de-DE', { day: '2-digit', month: '2-digit' }).format(date);
}

function dayAxisLabel(value: string) {
  const [year, month, day] = value.split('-').map(Number);
  const date = new Date(year, (month || 1) - 1, day || 1);
  if (isSameLocalDay(date, new Date())) return 'Heute';
  const weekday = new Intl.DateTimeFormat('de-DE', { weekday: 'short' }).format(date);
  return `${weekday} ${String(day || 1).padStart(2, '0')}.${String(month || 1).padStart(2, '0')}.`;
}

function isSameLocalDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function formatValue(value: number, unit: string) {
  if (unit === 'time') {
    const minutes = Math.max(0, Math.round(value));
    return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
  }

  if (unit === 'minutes') {
    if (value >= 60) {
      const hours = Math.floor(value / 60);
      const rest = Math.round(value % 60);
      return rest ? `${hours} Std. ${rest} Min.` : `${hours} Std.`;
    }
    return `${Math.round(value)} Min.`;
  }

  return String(Math.round(value));
}

function formatAxisValue(value: number, unit: string) {
  if (unit === 'minutes') {
    if (value <= 0) return '0';
    if (value % 60 === 0) return `${Math.round(value / 60)}h`;
    if (value >= 60) return `${Math.round((value / 60) * 10) / 10}h`;
    return `${Math.round(value)}m`;
  }

  if (unit === 'time') {
    const minutes = Math.max(0, Math.round(value));
    return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}`;
  }

  return String(Math.round(value));
}

function unitWidth(unit: string) {
  return unit === 'time' ? 48 : 44;
}
