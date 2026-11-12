/**
 * LevelMeter · 26 段电平柱
 *
 * 来源：李梓萌界面原型 v1.1（`components.jsx` 的 LevelMeter），2026-11-12 人工转写。
 *
 * ## 为什么移植它
 *
 * 主线原先用原生 `<meter>` 元素画电平 —— 那是浏览器默认样式，**随平台变化**，
 * 且是横向的，和「麦克风测试弹窗」这个场景的竖向电平柱对不上。
 * ADR-003 §0 的验收要求是「试采 3 秒 / 电平 / 回听 / good·low·silent 判定」，
 * 其中「电平」这一项需要一个**看得懂、好看、且能表达瞬时峰值**的呈现。
 *
 * ## 移植纪律（这一版定下来的规矩，后面照做）
 *
 * 1. **原型是 `.jsx` + `useState` + 内存假数据，本文件是 `.tsx`** —— 必须人工转写，
 *    不能 cp。特别地：原型里的 `useMemo` 只为算波形，本组件**保留**（26 段高度
 *    每次渲染重算不划算），但**不引入**原型的任何假数据。
 * 2. **纯展示、无外部状态**：所有视觉输入都走 props；不订阅 store、不发事件。
 *    这样它可以被任何页面复用，也能被 SSR（`renderToStaticMarkup`）测试。
 * 3. **颜色一律走 `var(--x)`**，不许写死 hex —— 与 2026-11-12 色值定版同一套纪律。
 */

import { useMemo } from 'react';

export type LevelMeterTone = 'cyan' | 'purple';

export interface LevelMeterProps {
  /** 瞬时电平，0~1 */
  level?: number;
  /** 是否处于活动状态（活动时波形有脉动动画） */
  active?: boolean;
  /** 主题色：青=线下课，紫=线上课 */
  tone?: LevelMeterTone;
  /** 电平柱段数 */
  bars?: number;
}

/**
 * 生成一段固定的波形基线。
 *
 * 用两个不同频率的正弦/余弦叠加，得到"有起伏但不成规律重复"的形状 ——
 * 纯随机数会让每次渲染的形状都跳变，反而更像噪声；固定基线则每次都一样，
 * 视觉上更稳定，叠加实时 level 后既有秩序又有生气。
 */
function buildWave(bars: number): number[] {
  return Array.from({ length: bars }, (_, index) => {
    const wave = Math.sin(index * 0.72) * 18 + Math.cos(index * 1.31) * 10;
    const slope = (index / bars) * 26;
    return Math.max(12, Math.min(98, 30 + wave + slope));
  });
}

export function LevelMeter({
  level = 0.55,
  active = false,
  tone = 'cyan',
  bars = 26,
}: LevelMeterProps) {
  const heights = useMemo(() => buildWave(bars), [bars]);

  return (
    <div className={`level-meter ${tone}${active ? ' active' : ''}`} aria-hidden="true">
      {heights.map((height, index) => (
        <span
          key={index}
          style={{
            height: `${active ? Math.min(100, height + level * 24) : height * 0.62}%`,
            animationDelay: `${index * 0.035}s`,
          }}
        />
      ))}
    </div>
  );
}

export default LevelMeter;
