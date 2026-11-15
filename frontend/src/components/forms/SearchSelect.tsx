import { useId, useMemo, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { ChevronDown, Search } from "lucide-react";

export interface SearchSelectOption {
  value: string;
  // The identifying text an operator types, such as a subject code.
  label: string;
  // Secondary searchable text, such as a subject title.
  description?: string;
}

interface SearchSelectProps {
  label: string;
  value: string;
  options: SearchSelectOption[];
  onChange: (value: string) => void;
  placeholder?: string;
  loading?: boolean;
  error?: string;
  emptyMessage?: string;
  loadingMessage?: string;
  className?: string;
  // Replaces the default label/description substring match; gets the
  // lowercased, trimmed query.
  filter?: (option: SearchSelectOption, query: string) => boolean;
}

function optionText(option: SearchSelectOption): string {
  return option.description ? `${option.label} — ${option.description}` : option.label;
}

function matches(option: SearchSelectOption, query: string): boolean {
  return `${option.label} ${option.description ?? ""}`.toLowerCase().includes(query);
}

const controlClass =
  "w-full border border-outline-variant rounded-lg pl-9 pr-9 py-2.5 bg-surface-container-lowest outline-none focus:border-primary";

/**
 * A combobox with a filtered listbox popup, following the ARIA 1.2 pattern so
 * the catalog stays reachable from the keyboard alone. It reports selection by
 * `value` only — a caller derives everything else from its own record, which is
 * what keeps a derived field such as Subject units out of the submitted form.
 */
export default function SearchSelect({
  label,
  value,
  options,
  onChange,
  placeholder,
  loading = false,
  error = "",
  emptyMessage = "No records available yet.",
  loadingMessage = "Loading records...",
  className = "",
  filter = matches,
}: SearchSelectProps) {
  const id = useId();
  const inputId = `${id}-input`;
  const listId = `${id}-list`;
  const statusId = `${id}-status`;
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);

  const selected = options.find((option) => option.value === value) ?? null;
  const trimmed = query.trim().toLowerCase();
  const filtered = useMemo(
    () => (trimmed ? options.filter((option) => filter(option, trimmed)) : options),
    [options, trimmed, filter],
  );

  // Typing narrows the list, so the previously highlighted row may no longer
  // exist. Clamping keeps the active descendant on a real option.
  const active = filtered.length === 0 ? -1 : Math.min(activeIndex, filtered.length - 1);
  const activeOptionId = open && active >= 0 ? `${id}-option-${active}` : undefined;

  function openList() {
    setOpen(true);
    setQuery("");
    setActiveIndex(Math.max(0, options.findIndex((option) => option.value === value)));
  }

  function close() {
    setOpen(false);
    setQuery("");
  }

  function select(option: SearchSelectOption) {
    onChange(option.value);
    close();
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        openList();
        return;
      }
      if (filtered.length === 0) return;
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((current) => {
        const next = Math.min(current, filtered.length - 1) + step;
        return (next + filtered.length) % filtered.length;
      });
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      if (!open) return;
      event.preventDefault();
      setActiveIndex(event.key === "Home" ? 0 : filtered.length - 1);
      return;
    }
    if (event.key === "Enter") {
      if (!open || active < 0) return;
      // The combobox lives inside a form, so a selection must not double as a
      // submission.
      event.preventDefault();
      select(filtered[active]);
      return;
    }
    if (event.key === "Escape") {
      if (!open) return;
      event.preventDefault();
      // Closing the list is this keypress's whole job; a dialog's own Escape
      // handler must not also close the dialog.
      event.stopPropagation();
      close();
      return;
    }
    if (event.key === "Tab" && open) close();
  }

  const status = error
    ? ""
    : loading
      ? loadingMessage
      : options.length === 0
        ? emptyMessage
        : open && filtered.length === 0
          ? `No matches for "${query.trim()}".`
          : open
            ? `${filtered.length} ${filtered.length === 1 ? "result" : "results"} available.`
            : "";
  // A plain result count is noise on screen but useful to a screen reader; the
  // states an operator must act on stay visible.
  const statusHidden = !error && !loading && options.length > 0 && filtered.length > 0;

  return (
    <div className={`block space-y-1.5 ${className}`}>
      <label htmlFor={inputId} className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">
        {label}
      </label>
      <div className="relative">
        <Search aria-hidden="true" className="w-4 h-4 absolute left-3 top-[13px] text-on-surface-variant" />
        <input
          id={inputId}
          type="text"
          role="combobox"
          autoComplete="off"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={activeOptionId}
          aria-describedby={statusId}
          aria-invalid={error ? true : undefined}
          placeholder={placeholder}
          value={open ? query : selected ? optionText(selected) : ""}
          onChange={(event) => {
            setQuery(event.target.value);
            setActiveIndex(0);
            if (!open) setOpen(true);
          }}
          onClick={openList}
          onBlur={close}
          onKeyDown={handleKeyDown}
          className={controlClass}
        />
        {/* The arrow toggles the list. It sits over the input, so without its
            own handler a click on it would land nowhere. Keyboard users open
            the list from the input itself, so it stays out of the tab order. */}
        <button
          type="button"
          tabIndex={-1}
          aria-label={open ? `Hide ${label} options` : `Show ${label} options`}
          // Keep focus in the input so its blur does not close the list first.
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => {
            if (open) {
              close();
              return;
            }
            document.getElementById(inputId)?.focus();
            openList();
          }}
          className="absolute right-1 top-1 bottom-1 w-8 flex items-center justify-center rounded-md text-on-surface-variant hover:bg-surface-container-high"
        >
          <ChevronDown aria-hidden="true" className={`w-4 h-4 transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
        <ul
          id={listId}
          role="listbox"
          aria-label={label}
          hidden={!open}
          className="absolute z-20 left-0 right-0 mt-1 max-h-56 overflow-y-auto border border-outline-variant rounded-lg bg-surface-container-lowest shadow-lg"
        >
          {filtered.map((option, index) => (
            <li
              key={option.value}
              id={`${id}-option-${index}`}
              role="option"
              aria-selected={option.value === value}
              // Pressing the mouse must not blur the input before the click
              // lands, which would close the list out from under the pointer.
              onMouseDown={(event) => event.preventDefault()}
              onMouseMove={() => setActiveIndex(index)}
              onClick={() => select(option)}
              className={`px-3 py-2 cursor-pointer ${index === active ? "bg-primary/10" : ""}`}
            >
              <strong>{option.label}</strong>
              {option.description && <span className="text-sm text-on-surface-variant ml-2">{option.description}</span>}
            </li>
          ))}
        </ul>
      </div>
      {error
        ? <p id={statusId} role="alert" className="text-sm text-error">{error}</p>
        : <p id={statusId} role="status" className={statusHidden ? "sr-only" : "text-sm text-on-surface-variant"}>{status}</p>}
    </div>
  );
}
