import {
  Component, Input, ElementRef, ViewChild, AfterViewInit,
  OnDestroy, OnChanges, SimpleChanges, ChangeDetectionStrategy,
} from '@angular/core';
import { Chart, registerables } from 'chart.js';

Chart.register(...registerables);

export interface LineChartSeries {
  label: string;
  color: string;
  data: { x: number; y: number }[];
}

@Component({
  selector: 'nls-line-chart',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<div class="chart-wrap" [style.height.px]="height"><canvas #canvas></canvas></div>`,
  styles: [`
    :host { display: block; width: 100%; }
    .chart-wrap { width: 100%; position: relative; }
    canvas { width: 100% !important; height: 100% !important; }
  `],
})
export class LineChartComponent implements AfterViewInit, OnDestroy, OnChanges {
  @ViewChild('canvas') canvasRef!: ElementRef<HTMLCanvasElement>;

  @Input() series: LineChartSeries[] = [];
  @Input() height = 180;
  @Input() xLabel = 'Turn';
  @Input() yLabel = '';
  @Input() yMin?: number;
  @Input() yMax?: number;
  @Input() showLegend = true;

  private chart: Chart | null = null;
  private ready = false;

  ngAfterViewInit(): void {
    this.ready = true;
    this.buildChart();
  }

  ngOnChanges(_changes: SimpleChanges): void {
    if (this.ready) this.buildChart();
  }

  ngOnDestroy(): void {
    this.chart?.destroy();
  }

  private buildChart(): void {
    if (!this.canvasRef?.nativeElement) return;
    this.chart?.destroy();

    const datasets = this.series.map(s => ({
      label: s.label,
      data: s.data.map(p => ({ x: p.x, y: p.y })),
      borderColor: s.color,
      backgroundColor: s.color + '22',
      borderWidth: 1.5,
      pointRadius: 0,
      pointHitRadius: 6,
      tension: 0.3,
      fill: false,
    }));

    this.chart = new Chart(this.canvasRef.nativeElement, {
      type: 'line',
      data: { datasets } as any,
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            display: this.showLegend && this.series.length > 1,
            position: 'top',
            labels: { color: 'rgba(255,255,255,0.6)', boxWidth: 10, font: { size: 10 } },
          },
          tooltip: {
            backgroundColor: 'rgba(20,20,30,0.9)',
            titleColor: '#fff',
            bodyColor: 'rgba(255,255,255,0.8)',
            borderColor: 'rgba(255,255,255,0.1)',
            borderWidth: 1,
            padding: 8,
            bodyFont: { size: 11 },
            callbacks: {
              title: (items: any[]) => items[0] ? `${this.xLabel} ${items[0].parsed.x}` : '',
              label: (ctx: any) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(3)}`,
            },
          },
        },
        scales: {
          x: {
            type: 'linear',
            title: { display: !!this.xLabel, text: this.xLabel, color: 'rgba(255,255,255,0.4)', font: { size: 10 } },
            ticks: { color: 'rgba(255,255,255,0.35)', font: { size: 9 }, maxTicksLimit: 8 },
            grid: { color: 'rgba(255,255,255,0.05)' },
          },
          y: {
            min: this.yMin,
            max: this.yMax,
            title: { display: !!this.yLabel, text: this.yLabel, color: 'rgba(255,255,255,0.4)', font: { size: 10 } },
            ticks: { color: 'rgba(255,255,255,0.35)', font: { size: 9 }, maxTicksLimit: 6 },
            grid: { color: 'rgba(255,255,255,0.05)' },
          },
        },
      },
    });
  }
}
