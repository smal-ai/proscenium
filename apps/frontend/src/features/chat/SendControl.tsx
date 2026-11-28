import { useEffect, useRef, useState } from "react";
import { ArrowUp, ChevronDown } from "lucide-react";

export function SendControl({ mode, disabled, onMode, onSend }: { mode: "queue" | "steer"; disabled: boolean; onMode: (mode: "queue" | "steer") => void; onSend: () => void }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    menu.current?.querySelector<HTMLButtonElement>(`[aria-checked="true"]`)?.focus();
    const outside = (event: PointerEvent) => { if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false); };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  function close() { setOpen(false); toggle.current?.focus(); }
  return <div className="split-send" ref={root} onKeyDown={event => {
    if (open && event.key === "Escape" && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) { event.preventDefault(); close(); }
    if (open && ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const buttons = [...(menu.current?.querySelectorAll<HTMLButtonElement>("button") ?? [])];
      const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
      buttons[event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowUp" ? -1 : 1) + buttons.length) % buttons.length]?.focus();
    }
    if (event.key === "Tab") setOpen(false);
  }}>
    <button className="primary send" aria-label={mode === "queue" ? "Queue message" : "Steer active work"} disabled={disabled} onClick={onSend}>{mode === "queue" ? "Queue" : "Steer"}<ArrowUp size={16} /></button>
    <button ref={toggle} className="primary send-mode" aria-label="Select send mode" aria-haspopup="menu" aria-expanded={open} aria-controls="send-mode-menu" onClick={() => setOpen(!open)}><ChevronDown size={13} /></button>
    {open && <div ref={menu} id="send-mode-menu" role="menu" aria-label="Send mode" className="send-mode-menu">{(["queue", "steer"] as const).map(value => <button key={value} role="menuitemradio" aria-checked={value === mode} onClick={() => { onMode(value); close(); }}>{value === "queue" ? "Queue" : "Steer"}</button>)}</div>}
  </div>;
}
