import { useRef, type KeyboardEvent, type PointerEvent } from "react";
import { clampWidth } from "./useStudioLayout";

export function Splitter({ label, value, min, max, direction = 1, onChange }: {
  label: string; value: number; min: number; max: number; direction?: 1 | -1; onChange: (width: number) => void;
}) {
  const drag = useRef<{ pointerId: number; x: number; width: number } | null>(null);
  function finish(event: PointerEvent<HTMLDivElement>) {
    if (drag.current?.pointerId !== event.pointerId) return;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }
  function key(event: KeyboardEvent<HTMLDivElement>) {
    const step = event.shiftKey ? 48 : 16;
    const next = event.key === "Home" ? min : event.key === "End" ? max : event.key === "ArrowLeft" ? value - step * direction : event.key === "ArrowRight" ? value + step * direction : null;
    if (next === null) return;
    event.preventDefault(); event.stopPropagation(); onChange(clampWidth(next, min, max));
  }
  return <div className="studio-splitter" role="separator" aria-label={label} aria-orientation="vertical" tabIndex={0} aria-valuenow={Math.round(value)} aria-valuemin={min} aria-valuemax={Math.round(max)}
    onKeyDown={key} onPointerDown={event => {
      if (event.button !== 0) return;
      event.preventDefault(); event.currentTarget.focus();
      drag.current = { pointerId: event.pointerId, x: event.clientX, width: value };
      event.currentTarget.setPointerCapture(event.pointerId);
    }} onPointerMove={event => {
      const start = drag.current;
      if (start?.pointerId === event.pointerId) onChange(clampWidth(start.width + (event.clientX - start.x) * direction, min, max));
    }} onPointerUp={finish} onPointerCancel={finish} onLostPointerCapture={() => { drag.current = null; }} />;
}
