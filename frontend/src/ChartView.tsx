import { BarChart, LineChart, ScatterChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import ReactEChartsCore from "echarts-for-react/lib/core";

import type { ChartResult } from "./types";

echarts.use([
  BarChart,
  LineChart,
  ScatterChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
]);

export function ChartView({ chart }: { chart: ChartResult }) {
  const series = chart.y.map((field) => ({
    name: field,
    type: chart.type,
    data:
      chart.type === "scatter"
        ? chart.data.map((row) => [row[chart.x], row[field]])
        : chart.data.map((row) => row[field]),
    smooth: chart.type === "line",
    symbolSize: chart.type === "scatter" ? 8 : 5,
  }));
  const option = {
    animationDuration: 260,
    color: ["#176b55", "#c0593e", "#426a91"],
    grid: { left: 54, right: 24, top: 36, bottom: 48 },
    legend: { top: 4, textStyle: { color: "#4f5954" } },
    tooltip: { trigger: chart.type === "scatter" ? "item" : "axis" },
    xAxis: {
      type: chart.type === "scatter" ? "value" : "category",
      data: chart.type === "scatter" ? undefined : chart.data.map((row) => row[chart.x]),
      axisLabel: { color: "#68716c", hideOverlap: true },
      axisLine: { lineStyle: { color: "#cfd5d1" } },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: "#68716c" },
      splitLine: { lineStyle: { color: "#e8ebe8" } },
    },
    series,
  };

  return (
    <div className="chart-wrap">
      <ReactEChartsCore echarts={echarts} option={option} style={{ height: 320 }} notMerge />
      <p className="chart-scope">
        展示 {chart.displayed_points.toLocaleString()} / {chart.source_points.toLocaleString()} 个点
        {chart.downsampled ? "（已下采样）" : ""}
      </p>
    </div>
  );
}
