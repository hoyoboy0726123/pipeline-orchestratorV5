'use client'
import { useEffect, useRef, useState } from 'react'

/**
 * 一條水平 toolbar:子元素保持原本寬度,當總寬超過容器時,
 * 兩端會浮出半透明的 ‹ / › 提示;**滑鼠停在那個提示區**就會
 * 連續往那個方向捲動(rAF + transform、無壓縮按鈕)。
 *
 * 用途:canvas 左上角新增節點的按鈕列 —— 小螢幕時按鈕會超出視野,
 * 不想壓縮按鈕,改成 hover 邊緣自動捲動。
 */
export default function HoverScrollRow({
  children,
  className = '',
  speed = 5,
  edgeWidth = 56,
}: {
  children: React.ReactNode
  className?: string
  speed?: number       // 每幀像素數
  edgeWidth?: number   // 兩端 hover 觸發區寬度
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const trackRef = useRef<HTMLDivElement>(null)
  const scrollXRef = useRef(0)            // 真正的位置(以 ref 為主、避免每幀 setState)
  const maxOverflowRef = useRef(0)
  const hoverDirRef = useRef<'left' | 'right' | null>(null)
  const rafRef = useRef<number | null>(null)
  // 只用兩個 boolean state 給「箭頭要不要顯示」用,避免每幀重 render
  const [canLeft, setCanLeft] = useState(false)
  const [canRight, setCanRight] = useState(false)

  // 量內外寬、決定可滾範圍;同時 re-clamp scrollX
  useEffect(() => {
    const measure = () => {
      const inner = trackRef.current?.scrollWidth ?? 0
      const outer = containerRef.current?.clientWidth ?? 0
      const overflow = Math.max(0, inner - outer)
      maxOverflowRef.current = overflow
      // 視窗變窄、原本的 scrollX 超出新範圍 → 夾回去
      if (scrollXRef.current < -overflow) {
        scrollXRef.current = -overflow
        if (trackRef.current) trackRef.current.style.transform = `translateX(${-overflow}px)`
      }
      setCanLeft(scrollXRef.current < 0)
      setCanRight(scrollXRef.current > -overflow)
    }
    measure()
    const ro = new ResizeObserver(measure)
    if (containerRef.current) ro.observe(containerRef.current)
    if (trackRef.current) ro.observe(trackRef.current)
    window.addEventListener('resize', measure)
    return () => { ro.disconnect(); window.removeEventListener('resize', measure) }
  }, [children])

  const startHover = (dir: 'left' | 'right') => {
    hoverDirRef.current = dir
    if (rafRef.current != null) return
    const tick = () => {
      const d = hoverDirRef.current
      if (!d) { rafRef.current = null; return }
      const cur = scrollXRef.current
      const max = maxOverflowRef.current
      const next = d === 'right'
        ? Math.max(-max, cur - speed)
        : Math.min(0, cur + speed)
      if (next !== cur) {
        scrollXRef.current = next
        if (trackRef.current) trackRef.current.style.transform = `translateX(${next}px)`
        // 只在「箭頭要不要顯示」變了才 setState、避免每幀 re-render
        const newLeft = next < 0
        const newRight = next > -max
        if (newLeft !== canLeft) setCanLeft(newLeft)
        if (newRight !== canRight) setCanRight(newRight)
      }
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
  }
  const stopHover = () => { hoverDirRef.current = null }

  // 元件卸載清掉 rAF
  useEffect(() => () => { if (rafRef.current != null) cancelAnimationFrame(rafRef.current) }, [])

  return (
    <div
      ref={containerRef}
      className={`relative overflow-hidden ${className}`}
      style={{ maxWidth: 'calc(100vw - 4rem)' }}
    >
      <div
        ref={trackRef}
        className="flex gap-2 w-max"
        style={{ transform: `translateX(${scrollXRef.current}px)`, willChange: 'transform' }}
      >
        {children}
      </div>

      {/* 左側 hover 區(可滾左才顯示)*/}
      {canLeft && (
        <div
          onMouseEnter={() => startHover('left')}
          onMouseLeave={stopHover}
          className="absolute left-0 top-0 bottom-0 flex items-center justify-start pl-1.5 cursor-w-resize select-none"
          style={{
            width: edgeWidth,
            background: 'linear-gradient(to right, rgba(255,255,255,0.95) 30%, rgba(255,255,255,0))',
          }}
        >
          <span className="text-gray-500 text-xl font-semibold leading-none">‹</span>
        </div>
      )}

      {/* 右側 hover 區(可滾右才顯示)*/}
      {canRight && (
        <div
          onMouseEnter={() => startHover('right')}
          onMouseLeave={stopHover}
          className="absolute right-0 top-0 bottom-0 flex items-center justify-end pr-1.5 cursor-e-resize select-none"
          style={{
            width: edgeWidth,
            background: 'linear-gradient(to left, rgba(255,255,255,0.95) 30%, rgba(255,255,255,0))',
          }}
        >
          <span className="text-gray-500 text-xl font-semibold leading-none">›</span>
        </div>
      )}
    </div>
  )
}
