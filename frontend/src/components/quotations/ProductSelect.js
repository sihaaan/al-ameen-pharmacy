import React, { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import './ProductSelect.css';

const MAX_RESULTS = 10;
const MIN_SEARCH_LENGTH = 2;

// Build and sort once per catalogue/customer change, shared by every line picker.
export const buildProductCatalogue = (items, companyItems) => {
  const preferredIds = new Set(companyItems.map((item) => String(item.id)));
  const byId = new Map(items.map((item) => [String(item.id), item]));
  const options = Array.from(byId.values()).filter((item) => !item.canonical_product).map((item) => ({
    ...item,
    previouslyUsed: preferredIds.has(String(item.id)),
    searchText: [item.name, item.brand_name, item.sku, item.barcode, item.unit, item.pack_size]
      .filter(Boolean).join(' ').toLowerCase(),
  })).sort((a, b) => (
    Number(b.previouslyUsed) - Number(a.previouslyUsed) || a.name.localeCompare(b.name)
  ));
  return { options, byId };
};

const ProductSelect = ({
  catalogue,
  value,
  fallbackName = '',
  label,
  placeholder = 'Search products…',
  disabled = false,
  loading = false,
  allowCreate = false,
  onChange,
}) => {
  const [search, setSearch] = useState(null);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [position, setPosition] = useState({});
  const input = useRef(null);
  const picker = useRef(null);
  const popup = useRef(null);
  const popupId = useId();
  const listId = `${popupId}-list`;
  const helpId = `${popupId}-help`;
  const selectedId = String(value || '');
  const selectedProduct = catalogue.byId.get(selectedId);
  const selectedName = selectedId ? selectedProduct?.name || fallbackName || `Product ${selectedId}` : '';
  const expanded = open && !disabled && !loading;
  const query = (search || '').trim().toLowerCase();
  const { matches, hasMore } = useMemo(() => {
    // Closed/untouched rows do no catalogue scanning and render no suggestions.
    if (!expanded || query.length < MIN_SEARCH_LENGTH) return { matches: [], hasMore: false };
    const terms = query.split(/\s+/);
    const found = [];
    for (const item of catalogue.options) {
      if (!terms.every((term) => item.searchText.includes(term))) continue;
      if (found.length === MAX_RESULTS) return { matches: found, hasMore: true };
      found.push(item);
    }
    return { matches: found, hasMore: false };
  }, [catalogue, query, expanded]);
  const choices = allowCreate ? [...matches, { id: '__create__', name: '+ Create a new product…' }] : matches;
  const activeChoice = expanded && activeIndex >= 0 ? choices[activeIndex] : null;
  const optionId = (id) => `${listId}-${id}`;
  const activeOptionId = activeChoice ? optionId(activeChoice.id) : undefined;
  const blocked = () => disabled || loading || input.current?.matches(':disabled');
  const close = useCallback(() => { setOpen(false); setSearch(null); setActiveIndex(-1); }, []);
  const choose = (id) => {
    if (blocked()) return;
    input.current?.focus({ preventScroll: true });
    close();
    onChange(String(id));
  };

  useEffect(() => {
    setOpen(false);
    setSearch(null);
    setActiveIndex(-1);
  }, [selectedId, disabled, loading]);

  useLayoutEffect(() => {
    if (!expanded) return undefined;
    const positionPopup = () => {
      const rect = input.current.getBoundingClientRect();
      if (rect.bottom < 0 || rect.top > window.innerHeight || rect.right < 0 || rect.left > window.innerWidth) {
        close();
        return;
      }
      // A portal must not float over unrelated rows once its field is clipped
      // by a horizontally/vertically scrolling table or modal.
      for (let parent = input.current.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
        const clip = parent.getBoundingClientRect();
        const style = window.getComputedStyle(parent);
        const clippedX = /auto|scroll|hidden/.test(style.overflowX || style.overflow)
          && clip.width > 0 && (rect.right <= clip.left || rect.left >= clip.right);
        const clippedY = /auto|scroll|hidden/.test(style.overflowY || style.overflow)
          && clip.height > 0 && (rect.bottom <= clip.top || rect.top >= clip.bottom);
        if (clippedX || clippedY) { close(); return; }
      }
      const below = window.innerHeight - rect.bottom - 12;
      const above = rect.top - 12;
      const placeAbove = below < 240 && above > below;
      const width = Math.min(Math.max(rect.width, 320), window.innerWidth - 16);
      setPosition({
        width,
        left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
        ...(placeAbove ? { bottom: window.innerHeight - rect.top + 4 } : { top: rect.bottom + 4 }),
        maxHeight: Math.max(80, Math.min(360, placeAbove ? above : below)),
      });
    };
    const onMove = (event) => {
      if (!popup.current?.contains(event.target)) positionPopup();
    };
    const onOutside = (event) => {
      if (!picker.current?.contains(event.target) && !popup.current?.contains(event.target)) close();
    };
    positionPopup();
    window.addEventListener('resize', positionPopup);
    window.addEventListener('scroll', onMove, true);
    document.addEventListener('pointerdown', onOutside);
    return () => {
      window.removeEventListener('resize', positionPopup);
      window.removeEventListener('scroll', onMove, true);
      document.removeEventListener('pointerdown', onOutside);
    };
  }, [expanded, close]);

  useEffect(() => {
    if (activeOptionId) document.getElementById(activeOptionId)?.scrollIntoView?.({ block: 'nearest' });
  }, [activeOptionId]);

  const help = query.length < MIN_SEARCH_LENGTH ? 'Type at least 2 characters to search.'
    : matches.length === 0 ? 'No matching products. Try another search.'
      : hasMore ? '10 suggestions. Keep typing to narrow the results.'
        : `${matches.length} matching product${matches.length === 1 ? '' : 's'}.`;

  return (
    <div className="qm-product-picker" ref={picker}>
      <input
        ref={input}
        type="text"
        role="combobox"
        aria-label={label}
        aria-autocomplete="list"
        aria-expanded={expanded}
        aria-controls={expanded ? listId : undefined}
        aria-activedescendant={activeOptionId}
        aria-describedby={expanded ? helpId : undefined}
        aria-busy={loading}
        autoComplete="off"
        spellCheck={false}
        placeholder={loading ? 'Loading products…' : placeholder}
        title={selectedName || undefined}
        value={search === null ? selectedName : search}
        disabled={disabled || loading}
        onFocus={(event) => { if (!blocked()) setOpen(true); event.target.select(); }}
        onClick={() => { if (!blocked()) setOpen(true); }}
        onBlur={close}
        onChange={(event) => {
          if (blocked()) return;
          setSearch(event.target.value);
          setActiveIndex(-1);
          setOpen(true);
        }}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing || blocked()) return;
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            setOpen(true);
            if (choices.length) setActiveIndex((current) => event.key === 'ArrowDown'
              ? (current + 1) % choices.length : (current <= 0 ? choices.length - 1 : current - 1));
          }
          // Enter must never submit the containing add-line form.
          if (event.key === 'Enter') {
            event.preventDefault();
            if (activeChoice) choose(activeChoice.id);
          }
          if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            close();
          }
          if (event.key === 'Tab') close();
        }}
      />
      {selectedId && <button type="button" className="qm-product-clear" aria-label={`Clear ${label}`}
        title="Remove product match" disabled={disabled || loading} onClick={() => choose('')}>×</button>}
      {expanded && createPortal(
        <div className="qm-product-suggestions" ref={popup} style={position} onMouseDown={(event) => event.preventDefault()}>
          <div id={helpId} className="qm-product-search-help" role="status">{help}</div>
          <ul id={listId} role="listbox" aria-label={`${label} suggestions`}>
            {choices.map((item, index) => (
              <li key={item.id} id={optionId(item.id)} role="option" aria-selected={index === activeIndex}
                className={item.id === '__create__' ? 'qm-product-create' : ''}
                onMouseMove={() => setActiveIndex(index)} onClick={() => choose(item.id)}>
                <strong>{item.name}</strong>
                {item.id !== '__create__' && <>
                  <span>{[...new Set([item.brand_name, item.pack_size, item.unit, item.sku].filter(Boolean))].join(' · ')}</span>
                  {item.previouslyUsed && <small>Previously quoted for this customer</small>}
                  {item.identity_review_state === 'provisional' && <small className="qm-product-provisional">Provisional product</small>}
                </>}
              </li>
            ))}
          </ul>
        </div>, document.body
      )}
    </div>
  );
};

export default ProductSelect;
