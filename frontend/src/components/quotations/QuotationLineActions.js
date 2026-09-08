import React, { useLayoutEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

export default function QuotationLineActions({ label, isDirty, canSave, canEditImage, canUpload, canDelete,
  includeImage, onSave, onToggleImage, onUpload, onDelete }) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({});
  const trigger = useRef(null);
  const menu = useRef(null);
  const fileInput = useRef(null);
  const menuId = useId();

  const close = (restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) trigger.current?.focus();
  };

  useLayoutEffect(() => {
    if (!open) return undefined;
    const rect = trigger.current.getBoundingClientRect();
    const height = menu.current.getBoundingClientRect().height;
    setPosition({
      left: Math.max(8, Math.min(rect.right - 208, window.innerWidth - 216)),
      top: Math.max(8, rect.bottom + height + 8 <= window.innerHeight ? rect.bottom + 4 : rect.top - height - 4),
    });
    (menu.current.querySelector('button:not(:disabled)') || menu.current).focus({ preventScroll: true });
    const dismissOutside = (event) => {
      if (!menu.current?.contains(event.target) && !trigger.current?.contains(event.target)) setOpen(false);
    };
    const dismissOnMove = (event) => {
      if (!menu.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener('pointerdown', dismissOutside);
    window.addEventListener('resize', dismissOnMove);
    window.addEventListener('scroll', dismissOnMove, true);
    return () => {
      document.removeEventListener('pointerdown', dismissOutside);
      window.removeEventListener('resize', dismissOnMove);
      window.removeEventListener('scroll', dismissOnMove, true);
    };
  }, [open]);

  const navigateMenu = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close(true);
    } else if (event.key === 'Tab') {
      // Return to the row before continuing its natural tab order.
      event.preventDefault();
      close(true);
    } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const buttons = Array.from(menu.current.querySelectorAll('button:not(:disabled)'));
      if (!buttons.length) return;
      const index = buttons.indexOf(document.activeElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
        : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next].focus();
    }
  };

  return <div className="qm-line-actions">
    {isDirty
      ? <><span className="qm-sr-only">Unsaved</span><button type="button" className="qm-secondary small" title="Save unsaved line changes" disabled={!canSave} onClick={onSave}>Save</button></>
      : <span className="qm-line-state saved">Saved</span>}
    <button type="button" className="qm-secondary small qm-line-menu-trigger" ref={trigger}
      aria-label={`Actions for ${label}`} title={`Actions for ${label}`} aria-haspopup="menu"
      aria-expanded={open} aria-controls={open ? menuId : undefined}
      onClick={() => setOpen(!open)} onKeyDown={(event) => {
        if (event.key === 'ArrowDown') { event.preventDefault(); setOpen(true); }
      }}>
      <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="currentColor">
        <circle cx="5" cy="12" r="2" /><circle cx="12" cy="12" r="2" /><circle cx="19" cy="12" r="2" />
      </svg>
    </button>
    <input ref={fileInput} type="file" hidden tabIndex={-1} accept="image/png,image/jpeg,image/webp"
      disabled={!canUpload} aria-label={`Upload image for ${label}`} onChange={(event) => {
        const file = event.target.files?.[0];
        event.target.value = '';
        if (file) onUpload(file);
      }} />
    {open && createPortal(<div className="qm-line-menu" style={position} ref={menu} id={menuId}
      role="menu" tabIndex={-1} aria-label={`Actions for ${label}`} onKeyDown={navigateMenu}>
      <button type="button" role="menuitemcheckbox" aria-checked={includeImage} disabled={!canEditImage}
        onClick={onToggleImage}><span className="qm-menu-check" aria-hidden="true">{includeImage ? '✓' : ''}</span>Image in PDF</button>
      <button type="button" role="menuitem" disabled={!canUpload}
        onClick={() => { close(true); fileInput.current?.click(); }}>Upload image</button>
      <button type="button" role="menuitem" className="danger" disabled={!canDelete}
        onClick={() => { close(true); onDelete(); }}>Delete line</button>
    </div>, document.body)}
  </div>;
}
