import { BarChart, LineChart, ScatterChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import ReactEChartsCore from "echarts-for-react/lib/core";
import { Download } from "lucide-react";
import { useRef } from "react";

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
  const chartRef = useRef<ReactEChartsCore>(null);
  const pointCount = chart.data.length;
  const yAxisName = chart.y.length === 1 ? chart.y[0] : `数值（${chart.y.join("、")}）`;
  const series = chart.y.map((field) => ({
    name: field,
    type: chart.type,
    data:
      chart.type === "scatter"
        ? chart.data.map((row) => [row[chart.x], row[field]])
        : chart.data.map((row) => row[field]),
    smooth: chart.type === "line",
    showSymbol: pointCount <= 1000,
    symbolSize: chart.type === "scatter" ? 8 : 5,
    large: pointCount > 5000,
    largeThreshold: 5000,
    progressive: 5000,
    progressiveThreshold: 10000,
  }));
  const option = {
    animation: pointCount <= 2000,
    animationDuration: 260,
    color: ["#176b55", "#c0593e", "#426a91"],
    grid: { left: 54, right: 24, top: 36, bottom: 48 },
    legend: { top: 4, textStyle: { color: "#4f5954" } },
    tooltip: { trigger: chart.type === "scatter" ? "item" : "axis" },
    xAxis: {
      type: chart.type === "scatter" ? "value" : "category",
      data: chart.type === "scatter" ? undefined : chart.data.map((row) => row[chart.x]),
      name: chart.x,
      nameLocation: "middle",
      nameGap: 30,
      axisLabel: { color: "#68716c", hideOverlap: true },
      axisLine: { lineStyle: { color: "#cfd5d1" } },
    },
    yAxis: {
      type: "value",
      name: yAxisName,
      nameLocation: "middle",
      nameGap: 42,
      nameTextStyle: { color: "#68716c", align: "center" },
      axisLabel: { color: "#68716c" },
      splitLine: { lineStyle: { color: "#e8ebe8" } },
    },
    series,
  };

  function downloadImage() {
    const instance = chartRef.current?.getEchartsInstance();
    if (!instance) return;
    const url = instance.getDataURL({
      type: "png",
      pixelRatio: 2,
      backgroundColor: "#ffffff",
    });
    const link = document.createElement("a");
    link.download = `dataquery-${chart.type}-${chart.x}.png`;
    link.href = url;
    link.click();
  }

  return (
    <div className="chart-wrap">
      <ReactEChartsCore ref={chartRef} echarts={echarts} option={option} style={{ height: 360 }} notMerge />
      <div className="chart-actions">
        <button className="compact-button" type="button" onClick={downloadImage} title="下载图表图片">
          <Download size={14} />
          下载图片
        </button>
      </div>
      <p className="chart-scope">
        完整绘制 {chart.source_points.toLocaleString()} 个点
      </p>
    </div>
  );
}
